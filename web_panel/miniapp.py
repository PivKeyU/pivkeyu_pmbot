"""Telegram Mini App：挂在面板 /miniapp/ 路径下的子应用，提供人机验证页和手机管理页。

它和面板共用端口和反向代理，但接口是独立注册的：/miniapp/api/ 下只有 ALLOWED_ROUTES
里的接口，密码登录、首次设密、改密和会话回复在这个路径下根本不存在。
新增面板接口时默认不开放，需要进手机管理页才加入白名单。

身份来自 Telegram 签名的 initData。会话令牌只放在页面内存里，用请求头传递，不用 Cookie：
网页版 Telegram 把 Mini App 嵌在跨站 iframe 里，SameSite=Strict 的 Cookie 带不过去。
"""

import asyncio
import hashlib
import hmac
import json
import logging
import secrets
import time
from urllib.parse import parse_qsl, urlsplit

import aiohttp
from aiohttp import web
from telegram.ext import CallbackContext

from config import config
from database import models as db
from services import verification
from utils import copy as copy_text
from . import api_admin, api_config, api_overview, http_utils, keys
from .server import (
    MINIAPP_API_PREFIX, MINIAPP_PREFIX, STATIC_DIR, VERSIONED_STATIC_PREFIX,
    compute_asset_version, error_middleware, render_index,
)

logger = logging.getLogger(__name__)

routes = web.RouteTableDef()

TOKEN_HEADER = 'X-MiniApp-Token'
INIT_DATA_MAX_AGE_SECONDS = 3600
SESSION_TTL_SECONDS = 2 * 3600
TURNSTILE_VERIFY_URL = 'https://challenges.cloudflare.com/turnstile/v0/siteverify'
TURNSTILE_ACTION = 'verify'
TURNSTILE_TIMEOUT_SECONDS = 10
WRITE_METHODS = {'POST', 'PUT', 'PATCH', 'DELETE'}
PUBLIC_API_PATHS = {f'{MINIAPP_API_PREFIX}session', f'{MINIAPP_API_PREFIX}verify'}
VERSIONED_ASSET_PREFIX = f'{MINIAPP_PREFIX.rstrip("/")}{VERSIONED_STATIC_PREFIX}'

