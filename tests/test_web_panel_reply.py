"""回归：Web 面板回复必须返回 200 并落进会话历史。

回复会原样镜像到论坛话题（不加来源前缀），也不再给消息打表情回执，
避免干扰双方的正常交流。
"""

import asyncio
from types import SimpleNamespace

import aiohttp
from aiohttp.test_utils import TestClient, TestServer

from config import config
from database import models as db
from database.db_manager import DatabaseManager
from web_panel import auth
from web_panel.server import create_app

PANEL_HEADERS = {'X-Requested-With': 'PMBotPanel'}


class RecordingBot:
    """记录所有发送调用；表情回执已移除，被调用即视为回归。"""

    id = 424242
    username = 'stub_bot'
    token = '123456:STUB'
    base_url = 'https://api.telegram.org/bot'

    def __init__(self):
        self.sent = []
        self.messages = []
        self.reactions = []
        self._next_id = 900

    async def send_message(self, **kwargs):
        self._next_id += 1
        self.sent.append(kwargs)
        message = SimpleNamespace(
            message_id=self._next_id,
            message_thread_id=kwargs.get('message_thread_id'),
            chat_id=kwargs.get('chat_id'),
            text=kwargs.get('text'),
        )
        self.messages.append(message)
        return message

    async def set_message_reaction(self, **kwargs):
        self.reactions.append(kwargs)
        return True

    async def get_file(self, file_id):
        raise AssertionError('测试环境不应请求 Telegram 文件')


async def _reply_flow(tmp_path):
    db_manager = DatabaseManager(str(tmp_path / 'reply_test.db'))
    await db_manager.initialize()
    await db.add_user(555, 'alice', 'Alice', None)
    await db.update_user_verification(555, True)
    await db.update_user_thread_id(555, 42)

    original_password = config.WEB_PANEL_PASSWORD
    original_sha = config.WEB_PANEL_PASSWORD_SHA256
    config.WEB_PANEL_PASSWORD = 'test-pass'
    config.WEB_PANEL_PASSWORD_SHA256 = ''
    auth._sessions.clear()
    auth._login_failures.clear()

    bot = RecordingBot()
    http_session = aiohttp.ClientSession()
    app = create_app(bot, http_session)
    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        response = await client.post('/api/login', json={'password': 'test-pass'})
        assert response.status == 200

        response = await client.post(
            '/api/conversations/555/reply', json={'text': '你好'}, headers=PANEL_HEADERS,
        )
        body = await response.json()
        assert response.status == 200, body
        assert body['ok'] is True
        assert body['data']['message_id'] == bot.messages[0].message_id
        assert body['data']['mirrored'] is True

        # 私聊 + 论坛话题各发一条
        assert [call['chat_id'] for call in bot.sent] == [555, config.FORUM_GROUP_ID]
        assert bot.sent[1]['message_thread_id'] == 42

        # 镜像原样转发，不加「Web 面板回复」前缀；也不再打表情回执
        assert bot.sent[1]['text'] == '你好'
        assert bot.reactions == [], '不应再给消息打表情回执'


        # 回复必须落进会话历史，否则面板看不到自己发出的消息
        history = await db.get_conversation_history(555)
        assert len(history) == 1, history
        assert history[0]['content'] == '你好'
        assert history[0]['direction'] == 'admin_to_user'
        assert history[0]['message_id'] == bot.messages[0].message_id
        assert history[0]['dest_message_id'] == bot.messages[1].message_id

        # 会话详情接口也应能正常返回这条记录
        response = await client.get('/api/conversations/555')
        assert response.status == 200
        detail = (await response.json())['data']
        assert [item['content'] for item in detail['messages']['items']] == ['你好']

        # 即使镜像失败（或用户尚无 thread_id 且无法建话题），回复也应写入 message_mappings，消除待回复状态
        await db.add_user(556, 'bob', 'Bob', None)
        await db.update_user_verification(556, True)
        await db.save_message_mapping(
            user_id=556,
            source_chat_id=556,
            source_message_id=10,
            dest_chat_id=config.FORUM_GROUP_ID,
            dest_message_id=20,
            direction='user_to_admin',
            thread_id=None,
        )
        assert await db.count_pending_reply_topics() == 1

        response = await client.post(
            '/api/conversations/556/reply', json={'text': '收到啦'}, headers=PANEL_HEADERS,
        )
        assert response.status == 200
        assert await db.count_pending_reply_topics() == 0
    finally:
        await client.close()
        await http_session.close()
        config.WEB_PANEL_PASSWORD = original_password
        config.WEB_PANEL_PASSWORD_SHA256 = original_sha
        auth._sessions.clear()
        auth._login_failures.clear()
        await db_manager.close_all()


def test_reply_succeeds_and_is_recorded(tmp_path):
    asyncio.run(_reply_flow(tmp_path))
