"""回归：Web 面板回复必须返回 200 并落进会话历史，即使表情回执这一步出问题。

历史 bug：``POST /api/conversations/{id}/reply`` 在消息已发给用户和论坛话题之后
调用 ``set_message_reaction(reaction=[{"type": "emoji", ...}])``，PTB 在参数解析
阶段抛 ``TypeError``，于是面板收到 500「服务器内部错误」，回复也没有写进
``messages`` 表，会话中心里看不到自己刚发出去的消息。
"""

import asyncio
from types import SimpleNamespace

import aiohttp
from aiohttp.test_utils import TestClient, TestServer
from telegram import ReactionType, ReactionTypeEmoji

from config import config
from database import models as db
from database.db_manager import DatabaseManager
from web_panel import auth
from web_panel.server import create_app

PANEL_HEADERS = {'X-Requested-With': 'PMBotPanel'}


class RecordingBot:
    """按 PTB 的真实约束校验 reaction 载荷，并记录所有调用。"""

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
        # 复刻 PTB 的解析：dict 不可哈希，会在发请求之前就抛 TypeError
        for entry in kwargs.get('reaction') or []:
            if not isinstance(entry, ReactionType):
                raise TypeError("unhashable type: 'dict'")
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

        # 表情回执用 ReactionTypeEmoji，落在镜像消息上
        assert len(bot.reactions) == 1, '表情回执没有被调用（载荷可能不合法）'
        assert isinstance(bot.reactions[0]['reaction'][0], ReactionTypeEmoji)
        assert bot.reactions[0]['message_id'] == bot.messages[1].message_id

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
