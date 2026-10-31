"""aiohttp Application 的键定义（避免散落的字符串键与拼写错误）。"""

from aiohttp import web

BOT_KEY = web.AppKey('bot', object)
APPLICATION_KEY = web.AppKey('application', object)
# aiohttp 3.13 起推荐 RequestKey，旧版本没有这个类，用字符串键
MINIAPP_USER_KEY = web.RequestKey('miniapp_user_id', int) if hasattr(web, 'RequestKey') else 'miniapp_user_id'
HTTP_SESSION_KEY = web.AppKey('http_session', object)
BROADCAST_TASKS_KEY = web.AppKey('broadcast_tasks', set)

# 面板和 Mini App 共用，保证两边同时只有一个广播任务
BROADCAST_TASKS = set()
