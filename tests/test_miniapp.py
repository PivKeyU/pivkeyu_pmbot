"""Telegram Mini App：initData 校验、接口白名单、Turnstile 验证流程与网页监控内网拦截。"""

import asyncio
import hashlib
import hmac
import json
import re
import time
from collections import defaultdict
from types import SimpleNamespace
from urllib.parse import urlencode

import aiohttp
import pytest
from aiohttp.test_utils import TestClient, TestServer
from telegram import MenuButtonDefault, MenuButtonWebApp
from telegram.error import BadRequest

from config import config
from database import models as db
from database.db_manager import DatabaseManager
from services import telegram_commands, verification, web_monitor
from web_panel import api_admin, api_config, api_overview, keys, miniapp
from web_panel.server import MINIAPP_API_PREFIX, MINIAPP_PREFIX, create_app as create_panel_app

BOT_TOKEN = '123456:STUB'
SESSION_PATH = f'{MINIAPP_API_PREFIX}session'
VERIFY_PATH = f'{MINIAPP_API_PREFIX}verify'
ADMIN_ID = 111
USER_ID = 222
PUBLIC_URL = 'https://bot.example.com/miniapp'


def _init_data(user_id, token=BOT_TOKEN, auth_date=None):
    fields = {
        'auth_date': str(int(time.time()) if auth_date is None else auth_date),
        'query_id': 'AAA',
        'user': json.dumps({'id': user_id, 'first_name': '客人'}, ensure_ascii=False),
    }
    data_check = '\n'.join(f'{key}={fields[key]}' for key in sorted(fields))
    secret = hmac.new(b'WebAppData', token.encode(), hashlib.sha256).digest()
    fields['hash'] = hmac.new(secret, data_check.encode(), hashlib.sha256).hexdigest()
    return urlencode(fields)


class StubApplication:
    """只记录后台任务，不启动真实的 Telegram Application。"""

    def __init__(self):
        self.bot = SimpleNamespace(token=BOT_TOKEN)
        self.user_data = defaultdict(dict)
        self.tasks = []

    def create_task(self, coroutine, **kwargs):
        coroutine.close()
        self.tasks.append(coroutine)


@pytest.fixture
def patched_config(monkeypatch):
    monkeypatch.setattr(config, 'ADMIN_IDS', [ADMIN_ID])
    monkeypatch.setattr(config, 'WEB_PANEL_PASSWORD', 'test-pass')
    monkeypatch.setattr(config, 'VERIFICATION_MODE', 'turnstile')
    monkeypatch.setattr(config, 'VERIFICATION_TIMEOUT', 300)
    monkeypatch.setattr(config, 'MAX_VERIFICATION_ATTEMPTS', 3)
    monkeypatch.setattr(config, 'MINIAPP_PUBLIC_URL', PUBLIC_URL)
    monkeypatch.setattr(config, 'TURNSTILE_SITE_KEY', 'site-key')
    monkeypatch.setattr(config, 'TURNSTILE_SECRET_KEY', 'secret-key')
    verification.pending_verifications.clear()
    miniapp._sessions.clear()
    yield
    verification.pending_verifications.clear()
    miniapp._sessions.clear()


async def _with_client(tmp_path, scenario):
    db_manager = DatabaseManager(str(tmp_path / 'miniapp_test.db'))
    await db_manager.initialize()
    await db.add_user(user_id=USER_ID, username='guest', first_name='客人', last_name=None, language_code='zh')
    await db.update_user_verification(USER_ID, False)

    application = StubApplication()
    http_session = aiohttp.ClientSession()
    panel_app = create_panel_app(application.bot, http_session)
    miniapp.mount(panel_app, application, http_session)
    client = TestClient(TestServer(panel_app))
    await client.start_server()
    try:
        await scenario(client, application)
    finally:
        await client.close()
        await http_session.close()


def _registered_routes(app):
    registered = set()
    for route in app.router.routes():
        info = route.resource.get_info() if route.resource else {}
        path = info.get('formatter') or info.get('path')
        if path and path.startswith('/api/') and route.method != 'HEAD':
            registered.add((route.method, path))
    return registered


def test_init_data_accepts_valid_signature():
    user = miniapp.parse_init_data(_init_data(USER_ID), BOT_TOKEN)
    assert user['id'] == USER_ID


def test_init_data_rejects_forgery_and_replay():
    assert miniapp.parse_init_data(_init_data(USER_ID, token='999:OTHER'), BOT_TOKEN) is None
    assert miniapp.parse_init_data(_init_data(USER_ID).replace('hash=', 'hash=0'), BOT_TOKEN) is None
    assert miniapp.parse_init_data('', BOT_TOKEN) is None

    expired = int(time.time()) - miniapp.INIT_DATA_MAX_AGE_SECONDS - 60
    assert miniapp.parse_init_data(_init_data(USER_ID, auth_date=expired), BOT_TOKEN) is None


