import json
from datetime import datetime, timezone, timedelta
from .db_manager import db_manager
from config import config


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


# --- Read Receipts ---

async def add_read_receipt(user_id: int, forum_message_id: int, thread_id: int):
    async with db_manager.get_connection() as db:
        await db.execute('''
            INSERT INTO read_receipts (user_id, forum_message_id, thread_id)
            VALUES (?, ?, ?)
        ''', (user_id, forum_message_id, thread_id))
        await db.commit()


async def mark_receipts_read(user_id: int):
    async with db_manager.get_connection() as db:
        async with db.execute('''
            SELECT forum_message_id, thread_id FROM read_receipts
            WHERE user_id = ? AND is_read = 0
        ''', (user_id,)) as cursor:
            rows = await cursor.fetchall()
            unread = [{'forum_message_id': r[0], 'thread_id': r[1]} for r in rows]
        if unread:
            await db.execute('''
                UPDATE read_receipts SET is_read = 1, read_at = CURRENT_TIMESTAMP
                WHERE user_id = ? AND is_read = 0
            ''', (user_id,))
            await db.commit()
        return unread


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

    async with db_manager.get_connection() as db:
        async with db.execute(
            'SELECT consecutive_failures FROM runtime_status WHERE name = ?',
            (name,),
        ) as cursor:
            row = await cursor.fetchone()
            previous_failures = int(row[0]) if row else 0

        failures = 0 if ok else previous_failures + 1
        await db.execute('''
            INSERT INTO runtime_status (
                name, category, last_run_at, last_success_at, last_error_at, last_error,
                last_duration_ms, last_sent_count, consecutive_failures, updated_at
            )
            VALUES (
                ?, ?, CURRENT_TIMESTAMP,
                CASE WHEN ? THEN CURRENT_TIMESTAMP ELSE NULL END,
                CASE WHEN ? THEN NULL ELSE CURRENT_TIMESTAMP END,
                ?, ?, ?, ?, CURRENT_TIMESTAMP
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
                consecutive_failures = excluded.consecutive_failures,
                updated_at = CURRENT_TIMESTAMP
        ''', (
            name,
            category,
            1 if ok else 0,
            1 if ok else 0,
            '' if ok else str(error or '')[:1000],
            max(0, int(duration_ms or 0)),
            max(0, int(sent_count or 0)),
            failures,
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


async def get_tg_group_monitor_for_chat(chat_id: int, listen_source: str):
    source = (listen_source or 'user_session').strip().lower()
    async with db_manager.get_connection() as db:
        async with db.execute('''
            SELECT * FROM tg_group_monitors
            WHERE chat_id = ? AND listen_source = ? AND enabled = 1
            ORDER BY id DESC
            LIMIT 1
        ''', (int(chat_id), source)) as cursor:
            row = await cursor.fetchone()
            if not row:
                return None
            return normalize_tg_monitor(_row_to_dict(cursor, row))


async def tg_monitor_allow_send(
    monitor_id: int,
    fingerprint: str,
    min_interval_seconds: int,
    dedupe_window_seconds: int,
    now_ts: float,
):
    min_interval_seconds = max(0, int(min_interval_seconds or 0))
    dedupe_window_seconds = max(0, int(dedupe_window_seconds or 0))

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
                'DELETE FROM tg_monitor_recent WHERE sent_at_ts < ?',
                (now_ts - dedupe_window_seconds,),
            )

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
):
    async with db_manager.get_connection() as db:
        cursor = await db.execute('''
            INSERT INTO web_monitors (
                name, url, keywords, interval_seconds, item_selector, title_selector,
                link_selector, price_selector, stock_selector, created_by
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
