"""机器人自重启：Bot Token 预检、拒绝条件、重启通知与面板入口。"""

import asyncio
import json
from types import SimpleNamespace

import aiohttp
import pytest
from aiohttp.test_utils import TestClient, TestServer
from telegram.error import InvalidToken, NetworkError

from config import config
from database import models as db
from database.db_manager import DatabaseManager
from services import broadcast, restart
from utils import copy as copy_text
from web_panel import auth, miniapp
from web_panel.server import MINIAPP_API_PREFIX, MINIAPP_PREFIX, create_app

ADMIN_ID = 111


class FakeBot:
    """顶替 telegram.Bot，记录验证用的 Token，并按需抛出错误。"""

    error = None
    tokens = []

    def __init__(self, token):
        FakeBot.tokens.append(token)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get_me(self):
        if FakeBot.error:
            raise FakeBot.error


class FakeApplication:
    def __init__(self):
        self.tasks = []
        self.stopped = 0
        self.sent = []
        self.bot = SimpleNamespace(token='123456:STUB', send_message=self._send_message)
        self.user_data = {}

    async def _send_message(self, **kwargs):
        self.sent.append(kwargs)

    def create_task(self, coroutine, **kwargs):
        task = asyncio.ensure_future(coroutine)
        self.tasks.append(task)
        return task

    def stop_running(self):
        self.stopped += 1


@pytest.fixture
def restart_env(tmp_path, monkeypatch):
    monkeypatch.setattr(restart, 'Bot', FakeBot)
    monkeypatch.setattr(restart, 'STOP_DELAY_SECONDS', 0)
    monkeypatch.setattr(restart, '_restarting', False)
    monkeypatch.setattr(config, 'BOT_TOKEN', 'saved-token')
    monkeypatch.setattr(config, 'ADMIN_IDS', [ADMIN_ID])
    monkeypatch.setattr(config, 'WEB_PANEL_PASSWORD', 'test-pass')
    monkeypatch.setattr(config, 'WEB_PANEL_PASSWORD_SHA256', '')
    FakeBot.error = None
    FakeBot.tokens = []
    auth._sessions.clear()
    auth._login_failures.clear()
    return str(tmp_path / 'restart_test.db')


def _run(db_path, scenario):
    async def main():
        await DatabaseManager(db_path).initialize()
        await db.set_app_meta(restart.NOTIFY_META_KEY, '')
        await scenario()

    asyncio.run(main())


async def _finish(application):
    await asyncio.gather(*application.tasks)


def test_valid_token_schedules_restart_and_saves_notify_target(restart_env):
    async def scenario():
        application = FakeApplication()
        await restart.request_restart(application, [ADMIN_ID])
        await _finish(application)

        assert FakeBot.tokens == ['saved-token']
        assert application.stopped == 1
        assert json.loads(await db.get_app_meta(restart.NOTIFY_META_KEY)) == [ADMIN_ID]

    _run(restart_env, scenario)


@pytest.mark.parametrize('error, message', [
    (InvalidToken('nope'), copy_text.RESTART_BAD_TOKEN),
    (NetworkError('down'), copy_text.RESTART_TOKEN_UNREACHABLE),
])
def test_failed_token_check_refuses_and_allows_retry(restart_env, error, message):
    async def scenario():
        application = FakeApplication()
        FakeBot.error = error
        with pytest.raises(restart.RestartRefused) as refused:
            await restart.request_restart(application, [ADMIN_ID])
        assert str(refused.value) == message
        assert application.tasks == []
        assert await db.get_app_meta(restart.NOTIFY_META_KEY) == ''

        # 修好之后可以重新发起
        FakeBot.error = None
        await restart.request_restart(application, [ADMIN_ID])
        await _finish(application)
        assert application.stopped == 1

    _run(restart_env, scenario)


def test_empty_token_is_refused_by_real_client(restart_env, monkeypatch):
    from telegram import Bot

    monkeypatch.setattr(restart, 'Bot', Bot)
    monkeypatch.setattr(config, 'BOT_TOKEN', '')

    async def scenario():
        application = FakeApplication()
        with pytest.raises(restart.RestartRefused) as refused:
            await restart.request_restart(application, [ADMIN_ID])
        assert str(refused.value) == copy_text.RESTART_BAD_TOKEN

    _run(restart_env, scenario)


