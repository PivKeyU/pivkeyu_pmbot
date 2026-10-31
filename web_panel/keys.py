"""aiohttp Application 的键定义（避免散落的字符串键与拼写错误）。"""

from aiohttp import web

BOT_KEY = web.AppKey('bot', object)
APPLICATION_KEY = web.AppKey('application', object)
HTTP_SESSION_KEY = web.AppKey('http_session', object)
BROADCAST_TASKS_KEY = web.AppKey('broadcast_tasks', set)

# 面板和 Mini App 共用，保证两边同时只有一个广播任务
BROADCAST_TASKS = set()
