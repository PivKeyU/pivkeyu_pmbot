"""Web 面板认证：密码校验、会话 Cookie、登录限速与首次初始化。

密码来源（二选一，摘要优先）：
1. 首次访问初始化：面板启动后的 SETUP_WINDOW_SECONDS 内，打开页面即可
   设置密码（只落 SHA256 摘要，存 settings 表覆盖层）；
2. 手动配置：.env 的 WEB_PANEL_PASSWORD 或 WEB_PANEL_PASSWORD_SHA256。

初始化窗口关闭仍未设置密码时面板锁定，直到通过上面任一方式设置并重启进程。
会话保存在进程内存中，进程重启后需要重新登录。
"""

import hashlib
import hmac
import ipaddress
import logging
import secrets
import time

from config import config

logger = logging.getLogger(__name__)

SESSION_COOKIE = 'pmbot_panel_session'
SESSION_TTL_SECONDS = 24 * 3600
LOGIN_MAX_FAILURES = 5
LOGIN_LOCK_SECONDS = 15 * 60

# 首次初始化窗口：面板启动后多久内允许通过网页设置密码（超时未设置则锁定）。
# 与 Portainer 的首次启动设置管理员密码同一思路：把「第一个打开页面的人」
# 限制在部署后的短窗口内，窗口一过就永久关闭，避免长期裸奔。
SETUP_WINDOW_SECONDS = 15 * 60
_setup_deadline = time.time() + SETUP_WINDOW_SECONDS

_sessions: dict = {}
_login_failures: dict = {}


def begin_setup_window() -> None:
    """从 Web 面板开始监听时启动首次密码设置窗口。"""
    global _setup_deadline
    _setup_deadline = time.time() + SETUP_WINDOW_SECONDS


def password_configured() -> bool:
    return bool(config.WEB_PANEL_PASSWORD_SHA256 or config.WEB_PANEL_PASSWORD)


def setup_mode_active() -> bool:
    """未设置密码且初始化窗口未关闭时为 True。"""
    return not password_configured() and time.time() < _setup_deadline


def setup_remaining() -> int:
    return max(0, int(_setup_deadline - time.time()))


def hash_password(password: str) -> str:
    """面板密码只存 SHA256 摘要，不落明文。"""
    return hashlib.sha256(password.encode('utf-8')).hexdigest()


def verify_password(candidate: str) -> bool:
    candidate = candidate or ''
    if config.WEB_PANEL_PASSWORD_SHA256:
        digest = hashlib.sha256(candidate.encode('utf-8')).hexdigest()
        return hmac.compare_digest(digest, config.WEB_PANEL_PASSWORD_SHA256)
    if config.WEB_PANEL_PASSWORD:
        return hmac.compare_digest(candidate, config.WEB_PANEL_PASSWORD)
    return False


def client_ip(request) -> str:
    """取来源 IP。经反向代理时（WEB_PANEL_TRUST_PROXY=true）读 X-Forwarded-For 首段。"""
    if config.WEB_PANEL_TRUST_PROXY:
        forwarded = request.headers.get('X-Forwarded-For') or ''
        if forwarded:
            return forwarded.split(',')[0].strip() or 'unknown'
    return request.remote or 'unknown'


def is_loopback_ip(ip: str) -> bool:
    if not ip:
        return False
    if ip == 'localhost':
        return True
    try:
        return ipaddress.ip_address(ip).is_loopback
    except ValueError:
        return False


def login_lock_remaining(ip: str) -> int:
    record = _login_failures.get(ip)
    if not record:
        return 0
    remaining = int(record.get('locked_until', 0) - time.time())
    return remaining if remaining > 0 else 0


def record_login_failure(ip: str) -> int:
    """记一次失败；连续失败达到上限后锁定。返回剩余锁定秒数。"""
    record = _login_failures.setdefault(ip, {'count': 0, 'locked_until': 0})
    record['count'] += 1
    if record['count'] >= LOGIN_MAX_FAILURES:
        record['locked_until'] = time.time() + LOGIN_LOCK_SECONDS
        record['count'] = 0
    return login_lock_remaining(ip)


def record_login_success(ip: str) -> None:
    _login_failures.pop(ip, None)


def create_session(ip: str):
    _purge_expired()
    token = secrets.token_urlsafe(32)
    now = time.time()
    _sessions[token] = {'ip': ip, 'created_at': now, 'expires_at': now + SESSION_TTL_SECONDS}
    return token, _sessions[token]['expires_at']


def validate_session(token: str) -> bool:
    if not token:
        return False
    session = _sessions.get(token)
    if not session:
        return False
    now = time.time()
    if session['expires_at'] <= now:
        _sessions.pop(token, None)
        return False
    # 滑动续期：持续使用则不过期
    session['expires_at'] = now + SESSION_TTL_SECONDS
    return True


def destroy_session(token: str) -> None:
    _sessions.pop(token, None)


def destroy_other_sessions(keep_token: str) -> None:
    """密码变更后撤销其它浏览器会话，保留当前操作会话。"""
    for token in list(_sessions):
        if token != keep_token:
            _sessions.pop(token, None)


def session_count() -> int:
    return len(_sessions)


def _purge_expired() -> None:
    now = time.time()
    for token in [t for t, s in _sessions.items() if s['expires_at'] <= now]:
        _sessions.pop(token, None)
