"""仪表盘：统计概览与定时任务运行状态。"""

from aiohttp import web

from config import config
from database import models as db
from . import http_utils

routes = web.RouteTableDef()


@routes.get('/api/overview')
async def overview(request):
    data = {
        'bot': {
            'id': config.BOT_ID,
            'username': config.BOT_USERNAME,
            'forum_group_id': config.FORUM_GROUP_ID,
        },
        'stats': {
            'total_users': await db.get_total_users_count(),
            'verified_users': await db.get_verified_users_count(),
            'blocked_users': await db.get_blocked_users_count(),
            'filtered_messages': await db.get_filtered_messages_count(),
            'pending_topics': await db.count_pending_reply_topics(),
            'exemptions': await db.get_exemptions_count(),
            'messages': await db.get_message_stats(),
        },
        'runtime_status': await db.get_runtime_statuses(limit=50),
    }
    return http_utils.ok(data)


@routes.get('/api/runtime-status')
async def runtime_status(request):
    category = (request.query.get('category') or '').strip() or None
    limit = http_utils.get_int(request, 'limit', 100, minimum=1, maximum=200) or 100
    return http_utils.ok({'items': await db.get_runtime_statuses(category=category, limit=limit)})
