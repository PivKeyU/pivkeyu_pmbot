"""WebSocket 通道：把机器人侧的新消息事件实时推给面板页面。"""

import asyncio
import logging
import time
from urllib.parse import urlparse

from aiohttp import WSMsgType, web

from . import events, http_utils

logger = logging.getLogger(__name__)

routes = web.RouteTableDef()


def _origin_allowed(request) -> bool:
    """防跨站 WebSocket 劫持：Origin 非空时必须与 Host 同源。"""
    origin = request.headers.get('Origin')
    if not origin:
        return True
    host = request.headers.get('Host') or ''
    try:
        return urlparse(origin).netloc == host
    except ValueError:
        return False


@routes.get('/ws')
async def websocket_handler(request):
    if not _origin_allowed(request):
        return http_utils.fail('非法来源', status=403)

    ws = web.WebSocketResponse(heartbeat=30, max_msg_size=64 * 1024)
    await ws.prepare(request)

    queue = events.subscribe()
    pump = asyncio.ensure_future(_pump_events(ws, queue))
    try:
        await ws.send_json({'type': 'hello', 'ts': time.time()})
        async for message in ws:
            if message.type == WSMsgType.TEXT and message.data == 'ping':
                await ws.send_json({'type': 'pong', 'ts': time.time()})
            elif message.type == WSMsgType.ERROR:
                break
    finally:
        pump.cancel()
        events.unsubscribe(queue)
    return ws


async def _pump_events(ws, queue) -> None:
    try:
        while True:
            event = await queue.get()
            await ws.send_json(event)
    except asyncio.CancelledError:
        pass
    except (ConnectionResetError, RuntimeError):
        pass
    except Exception:
        logger.debug('WebSocket 推送中断', exc_info=True)
