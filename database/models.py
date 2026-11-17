import asyncio
import json
import logging
import aiosqlite
from datetime import datetime, timezone, timedelta
from .db_manager import db_manager
from config import config

logger = logging.getLogger(__name__)


def _utc_now_str() -> str:
    return datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')


_tg_monitor_locks: dict[tuple[str, int], asyncio.Lock] = {}


def _get_tg_monitor_lock(monitor_id: int) -> asyncio.Lock:
    key = (str(db_manager.db_path), int(monitor_id))
    lock = _tg_monitor_locks.get(key)
    if lock is None:
        if len(_tg_monitor_locks) >= 512:
            for k in [k for k, v in _tg_monitor_locks.items() if not v.locked()]:
                _tg_monitor_locks.pop(k, None)
        lock = asyncio.Lock()
        _tg_monitor_locks[key] = lock
    return lock

# 拦截消息篮（filtered_messages）的保留策略。两条并用：
#   * 按时间：超过 RETENTION_DAYS 天的记录会被清理
#   * 按行数：总量超过 MAX_ROWS 时只保留最新的 MAX_ROWS 条
# 两者结合，避免「某天被灌爆」或「长期冷清留一堆无用旧数据」两种失控。
FILTERED_RETENTION_DAYS = 90
FILTERED_MAX_ROWS = 20000

# 机会性清理的写计数器：每 _FILTERED_PRUNE_EVERY 次插入才做一次清理，
# 把 O(n) 的 DELETE 摊薄成可忽略的均摊成本。只要持续有写入，清理一定会发生。
_FILTERED_PRUNE_EVERY = 100
_filtered_writes_since_prune = 0


def _row_to_dict(cursor, row):
    return dict(zip([col[0] for col in cursor.description], row))


def _encode_list(values) -> str:
    return json.dumps([str(item).strip() for item in values if str(item).strip()], ensure_ascii=False)


def _decode_list(raw_value) -> list[str]:
    if raw_value is None:
        return []
    if isinstance(raw_value, list):
        return [str(item).strip() for item in raw_value if str(item).strip()]
    text = str(raw_value).strip()
    if not text:
        return []
    try:
        loaded = json.loads(text)
        if isinstance(loaded, list):
            return [str(item).strip() for item in loaded if str(item).strip()]
    except json.JSONDecodeError:
        pass
    return [item.strip() for item in text.replace(",", "\n").splitlines() if item.strip()]

async def get_user(user_id: int):
    async with db_manager.get_connection() as db:
        async with db.execute(
            'SELECT * FROM users WHERE user_id = ?',
            (user_id,)
        ) as cursor:
            row = await cursor.fetchone()
            if row:
                return dict(zip([col[0] for col in cursor.description], row))
            return None

async def add_user(user_id: int, username: str, first_name: str, last_name: str = None, language_code: str = None):
    now = _utc_now_str()
    async with db_manager.get_connection() as db:
        await db.execute('''
            INSERT INTO users
            (user_id, username, first_name, last_name, language_code, last_active)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                username = excluded.username,
                first_name = excluded.first_name,
                last_name = excluded.last_name,
                language_code = excluded.language_code,
                last_active = excluded.last_active
        ''', (user_id, username, first_name, last_name, language_code, now))
        await db.commit()

async def update_user_profile(user_id: int, username: str, first_name: str, last_name: str = None, language_code: str = None):
    now = _utc_now_str()
    async with db_manager.get_connection() as db:
        await db.execute(
            '''
            UPDATE users
            SET username = ?, first_name = ?, last_name = ?, language_code = ?, last_active = ?
            WHERE user_id = ?
            ''',
            (username, first_name, last_name, language_code, now, user_id)
        )
        await db.commit()

async def update_user_verification(user_id: int, is_verified: bool):
    async with db_manager.get_connection() as db:
        await db.execute(
            'UPDATE users SET is_verified = ? WHERE user_id = ?',
            (1 if is_verified else 0, user_id)
        )
        await db.commit()

async def update_user_thread_id(user_id: int, thread_id: int):
    async with db_manager.get_connection() as db:
        await db.execute(
            'UPDATE users SET thread_id = ? WHERE user_id = ?',
            (thread_id, user_id)
        )
        await db.commit()

async def get_user_by_thread_id(thread_id: int):
    async with db_manager.get_connection() as db:
        async with db.execute(
            'SELECT * FROM users WHERE thread_id = ?',
            (thread_id,)
        ) as cursor:
            row = await cursor.fetchone()
            if row:
                return dict(zip([col[0] for col in cursor.description], row))
            return None

async def _ensure_messages_dest_column(db):
    """幂等确保 messages 表包含 dest_message_id 列，兼容旧数据库平滑升级。

    SQLite 的 ALTER TABLE ADD COLUMN 不支持 IF NOT EXISTS，因此先用
    PRAGMA table_info 检查列是否存在，缺失时才执行 ALTER。
    """
    async with db.execute('PRAGMA table_info(messages)') as cursor:
        rows = await cursor.fetchall()
    columns = {row[1] for row in rows}
    if 'dest_message_id' not in columns:
        try:
            await db.execute('ALTER TABLE messages ADD COLUMN dest_message_id INTEGER')
        except aiosqlite.OperationalError as e:
            # 并发连接可能同时检测到列缺失并 ALTER，后者会因重复列名失败
            if 'duplicate column name' not in str(e):
                raise
    await db.execute('CREATE INDEX IF NOT EXISTS idx_messages_dest ON messages(dest_message_id)')


async def save_message(user_id: int, message_id: int, content: str, direction: str, media_type: str = None, media_file_id: str = None, dest_message_id: int = None, thread_id: int = None, forward_source: str = None):
    """保存一条消息记录。

    message_id 为用户私聊侧消息 id；dest_message_id 为论坛侧消息 id
    （转发到论坛后由 Telegram 返回），供 get_pending_reply_topics 做预览匹配。
    """
    async with db_manager.get_connection() as db:
        await db.execute('''
            INSERT INTO messages
            (user_id, message_id, content, direction, media_type, media_file_id, dest_message_id, thread_id,
             forward_source, is_forwarded)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (user_id, message_id, content, direction, media_type, media_file_id, dest_message_id, thread_id,
              forward_source, 1 if forward_source else 0))
        await db.commit()

async def _prune_filtered_messages(db) -> int:
    """清理拦截消息篮：既按时间也按行数上限。返回删除条数。

    复用调用方已 checkout 的连接与事务，避免额外占用池资源。
    """
    deleted = 0

    # 先取一次总行数。COUNT(*) 走表扫描，实测 2 万行表约 0.3 ms，而策略 B 的
    # NOT IN 子查询即使无事可做也要付约 15 ms，所以用一个廉价的「有没有活要干」
    # 判据把它跳过，稳赚不赔。不需要额外索引。
    async with db.execute('SELECT COUNT(*) FROM filtered_messages') as cursor:
        total_rows = (await cursor.fetchone())[0]

    # 策略 A —— 按时间。SQLite 的 CURRENT_TIMESTAMP 生成 'YYYY-MM-DD HH:MM:SS'，
    # 定长格式下字典序等价于时间序，直接字符串比较即可。
    # 这里不加前置判断：MIN(filtered_at) 实测约 5.5 ms（2 万行）/ 50 ms（20 万行），
    # 比它想替代的 DELETE（约 5.5 ms，且随表增大但不随行数恶化）还贵。
    if FILTERED_RETENTION_DAYS > 0:
        cursor = await db.execute(
            "DELETE FROM filtered_messages WHERE filtered_at < datetime('now', ?)",
            ('-%d days' % FILTERED_RETENTION_DAYS,),
        )
        if cursor.rowcount > 0:
            deleted += cursor.rowcount

    # 策略 B —— 按行数上限，只保留最新的 FILTERED_MAX_ROWS 条。
    # 必须用单调递增的 id 排序：filtered_at 只有秒级精度，同一秒内的记录时间
    # 完全相同，排序不稳定，会出现「删掉新的、留下旧的」。
    if FILTERED_MAX_ROWS > 0 and total_rows > FILTERED_MAX_ROWS:
        cursor = await db.execute(
            '''
            DELETE FROM filtered_messages
            WHERE id NOT IN (
                SELECT id FROM filtered_messages ORDER BY id DESC LIMIT ?
            )
            ''',
            (FILTERED_MAX_ROWS,),
        )
        if cursor.rowcount > 0:
            deleted += cursor.rowcount

    return deleted


async def prune_filtered_messages() -> int:
    """显式清理拦截消息篮，返回删除条数。

    正常运行时不需要主动调用 —— save_filtered_message 会机会性自动清理。
    保留这个入口是为了测试与将来的定期任务。
    """
    async with db_manager.get_connection() as db:
        deleted = await _prune_filtered_messages(db)
        await db.commit()
    if deleted:
        logger.info("拦截消息篮清理：删除 %d 条超期/超量记录", deleted)
    return deleted


async def save_filtered_message(user_id: int, message_id: int, content: str, reason: str, media_type: str = None, media_file_id: str = None, forward_source: str = None):
    global _filtered_writes_since_prune
    async with db_manager.get_connection() as db:
        await db.execute('''
            INSERT INTO filtered_messages
            (user_id, message_id, content, reason, media_type, media_file_id, forward_source)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (user_id, message_id, content, reason, media_type, media_file_id, forward_source))
        # 机会性清理：每 _FILTERED_PRUNE_EVERY 次插入顺手清理一次，防止拦截
        # 消息篮单调增长。清理与插入共用同一个事务与连接（不多占池连接）。
        # 清理只是家务活，不能让它连累业务：调用方（handlers/user_handler.py）
        # 在这之后还有拉黑与回复逻辑，这里抛异常会连带跳过它们，所以单独兜住
        # 并记日志；本条记录照常提交，清理下次再试。
        _filtered_writes_since_prune += 1
        if _filtered_writes_since_prune >= _FILTERED_PRUNE_EVERY:
            _filtered_writes_since_prune = 0
            try:
                await _prune_filtered_messages(db)
            except Exception:
                logger.exception("拦截消息篮机会性清理失败，本条记录仍会保存")
        await db.commit()

