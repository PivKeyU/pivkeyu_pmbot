"""WebSocket 通道：把机器人侧的新消息事件实时推给面板页面。"""

import asyncio
import contextlib
import logging
import time
from urllib.parse import urlparse

from aiohttp import WSMsgType, web

from . import auth, events, http_utils

logger = logging.getLogger(__name__)

routes = web.RouteTableDef()

MAX_WS_CLIENTS = 100
_active_sockets: dict[web.WebSocketResponse, str] = {}


def _origin_allowed(request) -> bool:
    """防跨站 WebSocket 劫持：Origin 非空时校验 scheme 与 netloc 同源；浏览器无 Origin 拒绝。"""
    origin = request.headers.get('Origin')
    if not origin:
        # 浏览器发起跨站或常规 WS 必带 Origin；仅当明确是浏览器 Sec-Fetch 且缺少 Origin 时拒绝
        if request.headers.get('Sec-Fetch-Mode') == 'websocket':
            return False
        return True
    host = request.headers.get('Host') or ''
    expected_scheme = 'https' if auth.is_https_request(request) else 'http'
    try:
        parsed = urlparse(origin)
        return parsed.netloc == host and parsed.scheme.lower() == expected_scheme
    except ValueError:
        return False


async def close_session_websockets(token: str | None = None, *, keep_token: str | None = None) -> None:
    """注销或修改密码后立即断开已撤销会话对应的 WebSocket 连接。"""
    targets = []
    for socket_obj, sock_token in list(_active_sockets.items()):
        if token is not None and sock_token == token:
            targets.append(socket_obj)
        elif keep_token is not None and sock_token != keep_token:
            targets.append(socket_obj)
    for socket_obj in targets:
        if not socket_obj.closed:
            with contextlib.suppress(Exception):
                await socket_obj.close(code=4001, message=b'session revoked')


async def close_all_websockets() -> None:
    for socket_obj in list(_active_sockets.keys()):
        if not socket_obj.closed:
            with contextlib.suppress(Exception):
                await socket_obj.close(code=1001, message=b'server shutdown')
    _active_sockets.clear()


@routes.get('/ws')
async def websocket_handler(request):
    if not _origin_allowed(request):
        return http_utils.fail('非法来源', status=403)
    if len(_active_sockets) >= MAX_WS_CLIENTS:
        return http_utils.fail('WebSocket 连接数已达上限', status=503)

    token = request.cookies.get(auth.SESSION_COOKIE, '')
    ws = web.WebSocketResponse(heartbeat=30, max_msg_size=64 * 1024)
    await ws.prepare(request)

    _active_sockets[ws] = token
    queue = events.subscribe()
    pump = asyncio.ensure_future(_pump_events(ws, queue, token))
    try:
        await ws.send_json({'type': 'hello', 'ts': time.time()})
        async for message in ws:
            if not auth.validate_session(token):
                await ws.close(code=4001, message=b'session expired')
                break
            if message.type == WSMsgType.TEXT and message.data == 'ping':
                await ws.send_json({'type': 'pong', 'ts': time.time()})
            elif message.type == WSMsgType.ERROR:
                break
    finally:
        _active_sockets.pop(ws, None)
        pump.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await pump
        events.unsubscribe(queue)
    return ws


async def _pump_events(ws, queue, token: str) -> None:
    try:
        while True:
            event = await queue.get()
            if not auth.validate_session(token):
                with contextlib.suppress(Exception):
                    await ws.close(code=4001, message=b'session expired')
                return
            await ws.send_json(event)
    except asyncio.CancelledError:
        pass
    except (ConnectionResetError, RuntimeError):
        with contextlib.suppress(Exception):
            if not ws.closed:
                await ws.close()
    except Exception:
        logger.debug('WebSocket 推送中断', exc_info=True)
        with contextlib.suppress(Exception):
            if not ws.closed:
                await ws.close()