def test_allowed_routes_exist_and_exclude_sensitive_interfaces():
    # 白名单写错时路由会静默消失，这里要求每一项都对应真实接口
    tables = (api_overview.routes, api_config.routes, api_admin.routes)
    defined = {(route.method, route.path) for table in tables for route in table}
    assert miniapp.ALLOWED_ROUTES <= defined

    app = miniapp.create_app(StubApplication(), None)
    registered = _registered_routes(app)
    assert registered == miniapp.ALLOWED_ROUTES | {
        ('POST', '/api/session'),
        ('POST', '/api/verify'),
    }
    for _, path in registered:
        for blocked in ('conversations', 'media', 'login', 'setup', 'password'):
            assert blocked not in path


def test_sensitive_paths_are_absent(tmp_path, patched_config):
    async def scenario(client, application):
        response = await client.post(f'{MINIAPP_API_PREFIX}login', json={'password': 'whatever'})
        assert response.status == 404

        response = await client.post(SESSION_PATH, json={'init_data': _init_data(ADMIN_ID)})
        token = (await response.json())['data']['token']
        headers = {miniapp.TOKEN_HEADER: token}

        for path in ('api/conversations', 'api/me', 'ws'):
            response = await client.get(f'{MINIAPP_PREFIX}{path}', headers=headers)
            assert response.status == 404, path
        for path in ('api/login', 'api/setup', 'api/password'):
            response = await client.post(f'{MINIAPP_PREFIX}{path}', json={'password': 'whatever'}, headers=headers)
            assert response.status == 404, path

        # 会话令牌只在 Mini App 路径下有效，不能用来访问面板自己的接口
        response = await client.get('/api/conversations', headers=headers)
        assert response.status == 401

        # 配置中心开放后，面板密码仍然不能从这里改
        for key in ('WEB_PANEL_PASSWORD', 'WEB_PANEL_PASSWORD_SHA256'):
            response = await client.put(f'{MINIAPP_API_PREFIX}config/{key}', json={'value': 'hacked'}, headers=headers)
            assert response.status == 400, key
            assert getattr(config, key) != 'hacked'

    asyncio.run(_with_client(tmp_path, scenario))


def test_config_requires_admin_session_and_masks_secrets(tmp_path, patched_config, monkeypatch):
    monkeypatch.setattr(config, 'BOT_TOKEN', 'real-bot-token-value')

    async def scenario(client, application):
        response = await client.get(f'{MINIAPP_API_PREFIX}config')
        assert response.status == 401

        response = await client.post(SESSION_PATH, json={'init_data': _init_data(ADMIN_ID)})
        headers = {miniapp.TOKEN_HEADER: (await response.json())['data']['token']}
        response = await client.get(f'{MINIAPP_API_PREFIX}config', headers=headers)
        assert response.status == 200
        assert 'real-bot-token-value' not in await response.text()

    asyncio.run(_with_client(tmp_path, scenario))


def test_miniapp_url_appends_mount_path(patched_config, monkeypatch):
    monkeypatch.setattr(config, 'MINIAPP_PUBLIC_URL', 'https://panel.example.com')
    assert verification.miniapp_url() == 'https://panel.example.com/miniapp/'
    monkeypatch.setattr(config, 'MINIAPP_PUBLIC_URL', 'https://panel.example.com/miniapp')
    assert verification.miniapp_url() == 'https://panel.example.com/miniapp/'


def test_miniapp_url_tolerates_trailing_slash(patched_config, monkeypatch):
    for value in ('https://panel.example.com/miniapp/', 'https://panel.example.com/', ' https://panel.example.com/miniapp '):
        monkeypatch.setattr(config, 'MINIAPP_PUBLIC_URL', value)
        assert verification.miniapp_url() == 'https://panel.example.com/miniapp/'


def test_entry_without_trailing_slash_redirects(tmp_path, patched_config):
    async def scenario(client, application):
        response = await client.get('/miniapp?startapp=1', allow_redirects=False)
        assert response.status == 302
        assert response.headers['Location'] == '/miniapp/?startapp=1'

        response = await client.get('/miniapp')
        assert response.status == 200
        assert 'class="miniapp"' in await response.text()

    asyncio.run(_with_client(tmp_path, scenario))


def _menu_app(calls, failing=()):
    async def set_chat_menu_button(chat_id, menu_button):
        if chat_id in failing:
            raise BadRequest('chat not found')
        calls.append((chat_id, menu_button))

    return SimpleNamespace(bot=SimpleNamespace(set_chat_menu_button=set_chat_menu_button))