async def get_latest_filtered_message(user_id: int):
    async with db_manager.get_connection() as db:
        async with db.execute('''
            SELECT * FROM filtered_messages WHERE user_id = ? ORDER BY id DESC LIMIT 1
        ''', (user_id,)) as cursor:
            row = await cursor.fetchone()
            if not row:
                return None
            cols = [description[0] for description in cursor.description]
            return dict(zip(cols, row))


def _like(query: str) -> str:
    """用户输入里的 % 和 _ 按字面匹配。"""
    escaped = query.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
    return f'%{escaped}%'


_LIKE_SQL = "LIKE ? ESCAPE '\\'"
_USER_MATCH_SQL = f"""(
    CAST(u.user_id AS TEXT) {_LIKE_SQL}
    OR u.username {_LIKE_SQL}
    OR COALESCE(u.first_name, '') || ' ' || COALESCE(u.last_name, '') {_LIKE_SQL}
    OR COALESCE(u.note, '') {_LIKE_SQL}
    OR COALESCE(u.tags, '') {_LIKE_SQL}
)"""
# datetime() 同时认识面板写入的空格格式和 TG 写入的 ISO 格式，直接比字符串会算错
_EXEMPTION_ACTIVE_SQL = "(e.is_permanent = 1 OR datetime(e.expires_at) > datetime('now'))"


def _user_match_params(query: str) -> list:
    return [_like(query), _like(query.lstrip('@')), _like(query), _like(query), _like(query)]


def _filtered_match(query: str) -> tuple:
    if not query:
        return '', []
    return (
        f'WHERE fm.content {_LIKE_SQL} OR fm.reason {_LIKE_SQL} OR fm.forward_source {_LIKE_SQL} OR {_USER_MATCH_SQL}',
        [_like(query), _like(query), _like(query), *_user_match_params(query)],
    )


async def get_filtered_messages(limit: int = 20, offset: int = 0, query: str = None):
    where_sql, params = _filtered_match(query)
    async with db_manager.get_connection() as db:
        async with db.execute(f'''
            SELECT fm.*, u.first_name, u.username
            FROM filtered_messages fm
            JOIN users u ON fm.user_id = u.user_id
            {where_sql}
            -- filtered_at 只有秒级精度（CURRENT_TIMESTAMP），而拦截往往是同一秒内
            -- 连着好几条。此时只按 filtered_at 排序结果不确定，SQLite 实测会把
            -- 【最早】插入的排到最前，管理员看到的「最新拦截」反而是旧的。
            -- 加上单调递增的 id 作为决定性 tie-breaker 才可靠。
            ORDER BY fm.filtered_at DESC, fm.id DESC
            LIMIT ? OFFSET ?
        ''', (*params, limit, offset)) as cursor:
            rows = await cursor.fetchall()
            if not rows:
                return []
            cols = [description[0] for description in cursor.description]
            return [dict(zip(cols, row)) for row in rows]

async def get_filtered_messages_count(query: str = None) -> int:
    where_sql, params = _filtered_match(query)
    sql = f'SELECT COUNT(*) FROM filtered_messages fm JOIN users u ON fm.user_id = u.user_id {where_sql}'
    async with db_manager.get_connection() as db:
        async with db.execute(sql, params) as cursor:
            row = await cursor.fetchone()
            return row[0] if row else 0


async def get_filtered_message(filtered_id: int):
    async with db_manager.get_connection() as db:
        async with db.execute('SELECT * FROM filtered_messages WHERE id = ?', (filtered_id,)) as cursor:
            row = await cursor.fetchone()
            return _row_to_dict(cursor, row) if row else None


async def delete_filtered_message(message_id: int) -> bool:
    async with db_manager.get_connection() as db:
        cursor = await db.execute('DELETE FROM filtered_messages WHERE id = ?', (message_id,))
        await db.commit()
        return cursor.rowcount > 0


async def clear_filtered_messages() -> int:
    async with db_manager.get_connection() as db:
        cursor = await db.execute('DELETE FROM filtered_messages')
        await db.commit()
        return cursor.rowcount

async def is_blacklisted(user_id: int):
    async with db_manager.get_connection() as db:
        async with db.execute(
            'SELECT permanent FROM blacklist WHERE user_id = ?',
            (user_id,)
        ) as cursor:
            row = await cursor.fetchone()
            if row:
                return True, bool(row[0])
            return False, False

async def add_to_blacklist(user_id: int, reason: str, blocked_by: int, permanent: bool = False):
    async with db_manager.get_connection() as db:
        # 支持拉黑从未与机器人私聊过的 ID（如管理员手动封禁）：
        # blacklist 表对 users 有外键，先补一行占位用户（与 set_user_blacklist_strikes 一致）
        await db.execute(
            'INSERT OR IGNORE INTO users (user_id, first_name) VALUES (?, ?)',
            (user_id, f"User_{user_id}")
        )
        await db.execute(
            'UPDATE users SET is_blacklisted = 1, blacklist_strikes = blacklist_strikes + 1 WHERE user_id = ?',
            (user_id,)
        )
        await db.execute('''
            INSERT INTO blacklist (user_id, reason, blocked_by, permanent)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                reason = excluded.reason,
                blocked_by = excluded.blocked_by,
                permanent = excluded.permanent,
                blocked_at = CURRENT_TIMESTAMP
        ''', (user_id, reason, blocked_by, 1 if permanent else 0))
        await db.commit()

async def remove_from_blacklist(user_id: int):
    async with db_manager.get_connection() as db:
        await db.execute(
            'UPDATE users SET is_blacklisted = 0 WHERE user_id = ?',
            (user_id,)
        )
        await db.execute('DELETE FROM blacklist WHERE user_id = ?', (user_id,))
        await db.commit()

async def get_blacklist():
    async with db_manager.get_connection() as db:
        async with db.execute('''
            SELECT b.user_id, u.first_name, u.username, b.reason, b.blocked_at
            FROM blacklist b
            LEFT JOIN users u ON b.user_id = u.user_id
            ORDER BY b.blocked_at DESC
        ''') as cursor:
            rows = await cursor.fetchall()
            if not rows:
                return []
            cols = [description[0] for description in cursor.description]
            return [dict(zip(cols, row)) for row in rows]

def _reason_match(alias: str, query: str) -> tuple:
    """黑名单 / 通行证共用的搜索条件：理由或用户信息。"""
    if not query:
        return '', []
    return (
        f'WHERE {alias}.reason {_LIKE_SQL} OR {_USER_MATCH_SQL}',
        [_like(query), *_user_match_params(query)],
    )


async def get_blacklist_paginated(limit: int = 5, offset: int = 0, query: str = None):
    where_sql, params = _reason_match('b', query)
    async with db_manager.get_connection() as db:
        async with db.execute(f'''
            SELECT b.user_id, u.first_name, u.username, b.reason, b.blocked_at
            FROM blacklist b
            LEFT JOIN users u ON b.user_id = u.user_id
            {where_sql}
            ORDER BY b.blocked_at DESC
            LIMIT ? OFFSET ?
        ''', (*params, limit, offset)) as cursor:
            rows = await cursor.fetchall()
            if not rows:
                return []
            cols = [description[0] for description in cursor.description]
            return [dict(zip(cols, row)) for row in rows]

async def get_blacklist_count(query: str = None) -> int:
    where_sql, params = _reason_match('b', query)
    sql = f'SELECT COUNT(*) FROM blacklist b LEFT JOIN users u ON b.user_id = u.user_id {where_sql}'
    async with db_manager.get_connection() as db:
        async with db.execute(sql, params) as cursor:
            row = await cursor.fetchone()
            return row[0] if row else 0

async def set_user_blacklist_strikes(user_id: int, strikes: int):
    async with db_manager.get_connection() as db:
        await db.execute(
            'INSERT OR IGNORE INTO users (user_id, first_name) VALUES (?, ?)',
            (user_id, f"User_{user_id}")
        )
        await db.execute(
            'UPDATE users SET blacklist_strikes = ? WHERE user_id = ?',
            (strikes, user_id)
        )
        await db.commit()

async def is_admin(user_id: int) -> bool:
    return user_id in config.ADMIN_IDS

def _user_search(query: str) -> tuple:
    """用户搜索，返回 (JOIN 子句, WHERE 子句, 按顺序的参数)。

    资料命中，或者发过 / 被拦下过包含关键词的消息都算。只看用户发出的内容，这样搜到的
    就是消息来源；管理员回复和广播会让所有收件人都命中。每个用户保留最新一条命中的
    消息（SQLite 里 MAX 的同行列取自最大值所在行）。
    """
    if not query:
        return '', '', []
    join_sql = f'''
        LEFT JOIN (
            SELECT user_id, content AS matched_content, reason AS matched_reason,
                   forward_source AS matched_forward,
                   source AS matched_source, MAX(matched_at) AS matched_at, COUNT(*) AS match_count
            FROM (
                SELECT user_id, content, NULL AS reason, forward_source, 'message' AS source, created_at AS matched_at
                FROM messages
                WHERE direction = 'user_to_admin' AND (content {_LIKE_SQL} OR forward_source {_LIKE_SQL})
                UNION ALL
                SELECT user_id, content, reason, forward_source, 'filtered' AS source, filtered_at AS matched_at
                FROM filtered_messages
                WHERE content {_LIKE_SQL} OR forward_source {_LIKE_SQL}
            )
            GROUP BY user_id
        ) hit ON hit.user_id = u.user_id
    '''
    where_sql = f'WHERE hit.user_id IS NOT NULL OR {_USER_MATCH_SQL}'
    return join_sql, where_sql, [*[_like(query)] * 4, *_user_match_params(query)]


async def get_total_users_count(query: str = None) -> int:
    join_sql, where_sql, params = _user_search(query)
    async with db_manager.get_connection() as db:
        async with db.execute(f'SELECT COUNT(*) FROM users u {join_sql} {where_sql}', params) as cursor:
            row = await cursor.fetchone()
            return row[0] if row else 0

