"""登录 / 登出 / 首次初始化 / 当前身份。"""

import logging

from aiohttp import web

from config import config
from database import models as db
from . import auth, http_utils

logger = logging.getLogger(__name__)

routes = web.RouteTableDef()

SETUP_PASSWORD_MIN = 8
SETUP_PASSWORD_MAX = 128


def _bot_identity() -> dict:
    return {'bot_username': config.BOT_USERNAME, 'bot_id': config.BOT_ID}


@routes.get('/api/me')
async def me(request):
    if not auth.password_configured():
        # 未初始化：告知前端进入「设置密码」或「已锁定」界面
        if auth.setup_mode_active():
            return http_utils.ok({
                'authenticated': False,
                'password_required': True,
                'setup_mode': True,
                'setup_remaining': auth.setup_remaining(),
                **_bot_identity(),
            })
        return http_utils.ok({
            'authenticated': False,
            'password_required': True,
            'setup_mode': False,
            'locked': True,
            **_bot_identity(),
        })

    token = request.cookies.get(auth.SESSION_COOKIE, '')
    authenticated = auth.validate_session(token) if token else False
    return http_utils.ok({
        'authenticated': bool(authenticated),
        'password_required': True,
        **_bot_identity(),
    })


@routes.post('/api/setup')
async def setup(request):
    """首次访问初始化：设置面板密码并直接登录。

    只在「未设置密码且初始化窗口未关闭」时可用；成功后密码以 SHA256
    摘要写入 settings 覆盖层（重启自动加载），明文不落任何地方。
    """
    if auth.password_configured():
        return http_utils.fail('面板密码已设置，请直接登录', status=403)
    if not auth.setup_mode_active():
        return http_utils.fail(
            '初始化窗口已关闭。请在 .env 设置 WEB_PANEL_PASSWORD 后重启进程解锁面板',
            status=403,
        )

    data = await http_utils.read_json(request)
    password = str(data.get('password') or '')
    if len(password) < SETUP_PASSWORD_MIN:
        return http_utils.fail(f'密码至少 {SETUP_PASSWORD_MIN} 位')
    if len(password) > SETUP_PASSWORD_MAX:
        return http_utils.fail(f'密码最长 {SETUP_PASSWORD_MAX} 位')

    digest = auth.hash_password(password)
    config.apply_override('WEB_PANEL_PASSWORD_SHA256', digest)
    await db.set_setting('WEB_PANEL_PASSWORD_SHA256', digest, 'Web 面板首次初始化设置的密码摘要')

    ip = auth.client_ip(request)
    token, expires_at = auth.create_session(ip)
    response = http_utils.ok({'expires_at': expires_at})
    response.set_cookie(
        auth.SESSION_COOKIE,
        token,
        max_age=auth.SESSION_TTL_SECONDS,
        httponly=True,
        samesite='Strict',
        secure=(request.scheme == 'https'),
        path='/',
    )
    http_utils.audit(request, 'panel.setup', '首次访问完成密码初始化')
    logger.info('Web 面板密码已通过首次访问初始化 ip=%s', ip)
    return response


@routes.post('/api/login')
async def login(request):
    if not auth.password_configured():
        if auth.setup_mode_active():
            return http_utils.fail('面板尚未初始化，请先设置密码', status=403)
        return http_utils.fail('面板已锁定：初始化窗口已关闭，请在 .env 设置 WEB_PANEL_PASSWORD 后重启', status=403)

    ip = auth.client_ip(request)
    remaining = auth.login_lock_remaining(ip)
    if remaining > 0:
        minutes = max(1, (remaining + 59) // 60)
        return http_utils.fail(f'尝试次数过多，请 {minutes} 分钟后再试', status=429)

    data = await http_utils.read_json(request)
    password = str(data.get('password') or '')
    if not auth.verify_password(password):
        auth.record_login_failure(ip)
        logger.warning('Web 面板登录失败 ip=%s', ip)
        return http_utils.fail('密码不正确', status=401)

    auth.record_login_success(ip)
    token, expires_at = auth.create_session(ip)
    response = http_utils.ok({'expires_at': expires_at})
    response.set_cookie(
        auth.SESSION_COOKIE,
        token,
        max_age=auth.SESSION_TTL_SECONDS,
        httponly=True,
        samesite='Strict',
        secure=(request.scheme == 'https'),
        path='/',
    )
    logger.info('Web 面板登录成功 ip=%s', ip)
    return response


@routes.post('/api/password')
async def change_password(request):
    """已登录用户随时更换面板密码，不受首次初始化窗口限制。"""
    data = await http_utils.read_json(request)
    current_password = str(data.get('current_password') or '')
    new_password = str(data.get('new_password') or '')

    if not auth.verify_password(current_password):
        return http_utils.fail('当前密码不正确', status=401)
    if len(new_password) < SETUP_PASSWORD_MIN:
        return http_utils.fail(f'新密码至少 {SETUP_PASSWORD_MIN} 位')
    if len(new_password) > SETUP_PASSWORD_MAX:
        return http_utils.fail(f'新密码最长 {SETUP_PASSWORD_MAX} 位')

    digest = auth.hash_password(new_password)
    await db.set_setting('WEB_PANEL_PASSWORD_SHA256', digest, 'Web 面板密码摘要')
    config.apply_override('WEB_PANEL_PASSWORD_SHA256', digest)

    token = request.cookies.get(auth.SESSION_COOKIE, '')
    auth.destroy_other_sessions(token)
    http_utils.audit(request, 'panel.password.change', '已更改 Web 面板密码')
    logger.info('Web 面板密码已更改')
    return http_utils.ok({'password_changed': True})


@routes.post('/api/logout')
async def logout(request):
    token = request.cookies.get(auth.SESSION_COOKIE, '')
    auth.destroy_session(token)
    response = http_utils.ok()
    response.del_cookie(auth.SESSION_COOKIE, path='/')
    return response