# 手机管理页用到的接口（方法, 路由模板），没有列出的一律不注册
ALLOWED_ROUTES = {
    # 概览
    ('GET', '/api/overview'),
    ('GET', '/api/runtime-status'),
    # 用户 / 黑名单 / 通行证 / 拦截篮
    ('GET', '/api/users'),
    ('GET', '/api/blacklist'),
    ('POST', '/api/blacklist'),
    ('DELETE', '/api/blacklist/{user_id}'),
    ('GET', '/api/exemptions'),
    ('POST', '/api/exemptions'),
    ('DELETE', '/api/exemptions/{user_id}'),
    ('GET', '/api/filtered'),
    ('DELETE', '/api/filtered'),
    ('DELETE', '/api/filtered/{message_id}'),
    # 配置中心（面板密码相关配置项由接口自身拒绝）
    ('GET', '/api/config'),
    ('PUT', '/api/config/{key}'),
    ('DELETE', '/api/config/{key}'),
    # AI 设置
    ('GET', '/api/ai-settings'),
    ('PUT', '/api/ai-settings'),
    ('GET', '/api/ai-models'),
    ('POST', '/api/ai-test'),
    ('POST', '/api/ai-decision-test'),
    ('GET', '/api/ai-options'),
    ('PUT', '/api/ai-options'),
    # 回复与关键词
    ('GET', '/api/autoreply'),
    ('PUT', '/api/autoreply'),
    ('GET', '/api/knowledge'),
    ('POST', '/api/knowledge'),
    ('PUT', '/api/knowledge/{knowledge_id}'),
    ('DELETE', '/api/knowledge/{knowledge_id}'),
    ('GET', '/api/spam-keywords'),
    ('PUT', '/api/spam-keywords/settings'),
    ('POST', '/api/spam-keywords'),
    ('DELETE', '/api/spam-keywords/{keyword}'),
    ('DELETE', '/api/spam-keywords'),
    # RSS
    ('GET', '/api/rss'),
    ('PUT', '/api/rss/settings'),
    ('POST', '/api/rss/feeds'),
    ('DELETE', '/api/rss/feeds'),
    ('POST', '/api/rss/keywords'),
    ('DELETE', '/api/rss/keywords'),
    ('POST', '/api/rss/authorized'),
    ('DELETE', '/api/rss/authorized/{user_id}'),
    # 消息监控
    ('GET', '/api/tg-monitors'),
    ('POST', '/api/tg-monitors'),
    ('PUT', '/api/tg-monitors/{monitor_id}'),
    ('DELETE', '/api/tg-monitors/{monitor_id}'),
    ('GET', '/api/tg-discovered'),
    # 网页监控
    ('GET', '/api/web-monitors'),
    ('POST', '/api/web-monitors'),
    ('PUT', '/api/web-monitors/{monitor_id}'),
    ('DELETE', '/api/web-monitors/{monitor_id}'),
    # 广播与用户分组
    ('GET', '/api/user-groups'),
    ('POST', '/api/user-groups'),
    ('DELETE', '/api/user-groups/{name}'),
    ('GET', '/api/user-groups/{name}/members'),
    ('POST', '/api/user-groups/{name}/members'),
    ('DELETE', '/api/user-groups/{name}/members/{user_id}'),
    ('GET', '/api/broadcasts'),
    ('POST', '/api/broadcast'),
    # 网络工具
    ('GET', '/api/network/servers'),
    ('POST', '/api/network/servers'),
    ('DELETE', '/api/network/servers/{name}'),
    ('POST', '/api/network/ping'),
    ('POST', '/api/network/nexttrace'),
    ('POST', '/api/network/install'),
    # 机器人更新
    ('GET', '/api/update/git'),
    ('POST', '/api/update/git'),
    ('POST', '/api/update/git/rollback'),
    ('GET', '/api/update/image'),
    ('POST', '/api/update/image'),
    ('POST', '/api/restart'),
    ('GET', '/api/update/auto'),
    ('PUT', '/api/update/auto'),
}

# 网页版 Telegram 用 iframe 嵌入 Mini App，所以这里不能沿用面板的 frame-ancestors 'none'
CONTENT_SECURITY_POLICY = (
    "default-src 'self'; img-src 'self' data:; connect-src 'self'; "
    "script-src 'self' https://telegram.org https://challenges.cloudflare.com; "
    "style-src 'self' 'unsafe-inline'; frame-src https://challenges.cloudflare.com; "
    "frame-ancestors https://web.telegram.org https://*.telegram.org; "
    "base-uri 'none'; form-action 'none'"
)

_sessions: dict = {}


def parse_init_data(init_data: str, bot_token: str, now: float = None):
    """校验 Telegram 的 initData 签名与时效，通过时返回 user 字典，否则返回 None。"""
    if not init_data or not bot_token:
        return None
    fields = dict(parse_qsl(init_data, keep_blank_values=True))
    received_hash = fields.pop('hash', '')
    data_check = '\n'.join(f'{key}={fields[key]}' for key in sorted(fields))
    secret = hmac.new(b'WebAppData', bot_token.encode('utf-8'), hashlib.sha256).digest()
    expected = hmac.new(secret, data_check.encode('utf-8'), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, received_hash):
        return None

    try:
        age = (time.time() if now is None else now) - int(fields.get('auth_date', 0))
        user = json.loads(fields.get('user', ''))
        user['id'] = int(user['id'])
    except (TypeError, ValueError, KeyError):
        return None
    return user if age <= INIT_DATA_MAX_AGE_SECONDS else None


