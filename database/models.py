from datetime import datetime, timezone, timedelta
from .db_manager import db_manager
from config import config

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
    async with db_manager.get_connection() as db:
        await db.execute('''
            INSERT OR REPLACE INTO users
            (user_id, username, first_name, last_name, language_code, last_active)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (user_id, username, first_name, last_name, language_code, datetime.now()))
        await db.commit()

async def update_user_profile(user_id: int, username: str, first_name: str, last_name: str = None, language_code: str = None):
    async with db_manager.get_connection() as db:
        await db.execute(
            '''
            UPDATE users
            SET username = ?, first_name = ?, last_name = ?, language_code = ?, last_active = ?
            WHERE user_id = ?
            ''',
            (username, first_name, last_name, language_code, datetime.now(), user_id)
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

async def save_message(user_id: int, message_id: int, content: str, direction: str, media_type: str = None, media_file_id: str = None):
    async with db_manager.get_connection() as db:
        await db.execute('''
            INSERT INTO messages
            (user_id, message_id, content, direction, media_type, media_file_id)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (user_id, message_id, content, direction, media_type, media_file_id))
        await db.commit()

async def save_filtered_message(user_id: int, message_id: int, content: str, reason: str, media_type: str = None, media_file_id: str = None):
    async with db_manager.get_connection() as db:
        await db.execute('''
            INSERT INTO filtered_messages
            (user_id, message_id, content, reason, media_type, media_file_id)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (user_id, message_id, content, reason, media_type, media_file_id))
        await db.commit()

async def get_filtered_messages(limit: int = 20, offset: int = 0):
    async with db_manager.get_connection() as db:
        async with db.execute('''
            SELECT fm.*, u.first_name, u.username
            FROM filtered_messages fm
            JOIN users u ON fm.user_id = u.user_id
            ORDER BY fm.filtered_at DESC
            LIMIT ? OFFSET ?
        ''', (limit, offset)) as cursor:
            rows = await cursor.fetchall()
            if not rows:
                return []
            cols = [description[0] for description in cursor.description]
            return [dict(zip(cols, row)) for row in rows]

async def get_filtered_messages_count() -> int:
    async with db_manager.get_connection() as db:
        async with db.execute('SELECT COUNT(*) FROM filtered_messages') as cursor:
            row = await cursor.fetchone()
            return row[0] if row else 0

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
        await db.execute(
            'UPDATE users SET is_blacklisted = 1, blacklist_strikes = blacklist_strikes + 1 WHERE user_id = ?',
            (user_id,)
        )
        await db.execute('''
            INSERT OR REPLACE INTO blacklist (user_id, reason, blocked_by, permanent)
            VALUES (?, ?, ?, ?)
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

async def get_blacklist_paginated(limit: int = 5, offset: int = 0):
    async with db_manager.get_connection() as db:
        async with db.execute('''
            SELECT b.user_id, u.first_name, u.username, b.reason, b.blocked_at
            FROM blacklist b
            LEFT JOIN users u ON b.user_id = u.user_id
            ORDER BY b.blocked_at DESC
            LIMIT ? OFFSET ?
        ''', (limit, offset)) as cursor:
            rows = await cursor.fetchall()
            if not rows:
                return []
            cols = [description[0] for description in cursor.description]
            return [dict(zip(cols, row)) for row in rows]

async def get_blacklist_count() -> int:
    async with db_manager.get_connection() as db:
        async with db.execute('SELECT COUNT(*) FROM blacklist') as cursor:
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

async def get_total_users_count() -> int:
    async with db_manager.get_connection() as db:
        async with db.execute('SELECT COUNT(*) FROM users') as cursor:
            row = await cursor.fetchone()
            return row[0] if row else 0

async def get_blocked_users_count() -> int:
    async with db_manager.get_connection() as db:
        async with db.execute('SELECT COUNT(*) FROM blacklist') as cursor:
            row = await cursor.fetchone()
            return row[0] if row else 0

async def get_user_spam_count(user_id: int) -> int:
    async with db_manager.get_connection() as db:
        async with db.execute('SELECT COUNT(*) FROM filtered_messages WHERE user_id = ?', (user_id,)) as cursor:
            row = await cursor.fetchone()
            return row[0] if row else 0

async def get_all_users_paginated(limit: int = 5, offset: int = 0):
    async with db_manager.get_connection() as db:
        async with db.execute('''
            SELECT 
                u.user_id,
                u.first_name,
                u.username,
                u.is_blacklisted,
                COALESCE(spam_count.count, 0) as spam_count
            FROM users u
            LEFT JOIN (
                SELECT user_id, COUNT(*) as count
                FROM filtered_messages
                GROUP BY user_id
            ) spam_count ON u.user_id = spam_count.user_id
            ORDER BY u.created_at DESC
            LIMIT ? OFFSET ?
        ''', (limit, offset)) as cursor:
            rows = await cursor.fetchall()
            if not rows:
                return []
            cols = [description[0] for description in cursor.description]
            return [dict(zip(cols, row)) for row in rows]

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

async def get_exemptions_paginated(limit: int = 5, offset: int = 0):
    async with db_manager.get_connection() as db:
        async with db.execute('''
            SELECT e.user_id, u.first_name, u.username, e.is_permanent, e.expires_at, 
                   e.exempted_by, e.reason, e.created_at
            FROM exemptions e
            LEFT JOIN users u ON e.user_id = u.user_id
            ORDER BY e.created_at DESC
            LIMIT ? OFFSET ?
        ''', (limit, offset)) as cursor:
            rows = await cursor.fetchall()
            if not rows:
                return []
            cols = [description[0] for description in cursor.description]
            return [dict(zip(cols, row)) for row in rows]

async def get_exemptions_count() -> int:
    async with db_manager.get_connection() as db:
        async with db.execute('SELECT COUNT(*) FROM exemptions') as cursor:
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


async def get_broadcast_recipients(group_name: str = None):
    async with db_manager.get_connection() as db:
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
