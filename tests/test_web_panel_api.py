"""Web 面板 HTTP 集成测试：真实 aiohttp 应用 + 临时 SQLite（用假 Bot 顶替 Telegram）。"""

import asyncio

import aiohttp
from aiohttp.test_utils import TestClient, TestServer

from config import config
from database import models as db
from database.db_manager import DatabaseManager
from web_panel import auth
from web_panel.server import create_app

PANEL_HEADERS = {'X-Requested-With': 'PMBotPanel'}


class StubBot:
    """只实现 Web 面板用到的 Bot 接口，任何真实外呼都视为测试失败。"""

    id = 424242
    username = 'stub_bot'
    token = '123456:STUB'
    base_url = 'https://api.telegram.org/bot'

    @property
    def base_file_url(self):
        return f'{self.base_url}{self.token}/'

    async def send_message(self, **kwargs):
        raise AssertionError('测试环境不应真的发送 Telegram 消息')

    async def get_file(self, file_id):
        raise AssertionError('测试环境不应真的请求 Telegram 文件')


async def _flow(tmp_path):
    db_manager = DatabaseManager(str(tmp_path / 'panel_test.db'))
    await db_manager.initialize()
    assert await db.get_conversation_history(999999) == []

    original_password = config.WEB_PANEL_PASSWORD
    original_sha = config.WEB_PANEL_PASSWORD_SHA256
    original_trust_proxy = config.WEB_PANEL_TRUST_PROXY
    original_bot_id = config.BOT_ID
    original_bot_username = config.BOT_USERNAME
    config.WEB_PANEL_PASSWORD = 'test-pass'
    config.WEB_PANEL_PASSWORD_SHA256 = ''
    config.WEB_PANEL_TRUST_PROXY = False
    # 模拟 post_init 里的注入
    config.BOT_ID = StubBot.id
    config.BOT_USERNAME = StubBot.username
    auth._sessions.clear()
    auth._login_failures.clear()

    http_session = aiohttp.ClientSession()
    app = create_app(StubBot(), http_session)
    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        # 未登录访问受保护接口
        response = await client.get('/api/overview')
        assert response.status == 401

        # 首页静态 SPA 可访问
        response = await client.get('/')
        assert response.status == 200
        assert '片刻月小女仆' in await response.text()

        # 错误密码
        response = await client.post('/api/login', json={'password': 'nope'})
        assert response.status == 401

        # 正确密码
        response = await client.post('/api/login', json={'password': 'test-pass'})
        assert response.status == 200
        assert (await response.json())['ok'] is True

        # 会话生效
        response = await client.get('/api/overview')
        assert response.status == 200
        data = (await response.json())['data']
        assert data['stats']['total_users'] == 0
        assert data['bot']['username'] == 'stub_bot'

        # CSRF：写操作缺少自定义头
        response = await client.put('/api/config/MAX_MESSAGES_PER_MINUTE', json={'value': '12'})
        assert response.status == 403

        # 带头写入成功且热生效
        response = await client.put(
            '/api/config/MAX_MESSAGES_PER_MINUTE', json={'value': '12'}, headers=PANEL_HEADERS,
        )
        assert response.status == 200
        item = (await response.json())['data']
        assert item['value'] == '12'
        assert item['source'] == 'override'
        assert config.MAX_MESSAGES_PER_MINUTE == 12

        # 敏感项只返回掩码，不回显明文
        response = await client.get('/api/config')
        items = {entry['key']: entry for entry in (await response.json())['data']['items']}
        assert items['BOT_TOKEN']['value_masked'] is True
        assert 'TEST-TOKEN' not in items['BOT_TOKEN']['value']
        assert items['WEB_PANEL_PASSWORD']['value_masked'] is True

        # 还原覆盖
        response = await client.delete('/api/config/MAX_MESSAGES_PER_MINUTE', headers=PANEL_HEADERS)
        assert response.status == 200
        assert config.MAX_MESSAGES_PER_MINUTE == 30

        # 未知配置键
        response = await client.put('/api/config/NOT_A_KEY', json={'value': '1'}, headers=PANEL_HEADERS)
        assert response.status == 404

        # 非法取值
        response = await client.put('/api/config/AI_CONFIDENCE_THRESHOLD', json={'value': '999'}, headers=PANEL_HEADERS)
        assert response.status == 400

        # 只读接口全量探活
        for path in (
            '/api/conversations', '/api/users', '/api/blacklist', '/api/exemptions',
            '/api/filtered', '/api/knowledge', '/api/spam-keywords', '/api/ai-settings',
            '/api/user-groups', '/api/broadcasts', '/api/rss', '/api/tg-monitors',
            '/api/tg-discovered', '/api/web-monitors', '/api/runtime-status', '/api/network/servers',
        ):
            response = await client.get(path)
            assert response.status == 200, f'{path} -> {response.status}'

        # 广播：此时库里没有任何用户，应当拒绝
        response = await client.post('/api/broadcast', json={'text': 'hello'}, headers=PANEL_HEADERS)
        assert response.status == 400

        # 黑名单增删（含"从未私聊过的用户 ID"手动拉黑）
        response = await client.post(
            '/api/blacklist', json={'user_id': 777, 'reason': '测试', 'permanent': False}, headers=PANEL_HEADERS,
        )
        assert response.status == 200
        response = await client.get('/api/blacklist')
        rows = (await response.json())['data']['items']
        assert any(row['user_id'] == 777 for row in rows)
        response = await client.delete('/api/blacklist/777', headers=PANEL_HEADERS)
        assert response.status == 200

        # 知识库 CRUD
        response = await client.post('/api/knowledge', json={'title': '价格', 'content': '10 元'}, headers=PANEL_HEADERS)
        assert response.status == 200
        response = await client.get('/api/knowledge')
        entries = (await response.json())['data']['items']
        assert entries and entries[0]['title'] == '价格'
        response = await client.put(
            f"/api/knowledge/{entries[0]['id']}", json={'title': '价格表', 'content': '20 元'}, headers=PANEL_HEADERS,
        )
        assert response.status == 200
        response = await client.delete(f"/api/knowledge/{entries[0]['id']}", headers=PANEL_HEADERS)
        assert response.status == 200

        # 关键词
        response = await client.post('/api/spam-keywords', json={'keyword': '加微信'}, headers=PANEL_HEADERS)
        assert response.status == 200
        response = await client.post('/api/spam-keywords', json={'keyword': '加微信'}, headers=PANEL_HEADERS)
        assert response.status == 409
        response = await client.get('/api/spam-keywords')
        assert '加微信' in (await response.json())['data']['keywords']
        response = await client.delete('/api/spam-keywords/%E5%8A%A0%E5%BE%AE%E4%BF%A1', headers=PANEL_HEADERS)
        assert response.status == 200

        # 会话详情：不存在的用户
        response = await client.get('/api/conversations/999999')
        assert response.status == 404

        # 回复不存在的用户
        response = await client.post('/api/conversations/999999/reply', json={'text': 'hi'}, headers=PANEL_HEADERS)
        assert response.status == 404

        # 登出后恢复 401
        response = await client.post('/api/logout', headers=PANEL_HEADERS)
        assert response.status == 200
        response = await client.get('/api/overview')
        assert response.status == 401
    finally:
        await client.close()
        await http_session.close()
        config.WEB_PANEL_PASSWORD = original_password
        config.WEB_PANEL_PASSWORD_SHA256 = original_sha
        config.WEB_PANEL_TRUST_PROXY = original_trust_proxy
        config.BOT_ID = original_bot_id
        config.BOT_USERNAME = original_bot_username
        auth._sessions.clear()
        auth._login_failures.clear()
        await db_manager.close_all()


