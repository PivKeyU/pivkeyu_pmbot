"""用户工具：拦截放行、转发来源、备注标签、只读记录、一键收回、过期清理、批量分组。"""

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import aiohttp
from aiohttp.test_utils import TestClient, TestServer
from telegram import MessageOriginChannel, MessageOriginHiddenUser, MessageOriginUser, Chat, User
from telegram.error import BadRequest

from config import config
from database import models as db
from database.db_manager import DatabaseManager
from services import moderation, verification
from utils.forward import forward_label
from web_panel import auth
from web_panel.server import create_app

HEADERS = {'X-Requested-With': 'PMBotPanel'}
FORUM_ID = -1001


class Bot:
    id = 42
    username = 'stub_bot'
    token = '1:STUB'
    base_url = 'https://api.telegram.org/bot'

    def __init__(self):
        self.sent = []
        self.fail_once = False
        self._next_id = 100

    def _record(self, kind, kwargs):
        if self.fail_once:
            self.fail_once = False
            raise BadRequest('Message thread not found')
        self._next_id += 1
        self.sent.append((kind, kwargs))
        return SimpleNamespace(message_id=self._next_id)

    async def send_message(self, **kwargs):
        return self._record('text', kwargs)

    async def send_photo(self, **kwargs):
        return self._record('photo', kwargs)

    async def send_sticker(self, **kwargs):
        return self._record('sticker', kwargs)

    async def create_forum_topic(self, **kwargs):
        return SimpleNamespace(message_thread_id=88)


def _run(tmp_path, monkeypatch, scenario):
    monkeypatch.setattr(config, 'WEB_PANEL_PASSWORD', 'test-pass')
    monkeypatch.setattr(config, 'WEB_PANEL_PASSWORD_SHA256', '')
    monkeypatch.setattr(config, 'FORUM_GROUP_ID', FORUM_ID)
    monkeypatch.setattr(config, 'ADMIN_IDS', [9])
    auth._sessions.clear()
    auth._login_failures.clear()

    async def wrapper():
        manager = DatabaseManager(str(tmp_path / 'tools.db'))
        await manager.initialize()
        bot = Bot()
        http_session = aiohttp.ClientSession()
        client = TestClient(TestServer(create_app(bot, http_session)))
        await client.start_server()
        try:
            assert (await client.post('/api/login', json={'password': 'test-pass'})).status == 200
            await db.add_user(1, 'alice', 'Alice', None)
            await db.add_user(2, 'bob', 'Bob', None)
            await db.add_user(9, 'owner', 'Owner', None)
            await scenario(client, bot)
        finally:
            await client.close()
            await http_session.close()
            await manager.close_all()

    asyncio.run(wrapper())


async def _data(response):
    body = await response.json()
    assert response.status == 200, body
    return body['data']


def test_forward_label():
    assert forward_label(SimpleNamespace(forward_origin=None)) is None
    assert forward_label(SimpleNamespace()) is None
    now = datetime.now(timezone.utc)
    channel = Chat(-100, 'channel', title='新闻台', username='news')
    assert forward_label(SimpleNamespace(forward_origin=MessageOriginChannel(now, channel, 1))) == '频道 新闻台 (@news)'
    user = User(5, '小明', False)
    assert forward_label(SimpleNamespace(forward_origin=MessageOriginUser(now, user))) == '用户 小明'
    hidden = MessageOriginHiddenUser(now, '神秘人')
    assert '神秘人' in forward_label(SimpleNamespace(forward_origin=hidden))