async def get_blocked_users_count() -> int:
    async with db_manager.get_connection() as db:
        async with db.execute('SELECT COUNT(*) FROM blacklist') as cursor:
            row = await cursor.fetchone()
            return row[0] if row else 0

async def get_verified_users_count() -> int:
    async with db_manager.get_connection() as db:
        async with db.execute('SELECT COUNT(*) FROM users WHERE is_verified = 1') as cursor:
            row = await cursor.fetchone()
            return row[0] if row else 0

async def get_message_stats() -> dict:
    """消息总量与今日量（按 SQLite 的 UTC 日期切分）。"""
    async with db_manager.get_connection() as db:
        async with db.execute('SELECT COUNT(*) FROM messages') as cursor:
            row = await cursor.fetchone()
            total = row[0] if row else 0
        async with db.execute(
            "SELECT COUNT(*) FROM messages WHERE created_at >= date('now')"
        ) as cursor:
            row = await cursor.fetchone()
            today = row[0] if row else 0
    return {'total': total, 'today': today}

# 会话列表的可见范围：已验证用户，或者曾经验证过、已有往来消息的用户（例如被撤销验证的人），
# 从没通过验证的机器人 / 骚扰号不会出现在列表里
_CONVERSATION_VISIBLE_SQL = '(u.is_verified = 1 OR EXISTS(SELECT 1 FROM messages mv WHERE mv.user_id = u.user_id))'


async def get_conversation_summaries(limit: int = 20, offset: int = 0, query: str = None):
    """会话列表：可见用户及其最后一条消息预览、未读与申诉状态。"""
    where_clauses = [_CONVERSATION_VISIBLE_SQL]
    params = []
    if query:
        where_clauses.append(_USER_MATCH_SQL)
        params = _user_match_params(query)
    where_sql = 'WHERE ' + ' AND '.join(where_clauses)

    async with db_manager.get_connection() as db:
        async with db.execute(f'''
            SELECT
                u.user_id,
                u.username,
                u.first_name,
                u.last_name,
                u.is_verified,
                u.thread_id,
                u.last_active,
                u.message_count,
                EXISTS(SELECT 1 FROM blacklist b WHERE b.user_id = u.user_id) AS blacklisted,
                m.content AS last_content,
                m.direction AS last_direction,
                m.media_type AS last_media_type,
                m.created_at AS last_message_at,
                CASE WHEN m.direction = 'user_to_admin'
                      AND (cr.read_at IS NULL OR m.created_at > cr.read_at)
                     THEN 1 ELSE 0 END AS is_unread,
                EXISTS(SELECT 1 FROM review_appeals ra
                       WHERE ra.user_id = u.user_id AND ra.status = 'pending') AS pending_appeal
            FROM users u
            LEFT JOIN messages m ON m.id = (
                SELECT id FROM messages WHERE user_id = u.user_id ORDER BY id DESC LIMIT 1
            )
            LEFT JOIN conversation_reads cr ON cr.user_id = u.user_id
            {where_sql}
            ORDER BY (m.created_at IS NULL) ASC, m.created_at DESC, u.last_active DESC
            LIMIT ? OFFSET ?
        ''', (*params, limit, offset)) as cursor:
            rows = await cursor.fetchall()
            if not rows:
                return []
            cols = [description[0] for description in cursor.description]
            return [dict(zip(cols, row)) for row in rows]

async def get_conversation_summaries_count(query: str = None) -> int:
    where_clauses = [_CONVERSATION_VISIBLE_SQL]
    params = []
    if query:
        where_clauses.append(_USER_MATCH_SQL)
        params = _user_match_params(query)
    where_sql = 'WHERE ' + ' AND '.join(where_clauses)
    async with db_manager.get_connection() as db:
        async with db.execute(f'SELECT COUNT(*) FROM users u {where_sql}', params) as cursor:
            row = await cursor.fetchone()
            return row[0] if row else 0

async def get_conversation_history(user_id: int, limit: int = 50, offset: int = 0):
    """会话历史（按时间正序返回一页，offset 从最新往回数）。"""
    async with db_manager.get_connection() as db:
        async with db.execute('''
            SELECT id, message_id, thread_id, content, media_type, media_file_id,
                   direction, dest_message_id, forward_source, created_at
            FROM messages
            WHERE user_id = ?
            ORDER BY id DESC
            LIMIT ? OFFSET ?
        ''', (user_id, limit, offset)) as cursor:
            rows = await cursor.fetchall()
            if not rows:
                return []
            cols = [description[0] for description in cursor.description]
            return [dict(zip(cols, row)) for row in rows][::-1]

async def get_conversation_message_count(user_id: int) -> int:
    async with db_manager.get_connection() as db:
        async with db.execute('SELECT COUNT(*) FROM messages WHERE user_id = ?', (user_id,)) as cursor:
            row = await cursor.fetchone()
            return row[0] if row else 0

async def get_user_spam_count(user_id: int) -> int:
    async with db_manager.get_connection() as db:
        async with db.execute('SELECT COUNT(*) FROM filtered_messages WHERE user_id = ?', (user_id,)) as cursor:
            row = await cursor.fetchone()
            return row[0] if row else 0

async def get_all_users_paginated(limit: int = 5, offset: int = 0, query: str = None):
    join_sql, where_sql, params = _user_search(query)
    hit_columns = ''
    order_sql = 'u.created_at DESC'
    if query:
        hit_columns = (
            'hit.matched_content, hit.matched_reason, hit.matched_forward, '
            'hit.matched_source, hit.matched_at, hit.match_count,'
        )
        order_sql = '(hit.matched_at IS NULL) ASC, hit.matched_at DESC, u.created_at DESC'
    async with db_manager.get_connection() as db:
        async with db.execute(f'''
            SELECT
                u.user_id,
                u.first_name,
                u.last_name,
                u.username,
                u.is_blacklisted,
                u.note,
                u.tags,
                {hit_columns}
                {_CONVERSATION_VISIBLE_SQL} AS has_conversation,
                EXISTS(SELECT 1 FROM exemptions e
                       WHERE e.user_id = u.user_id AND {_EXEMPTION_ACTIVE_SQL}) AS exempted,
                EXISTS(SELECT 1 FROM review_appeals ra
                       WHERE ra.user_id = u.user_id AND ra.status = 'pending') AS pending_appeal,
                COALESCE(spam_count.count, 0) as spam_count
            FROM users u
            LEFT JOIN (
                SELECT user_id, COUNT(*) as count
                FROM filtered_messages
                GROUP BY user_id
            ) spam_count ON u.user_id = spam_count.user_id
            {join_sql}
            {where_sql}
            ORDER BY {order_sql}
            LIMIT ? OFFSET ?
        ''', (*params, limit, offset)) as cursor:
            rows = await cursor.fetchall()
            if not rows:
                return []
            cols = [description[0] for description in cursor.description]
            items = [dict(zip(cols, row)) for row in rows]
            for item in items:
                item['tags'] = _decode_list(item.get('tags'))
            return items


async def update_user_metadata(user_id: int, note: str, tags: list) -> bool:
    async with db_manager.get_connection() as db:
        cursor = await db.execute(
            'UPDATE users SET note = ?, tags = ? WHERE user_id = ?',
            (note or None, _encode_list(tags) if tags else None, user_id),
        )
        await db.commit()
        return cursor.rowcount > 0


async def get_all_user_tags() -> list:
    async with db_manager.get_connection() as db:
        async with db.execute('SELECT tags FROM users WHERE tags IS NOT NULL') as cursor:
            rows = await cursor.fetchall()
    return sorted({tag for row in rows for tag in _decode_list(row[0])})


async def get_user_timeline(user_id: int, limit: int = 300) -> list:
    """用户的消息记录（含被拦下的），按时间正序。只读展示用。"""
    async with db_manager.get_connection() as db:
        async with db.execute('''
            SELECT * FROM (
                SELECT id, content, media_type, direction, forward_source, NULL AS reason,
                       'message' AS kind, created_at
                FROM messages WHERE user_id = ?
                UNION ALL
                SELECT id, content, media_type, 'user_to_admin' AS direction, forward_source, reason,
                       'filtered' AS kind, filtered_at AS created_at
                FROM filtered_messages WHERE user_id = ?
            )
            ORDER BY created_at DESC, id DESC
            LIMIT ?
        ''', (user_id, user_id, limit)) as cursor:
            rows = await cursor.fetchall()
            cols = [description[0] for description in cursor.description]
            return [dict(zip(cols, row)) for row in rows][::-1]


async def revoke_all_verifications(exclude_ids) -> int:
    """收回所有人的验证结果，返回实际收回的人数。"""
    exclude_ids = list(exclude_ids) or [0]
    placeholders = ','.join('?' * len(exclude_ids))
    async with db_manager.get_connection() as db:
        cursor = await db.execute(
            f'UPDATE users SET is_verified = 0 WHERE is_verified = 1 AND user_id NOT IN ({placeholders})',
            exclude_ids,
        )
        await db.commit()
        return cursor.rowcount or 0


async def clear_all_exemptions() -> int:
    async with db_manager.get_connection() as db:
        cursor = await db.execute('DELETE FROM exemptions')
        await db.commit()
        return cursor.rowcount or 0


async def prune_expired_exemptions() -> int:
    async with db_manager.get_connection() as db:
        cursor = await db.execute('''
            DELETE FROM exemptions
            WHERE is_permanent = 0
              AND (expires_at IS NULL OR datetime(expires_at) <= datetime('now'))
        ''')
        await db.commit()
        return cursor.rowcount or 0


MONITOR_EVENT_KEEP = 2000