def create_session(user_id: int) -> str:
    now = time.time()
    for token in [t for t, s in _sessions.items() if s['expires_at'] <= now]:
        _sessions.pop(token, None)
    token = secrets.token_urlsafe(32)
    _sessions[token] = {'user_id': user_id, 'expires_at': now + SESSION_TTL_SECONDS}
    return token


def session_user(token: str):
    session = _sessions.get(token or '')
    if not session or session['expires_at'] <= time.time():
        _sessions.pop(token or '', None)
        return None
    return session['user_id']


def _read_user(request, data: dict):
    return parse_init_data(str(data.get('init_data') or ''), request.app[keys.BOT_KEY].token)


@routes.post('/api/session')
async def open_session(request):
    data = await http_utils.read_json(request)
    user = _read_user(request, data)
    if not user:
        return http_utils.fail('身份校验失败，请从 Telegram 重新打开', status=401)

    user_id = user['id']
    is_admin = await db.is_admin(user_id)
    pending = verification.turnstile_pending(user_id)
    payload = {
        'user_id': user_id,
        'first_name': user.get('first_name') or '',
        'is_admin': is_admin,
        'verification_pending': pending,
        'turnstile_site_key': config.TURNSTILE_SITE_KEY if pending else '',
        'turnstile_action': TURNSTILE_ACTION,
    }
    if is_admin:
        payload['token'] = create_session(user_id)
        payload['bot_username'] = config.BOT_USERNAME
    return http_utils.ok(payload)


async def verify_turnstile_token(http_session, token: str) -> bool:
    """向 Cloudflare 校验 token；网络异常向上抛出，调用方要和「校验不通过」区分开。"""
    timeout = aiohttp.ClientTimeout(total=TURNSTILE_TIMEOUT_SECONDS)
    form = {'secret': config.TURNSTILE_SECRET_KEY, 'response': token}
    async with http_session.post(TURNSTILE_VERIFY_URL, data=form, timeout=timeout) as response:
        result = await response.json(content_type=None)
    passed = (
        result.get('success') is True
        and result.get('action') == TURNSTILE_ACTION
        and result.get('hostname') == urlsplit(config.MINIAPP_PUBLIC_URL).hostname
    )
    if not passed:
        logger.info(
            'Turnstile 校验未通过 codes=%s hostname=%s action=%s',
            result.get('error-codes'), result.get('hostname'), result.get('action'),
        )
    return passed


async def _finish_verification(application, user_id: int, session: dict) -> None:
    """收起聊天里的验证按钮，再把验证前暂存的消息补发出去。"""
    from handlers.user_handler import deliver_pending_update

    text = copy_text.with_deco(copy_text.VERIFY_TURNSTILE_PASSED, 'OK')
    message_id = session.get('message_id')
    try:
        if message_id:
            await application.bot.edit_message_text(chat_id=user_id, message_id=message_id, text=text)
        else:
            await application.bot.send_message(chat_id=user_id, text=text)
    except Exception:
        logger.warning('更新 Turnstile 验证消息失败 user_id=%s', user_id, exc_info=True)

    context = CallbackContext(application, chat_id=user_id, user_id=user_id)
    await deliver_pending_update(context, user_id)


@routes.post('/api/verify')
async def verify(request):
    data = await http_utils.read_json(request)
    user = _read_user(request, data)
    if not user:
        return http_utils.fail('身份校验失败，请从 Telegram 重新打开', status=401)

    user_id = user['id']
    token = str(data.get('token') or '')
    if not token:
        return http_utils.fail('缺少验证结果')
    if not verification.turnstile_pending(user_id):
        return http_utils.fail('小验证已经过期，请回到聊天重新发送消息', status=410, code='expired')

    # 这里的失败都不计入验证失败次数：加载不了或网络异常不是客人答错
    try:
        passed = await verify_turnstile_token(request.app[keys.HTTP_SESSION_KEY], token)
    except (aiohttp.ClientError, asyncio.TimeoutError, ValueError):
        logger.warning('请求 Turnstile 校验接口失败', exc_info=True)
        return http_utils.fail('验证服务暂时连不上，请稍后再试或换普通验证题', status=502, code='unavailable')
    if not passed:
        return http_utils.fail('验证没有通过，请再试一次', status=403, code='rejected')

    session = await verification.complete_turnstile(user_id)
    if session is None:
        return http_utils.fail('小验证已经过期，请回到聊天重新发送消息', status=410, code='expired')

    application = request.app[keys.APPLICATION_KEY]
    application.create_task(_finish_verification(application, user_id, session))
    return http_utils.ok({'verified': True})


