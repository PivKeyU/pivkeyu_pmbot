import asyncio
import threading
import aiosqlite
import os
import logging
from collections import deque
from datetime import datetime

logger = logging.getLogger(__name__)


class _PooledConnection:
    """async-with wrapper around a pooled aiosqlite connection.

    ``async with db_manager.get_connection() as db:`` checks out one of a small
    set of persistent connections and returns it to the pool afterwards instead
    of opening/closing a brand new connection on every statement, which is the
    dominant latency/overhead in this hot path.  On exception the transaction is
    rolled back so the pooled connection is never left in a dirty state.
    """

    __slots__ = ("_raw", "_owner")

    def __init__(self, owner):
        self._raw = None
        self._owner = owner

    def __getattr__(self, item):
        if self._raw is None:
            raise AttributeError(item)
        return getattr(self._raw, item)

    async def __aenter__(self):
        self._raw = await self._owner._acquire()
        return self._raw

    async def __aexit__(self, exc_type, exc, tb):
        if self._raw is None:
            return False
        raw, self._raw = self._raw, None
        reusable = True
        if exc_type is not None:
            try:
                await raw.rollback()
            except Exception:
                logger.exception("回滚事务失败，关闭该连接")
                reusable = False
        else:
            # 确保归还连接前事务状态干净，
            # 避免把未提交事务泄漏给下一个使用者
            try:
                await raw.commit()
            except Exception:
                logger.exception("提交事务失败，关闭该连接")
                reusable = False
        if reusable:
            try:
                await self._owner._release(raw)
            except Exception:
                # 归还失败不应掩盖调用方的异常，连接将随引用被 GC 回收
                logger.exception("归还连接失败")
        else:
            # 事务状态已不可信（可能残留未提交事务），不能放回池中复用
            await self._owner._discard(raw)
        return False