async def record_monitor_event(
    source: str, monitor_name: str, title: str, link: str = '', detail: str = '', pushed_count: int = 0,
) -> None:
    async with db_manager.get_connection() as db:
        await db.execute('''
            INSERT INTO monitor_events (source, monitor_name, title, link, detail, pushed_count)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (source, monitor_name, title, link, detail, pushed_count))
        await db.execute(
            'DELETE FROM monitor_events WHERE id <= (SELECT MAX(id) FROM monitor_events) - ?',
            (MONITOR_EVENT_KEEP,),
        )
        await db.commit()


async def get_monitor_events(limit: int = 20, offset: int = 0, source: str = None) -> list:
    where_sql, params = ('WHERE source = ?', [source]) if source else ('', [])
    async with db_manager.get_connection() as db:
        async with db.execute(
            f'SELECT * FROM monitor_events {where_sql} ORDER BY id DESC LIMIT ? OFFSET ?',
            (*params, limit, offset),
        ) as cursor:
            rows = await cursor.fetchall()
            return [_row_to_dict(cursor, row) for row in rows]


async def get_monitor_events_count(source: str = None) -> int:
    where_sql, params = ('WHERE source = ?', [source]) if source else ('', [])
    async with db_manager.get_connection() as db:
        async with db.execute(f'SELECT COUNT(*) FROM monitor_events {where_sql}', params) as cursor:
            row = await cursor.fetchone()
            return row[0] if row else 0


async def get_blacklist_user_details(user_id: int):
    async with db_manager.get_connection() as db:
        async with db.execute('''
            SELECT 
                b.user_id,
                u.first_name,
                u.username,
                u.last_name,
                u.language_code,
                u.is_blacklisted,
                u.blacklist_strikes,
                b.reason,
                b.blocked_by,
                b.blocked_at,
                b.permanent,
                COALESCE(spam_count.count, 0) as spam_count
            FROM blacklist b
            LEFT JOIN users u ON b.user_id = u.user_id
            LEFT JOIN (
                SELECT user_id, COUNT(*) as count
                FROM filtered_messages
                GROUP BY user_id
            ) spam_count ON b.user_id = spam_count.user_id
            WHERE b.user_id = ?
        ''', (user_id,)) as cursor:
            row = await cursor.fetchone()
            if row:
                cols = [description[0] for description in cursor.description]
                return dict(zip(cols, row))
            return None

async def add_knowledge_entry(title: str, content: str):
    async with db_manager.get_connection() as db:
        await db.execute('''
            INSERT INTO knowledge_base (title, content, updated_at)
            VALUES (?, ?, CURRENT_TIMESTAMP)
        ''', (title, content))
        await db.commit()

async def get_all_knowledge_entries():
    async with db_manager.get_connection() as db:
        async with db.execute('''
            SELECT id, title, content, created_at, updated_at
            FROM knowledge_base
            ORDER BY updated_at DESC
        ''') as cursor:
            rows = await cursor.fetchall()
            if not rows:
                return []
            cols = [description[0] for description in cursor.description]
            return [dict(zip(cols, row)) for row in rows]

async def get_knowledge_entry(knowledge_id: int):
    async with db_manager.get_connection() as db:
        async with db.execute('''
            SELECT id, title, content, created_at, updated_at
            FROM knowledge_base
            WHERE id = ?
        ''', (knowledge_id,)) as cursor:
            row = await cursor.fetchone()
            if row:
                cols = [description[0] for description in cursor.description]
                return dict(zip(cols, row))
            return None

async def update_knowledge_entry(knowledge_id: int, title: str, content: str):
    async with db_manager.get_connection() as db:
        await db.execute('''
            UPDATE knowledge_base
            SET title = ?, content = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
        ''', (title, content, knowledge_id))
        await db.commit()

async def delete_knowledge_entry(knowledge_id: int):
    async with db_manager.get_connection() as db:
        await db.execute('DELETE FROM knowledge_base WHERE id = ?', (knowledge_id,))
        await db.commit()

async def get_all_knowledge_content() -> str:
    entries = await get_all_knowledge_entries()
    if not entries:
        return ""
    
    knowledge_text = "知识库内容：\n\n"
    for entry in entries:
        knowledge_text += f"标题：{entry['title']}\n"
        knowledge_text += f"内容：{entry['content']}\n\n"
    
    return knowledge_text

async def get_autoreply_enabled() -> bool:
    async with db_manager.get_connection() as db:
        async with db.execute(
            'SELECT value FROM settings WHERE key = ?',
            ('autoreply_enabled',)
        ) as cursor:
            row = await cursor.fetchone()
            if row:
                return row[0] == '1'
            return False

async def set_autoreply_enabled(enabled: bool):
    async with db_manager.get_connection() as db:
        await db.execute(
            'UPDATE settings SET value = ?, updated_at = CURRENT_TIMESTAMP WHERE key = ?',
            ('1' if enabled else '0', 'autoreply_enabled')
        )
        await db.commit()

async def is_exempted(user_id: int) -> bool:
    async with db_manager.get_connection() as db:
        async with db.execute('''
            SELECT is_permanent, expires_at 
            FROM exemptions 
            WHERE user_id = ?
        ''', (user_id,)) as cursor:
            row = await cursor.fetchone()
            if not row:
                return False
            
            is_permanent = bool(row[0])
            expires_at = row[1]
            
            if is_permanent:
                return True
            
            if expires_at:
                try:
                    expires_datetime = datetime.fromisoformat(expires_at.replace('Z', '+00:00'))
                    if expires_datetime.tzinfo is None:
                        expires_datetime = expires_datetime.replace(tzinfo=timezone.utc)
                    now = datetime.now(timezone.utc)
                    return expires_datetime > now
                except Exception as e:
                    print(f"解析豁免过期时间失败: {e}")
                    return False
            
            return False

async def add_exemption(user_id: int, is_permanent: bool, exempted_by: int, reason: str = None, expires_at: str = None):
    async with db_manager.get_connection() as db:
        await db.execute('''
            INSERT OR REPLACE INTO exemptions 
            (user_id, is_permanent, expires_at, exempted_by, reason, created_at)
            VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        ''', (user_id, 1 if is_permanent else 0, expires_at, exempted_by, reason))
        await db.commit()

async def remove_exemption(user_id: int):
    async with db_manager.get_connection() as db:
        await db.execute('DELETE FROM exemptions WHERE user_id = ?', (user_id,))
        await db.commit()

async def get_exemption(user_id: int):
    async with db_manager.get_connection() as db:
        async with db.execute('''
            SELECT user_id, is_permanent, expires_at, exempted_by, reason, created_at
            FROM exemptions
            WHERE user_id = ?
        ''', (user_id,)) as cursor:
            row = await cursor.fetchone()
            if row:
                cols = [description[0] for description in cursor.description]
                return dict(zip(cols, row))
            return None

async def get_all_exemptions():
    async with db_manager.get_connection() as db:
        async with db.execute('''
            SELECT e.user_id, u.first_name, u.username, e.is_permanent, e.expires_at, 
                   e.exempted_by, e.reason, e.created_at
            FROM exemptions e
            LEFT JOIN users u ON e.user_id = u.user_id
            ORDER BY e.created_at DESC
        ''') as cursor:
            rows = await cursor.fetchall()
            if not rows:
                return []
            cols = [description[0] for description in cursor.description]
            return [dict(zip(cols, row)) for row in rows]

async def get_exemptions_paginated(limit: int = 5, offset: int = 0, query: str = None):
    where_sql, params = _reason_match('e', query)
    async with db_manager.get_connection() as db:
        async with db.execute(f'''
            SELECT e.user_id, u.first_name, u.username, e.is_permanent, e.expires_at,
                   e.exempted_by, e.reason, e.created_at,
                   {_EXEMPTION_ACTIVE_SQL} AS is_active
            FROM exemptions e
            LEFT JOIN users u ON e.user_id = u.user_id
            {where_sql}
            ORDER BY e.created_at DESC
            LIMIT ? OFFSET ?
        ''', (*params, limit, offset)) as cursor:
            rows = await cursor.fetchall()
            if not rows:
                return []
            cols = [description[0] for description in cursor.description]
            return [dict(zip(cols, row)) for row in rows]

async def get_exemptions_count(query: str = None) -> int:
    where_sql, params = _reason_match('e', query)
    sql = f'SELECT COUNT(*) FROM exemptions e LEFT JOIN users u ON e.user_id = u.user_id {where_sql}'
    async with db_manager.get_connection() as db:
        async with db.execute(sql, params) as cursor:
            row = await cursor.fetchone()
            return row[0] if row else 0


async def create_user_group(name: str, created_by: int, description: str = None):
    async with db_manager.get_connection() as db:
        await db.execute('''
            INSERT INTO user_groups (name, description, created_by)
            VALUES (?, ?, ?)
        ''', (name, description, created_by))
        await db.commit()

        async with db.execute('SELECT * FROM user_groups WHERE name = ? COLLATE NOCASE', (name,)) as cursor:
            row = await cursor.fetchone()
            if row:
                cols = [description[0] for description in cursor.description]
                return dict(zip(cols, row))
            return None


async def get_user_group_by_name(name: str):
    async with db_manager.get_connection() as db:
        async with db.execute(
            'SELECT * FROM user_groups WHERE name = ? COLLATE NOCASE',
            (name,)
        ) as cursor:
            row = await cursor.fetchone()
            if row:
                cols = [description[0] for description in cursor.description]
                return dict(zip(cols, row))
            return None


async def get_user_group_by_id(group_id: int):
    async with db_manager.get_connection() as db:
        async with db.execute('SELECT * FROM user_groups WHERE id = ?', (group_id,)) as cursor:
            row = await cursor.fetchone()
            if row:
                cols = [description[0] for description in cursor.description]
                return dict(zip(cols, row))
            return None


async def get_or_create_user_group(name: str, created_by: int, description: str = None):
    group = await get_user_group_by_name(name)
    if group:
        return group, False
    return await create_user_group(name, created_by, description), True


async def delete_user_group(name: str) -> bool:
    async with db_manager.get_connection() as db:
        cursor = await db.execute('DELETE FROM user_groups WHERE name = ? COLLATE NOCASE', (name,))
        await db.commit()
        return cursor.rowcount > 0


async def add_user_to_group(group_name: str, user_id: int, added_by: int):
    group, created = await get_or_create_user_group(group_name, added_by)
    if not group:
        return None, created, False

    async with db_manager.get_connection() as db:
        cursor = await db.execute('''
            INSERT OR IGNORE INTO user_group_members (group_id, user_id, added_by)
            VALUES (?, ?, ?)
        ''', (group['id'], user_id, added_by))
        await db.execute('''
            UPDATE user_groups
            SET updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
        ''', (group['id'],))
        await db.commit()
        return group, created, cursor.rowcount > 0


async def remove_user_from_group(group_name: str, user_id: int) -> bool:
    group = await get_user_group_by_name(group_name)
    if not group:
        return False

    async with db_manager.get_connection() as db:
        cursor = await db.execute('''
            DELETE FROM user_group_members
            WHERE group_id = ? AND user_id = ?
        ''', (group['id'], user_id))
        await db.execute('''
            UPDATE user_groups
            SET updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
        ''', (group['id'],))
        await db.commit()
        return cursor.rowcount > 0


async def get_groups_for_user(user_id: int):
    async with db_manager.get_connection() as db:
        async with db.execute('''
            SELECT g.id, g.name, g.description, gm.added_at
            FROM user_group_members gm
            JOIN user_groups g ON gm.group_id = g.id
            WHERE gm.user_id = ?
            ORDER BY g.name ASC
        ''', (user_id,)) as cursor:
            rows = await cursor.fetchall()
            if not rows:
                return []
            cols = [description[0] for description in cursor.description]
            return [dict(zip(cols, row)) for row in rows]


async def get_all_user_groups():
    async with db_manager.get_connection() as db:
        async with db.execute('''
            SELECT
                g.id,
                g.name,
                g.description,
                g.created_by,
                g.created_at,
                g.updated_at,
                COUNT(gm.user_id) AS member_count
            FROM user_groups g
            LEFT JOIN user_group_members gm ON gm.group_id = g.id
            GROUP BY g.id
            ORDER BY g.updated_at DESC, g.name ASC
        ''') as cursor:
            rows = await cursor.fetchall()
            if not rows:
                return []
            cols = [description[0] for description in cursor.description]
            return [dict(zip(cols, row)) for row in rows]


async def get_group_members(group_name: str, include_blacklisted: bool = False):
    group = await get_user_group_by_name(group_name)
    if not group:
        return None

    blacklist_clause = '' if include_blacklisted else 'AND u.is_blacklisted = 0'
    async with db_manager.get_connection() as db:
        async with db.execute(f'''
            SELECT u.*, gm.added_at
            FROM user_group_members gm
            JOIN users u ON gm.user_id = u.user_id
            WHERE gm.group_id = ? {blacklist_clause}
            ORDER BY gm.added_at DESC
        ''', (group['id'],)) as cursor:
            rows = await cursor.fetchall()
            cols = [description[0] for description in cursor.description]
            members = [dict(zip(cols, row)) for row in rows]
            return group, members


async def get_broadcast_recipients(group_name: str = None, tag: str = None):
    async with db_manager.get_connection() as db:
        if tag:
            async with db.execute(
                'SELECT * FROM users WHERE is_blacklisted = 0 AND tags IS NOT NULL ORDER BY created_at ASC'
            ) as cursor:
                rows = await cursor.fetchall()
                cols = [description[0] for description in cursor.description]
            users = [dict(zip(cols, row)) for row in rows]
            return None, [user for user in users if tag in _decode_list(user['tags'])]

        if group_name:
            group = await get_user_group_by_name(group_name)
            if not group:
                return None, []
            async with db.execute('''
                SELECT DISTINCT u.*
                FROM user_group_members gm
                JOIN users u ON gm.user_id = u.user_id
                WHERE gm.group_id = ? AND u.is_blacklisted = 0
                ORDER BY u.created_at ASC
            ''', (group['id'],)) as cursor:
                rows = await cursor.fetchall()
                cols = [description[0] for description in cursor.description]
                return group, [dict(zip(cols, row)) for row in rows]

        async with db.execute('''
            SELECT *
            FROM users
            WHERE is_blacklisted = 0
            ORDER BY created_at ASC
        ''') as cursor:
            rows = await cursor.fetchall()
            cols = [description[0] for description in cursor.description]
            return None, [dict(zip(cols, row)) for row in rows]


async def create_broadcast(scope: str, created_by: int, group_id: int = None, source_chat_id: int = None, source_message_id: int = None, content_preview: str = None, total_count: int = 0):
    async with db_manager.get_connection() as db:
        cursor = await db.execute('''
            INSERT INTO broadcasts
            (scope, group_id, source_chat_id, source_message_id, content_preview, created_by, total_count)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (scope, group_id, source_chat_id, source_message_id, content_preview, created_by, total_count))
        await db.commit()
        return cursor.lastrowid


