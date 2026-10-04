"""aiohttp 应用：中间件（错误兜底、安全响应头、认证与 CSRF）与静态资源托管。"""

import logging
from pathlib import Path

from aiohttp import web

from . import api_admin, api_auth, api_config, api_conversations, api_overview, auth, http_utils, keys, ws

logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / 'static'
PANEL_HEADER = 'X-Requested-With'
PANEL_HEADER_VALUE = 'PMBotPanel'
# 自守卫的公开接口：登录自有限速，/api/me 只读，/api/setup 只在初始化窗口内可用
PUBLIC_API_PATHS = {'/api/login', '/api/me', '/api/setup'}
WRITE_METHODS = {'POST', 'PUT', 'PATCH', 'DELETE'}

CONTENT_SECURITY_POLICY = (
    "default-src 'self'; img-src 'self' data:; media-src 'self'; "
    "connect-src 'self' ws: wss:; style-src 'self'; script-src 'self'; "
    "frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
)


@web.middleware
async def error_middleware(request, handler):
    try:
        return await handler(request)
    except web.HTTPException as exc:
        # API 一律回 JSON，避免前端解析 HTML 报错页
        if request.path.startswith('/api/'):
            return http_utils.fail(exc.reason or '请求失败', status=exc.status)
        raise
    except (ValueError, KeyError) as exc:
        return http_utils.fail(str(exc) or '请求参数有误', status=400)
    except Exception:
        logger.exception('Web 面板处理请求出错: %s %s', request.method, request.path)
        return http_utils.fail('服务器内部错误，请查看日志', status=500)


@web.middleware
async def security_headers_middleware(request, handler):
    response = await handler(request)
    response.headers.setdefault('X-Content-Type-Options', 'nosniff')
    response.headers.setdefault('X-Frame-Options', 'DENY')
    response.headers.setdefault('Referrer-Policy', 'no-referrer')
    response.headers.setdefault('Content-Security-Policy', CONTENT_SECURITY_POLICY)
    return response


@web.middleware
async def auth_middleware(request, handler):
    path = request.path
    if (
        path == '/api/setup'
        and request.method in WRITE_METHODS
        and request.headers.get(PANEL_HEADER) != PANEL_HEADER_VALUE
    ):
        return http_utils.fail('非法请求来源', status=403)

    protected = path == '/ws' or (path.startswith('/api/') and path not in PUBLIC_API_PATHS)
    if not protected:
        return await handler(request)

    if not auth.password_configured():
        # 未初始化：除自守卫的公开接口外一律拒绝，并告知前端当前状态
        # （setup_required=初始化窗口内；panel_locked=窗口已关闭，需 .env 设置后重启）
        if auth.setup_mode_active():
            return http_utils.fail('面板尚未初始化，请先设置密码', status=403, code='setup_required')
        return http_utils.fail(
            '面板已锁定：初始化窗口已关闭，请在 .env 设置 WEB_PANEL_PASSWORD 后重启进程',
            status=403,
            code='panel_locked',
        )

    token = request.cookies.get(auth.SESSION_COOKIE, '')
    if not auth.validate_session(token):
        return http_utils.fail('未登录或会话已过期', status=401)

    # CSRF：写操作必须带自定义头（跨站表单/链接无法携带）
    if request.method in WRITE_METHODS and request.headers.get(PANEL_HEADER) != PANEL_HEADER_VALUE:
        return http_utils.fail('非法请求来源', status=403)
    return await handler(request)


async def index_handler(request):
    index_path = STATIC_DIR / 'index.html'
    if not index_path.exists():
        return http_utils.fail('面板静态资源缺失', status=500)
    return web.FileResponse(index_path, headers={'Cache-Control': 'no-cache'})


def create_app(bot, http_session) -> web.Application:
    app = web.Application(
        middlewares=[error_middleware, security_headers_middleware, auth_middleware],
        client_max_size=1024 * 1024,
    )
    app[keys.BOT_KEY] = bot
    app[keys.HTTP_SESSION_KEY] = http_session
    app[keys.BROADCAST_TASKS_KEY] = set()
    app.add_routes(api_auth.routes)
    app.add_routes(api_overview.routes)
    app.add_routes(api_config.routes)
    app.add_routes(api_conversations.routes)
    app.add_routes(api_admin.routes)
    app.add_routes(ws.routes)
    app.router.add_get('/', index_handler)
    app.router.add_static('/static/', STATIC_DIR, name='static')
    return app
