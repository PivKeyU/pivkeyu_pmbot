"""自动更新：同意前不更新、更新窗口、检测提醒、失败还原与面板接口。"""

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import aiohttp
import pytest
from aiohttp.test_utils import TestClient, TestServer

from config import config
from database import models as db
from database.db_manager import DatabaseManager
from services import auto_update, broadcast, image_update, restart, safe_update
from utils import copy as copy_text
from web_panel import auth, miniapp
from web_panel.server import create_app

ADMIN_ID = 111
PANEL_HEADERS = {'X-Requested-With': 'PMBotPanel'}
UTC8 = timezone(timedelta(hours=8))


def at(day, hour, minute=0):
    return datetime(2026, 11, day, hour, minute, tzinfo=UTC8).timestamp()


class FakeBot:
    def __init__(self):
        self.sent = []

    async def send_message(self, chat_id, text):
        self.sent.append((chat_id, text))


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(config, 'ADMIN_IDS', [ADMIN_ID])
    monkeypatch.setattr(auto_update, '_startup_checked', False)
    monkeypatch.setattr(auto_update, '_tick_running', False)
    monkeypatch.setattr(broadcast, '_active_broadcasts', 0)
    return str(tmp_path / 'auto_update_test.db')


def _image_status(available=True, digest='sha256:new', error=''):
    return image_update.ImageUpdateStatus(
        in_container=True, image_repo='r', image_tag='t', local_sha='', remote_digest=digest,
        known_digest='sha256:old', update_available=available, error=error,
    )


def _run(db_path, monkeypatch, scenario, status=None, triggered=(True, 'ok')):
    calls = []

    async def check_for_update():
        return status or _image_status()

    async def trigger():
        calls.append('trigger')
        return triggered

    monkeypatch.setattr(image_update, 'detect_container', lambda: True)
    monkeypatch.setattr(image_update, 'check_for_update', check_for_update)
    monkeypatch.setattr(image_update, 'trigger_watchtower_update', trigger)

    async def main():
        await DatabaseManager(db_path).initialize()
        for key in (auto_update.SETTINGS_META_KEY, auto_update.STATE_META_KEY, restart.NOTIFY_META_KEY):
            await db.set_app_meta(key, '')
        await db.set_app_meta(image_update.IMAGE_DIGEST_META_KEY, 'sha256:old')
        application = SimpleNamespace(bot=FakeBot())
        await scenario(application, calls)

    asyncio.run(main())
    return calls


def test_no_update_without_consent_even_inside_window(env, monkeypatch):
    async def scenario(application, calls):
        await auto_update.tick(application, at(1, 4, 5))
        assert calls == []
        assert application.bot.sent[0][1].startswith(copy_text.AUTO_UPDATE_FOUND_NEEDS_CONSENT[:6])
        state = await auto_update.get_state()
        assert state['update_available'] is True

    _run(env, monkeypatch, scenario)


def test_consent_is_off_by_default(env, monkeypatch):
    async def scenario(application, calls):
        settings = await auto_update.get_settings()
        assert settings['consented'] is False
        assert (settings['time'], settings['utc_offset_minutes']) == ('04:00', 480)

    _run(env, monkeypatch, scenario)


def test_consented_update_runs_only_in_window_and_once_a_day(env, monkeypatch):
    async def scenario(application, calls):
        await auto_update.update_settings(True, '04:00', 480)

        await auto_update.tick(application, at(1, 3, 59))
        assert calls == []

        await auto_update.tick(application, at(1, 4, 1))
        assert calls == ['trigger']
        assert await db.get_app_meta(image_update.IMAGE_DIGEST_META_KEY) == 'sha256:new'

        await auto_update.tick(application, at(1, 4, 2))
        assert calls == ['trigger']

    _run(env, monkeypatch, scenario)


def test_missed_window_waits_for_next_day(env, monkeypatch):
    async def scenario(application, calls):
        await auto_update.update_settings(True, '04:00', 480)
        await auto_update.tick(application, at(1, 9, 0))
        assert calls == []

    _run(env, monkeypatch, scenario)


def test_broadcast_delays_update(env, monkeypatch):
    async def scenario(application, calls):
        await auto_update.update_settings(True, '04:00', 480)
        monkeypatch.setattr(broadcast, '_active_broadcasts', 1)
        await auto_update.tick(application, at(1, 4, 1))
        assert calls == []

        monkeypatch.setattr(broadcast, '_active_broadcasts', 0)
        await auto_update.tick(application, at(1, 4, 2))
        assert calls == ['trigger']

    _run(env, monkeypatch, scenario)


def test_same_version_is_announced_once(env, monkeypatch):
    async def scenario(application, calls):
        await auto_update.tick(application, at(1, 9))
        await auto_update.tick(application, at(1, 9) + auto_update.CHECK_INTERVAL_SECONDS)
        assert len(application.bot.sent) == 1

    _run(env, monkeypatch, scenario)