class DatabaseManager:
    _instance = None
    _POOL_SIZE = 8

    def __new__(cls, db_path=None):
        db_path = db_path or './data/bot.db'
        if cls._instance is None:
            cls._instance = super(DatabaseManager, cls).__new__(cls)
            cls._instance.db_path = db_path
            cls._instance._init_pool()
            cls._instance.ensure_data_directory()
            # 池中常驻的连接让 aiosqlite 的非 daemon worker 线程永不退出，
            # 解释器退出时 threading._shutdown 会无限期 join 它们（atexit 模块
            # 的回调在 join 之后才执行，救不了场）。threading._register_atexit
            # 注册的回调在 join 之前运行，在这里关掉所有池连接让 worker 线程
            # 自行退出。
            threading._register_atexit(cls._instance._close_on_exit)
        elif db_path and cls._instance.db_path != db_path:
            # 只更新路径（对之后新建的连接生效），绝不重复初始化池：重置会
            # 清空空闲连接并把 _live 归零，导致正在使用的连接数量失控，且旧
            # 空闲连接永远不会被关闭（泄漏）。
            cls._instance.db_path = db_path
            cls._instance.ensure_data_directory()
        return cls._instance

    def _init_pool(self):
        self._pool = deque()
        self._pool_size = self._POOL_SIZE
        # 当前由 Manager 创建且尚未关闭的连接数（含空闲与借出，不随归还回收）
        self._live = 0
        # 惰性初始化：asyncio.Condition 必须在 running loop 中创建，而模块
        # import 时还没有 loop（Python 3.11 会隐式绑定到一个永不运行的 loop，
        # 3.12+ 直接抛 RuntimeError）。首次真正使用时才在 _ensure_cond() 中创建。
        self._cond = None
        self._cond_loop = None

    def _ensure_cond(self):
        """返回绑定到当前 running loop 的 Condition（首次调用时惰性创建）。

        若之后换了一个新的事件循环（例如 bot 重启时新建 loop），也会重新创建，
        避免用到已废弃的旧 loop 对象。
        """
        loop = asyncio.get_running_loop()
        if self._cond is None or self._cond_loop is not loop:
            self._cond = asyncio.Condition()
            self._cond_loop = loop
        return self._cond

    def ensure_data_directory(self):
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)

    def get_connection(self):
        return _PooledConnection(self)

    async def _acquire(self):
        cond = self._ensure_cond()
        async with cond:
            # 优先复用空闲；少于上限时新建；否则等待一个连接归还。
            # 等待期间持锁，保证 wait/notify 配对与 _live 计数一致。
            while not self._pool and self._live >= self._pool_size:
                await cond.wait()
            if self._pool:
                raw = self._pool.popleft()
            else:
                raw = await aiosqlite.connect(self.db_path)
                await raw.execute("PRAGMA foreign_keys = ON")
                await raw.execute("PRAGMA busy_timeout = 5000")
                self._live += 1
            return raw

    async def _release(self, raw):
        cond = self._ensure_cond()
        async with cond:
            if len(self._pool) >= self._pool_size:
                # 极端情况：池已满，直接关闭避免超出容量
                if self._live > self._pool_size:
                    self._live -= 1
                await raw.close()
            else:
                self._pool.append(raw)
            # 只唤醒一个等待者：每归还一个连接只满足一个 _acquire
            cond.notify()

    async def close_all(self):
        """关闭全部空闲池连接（供进程退出钩子调用）。"""
        cond = self._ensure_cond()
        async with cond:
            while self._pool:
                raw = self._pool.popleft()
                self._live -= 1
                try:
                    await raw.close()
                except Exception:
                    logger.exception("关闭连接失败")
            cond.notify_all()

    def _close_on_exit(self):
        """threading 退出钩子：主事件循环已关闭，临时起一个 loop 同步关掉所有
        池连接，避免 aiosqlite 的非 daemon worker 线程在解释器退出时被
        无限期 join。"""
        try:
            asyncio.run(self.close_all())
        except Exception:
            logger.exception("进程退出时关闭连接池失败")

    async def _discard(self, raw):
        """关闭一个事务状态已不可信（提交/回滚失败）的连接，不放回池中复用。"""
        cond = self._ensure_cond()
        async with cond:
            if self._live > 0:
                self._live -= 1
            try:
                await raw.close()
            except Exception:
                logger.exception("关闭连接失败")
            cond.notify()

    async def initialize(self):
        async with self.get_connection() as db:
            await self.create_users_table(db)
            await self.create_messages_table(db)
            await self.create_blacklist_table(db)
            await self.create_admins_table(db)
            await self.create_verification_sessions_table(db)
            await self.create_settings_table(db)
            await self.create_statistics_table(db)
            await self.create_filtered_messages_table(db)
            await self.create_knowledge_base_table(db)
            await self.create_exemptions_table(db)
            await self.create_user_groups_table(db)
            await self.create_message_mappings_table(db)
            await self.create_broadcasts_table(db)
            await self.create_read_receipts_table(db)
            await self.create_spam_keywords_table(db)
            await self.create_runtime_status_table(db)
            await self.create_tg_group_monitors_table(db)
            await self.create_discovered_tg_chats_table(db)
            await self.create_web_monitors_table(db)
            await self.create_app_meta_table(db)
            await self.migrate_database(db)
            await db.commit()
        logging.info("数据库初始化完成。")

    async def create_users_table(self, db):
        await db.execute('''
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                first_name TEXT NOT NULL,
                last_name TEXT,
                language_code TEXT,
                is_verified INTEGER DEFAULT 0,
                is_blacklisted INTEGER DEFAULT 0,
                blacklist_strikes INTEGER DEFAULT 0 NOT NULL,
                thread_id INTEGER,
                verification_attempts INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_active TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                message_count INTEGER DEFAULT 0
            )
        ''')
        await db.execute('CREATE INDEX IF NOT EXISTS idx_users_username ON users(username)')
        await db.execute('CREATE INDEX IF NOT EXISTS idx_users_verified ON users(is_verified)')
        await db.execute('CREATE INDEX IF NOT EXISTS idx_users_blacklisted ON users(is_blacklisted)')
        await db.execute('CREATE INDEX IF NOT EXISTS idx_users_thread ON users(thread_id)')

    async def create_messages_table(self, db):
        await db.execute('''
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                message_id INTEGER NOT NULL,
                thread_id INTEGER,
                content TEXT,
                media_type TEXT,
                media_file_id TEXT,
                direction TEXT NOT NULL,
                is_forwarded INTEGER DEFAULT 0,
                reply_to_message_id INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
            )
        ''')
        await db.execute('CREATE INDEX IF NOT EXISTS idx_messages_user ON messages(user_id)')
        await db.execute('CREATE INDEX IF NOT EXISTS idx_messages_thread ON messages(thread_id)')
        await db.execute('CREATE INDEX IF NOT EXISTS idx_messages_direction ON messages(direction)')
        await db.execute('CREATE INDEX IF NOT EXISTS idx_messages_created ON messages(created_at)')

    async def create_blacklist_table(self, db):
        await db.execute('''
            CREATE TABLE IF NOT EXISTS blacklist (
                user_id INTEGER PRIMARY KEY,
                reason TEXT NOT NULL,
                blocked_by INTEGER NOT NULL,
                blocked_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                permanent INTEGER DEFAULT 0,
                unblock_question TEXT,
                unblock_answer TEXT,
                unblock_attempts INTEGER DEFAULT 0,
                last_unblock_attempt TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
            )
        ''')
        await db.execute('CREATE INDEX IF NOT EXISTS idx_blacklist_blocked_at ON blacklist(blocked_at)')

    async def create_admins_table(self, db):
        await db.execute('''
            CREATE TABLE IF NOT EXISTS admins (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                first_name TEXT,
                added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                added_by INTEGER,
                is_active INTEGER DEFAULT 1,
                permissions TEXT DEFAULT 'all'
            )
        ''')
        await db.execute('CREATE INDEX IF NOT EXISTS idx_admins_active ON admins(is_active)')

    async def create_verification_sessions_table(self, db):
        await db.execute('''
            CREATE TABLE IF NOT EXISTS verification_sessions (
                user_id INTEGER PRIMARY KEY,
                question TEXT NOT NULL,
                answer TEXT NOT NULL,
                attempts INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                expires_at TIMESTAMP NOT NULL,
                FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
            )
        ''')
        await db.execute('CREATE INDEX IF NOT EXISTS idx_verification_expires ON verification_sessions(expires_at)')

    async def create_settings_table(self, db):
        await db.execute('''
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                description TEXT,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        default_settings = [
            ('bot_version', '1.0.0', '机器人当前版本'),
            ('welcome_message', '欢迎使用本机器人！', '新用户收到的欢迎消息'),
            ('verification_enabled', '1', '是否启用新用户验证 (1=是, 0=否)'),
            ('ai_filter_enabled', '1', '是否启用AI垃圾消息过滤 (1=是, 0=否)'),
            ('max_message_length', '4096', '允许接收的最大消息长度'),
            ('queue_max_size', '1000', '内部消息处理队列的最大容量'),
            ('ai_provider', 'gemini', '当前使用的AI提供商 (gemini, openai)'),
            
            ('gemini_model_filter', 'gemini-2.5-flash', 'Gemini 内容审查模型'),
            ('gemini_model_verification', 'gemini-2.5-flash-lite', 'Gemini 验证码生成模型'),
            ('gemini_model_autoreply', 'gemini-2.5-flash', 'Gemini 自动回复模型'),

            ('openai_model_filter', 'gpt-4.1', 'OpenAI 内容审查模型'),
            ('openai_model_verification', 'gpt-4.1-mini', 'OpenAI 验证码生成模型'),
            ('openai_model_autoreply', 'gpt-4.1', 'OpenAI 自动回复模型')
        ]
        for key, value, description in default_settings:
            await db.execute(
                'INSERT OR IGNORE INTO settings (key, value, description) VALUES (?, ?, ?)',
                (key, value, description)
            )

    async def create_statistics_table(self, db):
        await db.execute('''
            CREATE TABLE IF NOT EXISTS statistics (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                stat_date DATE NOT NULL,
                total_users INTEGER DEFAULT 0,
                active_users INTEGER DEFAULT 0,
                messages_sent INTEGER DEFAULT 0,
                messages_received INTEGER DEFAULT 0,
                verifications_passed INTEGER DEFAULT 0,
                verifications_failed INTEGER DEFAULT 0,
                users_blocked INTEGER DEFAULT 0,
                users_unblocked INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(stat_date)
            )
        ''')
        await db.execute('CREATE INDEX IF NOT EXISTS idx_statistics_date ON statistics(stat_date)')

    async def create_filtered_messages_table(self, db):
        await db.execute('''
            CREATE TABLE IF NOT EXISTS filtered_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                message_id INTEGER NOT NULL,
                content TEXT,
                reason TEXT,
                media_type TEXT,
                media_file_id TEXT,
                filtered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
            )
        ''')
        await db.execute('CREATE INDEX IF NOT EXISTS idx_filtered_messages_user_id ON filtered_messages(user_id)')

    async def create_knowledge_base_table(self, db):
        await db.execute('''
            CREATE TABLE IF NOT EXISTS knowledge_base (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        await db.execute('CREATE INDEX IF NOT EXISTS idx_knowledge_base_title ON knowledge_base(title)')

    async def create_exemptions_table(self, db):
        await db.execute('''
            CREATE TABLE IF NOT EXISTS exemptions (
                user_id INTEGER PRIMARY KEY,
                is_permanent INTEGER DEFAULT 0,
                expires_at TIMESTAMP,
                exempted_by INTEGER NOT NULL,
                reason TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
            )
        ''')
        await db.execute('CREATE INDEX IF NOT EXISTS idx_exemptions_expires ON exemptions(expires_at)')
        await db.execute('CREATE INDEX IF NOT EXISTS idx_exemptions_permanent ON exemptions(is_permanent)')

    async def create_user_groups_table(self, db):
        await db.execute('''
            CREATE TABLE IF NOT EXISTS user_groups (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL COLLATE NOCASE UNIQUE,
                description TEXT,
                created_by INTEGER NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        await db.execute('CREATE INDEX IF NOT EXISTS idx_user_groups_name ON user_groups(name)')

        await db.execute('''
            CREATE TABLE IF NOT EXISTS user_group_members (
                group_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                added_by INTEGER NOT NULL,
                added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (group_id, user_id),
                FOREIGN KEY (group_id) REFERENCES user_groups(id) ON DELETE CASCADE,
                FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
            )
        ''')
        await db.execute('CREATE INDEX IF NOT EXISTS idx_user_group_members_user ON user_group_members(user_id)')
        await db.execute('CREATE INDEX IF NOT EXISTS idx_user_group_members_group ON user_group_members(group_id)')

    async def create_message_mappings_table(self, db):
        await db.execute('''
            CREATE TABLE IF NOT EXISTS message_mappings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                source_chat_id INTEGER NOT NULL,
                source_message_id INTEGER NOT NULL,
                dest_chat_id INTEGER NOT NULL,
                dest_message_id INTEGER NOT NULL,
                thread_id INTEGER,
                direction TEXT NOT NULL,
                broadcast_id INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(source_chat_id, source_message_id, dest_chat_id, dest_message_id),
                FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
            )
        ''')
        await db.execute('CREATE INDEX IF NOT EXISTS idx_message_mappings_source ON message_mappings(source_chat_id, source_message_id)')
        await db.execute('CREATE INDEX IF NOT EXISTS idx_message_mappings_dest ON message_mappings(dest_chat_id, dest_message_id)')
        await db.execute('CREATE INDEX IF NOT EXISTS idx_message_mappings_user ON message_mappings(user_id)')
        await db.execute('CREATE INDEX IF NOT EXISTS idx_message_mappings_broadcast ON message_mappings(broadcast_id)')

    async def create_broadcasts_table(self, db):
        await db.execute('''
            CREATE TABLE IF NOT EXISTS broadcasts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                scope TEXT NOT NULL,
                group_id INTEGER,
                source_chat_id INTEGER,
                source_message_id INTEGER,
                content_preview TEXT,
                created_by INTEGER NOT NULL,
                total_count INTEGER DEFAULT 0,
                success_count INTEGER DEFAULT 0,
                failed_count INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (group_id) REFERENCES user_groups(id) ON DELETE SET NULL
            )
        ''')
        await db.execute('CREATE INDEX IF NOT EXISTS idx_broadcasts_created ON broadcasts(created_at)')
        await db.execute('CREATE INDEX IF NOT EXISTS idx_broadcasts_scope ON broadcasts(scope)')

        await db.execute('''
            CREATE TABLE IF NOT EXISTS broadcast_deliveries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                broadcast_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                message_id INTEGER,
                status TEXT NOT NULL,
                error TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(broadcast_id, user_id),
                FOREIGN KEY (broadcast_id) REFERENCES broadcasts(id) ON DELETE CASCADE,
                FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
            )
        ''')
        await db.execute('CREATE INDEX IF NOT EXISTS idx_broadcast_deliveries_broadcast ON broadcast_deliveries(broadcast_id)')
        await db.execute('CREATE INDEX IF NOT EXISTS idx_broadcast_deliveries_user ON broadcast_deliveries(user_id)')

    async def create_read_receipts_table(self, db):
        await db.execute('''
            CREATE TABLE IF NOT EXISTS read_receipts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                forum_message_id INTEGER NOT NULL,
                thread_id INTEGER NOT NULL,
                is_read INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                read_at TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
            )
        ''')
        await db.execute('CREATE INDEX IF NOT EXISTS idx_read_receipts_user_unread ON read_receipts(user_id, is_read)')
        await db.execute('CREATE INDEX IF NOT EXISTS idx_read_receipts_thread ON read_receipts(thread_id)')

    async def create_spam_keywords_table(self, db):
        await db.execute('''
            CREATE TABLE IF NOT EXISTS spam_keywords (
                keyword TEXT PRIMARY KEY COLLATE NOCASE,
                created_by INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')

    async def create_runtime_status_table(self, db):
        await db.execute('''
            CREATE TABLE IF NOT EXISTS runtime_status (
                name TEXT PRIMARY KEY,
                category TEXT NOT NULL,
                last_run_at TIMESTAMP,
                last_success_at TIMESTAMP,
                last_error_at TIMESTAMP,
                last_error TEXT,
                last_duration_ms INTEGER DEFAULT 0,
                last_sent_count INTEGER DEFAULT 0,
                consecutive_failures INTEGER DEFAULT 0,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        await db.execute('CREATE INDEX IF NOT EXISTS idx_runtime_status_category ON runtime_status(category)')
        await db.execute('CREATE INDEX IF NOT EXISTS idx_runtime_status_updated ON runtime_status(updated_at)')

    async def create_tg_group_monitors_table(self, db):
        await db.execute('''
            CREATE TABLE IF NOT EXISTS tg_group_monitors (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL COLLATE NOCASE UNIQUE,
                chat_id INTEGER NOT NULL,
                chat_title TEXT,
                listen_source TEXT DEFAULT 'user_session',
                keywords TEXT NOT NULL,
                exclude_keywords TEXT DEFAULT '',
                enabled INTEGER DEFAULT 1,
                notify_telegram INTEGER DEFAULT 1,
                min_interval_seconds INTEGER DEFAULT 30,
                dedupe_window_seconds INTEGER DEFAULT 300,
                created_by INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        await db.execute('CREATE INDEX IF NOT EXISTS idx_tg_group_monitors_chat ON tg_group_monitors(chat_id)')
        await db.execute('CREATE INDEX IF NOT EXISTS idx_tg_group_monitors_enabled ON tg_group_monitors(enabled)')
        await db.execute('''
            CREATE TABLE IF NOT EXISTS tg_monitor_recent (
                monitor_id INTEGER NOT NULL,
                fingerprint TEXT NOT NULL,
                sent_at_ts REAL NOT NULL,
                PRIMARY KEY (monitor_id, fingerprint),
                FOREIGN KEY (monitor_id) REFERENCES tg_group_monitors(id) ON DELETE CASCADE
            )
        ''')
        await db.execute('CREATE INDEX IF NOT EXISTS idx_tg_monitor_recent_sent ON tg_monitor_recent(sent_at_ts)')
        await db.execute('''
            CREATE TABLE IF NOT EXISTS tg_monitor_last_send (
                monitor_id INTEGER PRIMARY KEY,
                sent_at_ts REAL NOT NULL,
                FOREIGN KEY (monitor_id) REFERENCES tg_group_monitors(id) ON DELETE CASCADE
            )
        ''')

    async def create_discovered_tg_chats_table(self, db):
        await db.execute('''
            CREATE TABLE IF NOT EXISTS discovered_tg_chats (
                chat_id INTEGER PRIMARY KEY,
                title TEXT,
                username TEXT,
                last_seen_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                active INTEGER DEFAULT 1
            )
        ''')
        await db.execute('CREATE INDEX IF NOT EXISTS idx_discovered_tg_chats_seen ON discovered_tg_chats(last_seen_at)')

    async def create_web_monitors_table(self, db):
        await db.execute('''
            CREATE TABLE IF NOT EXISTS web_monitors (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL COLLATE NOCASE UNIQUE,
                url TEXT NOT NULL,
                keywords TEXT DEFAULT '',
                item_selector TEXT DEFAULT 'article, .thread, .post, li',
                title_selector TEXT DEFAULT 'h1, h2, h3, a',
                link_selector TEXT DEFAULT 'a',
                price_selector TEXT DEFAULT '',
                stock_selector TEXT DEFAULT '',
                enabled INTEGER DEFAULT 1,
                interval_seconds INTEGER DEFAULT 300,
                notify_telegram INTEGER DEFAULT 1,
                notify_on_keyword INTEGER DEFAULT 1,
                notify_on_new_item INTEGER DEFAULT 1,
                notify_on_change INTEGER DEFAULT 1,
                last_checked_at TIMESTAMP,
                created_by INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        await db.execute('CREATE INDEX IF NOT EXISTS idx_web_monitors_enabled ON web_monitors(enabled)')
        await db.execute('CREATE INDEX IF NOT EXISTS idx_web_monitors_last_checked ON web_monitors(last_checked_at)')
        await db.execute('''
            CREATE TABLE IF NOT EXISTS web_monitor_state (
                monitor_id INTEGER NOT NULL,
                item_key TEXT NOT NULL,
                title TEXT,
                link TEXT,
                content_hash TEXT,
                price TEXT,
                stock TEXT,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (monitor_id, item_key),
                FOREIGN KEY (monitor_id) REFERENCES web_monitors(id) ON DELETE CASCADE
            )
        ''')
        await db.execute('CREATE INDEX IF NOT EXISTS idx_web_monitor_state_monitor ON web_monitor_state(monitor_id)')

    async def create_app_meta_table(self, db):
        await db.execute('''
            CREATE TABLE IF NOT EXISTS app_meta (
                meta_key TEXT PRIMARY KEY,
                meta_value TEXT,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')

    async def get_filtered_messages_by_user(self, user_id, limit=5):
        async with self.get_connection() as db:
            cursor = await db.execute(
                'SELECT content, reason FROM filtered_messages WHERE user_id = ? ORDER BY filtered_at DESC LIMIT ?',
                (user_id, limit)
            )
            rows = await cursor.fetchall()
            return [{"content": row[0], "reason": row[1]} for row in rows]

    async def migrate_database(self, db):
        try:
            await db.execute('ALTER TABLE users ADD COLUMN blacklist_strikes INTEGER DEFAULT 0 NOT NULL')
            logging.info("数据库迁移：成功为 'users' 表添加 'blacklist_strikes' 列。")
        except aiosqlite.OperationalError as e:
            if "duplicate column name" not in str(e):
                raise e

        try:
            await db.execute('ALTER TABLE blacklist ADD COLUMN permanent INTEGER DEFAULT 0')
            logging.info("数据库迁移：成功为 'blacklist' 表添加 'permanent' 列。")
        except aiosqlite.OperationalError as e:
            if "duplicate column name" not in str(e):
                raise e

        try:
            await db.execute(
                'INSERT OR IGNORE INTO settings (key, value, description) VALUES (?, ?, ?)',
                ('autoreply_enabled', '0', '是否启用自动回复功能 (1=是, 0=否)')
            )
            logging.info("数据库迁移：成功添加自动回复开关设置。")
        except Exception as e:
            logging.warning(f"添加自动回复设置时出错: {e}")

        try:
            await db.execute('INSERT OR IGNORE INTO settings (key, value, description) VALUES (?, ?, ?)', ('ai_provider', 'gemini', '当前使用的AI提供商 (gemini, openai)'))
            
            await db.execute('INSERT OR IGNORE INTO settings (key, value, description) VALUES (?, ?, ?)', ('gemini_model_filter', 'gemini-2.5-flash', 'Gemini 内容审查模型'))
            await db.execute('INSERT OR IGNORE INTO settings (key, value, description) VALUES (?, ?, ?)', ('gemini_model_verification', 'gemini-2.5-flash-lite', 'Gemini 验证码生成模型'))
            await db.execute('INSERT OR IGNORE INTO settings (key, value, description) VALUES (?, ?, ?)', ('gemini_model_autoreply', 'gemini-2.5-flash', 'Gemini 自动回复模型'))

            await db.execute('INSERT OR IGNORE INTO settings (key, value, description) VALUES (?, ?, ?)', ('openai_model_filter', 'gpt-4.1', 'OpenAI 内容审查模型'))
            await db.execute('INSERT OR IGNORE INTO settings (key, value, description) VALUES (?, ?, ?)', ('openai_model_verification', 'gpt-4.1-mini', 'OpenAI 验证码生成模型'))
            await db.execute('INSERT OR IGNORE INTO settings (key, value, description) VALUES (?, ?, ?)', ('openai_model_autoreply', 'gpt-4.1', 'OpenAI 自动回复模型'))

            await db.execute("DELETE FROM settings WHERE key IN ('openai_model', 'gemini_model')")

            logging.info("数据库迁移：成功添加细分AI设置。")
        except Exception as e:
            logging.warning(f"添加AI设置时出错: {e}")

        try:
            default_settings = [
                ('spam_keyword_filter_enabled', '0', '是否启用关键词广告拦截 (1=是, 0=否)'),
                ('spam_keyword_auto_block', '1', '关键词广告拦截命中后是否自动拉黑 (1=是, 0=否)'),
            ]
            for key, value, description in default_settings:
                await db.execute(
                    'INSERT OR IGNORE INTO settings (key, value, description) VALUES (?, ?, ?)',
                    (key, value, description)
                )
            logging.info("数据库迁移：成功添加关键词拦截设置。")
        except Exception as e:
            logging.warning(f"添加关键词拦截设置时出错: {e}")

db_manager = DatabaseManager()