async def update_broadcast_counts(broadcast_id: int, success_count: int, failed_count: int):
    async with db_manager.get_connection() as db:
        await db.execute('''
            UPDATE broadcasts
            SET success_count = ?, failed_count = ?
            WHERE id = ?
        ''', (success_count, failed_count, broadcast_id))
        await db.commit()


async def save_broadcast_delivery(broadcast_id: int, user_id: int, status: str, message_id: int = None, error: str = None):
    async with db_manager.get_connection() as db:
        await db.execute('''
            INSERT OR REPLACE INTO broadcast_deliveries
            (broadcast_id, user_id, message_id, status, error)
            VALUES (?, ?, ?, ?, ?)
        ''', (broadcast_id, user_id, message_id, status, error))
        await db.commit()


async def list_broadcasts(limit: int = 20, offset: int = 0):
    async with db_manager.get_connection() as db:
        async with db.execute('''
            SELECT b.id, b.scope, b.group_id, b.content_preview, b.created_by,
                   b.total_count, b.success_count, b.failed_count, b.created_at,
                   g.name AS group_name
            FROM broadcasts b
            LEFT JOIN user_groups g ON g.id = b.group_id
            ORDER BY b.id DESC
            LIMIT ? OFFSET ?
        ''', (limit, offset)) as cursor:
            rows = await cursor.fetchall()
            if not rows:
                return []
            cols = [description[0] for description in cursor.description]
            return [dict(zip(cols, row)) for row in rows]


async def get_broadcasts_count() -> int:
    async with db_manager.get_connection() as db:
        async with db.execute('SELECT COUNT(*) FROM broadcasts') as cursor:
            row = await cursor.fetchone()
            return row[0] if row else 0


async def save_message_mapping(
    user_id: int,
    source_chat_id: int,
    source_message_id: int,
    dest_chat_id: int,
    dest_message_id: int,
    direction: str,
    thread_id: int = None,
    broadcast_id: int = None,
):
    async with db_manager.get_connection() as db:
        await db.execute('''
            INSERT OR REPLACE INTO message_mappings
            (user_id, source_chat_id, source_message_id, dest_chat_id, dest_message_id, thread_id, direction, broadcast_id, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        ''', (
            user_id,
            source_chat_id,
            source_message_id,
            dest_chat_id,
            dest_message_id,
            thread_id,
            direction,
            broadcast_id,
        ))
        await db.commit()


async def get_message_mapping_by_source(chat_id: int, message_id: int):
    async with db_manager.get_connection() as db:
        async with db.execute('''
            SELECT *
            FROM message_mappings
            WHERE source_chat_id = ? AND source_message_id = ?
            ORDER BY id DESC
            LIMIT 1
        ''', (chat_id, message_id)) as cursor:
            row = await cursor.fetchone()
            if row:
                cols = [description[0] for description in cursor.description]
                return dict(zip(cols, row))
            return None


async def get_message_mappings_by_source(chat_id: int, message_id: int):
    async with db_manager.get_connection() as db:
        async with db.execute('''
            SELECT *
            FROM message_mappings
            WHERE source_chat_id = ? AND source_message_id = ?
            ORDER BY id ASC
        ''', (chat_id, message_id)) as cursor:
            rows = await cursor.fetchall()
            if not rows:
                return []
            cols = [description[0] for description in cursor.description]
            return [dict(zip(cols, row)) for row in rows]


async def get_message_mapping_by_dest(chat_id: int, message_id: int):
    async with db_manager.get_connection() as db:
        async with db.execute('''
            SELECT *
            FROM message_mappings
            WHERE dest_chat_id = ? AND dest_message_id = ?
            ORDER BY id DESC
            LIMIT 1
        ''', (chat_id, message_id)) as cursor:
            row = await cursor.fetchone()
            if row:
                cols = [description[0] for description in cursor.description]
                return dict(zip(cols, row))
            return None


async def get_message_mapping(chat_id: int, message_id: int):
    mapping = await get_message_mapping_by_source(chat_id, message_id)
    if mapping:
        return mapping
    return await get_message_mapping_by_dest(chat_id, message_id)


async def get_message_mapping_for_user(chat_id: int, message_id: int, user_id: int):
    async with db_manager.get_connection() as db:
        async with db.execute('''
            SELECT *
            FROM message_mappings
            WHERE user_id = ?
              AND (
                (source_chat_id = ? AND source_message_id = ?)
                OR (dest_chat_id = ? AND dest_message_id = ?)
              )
            ORDER BY id DESC
            LIMIT 1
        ''', (user_id, chat_id, message_id, chat_id, message_id)) as cursor:
            row = await cursor.fetchone()
            if row:
                cols = [description[0] for description in cursor.description]
                return dict(zip(cols, row))
            return None


async def get_broadcast_delivery_mapping(user_id: int, broadcast_id: int):
    async with db_manager.get_connection() as db:
        async with db.execute('''
            SELECT *
            FROM message_mappings
            WHERE user_id = ? AND broadcast_id = ?
            ORDER BY id DESC
            LIMIT 1
        ''', (user_id, broadcast_id)) as cursor:
            row = await cursor.fetchone()
            if row:
                cols = [description[0] for description in cursor.description]
                return dict(zip(cols, row))
            return None


