"""进程内事件总线：机器人侧的消息事件实时推送给 WebSocket 订阅者。

刻意保持零外部依赖（不 import aiohttp），方便在 handlers 里安全引用。
"""

import asyncio
import logging
import time

logger = logging.getLogger(__name__)

_subscribers: set = set()


def subscribe(maxsize: int = 200) -> asyncio.Queue:
    queue: asyncio.Queue = asyncio.Queue(maxsize=maxsize)
    _subscribers.add(queue)
    return queue


def unsubscribe(queue) -> None:
    _subscribers.discard(queue)


def subscriber_count() -> int:
    return len(_subscribers)


def publish(event_type: str, **payload) -> None:
    """向所有订阅者广播事件；队列满时丢弃最旧的，保证实时性。"""
    if not _subscribers:
        return
    event = {'type': event_type, 'ts': time.time(), **payload}
    for queue in list(_subscribers):
        try:
            queue.put_nowait(event)
        except asyncio.QueueFull:
            try:
                queue.get_nowait()
            except asyncio.QueueEmpty:
                pass
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                pass
