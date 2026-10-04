"""数据库升级回归测试。"""

import asyncio

import aiosqlite

from database.db_manager import DatabaseManager


async def _migrate_legacy_messages_table(database_path):
    async with aiosqlite.connect(database_path) as connection:
        await connection.execute('''
            CREATE TABLE messages (
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
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        await connection.commit()

    manager = DatabaseManager(str(database_path))
    try:
        await manager.initialize()
        async with manager.get_connection() as connection:
            async with connection.execute('PRAGMA table_info(messages)') as cursor:
                columns = {row[1] for row in await cursor.fetchall()}
        assert 'dest_message_id' in columns
    finally:
        await manager.close_all()


def test_initialize_migrates_legacy_messages_table(tmp_path):
    asyncio.run(_migrate_legacy_messages_table(tmp_path / 'legacy.db'))
