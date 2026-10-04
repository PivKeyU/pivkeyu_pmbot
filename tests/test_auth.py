"""Web 面板认证：密码校验、会话生命周期与登录限速。"""

import hashlib
import time

import pytest

from config import config
from web_panel import auth


@pytest.fixture(autouse=True)
def clean_state():
    auth._sessions.clear()
    auth._login_failures.clear()
    yield
    auth._sessions.clear()
    auth._login_failures.clear()


def test_plain_password_verify(monkeypatch):
    monkeypatch.setattr(config, 'WEB_PANEL_PASSWORD', 'sucret')
    monkeypatch.setattr(config, 'WEB_PANEL_PASSWORD_SHA256', '')
    assert auth.password_configured() is True
    assert auth.verify_password('sucret') is True
    assert auth.verify_password('wrong') is False
    assert auth.verify_password('') is False


def test_sha256_password_preferred(monkeypatch):
    digest = hashlib.sha256(b'better-secret').hexdigest()
    monkeypatch.setattr(config, 'WEB_PANEL_PASSWORD', 'plain-ignored')
    monkeypatch.setattr(config, 'WEB_PANEL_PASSWORD_SHA256', digest)
    assert auth.verify_password('better-secret') is True
    assert auth.verify_password('plain-ignored') is False


def test_no_password_configured(monkeypatch):
    monkeypatch.setattr(config, 'WEB_PANEL_PASSWORD', '')
    monkeypatch.setattr(config, 'WEB_PANEL_PASSWORD_SHA256', '')
    assert auth.password_configured() is False
    assert auth.verify_password('anything') is False


def test_login_lockout_after_failures():
    ip = '203.0.113.9'
    assert auth.login_lock_remaining(ip) == 0
    for _ in range(auth.LOGIN_MAX_FAILURES - 1):
        auth.record_login_failure(ip)
    assert auth.login_lock_remaining(ip) == 0, '未达上限不应锁定'
    auth.record_login_failure(ip)
    assert auth.login_lock_remaining(ip) > 0, '达到上限必须锁定'

    # 锁定期内继续失败不应把剩余时间清零
    remaining = auth.login_lock_remaining(ip)
    auth.record_login_failure(ip)
    assert auth.login_lock_remaining(ip) > 0
    assert auth.login_lock_remaining(ip) <= remaining + auth.LOGIN_LOCK_SECONDS

    auth.record_login_success(ip)
    assert auth.login_lock_remaining(ip) == 0, '成功登录应清除锁定'


def test_session_lifecycle():
    token, expires_at = auth.create_session('127.0.0.1')
    assert expires_at > time.time()
    assert auth.validate_session(token) is True
    assert auth.validate_session('not-a-token') is False

    auth.destroy_session(token)
    assert auth.validate_session(token) is False


def test_session_expiry(monkeypatch):
    token, _ = auth.create_session('127.0.0.1')
    future = time.time() + auth.SESSION_TTL_SECONDS + 10
    monkeypatch.setattr(auth.time, 'time', lambda: future)
    assert auth.validate_session(token) is False


def test_session_sliding_renewal(monkeypatch):
    token, _ = auth.create_session('127.0.0.1')
    half = time.time() + auth.SESSION_TTL_SECONDS - 60
    monkeypatch.setattr(auth.time, 'time', lambda: half)
    assert auth.validate_session(token) is True
    # 续期后再过一段（原过期点之后）仍应有效
    later = half + 120
    monkeypatch.setattr(auth.time, 'time', lambda: later)
    assert auth.validate_session(token) is True


@pytest.mark.parametrize('ip,expected', [
    ('127.0.0.1', True),
    ('::1', True),
    ('localhost', True),
    ('10.0.0.5', False),
    ('203.0.113.7', False),
    ('', False),
    ('not-an-ip', False),
])
def test_is_loopback_ip(ip, expected):
    assert auth.is_loopback_ip(ip) is expected


def test_setup_window(monkeypatch):
    monkeypatch.setattr(config, 'WEB_PANEL_PASSWORD', '')
    monkeypatch.setattr(config, 'WEB_PANEL_PASSWORD_SHA256', '')
    monkeypatch.setattr(auth, '_setup_deadline', time.time() + 600)
    assert auth.setup_mode_active() is True
    assert auth.setup_remaining() > 0

    # 窗口关闭后不再允许初始化
    monkeypatch.setattr(auth, '_setup_deadline', time.time() - 1)
    assert auth.setup_mode_active() is False
    assert auth.setup_remaining() == 0

    # 已设置密码时与窗口无关
    monkeypatch.setattr(config, 'WEB_PANEL_PASSWORD', 'secret')
    monkeypatch.setattr(auth, '_setup_deadline', time.time() + 600)
    assert auth.setup_mode_active() is False


def test_hash_password():
    digest = auth.hash_password('abc12345')
    assert digest == hashlib.sha256(b'abc12345').hexdigest()
    assert len(digest) == 64
