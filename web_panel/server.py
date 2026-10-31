"""aiohttp 应用：中间件（错误兜底、安全响应头、认证与 CSRF）与静态资源托管。"""

import hashlib
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
MINIAPP_PREFIX = '/miniapp/'
MINIAPP_API_PREFIX = f'{MINIAPP_PREFIX}api/'

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
        if request.path.startswith(('/api/', MINIAPP_API_PREFIX)):
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
    # 网页版 Telegram 用 iframe 嵌入 Mini App，它的 CSP 已限定只允许 Telegram 嵌入
    if not request.path.startswith(MINIAPP_PREFIX):
        response.headers.setdefault('X-Frame-Options', 'DENY')
    response.headers.setdefault('Referrer-Policy', 'no-referrer')
    response.headers.setdefault('Content-Security-Policy', CONTENT_SECURITY_POLICY)
    if request.path.startswith(VERSIONED_STATIC_PREFIX):
        # 带版本号的路径内容永不变化：版本号随文件内容变，更新后自然换成新地址
        response.headers.setdefault('Cache-Control', 'public, max-age=31536000, immutable')
    elif request.path.startswith('/static/'):
        response.headers.setdefault('Cache-Control', 'no-cache')
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


VERSIONED_STATIC_PREFIX = '/static/v-'


def compute_asset_version(static_dir: Path = STATIC_DIR) -> str:
    """按静态文件内容算版本号：任何 CSS / JS 改动都会得到新版本号。"""
    digest = hashlib.sha1()
    for path in sorted(static_dir.rglob('*')):
        if path.is_file() and path.name != 'index.html':
            digest.update(str(path.relative_to(static_dir)).encode('utf-8'))
            digest.update(path.read_bytes())
    return digest.hexdigest()[:10]


def render_index(version: str, static_dir: Path = STATIC_DIR, page: str = 'index.html', base: str = '/'):
    """把入口页里的 /static/ 换成带版本号的路径。JS 模块之间用相对路径互相引用，
    所以入口带上版本号后，整棵模块树都会从新地址加载，浏览器不可能混用新旧文件。"""
    index_path = static_dir / page
    if not index_path.exists():
        return None
    html = index_path.read_text(encoding='utf-8')
    return html.replace('"/static/', f'"{base.rstrip("/")}{VERSIONED_STATIC_PREFIX}{version}/')


def make_index_handler(version: str):
    async def index_handler(request):
        html = render_index(version)
        if html is None:
            return http_utils.fail('面板静态资源缺失', status=500)
        return web.Response(text=html, content_type='text/html', headers={'Cache-Control': 'no-cache'})
    return index_handler


def create_app(bot, http_session, application=None) -> web.Application:
    app = web.Application(
        middlewares=[error_middleware, security_headers_middleware, auth_middleware],
        client_max_size=1024 * 1024,
    )
    app[keys.BOT_KEY] = bot
    if application is not None:
        app[keys.APPLICATION_KEY] = application
    app[keys.HTTP_SESSION_KEY] = http_session
    app[keys.BROADCAST_TASKS_KEY] = keys.BROADCAST_TASKS
    app.add_routes(api_auth.routes)
    app.add_routes(api_overview.routes)
    app.add_routes(api_config.routes)
    app.add_routes(api_conversations.routes)
    app.add_routes(api_admin.routes)
    app.add_routes(ws.routes)
    asset_version = compute_asset_version()
    app.router.add_get('/', make_index_handler(asset_version))
    app.router.add_static(f'{VERSIONED_STATIC_PREFIX}{asset_version}/', STATIC_DIR, name='static_versioned')
    app.router.add_static('/static/', STATIC_DIR, name='static')
    return app