def test_admins_get_miniapp_menu_button(patched_config, monkeypatch):
    monkeypatch.setattr(config, 'ADMIN_IDS', [ADMIN_ID, 333])
    calls = []
    # 有管理员还没和机器人对话时，其余管理员不受影响
    asyncio.run(telegram_commands.register_miniapp_menu(_menu_app(calls, failing={ADMIN_ID})))

    assert [chat_id for chat_id, _ in calls] == [333]
    assert isinstance(calls[0][1], MenuButtonWebApp)
    assert calls[0][1].web_app.url == f'{PUBLIC_URL}/'


def test_menu_button_resets_when_url_removed(patched_config, monkeypatch):
    monkeypatch.setattr(config, 'MINIAPP_PUBLIC_URL', '')
    calls = []
    asyncio.run(telegram_commands.register_miniapp_menu(_menu_app(calls)))
    assert isinstance(calls[0][1], MenuButtonDefault)


def test_broadcast_lock_is_shared_with_panel():
    miniapp_app = miniapp.create_app(StubApplication(), None)
    panel_app = create_panel_app(SimpleNamespace(), None)
    assert miniapp_app[keys.BROADCAST_TASKS_KEY] is panel_app[keys.BROADCAST_TASKS_KEY]


def test_only_admin_gets_management_session(tmp_path, patched_config, monkeypatch):
    async def scenario(client, application):
        response = await client.get(f'{MINIAPP_API_PREFIX}overview')
        assert response.status == 401

        response = await client.post(SESSION_PATH, json={'init_data': _init_data(USER_ID)})
        data = (await response.json())['data']
        assert data['is_admin'] is False
        assert 'token' not in data

        response = await client.post(SESSION_PATH, json={'init_data': 'hash=forged'})
        assert response.status == 401

        response = await client.post(SESSION_PATH, json={'init_data': _init_data(ADMIN_ID)})
        token = (await response.json())['data']['token']
        headers = {miniapp.TOKEN_HEADER: token}

        response = await client.get(f'{MINIAPP_API_PREFIX}overview', headers=headers)
        assert response.status == 200

        # 管理员被移除后，已打开的页面立即失效
        monkeypatch.setattr(config, 'ADMIN_IDS', [])
        response = await client.get(f'{MINIAPP_API_PREFIX}overview', headers=headers)
        assert response.status == 403

    asyncio.run(_with_client(tmp_path, scenario))


def test_pages_can_be_embedded_by_telegram_web(tmp_path, patched_config):
    async def scenario(client, application):
        response = await client.get(MINIAPP_PREFIX)
        assert response.status == 200
        html = await response.text()
        assert 'class="miniapp"' in html
        assert 'X-Frame-Options' not in response.headers
        assert 'https://web.telegram.org' in response.headers['Content-Security-Policy']

        # 静态资源走版本化路径，且真实可取到
        asset = re.search(r'src="(/miniapp/static/v-[^"]+/js/miniapp\.js)"', html).group(1)
        response = await client.get(asset)
        assert response.status == 200
        assert 'immutable' in response.headers['Cache-Control']

        # 面板自身的防嵌入不受影响
        response = await client.get('/')
        assert response.headers['X-Frame-Options'] == 'DENY'
        assert "frame-ancestors 'none'" in response.headers['Content-Security-Policy']

    asyncio.run(_with_client(tmp_path, scenario))


def test_turnstile_pass_marks_user_verified(tmp_path, patched_config, monkeypatch):
    async def passed(http_session, token):
        return token == 'good'

    monkeypatch.setattr(miniapp, 'verify_turnstile_token', passed)

    async def scenario(client, application):
        await verification.create_verification(USER_ID)

        response = await client.post(SESSION_PATH, json={'init_data': _init_data(USER_ID)})
        data = (await response.json())['data']
        assert data['verification_pending'] is True
        assert data['turnstile_site_key'] == 'site-key'

        payload = {'init_data': _init_data(USER_ID), 'token': 'bad'}
        response = await client.post(VERIFY_PATH, json=payload)
        assert response.status == 403
        assert verification.turnstile_pending(USER_ID)
        assert verification.pending_verifications[USER_ID]['attempts'] == 0

        payload['token'] = 'good'
        response = await client.post(VERIFY_PATH, json=payload)
        assert response.status == 200
        assert (await db.get_user(USER_ID))['is_verified']
        assert not verification.turnstile_pending(USER_ID)
        assert len(application.tasks) == 1

        # 同一结果不能再用一次
        response = await client.post(VERIFY_PATH, json=payload)
        assert response.status == 410

    asyncio.run(_with_client(tmp_path, scenario))


