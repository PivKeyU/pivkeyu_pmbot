"""aiohttp Application 的键定义（避免散落的字符串键与拼写错误）。"""

from aiohttp import web

BOT_KEY = web.AppKey('bot', object)
HTTP_SESSION_KEY = web.AppKey('http_session', object)
BROADCAST_TASKS_KEY = web.AppKey('broadcast_tasks', set)
