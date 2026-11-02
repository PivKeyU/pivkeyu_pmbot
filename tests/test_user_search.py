"""用户页搜索：按资料或消息内容找到消息来源，通行证状态。"""

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from database import models as db
from database.db_manager import DatabaseManager


@pytest.fixture
def search_db(tmp_path):
    def run(scenario):
        async def wrapper():
            manager = DatabaseManager(str(tmp_path / 'search.db'))
            await manager.initialize()
            try:
                await db.add_user(1001, 'alice', 'Alice', 'Liddell')
                await db.add_user(1002, 'bob_99', '小明', None)
                await db.add_user(1003, None, 'Carol', None)
                await db.update_user_verification(1001, True)
                await scenario()
            finally:
                await manager.close_all()

        asyncio.run(wrapper())

    return run


async def _search(query):
    items = await db.get_all_users_paginated(limit=20, offset=0, query=query)
    assert await db.get_total_users_count(query=query) == len(items)
    return items


def test_finds_user_by_profile(search_db):
    async def scenario():
        assert [item['user_id'] for item in await _search('1002')] == [1002]
        assert [item['user_id'] for item in await _search('@bob')] == [1002]
        assert [item['user_id'] for item in await _search('ALICE')] == [1001]
        assert [item['user_id'] for item in await _search('Alice Lid')] == [1001]
        assert [item['user_id'] for item in await _search('小明')] == [1002]
        assert await _search('不存在的人') == []

        item = (await _search('1002'))[0]
        assert item['matched_content'] is None and item['match_count'] is None

    search_db(scenario)


def test_finds_message_source(search_db):
    async def scenario():
        await db.save_message(user_id=1001, message_id=1, content='早上好', direction='user_to_admin')
        await db.save_message(user_id=1001, message_id=2, content='我的订单号 A-778', direction='user_to_admin')
        await db.save_filtered_message(1002, 3, '加我领订单返利', 'AI: 广告')

        # 管理员回复和广播里的内容不算消息来源
        await db.save_message(user_id=1003, message_id=4, content='订单已发货', direction='admin_to_user')
        await db.save_message(user_id=1003, message_id=5, content='全体订单通知', direction='broadcast_to_user')

        items = {item['user_id']: item for item in await _search('订单')}
        assert set(items) == {1001, 1002}
        assert items[1001]['matched_source'] == 'message'
        assert items[1001]['matched_content'] == '我的订单号 A-778'
        assert items[1001]['match_count'] == 1
        assert items[1002]['matched_source'] == 'filtered'
        assert items[1002]['matched_reason'] == 'AI: 广告'

        # 没有消息记录也没有验证的用户不能打开会话
        assert items[1001]['has_conversation'] == 1
        assert items[1002]['has_conversation'] == 0

    search_db(scenario)


def test_like_wildcards_are_literal(search_db):
    async def scenario():
        await db.save_message(user_id=1001, message_id=1, content='折扣 100% 真实', direction='user_to_admin')
        await db.save_message(user_id=1002, message_id=2, content='折扣 1000 元', direction='user_to_admin')

        assert [item['user_id'] for item in await _search('100%')] == [1001]
        assert [item['user_id'] for item in await _search('b_b')] == []
        assert [item['user_id'] for item in await _search('bob_')] == [1002]

    search_db(scenario)


def test_other_lists_search(search_db):
    async def scenario():
        await db.add_to_blacklist(1002, '发广告', 0)
        await db.add_exemption(1001, True, 0, '老朋友')
        await db.save_filtered_message(1003, 1, '兼职日结', 'AI: 广告')
        await db.create_review_appeal(1003, 'AI: 广告')

        assert [row['user_id'] for row in await db.get_blacklist_paginated(10, 0, '广告')] == [1002]
        assert await db.get_blacklist_count('alice') == 0
        assert [row['user_id'] for row in await db.get_exemptions_paginated(10, 0, 'alice')] == [1001]
        assert await db.get_exemptions_count('1002') == 0
        assert [row['user_id'] for row in await db.get_filtered_messages(10, 0, '兼职')] == [1003]
        assert await db.get_filtered_messages_count('carol') == 1
        assert await db.get_filtered_messages_count('alice') == 0
        assert [row['user_id'] for row in await db.get_pending_review_appeals(10, 0, 'carol')] == [1003]
        assert await db.get_pending_review_appeals_count('alice') == 0

        # 不带关键词时列表不受影响
        assert await db.get_blacklist_count() == 1
        assert await db.get_exemptions_count() == 1
        assert await db.get_filtered_messages_count() == 1

    search_db(scenario)


@pytest.mark.parametrize('expires_at,active', [
    ((datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(), 1),
    ((datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(), 0),
    ((datetime.now(timezone.utc) + timedelta(hours=1)).strftime('%Y-%m-%d %H:%M:%S'), 1),
    ((datetime.now(timezone.utc) - timedelta(hours=1)).strftime('%Y-%m-%d %H:%M:%S'), 0),
])
def test_exemption_state_handles_both_time_formats(search_db, expires_at, active):
    async def scenario():
        await db.add_exemption(1001, False, 0, None, expires_at)
        assert (await db.get_exemptions_paginated(10, 0))[0]['is_active'] == active
        user = next(item for item in await db.get_all_users_paginated(10, 0) if item['user_id'] == 1001)
        assert user['exempted'] == active
        assert await db.is_exempted(1001) is bool(active)

    search_db(scenario)