def test_release_filtered_message(tmp_path, monkeypatch):
    async def scenario(client, bot):
        await db.update_user_thread_id(1, 31)
        await db.save_filtered_message(1, 500, '其实是好人', 'AI: 广告', forward_source='频道 新闻台')
        filtered_id = (await db.get_filtered_messages(10, 0))[0]['id']

        await _data(await client.post(f'/api/filtered/{filtered_id}/release', headers=HEADERS))
        kind, sent = bot.sent[-1]
        assert kind == 'text' and sent['chat_id'] == FORUM_ID and sent['message_thread_id'] == 31
        assert '其实是好人' in sent['text'] and '新闻台' in sent['text']
        assert await db.get_filtered_messages_count() == 0
        # 补发后回复能回到用户，消息也进入会话记录
        history = await db.get_conversation_history(1)
        assert history[-1]['content'] == '其实是好人'
        assert history[-1]['forward_source'] == '频道 新闻台'
        assert await db.get_message_mappings_by_source(1, 500)

        response = await client.post(f'/api/filtered/{filtered_id}/release', headers=HEADERS)
        assert response.status == 404

        # 图片保持原样；话题失效时重建一次
        await db.save_filtered_message(1, 501, '看图', 'AI: 广告', 'photo', 'file-1')
        filtered_id = (await db.get_filtered_messages(10, 0))[0]['id']
        bot.fail_once = True
        await _data(await client.post(f'/api/filtered/{filtered_id}/release', headers=HEADERS))
        kind, sent = bot.sent[-1]
        assert kind == 'photo' and sent['photo'] == 'file-1' and sent['message_thread_id'] == 88

    _run(tmp_path, monkeypatch, scenario)


def test_profile_search_and_tag_broadcast(tmp_path, monkeypatch):
    async def scenario(client, bot):
        await _data(await client.put('/api/users/1/profile', json={'note': '常问发货', 'tags': '老客户, 投诉过'}, headers=HEADERS))
        items = (await _data(await client.get('/api/users?q=发货')))['items']
        assert [item['user_id'] for item in items] == [1]
        assert items[0]['tags'] == ['老客户', '投诉过']
        assert (await _data(await client.get('/api/users?q=投诉')))['total'] == 1
        assert (await _data(await client.get('/api/user-tags')))['items'] == ['投诉过', '老客户']

        _, recipients = await db.get_broadcast_recipients(tag='老客户')
        assert [user['user_id'] for user in recipients] == [1]

        response = await client.put('/api/users/1/profile', json={'note': 'x' * 501}, headers=HEADERS)
        assert response.status == 400
        response = await client.put('/api/users/777/profile', json={'note': 'x'}, headers=HEADERS)
        assert response.status == 404

    _run(tmp_path, monkeypatch, scenario)


def test_timeline_is_read_only_context(tmp_path, monkeypatch):
    async def scenario(client, bot):
        for index in range(30):
            await db.save_message(user_id=1, message_id=index, content=f'第{index}条', direction='user_to_admin')
        # 时间戳只精确到秒，两张表的记录要错开才有确定的先后
        await asyncio.sleep(1.1)
        await db.save_filtered_message(1, 99, '被拦下的', 'AI: 广告')
        await db.add_user_to_group('常客', 1, 0)

        data = await _data(await client.get('/api/users/1/timeline'))
        assert data['groups'] == ['常客']
        assert len(data['items']) == 18
        assert any(item['kind'] == 'filtered' for item in data['items'])

        data = await _data(await client.get('/api/users/1/timeline?q=第10条'))
        contents = [item['content'] for item in data['items']]
        assert '第10条' in contents and len(contents) == 13
        assert 'media_file_id' not in data['items'][0]

    _run(tmp_path, monkeypatch, scenario)


def test_forward_source_is_searchable(tmp_path, monkeypatch):
    async def scenario(client, bot):
        await db.save_message(
            user_id=1, message_id=1, content='', direction='user_to_admin', media_type='photo',
            forward_source='频道 新闻台 (@news)',
        )
        items = (await _data(await client.get('/api/users?q=新闻台')))['items']
        assert items[0]['user_id'] == 1 and items[0]['matched_forward'] == '频道 新闻台 (@news)'

    _run(tmp_path, monkeypatch, scenario)