def test_failed_trigger_restores_baseline_and_notifies(env, monkeypatch):
    async def scenario(application, calls):
        await auto_update.update_settings(True, '04:00', 480)
        await auto_update.tick(application, at(1, 4, 1))

        assert await db.get_app_meta(image_update.IMAGE_DIGEST_META_KEY) == 'sha256:old'
        assert await db.get_app_meta(restart.NOTIFY_META_KEY) == ''
        assert '没配' in application.bot.sent[-1][1]
        state = await auto_update.get_state()
        assert state['last_result'] == '没配 token'

        await auto_update.tick(application, at(1, 4, 2))
        assert calls == ['trigger']

    _run(env, monkeypatch, scenario, triggered=(False, '没配 token'))


def test_recheck_without_new_version_skips_update(env, monkeypatch):
    statuses = [_image_status(), _image_status(available=False)]

    async def scenario(application, calls):
        async def check_for_update():
            return statuses.pop(0)

        monkeypatch.setattr(image_update, 'check_for_update', check_for_update)
        await auto_update.update_settings(True, '04:00', 480)
        await auto_update.tick(application, at(1, 4, 1))
        assert calls == []

    _run(env, monkeypatch, scenario)


def test_git_deployment_updates_then_restarts(env, monkeypatch):
    applied, restarts = [], []

    async def get_status(repo_dir, fetch_remote=True):
        return safe_update.GitUpdateStatus('main', 'aaa', 'bbb', 0, 2, False)

    async def apply_update(repo_dir):
        applied.append(repo_dir)

    async def request_restart(application, chat_ids):
        restarts.append(list(chat_ids))

    async def scenario(application, calls):
        monkeypatch.setattr(image_update, 'detect_container', lambda: False)
        monkeypatch.setattr(safe_update, 'get_status', get_status)
        monkeypatch.setattr(safe_update, 'apply_update', apply_update)
        monkeypatch.setattr(restart, 'request_restart', request_restart)
        await auto_update.update_settings(True, '04:00', 480)
        await auto_update.tick(application, at(1, 4, 1))
        assert len(applied) == 1
        assert restarts == [[ADMIN_ID]]
        assert calls == []

    _run(env, monkeypatch, scenario)


def test_window_can_cross_midnight():
    settings = {'time': '23:50', 'utc_offset_minutes': 480}
    assert auto_update.is_due(at(2, 0, 10), settings)
    date = auto_update.window_date(at(2, 0, 10), settings)
    assert date == '2026-11-01'
    assert not auto_update.is_due(at(2, 0, 20), settings, date)


@pytest.mark.parametrize('update_time, offset', [('25:00', 480), ('4:00', 480), ('04:00', 'x'), ('04:00', 99999), ('04:00', True)])
def test_invalid_settings_are_rejected(env, monkeypatch, update_time, offset):
    async def scenario(application, calls):
        with pytest.raises(ValueError):
            await auto_update.update_settings(True, update_time, offset)

    _run(env, monkeypatch, scenario)


def test_panel_routes_require_login_and_record_consent(env, monkeypatch):
    monkeypatch.setattr(config, 'WEB_PANEL_PASSWORD', 'test-pass')
    monkeypatch.setattr(config, 'WEB_PANEL_PASSWORD_SHA256', '')
    auth._sessions.clear()
    auth._login_failures.clear()
    assert ('PUT', '/api/update/auto') in miniapp.ALLOWED_ROUTES

    async def scenario(application, calls):
        http_session = aiohttp.ClientSession()
        client = TestClient(TestServer(create_app(application.bot, http_session)))
        await client.start_server()
        try:
            payload = {'consented': True, 'time': '03:30', 'utc_offset_minutes': 480}
            response = await client.put('/api/update/auto', json=payload, headers=PANEL_HEADERS)
            assert response.status == 401

            await client.post('/api/login', json={'password': 'test-pass'})
            response = await client.put('/api/update/auto', json=payload, headers=PANEL_HEADERS)
            assert response.status == 200
            data = (await response.json())['data']
            assert data['settings']['consented'] is True
            assert data['settings']['consented_at']
            assert data['next_update_at']

            response = await client.put(
                '/api/update/auto', json={**payload, 'consented': 'yes'}, headers=PANEL_HEADERS)
            assert response.status == 400

            response = await client.put(
                '/api/update/auto', json={**payload, 'consented': False}, headers=PANEL_HEADERS)
            data = (await response.json())['data']
            assert data['settings']['consented_at'] is None
            assert data['next_update_at'] is None
        finally:
            await client.close()
            await http_session.close()

    _run(env, monkeypatch, scenario)
