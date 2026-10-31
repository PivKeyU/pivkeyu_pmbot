"""Web 管理面板：内嵌 aiohttp 服务 + 零依赖静态 SPA。

生命周期（与 rss / web_monitor 的 setup(app) 模式一致）：
- main() 里调用 setup(app) 占位；
- post_init 里 await start(app) 启动监听；
- post_shutdown 里 await stop(app) 释放端口与 HTTP 会话。
"""

import logging

import aiohttp
from aiohttp import web

from config import config

logger = logging.getLogger(__name__)

RUNNER_KEY = 'web_panel_runner'
HTTP_SESSION_KEY = 'web_panel_http_session'


def setup(app) -> None:
    """在 main() 中调用，保持与 web_monitor.setup 一致的模式。"""
    app.bot_data.setdefault('web_panel_enabled', True)


def _mount_miniapp(app, web_app, http_session) -> None:
    if not config.MINIAPP_PUBLIC_URL:
        return
    if not config.MINIAPP_PUBLIC_URL.startswith('https://'):
        logger.warning('MINIAPP_PUBLIC_URL 必须以 https:// 开头，Mini App 不会启动。')
        return

    from .miniapp import create_app as create_miniapp
    from .server import MINIAPP_PREFIX

    web_app.add_subapp(MINIAPP_PREFIX, create_miniapp(app, http_session))
    logger.info('Mini App 已挂载到面板路径 %s', MINIAPP_PREFIX)


async def start(app) -> None:
    if not config.WEB_PANEL_ENABLED:
        logger.info('Web 面板未启用（WEB_PANEL_ENABLED=false）')
        if config.MINIAPP_PUBLIC_URL:
            logger.warning('Mini App 挂在 Web 面板下，面板未启用时不会启动。')
        return

    from . import auth
    from .server import create_app

    auth.begin_setup_window()

    if not auth.password_configured():
        logger.warning(
            'Web 面板未设置密码，进入首次初始化模式：%d 分钟内打开面板即可设置密码并自动登录；'
            '超时未设置面板将锁定（也可在 .env 预先设置 WEB_PANEL_PASSWORD 后重启）。',
            auth.SETUP_WINDOW_SECONDS // 60,
        )

    http_session = aiohttp.ClientSession()
    web_app = create_app(app.bot, http_session)
    _mount_miniapp(app, web_app, http_session)
    runner = web.AppRunner(web_app, access_log=None)
    await runner.setup()
    site = web.TCPSite(runner, config.WEB_PANEL_HOST, config.WEB_PANEL_PORT)
    try:
        await site.start()
    except OSError as exc:
        logger.error('Web 面板启动失败（端口被占用？）: %s', exc)
        await runner.cleanup()
        await http_session.close()
        return

    app.bot_data[RUNNER_KEY] = runner
    app.bot_data[HTTP_SESSION_KEY] = http_session
    logger.info('Web 面板已启动: http://%s:%s', config.WEB_PANEL_HOST, config.WEB_PANEL_PORT)


async def stop(app) -> None:
    runner = app.bot_data.pop(RUNNER_KEY, None)
    if runner is not None:
        await runner.cleanup()
        logger.info('Web 面板已停止')

    http_session = app.bot_data.pop(HTTP_SESSION_KEY, None)
    if http_session is not None and not http_session.closed:
        await http_session.close()