async def get_pending_reply_topics(limit: int = 15):
    """列出最近发来消息、管理员尚未回复的用户话题（待办聚合）。

    对每个 user_id 取其非广播映射（broadcast_id IS NULL）中 created_at 最新
    （同秒时取 id 最大）的一条，该条为 user_to_admin 的即为待回复。广播记录
    只是管理员主动推送的投递痕迹，不视为回复，也不影响待办判定。
    返回按 created_at 倒序，含 user_id / first_name / username / thread_id /
    dest_message_id / source_message_id / created_at / content / media_type。
    """
    async with db_manager.get_connection() as db:
        async with db.execute('''
            SELECT t.user_id, t.first_name, t.username, t.thread_id,
                   t.dest_message_id, t.source_message_id, t.created_at,
                   COALESCE(m_src.content, m_dst.content) AS content,
                   COALESCE(m_src.media_type, m_dst.media_type) AS media_type
            FROM (
                SELECT mm.*, u.first_name, u.username,
                       ROW_NUMBER() OVER (
                           PARTITION BY mm.user_id
                           ORDER BY mm.created_at DESC, mm.id DESC
                       ) AS rn
                FROM message_mappings mm
                JOIN users u ON u.user_id = mm.user_id
                WHERE mm.broadcast_id IS NULL
            ) t
            LEFT JOIN messages m_src
                ON m_src.user_id = t.user_id AND m_src.message_id = t.source_message_id
            LEFT JOIN messages m_dst
                ON m_dst.user_id = t.user_id AND m_dst.dest_message_id = t.dest_message_id
            LEFT JOIN conversation_reads cr ON cr.user_id = t.user_id
            WHERE t.rn = 1
              AND t.direction = 'user_to_admin'
              AND (cr.read_at IS NULL OR t.created_at > cr.read_at)
            ORDER BY t.created_at DESC, t.id DESC
            LIMIT ?
        ''', (int(limit),)) as cursor:
            rows = await cursor.fetchall()
            if not rows:
                return []
            cols = [description[0] for description in cursor.description]
            return [dict(zip(cols, row)) for row in rows]


async def count_pending_reply_topics() -> int:
    """统计待回复话题总数（与 get_pending_reply_topics 同一筛选口径，不限制条数）。"""
    async with db_manager.get_connection() as db:
        async with db.execute('''
            SELECT COUNT(*)
            FROM (
                SELECT mm.user_id, mm.direction, mm.created_at,
                       ROW_NUMBER() OVER (
                           PARTITION BY mm.user_id
                           ORDER BY mm.created_at DESC, mm.id DESC
                       ) AS rn
                FROM message_mappings mm
                WHERE mm.broadcast_id IS NULL
            ) t
            LEFT JOIN conversation_reads cr ON cr.user_id = t.user_id
            WHERE t.rn = 1
              AND t.direction = 'user_to_admin'
              AND (cr.read_at IS NULL OR t.created_at > cr.read_at)
        ''') as cursor:
            row = await cursor.fetchone()
            return int(row[0]) if row else 0


# --- 会话已读 ---

async def is_conversation_visible(user_id: int) -> bool:
    """与会话列表同一口径：已验证，或者曾经有过往来消息。"""
    async with db_manager.get_connection() as db:
        async with db.execute(
            f'SELECT {_CONVERSATION_VISIBLE_SQL} FROM users u WHERE u.user_id = ?', (user_id,),
        ) as cursor:
            row = await cursor.fetchone()
            return bool(row and row[0])


async def mark_conversation_read(user_id: int) -> None:
    async with db_manager.get_connection() as db:
        await db.execute('''
            INSERT INTO conversation_reads (user_id, read_at) VALUES (?, CURRENT_TIMESTAMP)
            ON CONFLICT(user_id) DO UPDATE SET read_at = CURRENT_TIMESTAMP
        ''', (user_id,))
        await db.commit()


async def mark_all_conversations_read() -> int:
    """把所有「最后一条是用户消息」的会话标为已读，返回处理的会话数。"""
    async with db_manager.get_connection() as db:
        cursor = await db.execute('''
            INSERT INTO conversation_reads (user_id, read_at)
            SELECT DISTINCT user_id, CURRENT_TIMESTAMP FROM messages
            WHERE true
            ON CONFLICT(user_id) DO UPDATE SET read_at = CURRENT_TIMESTAMP
        ''')
        await db.commit()
        return cursor.rowcount or 0


# --- 审查暂停申请 ---

async def count_recent_ai_filtered(user_id: int, hours: int) -> int:
    """最近 hours 小时内被 AI 审查拦下的消息数（关键词拦截不算）。"""
    async with db_manager.get_connection() as db:
        async with db.execute('''
            SELECT COUNT(*) FROM filtered_messages
            WHERE user_id = ?
              AND filtered_at >= datetime('now', ?)
              AND (reason IS NULL OR reason NOT LIKE '关键词广告拦截命中%')
        ''', (user_id, f'-{int(hours)} hours')) as cursor:
            row = await cursor.fetchone()
            return int(row[0]) if row else 0


async def create_review_appeal(user_id: int, blocked_reason: str) -> int:
    async with db_manager.get_connection() as db:
        cursor = await db.execute(
            'INSERT INTO review_appeals (user_id, blocked_reason) VALUES (?, ?)',
            (user_id, blocked_reason),
        )
        await db.commit()
        return cursor.lastrowid


async def set_review_appeal_topic_message(appeal_id: int, message_id: int) -> None:
    async with db_manager.get_connection() as db:
        await db.execute('UPDATE review_appeals SET topic_message_id = ? WHERE id = ?', (message_id, appeal_id))
        await db.commit()


async def _fetch_appeal(sql: str, params: tuple):
    async with db_manager.get_connection() as db:
        async with db.execute(sql, params) as cursor:
            row = await cursor.fetchone()
            if not row:
                return None
            cols = [description[0] for description in cursor.description]
            return dict(zip(cols, row))


async def get_review_appeal(appeal_id: int):
    return await _fetch_appeal('SELECT * FROM review_appeals WHERE id = ?', (appeal_id,))


async def get_pending_review_appeal(user_id: int):
    return await _fetch_appeal(
        "SELECT * FROM review_appeals WHERE user_id = ? AND status = 'pending' ORDER BY id DESC LIMIT 1",
        (user_id,),
    )


async def get_latest_review_appeal(user_id: int):
    return await _fetch_appeal(
        'SELECT * FROM review_appeals WHERE user_id = ? ORDER BY id DESC LIMIT 1', (user_id,),
    )


def _pending_appeal_match(query: str) -> tuple:
    if not query:
        return "WHERE ra.status = 'pending'", []
    return f"WHERE ra.status = 'pending' AND {_USER_MATCH_SQL}", _user_match_params(query)


async def get_pending_review_appeals(limit: int = 20, offset: int = 0, query: str = None):
    where_sql, params = _pending_appeal_match(query)
    async with db_manager.get_connection() as db:
        async with db.execute(f'''
            SELECT ra.id, ra.user_id, ra.blocked_reason, ra.created_at, u.first_name, u.username,
                   (SELECT fm.content FROM filtered_messages fm
                    WHERE fm.user_id = ra.user_id ORDER BY fm.id DESC LIMIT 1) AS blocked_content
            FROM review_appeals ra
            JOIN users u ON ra.user_id = u.user_id
            {where_sql}
            ORDER BY ra.id DESC
            LIMIT ? OFFSET ?
        ''', (*params, limit, offset)) as cursor:
            rows = await cursor.fetchall()
            if not rows:
                return []
            cols = [description[0] for description in cursor.description]
            return [dict(zip(cols, row)) for row in rows]


async def get_pending_review_appeals_count(query: str = None) -> int:
    where_sql, params = _pending_appeal_match(query)
    sql = f'SELECT COUNT(*) FROM review_appeals ra JOIN users u ON ra.user_id = u.user_id {where_sql}'
    async with db_manager.get_connection() as db:
        async with db.execute(sql, params) as cursor:
            row = await cursor.fetchone()
            return row[0] if row else 0


async def resolve_review_appeals(user_id: int, status: str, handled_by: int, duration_minutes: int = None) -> int:
    """把该用户所有待处理的申请标为 approved / rejected / cancelled，返回处理条数。"""
    async with db_manager.get_connection() as db:
        cursor = await db.execute('''
            UPDATE review_appeals
            SET status = ?, handled_by = ?, duration_minutes = ?, handled_at = CURRENT_TIMESTAMP
            WHERE user_id = ? AND status = 'pending'
        ''', (status, handled_by, duration_minutes, user_id))
        await db.commit()
        return cursor.rowcount or 0


# --- Settings ---

async def get_setting(key: str, default: str = None):
    async with db_manager.get_connection() as db:
        async with db.execute('SELECT value FROM settings WHERE key = ?', (key,)) as cursor:
            row = await cursor.fetchone()
            if row:
                return row[0]
            return default


async def set_setting(key: str, value: str, description: str = None):
    async with db_manager.get_connection() as db:
        await db.execute('''
            INSERT INTO settings (key, value, description, updated_at)
            VALUES (?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(key) DO UPDATE SET
                value = excluded.value,
                description = COALESCE(excluded.description, settings.description),
                updated_at = CURRENT_TIMESTAMP
        ''', (key, str(value), description))
        await db.commit()


async def get_all_settings() -> dict:
    """返回 settings 表全部键值（供启动时加载配置覆盖层）。"""
    async with db_manager.get_connection() as db:
        async with db.execute('SELECT key, value FROM settings') as cursor:
            rows = await cursor.fetchall()
            return {row[0]: row[1] for row in rows}


async def delete_setting(key: str) -> bool:
    async with db_manager.get_connection() as db:
        cursor = await db.execute('DELETE FROM settings WHERE key = ?', (key,))
        await db.commit()
        return cursor.rowcount > 0


# --- Keyword spam filter ---

async def get_spam_keyword_filter_settings():
    enabled = await get_setting(
        'spam_keyword_filter_enabled',
        '1' if config.SPAM_KEYWORD_FILTER_ENABLED else '0',
    )
    auto_block = await get_setting(
        'spam_keyword_auto_block',
        '1' if config.SPAM_KEYWORD_AUTO_BLOCK else '0',
    )
    return {
        'enabled': str(enabled) == '1',
        'auto_block': str(auto_block) == '1',
        'keywords': await get_spam_keywords(),
    }