def test_web_panel_http_flow(tmp_path):
    asyncio.run(_flow(tmp_path))


async def _setup_flow(tmp_path):
    db_manager = DatabaseManager(str(tmp_path / 'setup_test.db'))
    await db_manager.initialize()

    original_password = config.WEB_PANEL_PASSWORD
    original_sha = config.WEB_PANEL_PASSWORD_SHA256
    config.WEB_PANEL_PASSWORD = ''
    config.WEB_PANEL_PASSWORD_SHA256 = ''
    auth.begin_setup_window()
    auth._sessions.clear()
    auth._login_failures.clear()

    http_session = aiohttp.ClientSession()
    app = create_app(StubBot(), http_session)
    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        # 身份接口显示初始化模式（未认证但可设置密码）
        response = await client.get('/api/me')
        assert response.status == 200
        me = (await response.json())['data']
        assert me['setup_mode'] is True
        assert me['authenticated'] is False
        assert me['setup_remaining'] > 0

        # 初始化窗口内，其它 API 一律 403（不是 401）
        response = await client.get('/api/overview')
        assert response.status == 403
        assert (await response.json())['code'] == 'setup_required'

        # 密码太短
        response = await client.post('/api/setup', json={'password': 'short'})
        assert response.status == 403
        response = await client.post('/api/setup', json={'password': 'short'}, headers=PANEL_HEADERS)
        assert response.status == 400

        # 正常初始化：返回会话 Cookie，密码以摘要落库
        response = await client.post('/api/setup', json={'password': 'good-pass-123'}, headers=PANEL_HEADERS)
        assert response.status == 200, await response.text()
        digest = config.WEB_PANEL_PASSWORD_SHA256
        assert digest and len(digest) == 64
        assert await db.get_setting('WEB_PANEL_PASSWORD_SHA256') == digest

        # 初始化请求的会话已自动登录
        response = await client.get('/api/overview')
        assert response.status == 200

        # 初始化完成后再次调用被拒
        response = await client.post('/api/setup', json={'password': 'another-pass'}, headers=PANEL_HEADERS)
        assert response.status == 403

        # 新会话用该密码可以正常登录
        other = TestClient(TestServer(app))
        await other.start_server()
        try:
            response = await other.post('/api/login', json={'password': 'good-pass-123'})
            assert response.status == 200
            response = await other.get('/api/overview')
            assert response.status == 200
        finally:
            await other.close()
    finally:
        await client.close()
        await http_session.close()
        config.clear_override('WEB_PANEL_PASSWORD_SHA256')
        config.WEB_PANEL_PASSWORD = original_password
        config.WEB_PANEL_PASSWORD_SHA256 = original_sha
        auth._sessions.clear()
        auth._login_failures.clear()
        await db_manager.close_all()