def test_turnstile_outage_does_not_count_as_failure(tmp_path, patched_config, monkeypatch):
    async def unavailable(http_session, token):
        raise aiohttp.ClientError('boom')

    monkeypatch.setattr(miniapp, 'verify_turnstile_token', unavailable)

    async def scenario(client, application):
        await verification.create_verification(USER_ID)
        response = await client.post(
            VERIFY_PATH,
            json={'init_data': _init_data(USER_ID), 'token': 'good'},
        )
        assert response.status == 502
        assert verification.pending_verifications[USER_ID]['attempts'] == 0
        assert not (await db.get_user(USER_ID))['is_verified']
        assert await db.is_blacklisted(USER_ID) == (False, False)

    asyncio.run(_with_client(tmp_path, scenario))


def test_turnstile_requires_expected_hostname_and_action(patched_config):
    class Response:
        def __init__(self, payload):
            self.payload = payload

        async def json(self, content_type=None):
            return self.payload

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

    class Session:
        def __init__(self, payload):
            self.payload = payload

        def post(self, url, data, timeout):
            assert data['secret'] == 'secret-key'
            return Response(self.payload)

    good = {'success': True, 'hostname': 'bot.example.com', 'action': 'verify'}
    assert asyncio.run(miniapp.verify_turnstile_token(Session(good), 'x')) is True
    assert asyncio.run(miniapp.verify_turnstile_token(Session({**good, 'hostname': 'evil.example'}), 'x')) is False
    assert asyncio.run(miniapp.verify_turnstile_token(Session({**good, 'action': 'other'}), 'x')) is False
    assert asyncio.run(miniapp.verify_turnstile_token(Session({**good, 'success': False}), 'x')) is False


def test_finish_verification_updates_prompt_and_replays_pending_message():
    edits = []

    async def edit_message_text(**kwargs):
        edits.append(kwargs)

    application = StubApplication()
    application.bot = SimpleNamespace(token=BOT_TOKEN, edit_message_text=edit_message_text)
    # 没有 message 属性的假 Update：一旦走到补发逻辑就说明上下文取到了暂存消息
    application.user_data[USER_ID]['pending_update'] = SimpleNamespace()

    with pytest.raises(AttributeError):
        asyncio.run(miniapp._finish_verification(application, USER_ID, {'message_id': 5}))

    assert edits[0]['chat_id'] == USER_ID
    assert edits[0]['message_id'] == 5
    assert 'pending_update' not in application.user_data[USER_ID]


def test_turnstile_mode_falls_back_when_not_configured(patched_config, monkeypatch):
    monkeypatch.setattr(config, 'MINIAPP_PUBLIC_URL', 'http://insecure.example.com')
    prompt = asyncio.run(verification.create_verification(USER_ID))
    assert verification.pending_verifications[USER_ID]['mode'] in ('text', 'image')
    assert not any(button.web_app for row in prompt.keyboard.inline_keyboard for button in row)


def test_turnstile_prompt_offers_web_app_and_fallback(patched_config):
    prompt = asyncio.run(verification.create_verification(USER_ID))
    buttons = [button for row in prompt.keyboard.inline_keyboard for button in row]
    assert buttons[0].web_app.url == f'{PUBLIC_URL}/'
    assert buttons[1].callback_data == 'verify_fallback'


def test_fallback_keeps_attempts_and_stays_on_plain_questions(patched_config):
    async def scenario():
        await verification.create_verification(USER_ID)
        verification.pending_verifications[USER_ID]['attempts'] = 1

        prompt = await verification.fallback_verification(USER_ID)
        session = verification.pending_verifications[USER_ID]
        assert prompt is not None
        assert session['mode'] in ('text', 'image')
        assert session['attempts'] == 1

        wrong = next(option for option in session['options'] if option != session['answer'])
        _, _, _, new_prompt = await verification.verify_answer(USER_ID, wrong)
        assert new_prompt is not None
        assert verification.pending_verifications[USER_ID]['mode'] in ('text', 'image')

    asyncio.run(scenario())


def test_fallback_without_pending_session_returns_none(patched_config):
    assert asyncio.run(verification.fallback_verification(USER_ID)) is None


def test_stale_answer_button_is_not_counted_against_turnstile(patched_config):
    async def scenario():
        await verification.create_verification(USER_ID)
        success, _, banned, new_prompt = await verification.verify_answer(USER_ID, 'old-option')
        assert (success, banned, new_prompt) == (False, False, None)
        assert verification.pending_verifications[USER_ID]['attempts'] == 0

    asyncio.run(scenario())


def test_web_monitor_rejects_private_addresses():
    for url in ('http://127.0.0.1:18080/', 'http://192.168.1.1/', 'http://169.254.169.254/latest', 'http://[::1]/', 'file:///etc/passwd'):
        with pytest.raises(ValueError):
            asyncio.run(web_monitor._assert_public_url(url))


def test_web_monitor_accepts_public_address():
    asyncio.run(web_monitor._assert_public_url('http://1.1.1.1/'))