async def set_spam_keyword_filter_enabled(enabled: bool):
    await set_setting(
        'spam_keyword_filter_enabled',
        '1' if enabled else '0',
        '是否启用关键词广告拦截 (1=是, 0=否)',
    )


async def set_spam_keyword_auto_block(enabled: bool):
    await set_setting(
        'spam_keyword_auto_block',
        '1' if enabled else '0',
        '关键词广告拦截命中后是否自动拉黑 (1=是, 0=否)',
    )


async def get_spam_keywords():
    async with db_manager.get_connection() as db:
        async with db.execute('SELECT keyword FROM spam_keywords ORDER BY keyword COLLATE NOCASE ASC') as cursor:
            rows = await cursor.fetchall()
            return [row[0] for row in rows]


async def add_spam_keyword(keyword: str, created_by: int = None) -> bool:
    keyword = (keyword or '').strip()
    if not keyword:
        return False
    async with db_manager.get_connection() as db:
        cursor = await db.execute('''
            INSERT OR IGNORE INTO spam_keywords (keyword, created_by)
            VALUES (?, ?)
        ''', (keyword, created_by))
        await db.commit()
        return cursor.rowcount > 0


async def remove_spam_keyword(keyword: str) -> bool:
    keyword = (keyword or '').strip()
    if not keyword:
        return False
    async with db_manager.get_connection() as db:
        cursor = await db.execute('DELETE FROM spam_keywords WHERE keyword = ? COLLATE NOCASE', (keyword,))
        await db.commit()
        return cursor.rowcount > 0


async def clear_spam_keywords() -> int:
    async with db_manager.get_connection() as db:
        cursor = await db.execute('DELETE FROM spam_keywords')
        await db.commit()
        return cursor.rowcount


# --- Runtime status ---

async def record_runtime_status(
    name: str,
    category: str,
    ok: bool,
    duration_ms: int = 0,
    sent_count: int = 0,
    error: str = '',
):
    name = str(name or '').strip()
    category = str(category or 'general').strip()
    if not name:
        return

    ok_flag = 1 if ok else 0
    async with db_manager.get_connection() as db:
        await db.execute('''
            INSERT INTO runtime_status (
                name, category, last_run_at, last_success_at, last_error_at, last_error,
                last_duration_ms, last_sent_count, consecutive_failures, updated_at
            )
            VALUES (
                ?, ?, CURRENT_TIMESTAMP,
                CASE WHEN ? THEN CURRENT_TIMESTAMP ELSE NULL END,
                CASE WHEN ? THEN NULL ELSE CURRENT_TIMESTAMP END,
                ?, ?, ?, CASE WHEN ? THEN 0 ELSE 1 END, CURRENT_TIMESTAMP
            )
            ON CONFLICT(name) DO UPDATE SET
                category = excluded.category,
                last_run_at = excluded.last_run_at,
                last_success_at = CASE
                    WHEN excluded.last_success_at IS NOT NULL THEN excluded.last_success_at
                    ELSE runtime_status.last_success_at
                END,
                last_error_at = CASE
                    WHEN excluded.last_error_at IS NOT NULL THEN excluded.last_error_at
                    ELSE runtime_status.last_error_at
                END,
                last_error = CASE
                    WHEN excluded.last_error != '' THEN excluded.last_error
                    ELSE runtime_status.last_error
                END,
                last_duration_ms = excluded.last_duration_ms,
                last_sent_count = excluded.last_sent_count,
                consecutive_failures = CASE
                    WHEN ? THEN 0
                    ELSE runtime_status.consecutive_failures + 1
                END,
                updated_at = CURRENT_TIMESTAMP
        ''', (
            name,
            category,
            ok_flag,
            ok_flag,
            '' if ok else str(error or '')[:1000],
            max(0, int(duration_ms or 0)),
            max(0, int(sent_count or 0)),
            ok_flag,
            ok_flag,
        ))
        await db.commit()


async def get_runtime_statuses(category: str = None, limit: int = 50):
    async with db_manager.get_connection() as db:
        if category:
            query = '''
                SELECT * FROM runtime_status
                WHERE category = ?
                ORDER BY updated_at DESC
                LIMIT ?
            '''
            params = (category, limit)
        else:
            query = '''
                SELECT * FROM runtime_status
                ORDER BY updated_at DESC
                LIMIT ?
            '''
            params = (limit,)

        async with db.execute(query, params) as cursor:
            rows = await cursor.fetchall()
            if not rows:
                return []
            return [_row_to_dict(cursor, row) for row in rows]


# --- Telegram group/channel monitors ---

def normalize_tg_monitor(row: dict):
    if not row:
        return None
    row['keywords'] = _decode_list(row.get('keywords'))
    row['exclude_keywords'] = _decode_list(row.get('exclude_keywords'))
    row['enabled'] = bool(row.get('enabled'))
    row['notify_telegram'] = bool(row.get('notify_telegram'))
    return row