def test_refuses_during_broadcast_and_tracks_failures(restart_env, monkeypatch):
    monkeypatch.setattr(broadcast, '_active_broadcasts', 1)

    async def scenario():
        with pytest.raises(restart.RestartRefused) as refused:
            await restart.request_restart(FakeApplication(), [ADMIN_ID])
        assert str(refused.value) == copy_text.RESTART_BROADCAST_RUNNING
        assert FakeBot.tokens == []

    _run(restart_env, scenario)


def test_broadcast_counter_resets_after_error(monkeypatch):
    monkeypatch.setattr(broadcast, '_active_broadcasts', 0)

    @broadcast._tracked
    async def failing():
        assert broadcast.is_running()
        raise RuntimeError('boom')

    with pytest.raises(RuntimeError):
        asyncio.run(failing())
    assert not broadcast.is_running()


def test_second_request_is_refused(restart_env):
    async def scenario():
        application = FakeApplication()
        await restart.request_restart(application, [ADMIN_ID])
        with pytest.raises(restart.RestartRefused) as refused:
            await restart.request_restart(application, [ADMIN_ID])
        assert str(refused.value) == copy_text.RESTART_IN_PROGRESS
        await _finish(application)
        assert application.stopped == 1

    _run(restart_env, scenario)


def test_announce_sends_once_after_restart(restart_env):
    async def scenario():
        application = FakeApplication()
        await db.set_app_meta(restart.NOTIFY_META_KEY, json.dumps([ADMIN_ID]))

        await restart.announce_restart(application)
        await restart.announce_restart(application)

        assert [item['chat_id'] for item in application.sent] == [ADMIN_ID]

    _run(restart_env, scenario)


def test_exec_replaces_process_only_after_request(monkeypatch):
    calls = []
    monkeypatch.setattr(restart.os, 'execv', lambda path, args: calls.append((path, args)))
    monkeypatch.setattr(restart.sys, 'orig_argv', ['python', '-u', 'bot.py'])

    monkeypatch.setattr(restart, '_restarting', False)
    restart.exec_if_requested()
    assert calls == []

    monkeypatch.setattr(restart, '_restarting', True)
    restart.exec_if_requested()
    assert calls == [(restart.sys.executable, [restart.sys.executable, '-u', 'bot.py'])]


def test_panel_endpoint_requires_login_and_reports_refusal(restart_env):
    headers = {'X-Requested-With': 'PMBotPanel'}

    async def scenario():
        application = FakeApplication()
        http_session = aiohttp.ClientSession()
        client = TestClient(TestServer(create_app(application.bot, http_session, application)))
        await client.start_server()
        try:
            response = await client.post('/api/restart', headers=headers)
            assert response.status == 401

            await client.post('/api/login', json={'password': 'test-pass'})

            FakeBot.error = InvalidToken('nope')
            response = await client.post('/api/restart', headers=headers)
            assert response.status == 409
            assert (await response.json())['error'] == copy_text.RESTART_BAD_TOKEN
            assert application.tasks == []

            FakeBot.error = None
            response = await client.post('/api/restart', headers=headers)
            assert response.status == 200
            await _finish(application)
            assert application.stopped == 1
            assert json.loads(await db.get_app_meta(restart.NOTIFY_META_KEY)) == [ADMIN_ID]
        finally:
            await client.close()
            await http_session.close()

    _run(restart_env, scenario)


def test_miniapp_restart_notifies_the_admin_who_clicked(restart_env, monkeypatch):
    monkeypatch.setattr(config, 'ADMIN_IDS', [ADMIN_ID, 333])

    async def scenario():
        application = FakeApplication()
        http_session = aiohttp.ClientSession()
        panel = create_app(application.bot, http_session, application)
        panel.add_subapp(MINIAPP_PREFIX, miniapp.create_app(application, http_session))
        client = TestClient(TestServer(panel))
        await client.start_server()
        try:
            path = f'{MINIAPP_API_PREFIX}restart'
            response = await client.post(path)
            assert response.status == 401

            token = miniapp.create_session(333)
            response = await client.post(path, headers={miniapp.TOKEN_HEADER: token})
            assert response.status == 200
            await _finish(application)
            assert json.loads(await db.get_app_meta(restart.NOTIFY_META_KEY)) == [333]
        finally:
            miniapp._sessions.clear()
            await client.close()
            await http_session.close()

    _run(restart_env, scenario)