def test_revoke_all(tmp_path, monkeypatch):
    async def scenario(client, bot):
        for user_id in (1, 2, 9):
            await db.update_user_verification(user_id, True)
        await db.add_exemption(1, True, 0)
        await db.add_exemption(2, False, 0, None, (datetime.now(timezone.utc) + timedelta(days=1)).isoformat())
        verification.pending_verifications[2] = {'expires_at': 9e12}

        summary = await _data(await client.get('/api/moderation/summary'))
        assert summary['verified_users'] == 3 and summary['exemptions'] == 2

        result = await _data(await client.post('/api/moderation/revoke-all', json={'target': 'verification'}, headers=HEADERS))
        assert result['count'] == 2
        assert not (await db.get_user(1))['is_verified']
        assert (await db.get_user(9))['is_verified']  # 管理员不受影响
        assert not verification.pending_verifications

        result = await _data(await client.post('/api/moderation/revoke-all', json={'target': 'exemption'}, headers=HEADERS))
        assert result['count'] == 2
        assert not await db.is_exempted(1)

        response = await client.post('/api/moderation/revoke-all', json={'target': 'all'}, headers=HEADERS)
        assert response.status == 400

    _run(tmp_path, monkeypatch, scenario)


def test_prune_only_removes_expired_passes(tmp_path, monkeypatch):
    async def scenario(client, bot):
        past = datetime.now(timezone.utc) - timedelta(hours=1)
        future = datetime.now(timezone.utc) + timedelta(hours=1)
        await db.add_exemption(1, False, 0, None, past.isoformat())
        await db.add_exemption(2, False, 0, None, future.strftime('%Y-%m-%d %H:%M:%S'))
        await db.add_exemption(9, True, 0)
        assert await db.prune_expired_exemptions() == 1
        assert await db.get_exemption(1) is None
        assert await db.get_exemptions_count() == 2

    _run(tmp_path, monkeypatch, scenario)


def test_bulk_add_to_groups(tmp_path, monkeypatch):
    async def scenario(client, bot):
        await db.create_user_group('甲组', 0)
        await db.create_user_group('乙组', 0)
        await db.add_user_to_group('甲组', 1, 0)

        result = await _data(await client.post(
            '/api/user-groups/bulk-add', json={'user_ids': [1, 2, 2], 'groups': ['甲组', '乙组']}, headers=HEADERS,
        ))
        assert result == {'added': 3, 'skipped': 1}
        assert (await db.get_broadcast_recipients('乙组'))[1][0]['user_id'] == 1

        for payload in ({'user_ids': [], 'groups': ['甲组']}, {'user_ids': [1], 'groups': []}, {'user_ids': ['x'], 'groups': ['甲组']}):
            response = await client.post('/api/user-groups/bulk-add', json=payload, headers=HEADERS)
            assert response.status == 400
        response = await client.post('/api/user-groups/bulk-add', json={'user_ids': [1], 'groups': ['不存在']}, headers=HEADERS)
        assert response.status == 404
        assert await db.get_user_group_by_name('不存在') is None

    _run(tmp_path, monkeypatch, scenario)


def test_overview_todo_counts(tmp_path, monkeypatch):
    async def scenario(client, bot):
        await db.create_review_appeal(1, 'AI: 广告')
        await db.record_runtime_status('web:坏掉的', 'web', False, error='boom')
        stats = (await _data(await client.get('/api/overview')))['stats']
        assert stats['pending_appeals'] == 1 and stats['failing_monitors'] == 1

    _run(tmp_path, monkeypatch, scenario)


def test_cancelling_a_review_pause_takes_effect(tmp_path, monkeypatch):
    async def scenario(client, bot):
        await moderation.grant_review_pause(bot, 1, 60, 0, 'Web 面板')
        assert await db.is_exempted(1)
        assert (await client.delete('/api/exemptions/1', headers=HEADERS)).status == 200
        assert not await db.is_exempted(1)

    _run(tmp_path, monkeypatch, scenario)