async def create_tg_group_monitor(
    name: str,
    chat_id: int,
    keywords: list[str],
    created_by: int,
    chat_title: str = None,
    listen_source: str = 'user_session',
    exclude_keywords: list[str] = None,
    min_interval_seconds: int = 30,
    dedupe_window_seconds: int = 300,
):
    listen_source = (listen_source or 'user_session').strip().lower()
    if listen_source not in {'bot', 'user_session'}:
        listen_source = 'user_session'

    async with db_manager.get_connection() as db:
        cursor = await db.execute('''
            INSERT INTO tg_group_monitors (
                name, chat_id, chat_title, listen_source, keywords, exclude_keywords,
                created_by, min_interval_seconds, dedupe_window_seconds
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            name,
            int(chat_id),
            chat_title,
            listen_source,
            _encode_list(keywords),
            _encode_list(exclude_keywords or []),
            created_by,
            max(0, int(min_interval_seconds or 0)),
            max(0, int(dedupe_window_seconds or 0)),
        ))
        await db.commit()
        return cursor.lastrowid


async def update_tg_group_monitor(monitor_id: int, **kwargs):
    allowed = {
        'name',
        'chat_id',
        'chat_title',
        'listen_source',
        'keywords',
        'exclude_keywords',
        'enabled',
        'notify_telegram',
        'min_interval_seconds',
        'dedupe_window_seconds',
        'notify_chat_ids',
    }
    updates = []
    values = []
    for key, value in kwargs.items():
        if key not in allowed:
            continue
        if key in {'keywords', 'exclude_keywords'}:
            value = _encode_list(value or [])
        elif key in {'enabled', 'notify_telegram'}:
            value = 1 if value else 0
        elif key in {'chat_id', 'min_interval_seconds', 'dedupe_window_seconds'}:
            value = int(value)
        elif key == 'listen_source':
            value = str(value or 'user_session').strip().lower()
            if value not in {'bot', 'user_session'}:
                value = 'user_session'
        updates.append(f'{key} = ?')
        values.append(value)

    if not updates:
        return False

    updates.append('updated_at = CURRENT_TIMESTAMP')
    values.append(int(monitor_id))
    async with db_manager.get_connection() as db:
        cursor = await db.execute(
            f"UPDATE tg_group_monitors SET {', '.join(updates)} WHERE id = ?",
            values,
        )
        await db.commit()
        return cursor.rowcount > 0


async def delete_tg_group_monitor(monitor_id: int) -> bool:
    async with db_manager.get_connection() as db:
        cursor = await db.execute('DELETE FROM tg_group_monitors WHERE id = ?', (int(monitor_id),))
        await db.commit()
        return cursor.rowcount > 0


async def get_tg_group_monitor(monitor_id: int):
    async with db_manager.get_connection() as db:
        async with db.execute('SELECT * FROM tg_group_monitors WHERE id = ?', (int(monitor_id),)) as cursor:
            row = await cursor.fetchone()
            if not row:
                return None
            return normalize_tg_monitor(_row_to_dict(cursor, row))


async def get_tg_group_monitor_by_name(name: str):
    async with db_manager.get_connection() as db:
        async with db.execute(
            'SELECT * FROM tg_group_monitors WHERE name = ? COLLATE NOCASE',
            ((name or '').strip(),),
        ) as cursor:
            row = await cursor.fetchone()
            if not row:
                return None
            return normalize_tg_monitor(_row_to_dict(cursor, row))


async def list_tg_group_monitors(enabled_only: bool = False):
    async with db_manager.get_connection() as db:
        query = 'SELECT * FROM tg_group_monitors'
        params = ()
        if enabled_only:
            query += ' WHERE enabled = 1'
        query += ' ORDER BY updated_at DESC, id DESC'
        async with db.execute(query, params) as cursor:
            rows = await cursor.fetchall()
            if not rows:
                return []
            return [normalize_tg_monitor(_row_to_dict(cursor, row)) for row in rows]


async def get_tg_group_monitors_for_chat(chat_id: int, listen_source: str):
    source = (listen_source or 'user_session').strip().lower()
    async with db_manager.get_connection() as db:
        async with db.execute('''
            SELECT * FROM tg_group_monitors
            WHERE chat_id = ? AND listen_source = ? AND enabled = 1
            ORDER BY id DESC
        ''', (int(chat_id), source)) as cursor:
            rows = await cursor.fetchall()
            if not rows:
                return []
            return [normalize_tg_monitor(_row_to_dict(cursor, row)) for row in rows]


async def get_tg_group_monitor_for_chat(chat_id: int, listen_source: str):
    monitors = await get_tg_group_monitors_for_chat(chat_id, listen_source)
    return monitors[0] if monitors else None


async def tg_monitor_allow_send(
    monitor_id: int,
    fingerprint: str,
    min_interval_seconds: int,
    dedupe_window_seconds: int,
    now_ts: float,
):
    min_interval_seconds = max(0, int(min_interval_seconds or 0))
    dedupe_window_seconds = max(0, int(dedupe_window_seconds or 0))

    async with _get_tg_monitor_lock(monitor_id):
        async with db_manager.get_connection() as db:
            if min_interval_seconds > 0:
                async with db.execute(
                    'SELECT sent_at_ts FROM tg_monitor_last_send WHERE monitor_id = ?',
                    (int(monitor_id),),
                ) as cursor:
                    row = await cursor.fetchone()
                    if row and now_ts - float(row[0]) < min_interval_seconds:
                        return False, f'min-interval({min_interval_seconds}s)'

            if dedupe_window_seconds > 0:
                async with db.execute('''
                    SELECT sent_at_ts FROM tg_monitor_recent
                    WHERE monitor_id = ? AND fingerprint = ?
                ''', (int(monitor_id), fingerprint)) as cursor:
                    row = await cursor.fetchone()
                    if row and now_ts - float(row[0]) < dedupe_window_seconds:
                        return False, f'dedupe({dedupe_window_seconds}s)'

                await db.execute(
                    'DELETE FROM tg_monitor_recent WHERE monitor_id = ? AND sent_at_ts < ?',
                    (int(monitor_id), now_ts - dedupe_window_seconds),
                )

                # 原子去重：tg_monitor_recent 主键 (monitor_id, fingerprint) 保证并发
                # 触发时（bot 监听 / user_session 监听）只有一个任务能插入成功，
                # 冲突即代表该指纹刚刚已发送过，直接拒绝，避免 TOCTOU 重复推送。
                cursor = await db.execute(
                    'INSERT OR IGNORE INTO tg_monitor_recent (monitor_id, fingerprint, sent_at_ts) VALUES (?, ?, ?)',
                    (int(monitor_id), fingerprint, now_ts),
                )
                if cursor.rowcount == 0:
                    await db.commit()
                    return False, f'dedupe({dedupe_window_seconds}s)'
            else:
                # dedupe 窗口为 0 时不拦截，仅记录最近指纹供 min_interval 参考
                await db.execute('''
                    INSERT INTO tg_monitor_recent (monitor_id, fingerprint, sent_at_ts)
                    VALUES (?, ?, ?)
                    ON CONFLICT(monitor_id, fingerprint) DO UPDATE SET sent_at_ts = excluded.sent_at_ts
                ''', (int(monitor_id), fingerprint, now_ts))

            await db.execute('''
                INSERT INTO tg_monitor_last_send (monitor_id, sent_at_ts)
                VALUES (?, ?)
                ON CONFLICT(monitor_id) DO UPDATE SET sent_at_ts = excluded.sent_at_ts
            ''', (int(monitor_id), now_ts))
            await db.commit()

    return True, ''


async def record_discovered_tg_chat(chat_id: int, title: str = None, username: str = None):
    async with db_manager.get_connection() as db:
        await db.execute('''
            INSERT INTO discovered_tg_chats (chat_id, title, username, last_seen_at, active)
            VALUES (?, ?, ?, CURRENT_TIMESTAMP, 1)
            ON CONFLICT(chat_id) DO UPDATE SET
                title = excluded.title,
                username = excluded.username,
                last_seen_at = CURRENT_TIMESTAMP,
                active = 1
        ''', (int(chat_id), title, username))
        await db.commit()


async def list_discovered_tg_chats(limit: int = 30):
    async with db_manager.get_connection() as db:
        async with db.execute('''
            SELECT * FROM discovered_tg_chats
            ORDER BY last_seen_at DESC
            LIMIT ?
        ''', (int(limit),)) as cursor:
            rows = await cursor.fetchall()
            if not rows:
                return []
            return [_row_to_dict(cursor, row) for row in rows]


# --- App metadata ---

async def get_app_meta(key: str, default: str = ''):
    async with db_manager.get_connection() as db:
        async with db.execute('SELECT meta_value FROM app_meta WHERE meta_key = ?', (key,)) as cursor:
            row = await cursor.fetchone()
            if row and row[0] is not None:
                return str(row[0])
            return default


async def set_app_meta(key: str, value: str):
    async with db_manager.get_connection() as db:
        await db.execute('''
            INSERT INTO app_meta (meta_key, meta_value, updated_at)
            VALUES (?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(meta_key) DO UPDATE SET
                meta_value = excluded.meta_value,
                updated_at = CURRENT_TIMESTAMP
        ''', (key, value))
        await db.commit()


# --- Web monitors ---

def normalize_web_monitor(row: dict):
    if not row:
        return None
    row['keywords'] = _decode_list(row.get('keywords'))
    for key in ('enabled', 'notify_telegram', 'notify_on_keyword', 'notify_on_new_item', 'notify_on_change'):
        row[key] = bool(row.get(key))
    return row


async def create_web_monitor(
    name: str,
    url: str,
    keywords: list[str],
    created_by: int,
    interval_seconds: int = 300,
    item_selector: str = None,
    title_selector: str = None,
    link_selector: str = None,
    price_selector: str = None,
    stock_selector: str = None,
    monitor_type: str = 'page',
    product_keyword: str = '',
    in_stock_words: str = '',
    out_of_stock_words: str = '',
    notify_chat_ids: str = '',
):
    async with db_manager.get_connection() as db:
        cursor = await db.execute('''
            INSERT INTO web_monitors (
                name, url, keywords, interval_seconds, item_selector, title_selector,
                link_selector, price_selector, stock_selector, created_by,
                monitor_type, product_keyword, in_stock_words, out_of_stock_words, notify_chat_ids
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            name,
            url,
            _encode_list(keywords),
            max(60, int(interval_seconds or 300)),
            item_selector or 'article, .thread, .post, li',
            title_selector or 'h1, h2, h3, a',
            link_selector or 'a',
            price_selector or '',
            stock_selector or '',
            created_by,
            monitor_type,
            product_keyword or '',
            in_stock_words or '',
            out_of_stock_words or '',
            notify_chat_ids or '',
        ))
        await db.commit()
        return cursor.lastrowid


async def update_web_monitor(monitor_id: int, **kwargs):
    allowed = {
        'name',
        'url',
        'keywords',
        'item_selector',
        'title_selector',
        'link_selector',
        'price_selector',
        'stock_selector',
        'enabled',
        'interval_seconds',
        'notify_telegram',
        'notify_on_keyword',
        'notify_on_new_item',
        'notify_on_change',
        'last_checked_at',
        'product_keyword',
        'in_stock_words',
        'out_of_stock_words',
        'notify_chat_ids',
        'last_error',
    }
    updates = []
    values = []
    for key, value in kwargs.items():
        if key not in allowed:
            continue
        if key == 'keywords':
            value = _encode_list(value or [])
        elif key in {'enabled', 'notify_telegram', 'notify_on_keyword', 'notify_on_new_item', 'notify_on_change'}:
            value = 1 if value else 0
        elif key == 'interval_seconds':
            value = max(60, int(value))
        updates.append(f'{key} = ?')
        values.append(value)

    if not updates:
        return False

    updates.append('updated_at = CURRENT_TIMESTAMP')
    values.append(int(monitor_id))
    async with db_manager.get_connection() as db:
        cursor = await db.execute(
            f"UPDATE web_monitors SET {', '.join(updates)} WHERE id = ?",
            values,
        )
        await db.commit()
        return cursor.rowcount > 0


async def delete_web_monitor(monitor_id: int) -> bool:
    async with db_manager.get_connection() as db:
        cursor = await db.execute('DELETE FROM web_monitors WHERE id = ?', (int(monitor_id),))
        await db.commit()
        return cursor.rowcount > 0


async def get_web_monitor(monitor_id: int):
    async with db_manager.get_connection() as db:
        async with db.execute('SELECT * FROM web_monitors WHERE id = ?', (int(monitor_id),)) as cursor:
            row = await cursor.fetchone()
            if not row:
                return None
            return normalize_web_monitor(_row_to_dict(cursor, row))


async def list_web_monitors(enabled_only: bool = False):
    async with db_manager.get_connection() as db:
        query = 'SELECT * FROM web_monitors'
        params = ()
        if enabled_only:
            query += ' WHERE enabled = 1'
        query += ' ORDER BY updated_at DESC, id DESC'
        async with db.execute(query, params) as cursor:
            rows = await cursor.fetchall()
            if not rows:
                return []
            return [normalize_web_monitor(_row_to_dict(cursor, row)) for row in rows]


async def list_due_web_monitors():
    async with db_manager.get_connection() as db:
        async with db.execute('''
            SELECT * FROM web_monitors
            WHERE enabled = 1
              AND (
                last_checked_at IS NULL
                OR datetime(last_checked_at, '+' || interval_seconds || ' seconds') <= CURRENT_TIMESTAMP
              )
            ORDER BY COALESCE(last_checked_at, '1970-01-01') ASC
        ''') as cursor:
            rows = await cursor.fetchall()
            if not rows:
                return []
            return [normalize_web_monitor(_row_to_dict(cursor, row)) for row in rows]


async def get_web_monitor_state(monitor_id: int, item_key: str):
    async with db_manager.get_connection() as db:
        async with db.execute('''
            SELECT * FROM web_monitor_state
            WHERE monitor_id = ? AND item_key = ?
        ''', (int(monitor_id), item_key)) as cursor:
            row = await cursor.fetchone()
            if not row:
                return None
            return _row_to_dict(cursor, row)


async def has_web_monitor_state(monitor_id: int) -> bool:
    async with db_manager.get_connection() as db:
        async with db.execute(
            'SELECT 1 FROM web_monitor_state WHERE monitor_id = ? LIMIT 1',
            (int(monitor_id),),
        ) as cursor:
            return await cursor.fetchone() is not None


async def save_web_monitor_state(
    monitor_id: int,
    item_key: str,
    title: str,
    link: str,
    content_hash: str,
    price: str = '',
    stock: str = '',
):
    async with db_manager.get_connection() as db:
        await db.execute('''
            INSERT INTO web_monitor_state (
                monitor_id, item_key, title, link, content_hash, price, stock, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(monitor_id, item_key) DO UPDATE SET
                title = excluded.title,
                link = excluded.link,
                content_hash = excluded.content_hash,
                price = excluded.price,
                stock = excluded.stock,
                updated_at = CURRENT_TIMESTAMP
        ''', (int(monitor_id), item_key, title, link, content_hash, price, stock))
        await db.commit()