@web.middleware
async def security_headers_middleware(request, handler):
    response = await handler(request)
    response.headers.setdefault('X-Content-Type-Options', 'nosniff')
    response.headers.setdefault('Referrer-Policy', 'no-referrer')
    response.headers.setdefault('Content-Security-Policy', CONTENT_SECURITY_POLICY)
    if request.path.startswith(VERSIONED_ASSET_PREFIX):
        response.headers.setdefault('Cache-Control', 'public, max-age=31536000, immutable')
    return response


@web.middleware
async def auth_middleware(request, handler):
    path = request.path
    # 没有注册的路径直接走 404，让「这个接口不存在」对谁都一样
    if not path.startswith(MINIAPP_API_PREFIX) or path in PUBLIC_API_PATHS or request.match_info.http_exception:
        return await handler(request)

    user_id = session_user(request.headers.get(TOKEN_HEADER, ''))
    if user_id is None:
        return http_utils.fail('会话已过期，请从 Telegram 重新打开', status=401)
    # 每次都重新判断，管理员被移除后已打开的页面立即失效
    if not await db.is_admin(user_id):
        return http_utils.fail('没有管理权限', status=403)
    request[keys.MINIAPP_USER_KEY] = user_id
    if request.method in WRITE_METHODS:
        logger.info('Mini App 操作 user_id=%s %s %s', user_id, request.method, path)
    return await handler(request)


def _add_allowed_routes(app, route_tables) -> None:
    for table in route_tables:
        for route in table:
            if (route.method, route.path) in ALLOWED_ROUTES:
                app.router.add_route(route.method, route.path, route.handler, **route.kwargs)


def mount(web_app, application, http_session) -> None:
    """挂到面板下。子应用只匹配 /miniapp/ 开头的路径，不带斜杠的入口要单独跳转。"""
    async def redirect_to_miniapp(request):
        query = f'?{request.query_string}' if request.query_string else ''
        raise web.HTTPFound(f'{MINIAPP_PREFIX}{query}')

    web_app.router.add_get(MINIAPP_PREFIX.rstrip('/'), redirect_to_miniapp)
    web_app.add_subapp(MINIAPP_PREFIX, create_app(application, http_session))


def create_app(application, http_session) -> web.Application:
    app = web.Application(
        middlewares=[error_middleware, security_headers_middleware, auth_middleware],
        client_max_size=1024 * 1024,
    )
    app[keys.BOT_KEY] = application.bot
    app[keys.APPLICATION_KEY] = application
    app[keys.HTTP_SESSION_KEY] = http_session
    app[keys.BROADCAST_TASKS_KEY] = keys.BROADCAST_TASKS
    app.add_routes(routes)
    _add_allowed_routes(app, (api_overview.routes, api_config.routes, api_admin.routes))

    asset_version = compute_asset_version()

    async def index_handler(request):
        html = render_index(asset_version, page='miniapp.html', base=MINIAPP_PREFIX)
        if html is None:
            return http_utils.fail('Mini App 静态资源缺失', status=500)
        return web.Response(text=html, content_type='text/html', headers={'Cache-Control': 'no-cache'})

    app.router.add_get('/', index_handler)
    app.router.add_static(f'{VERSIONED_STATIC_PREFIX}{asset_version}/', STATIC_DIR, name='static_versioned')
    return app