def test_web_panel_setup_flow(tmp_path):
    asyncio.run(_setup_flow(tmp_path))


async def _locked_flow(tmp_path):
    db_manager = DatabaseManager(str(tmp_path / 'locked_test.db'))
    await db_manager.initialize()

    original_password = config.WEB_PANEL_PASSWORD
    original_sha = config.WEB_PANEL_PASSWORD_SHA256
    original_deadline = auth._setup_deadline
    config.WEB_PANEL_PASSWORD = ''
    config.WEB_PANEL_PASSWORD_SHA256 = ''
    auth._setup_deadline = 0  # 窗口已关闭
    auth._sessions.clear()

    http_session = aiohttp.ClientSession()
    app = create_app(StubBot(), http_session)
    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        response = await client.get('/api/me')
        assert response.status == 200
        me = (await response.json())['data']
        assert me['locked'] is True
        assert me['setup_mode'] is False

        response = await client.get('/api/overview')
        assert response.status == 403
        assert (await response.json())['code'] == 'panel_locked'

        response = await client.post('/api/setup', json={'password': 'good-pass-123'})
        assert response.status == 403

        response = await client.post('/api/login', json={'password': 'x'})
        assert response.status == 403

        # 首页（静态资源）仍然可访问，前端据此渲染锁定提示页
        response = await client.get('/')
        assert response.status == 200
    finally:
        await client.close()
        await http_session.close()
        auth._setup_deadline = original_deadline
        config.WEB_PANEL_PASSWORD = original_password
        config.WEB_PANEL_PASSWORD_SHA256 = original_sha
        auth._sessions.clear()
        await db_manager.close_all()


def test_web_panel_locked_flow(tmp_path):
    asyncio.run(_locked_flow(tmp_path))
