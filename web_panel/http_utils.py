"""Web 面板的 HTTP 小工具：统一响应格式、参数解析与审计日志。

响应约定：成功 ``{"ok": true, "data": ...}``，失败 ``{"ok": false, "error": "..."}``。
"""

import logging

from aiohttp import web

logger = logging.getLogger(__name__)


def ok(data=None, **extra):
    payload = {'ok': True}
    if data is not None:
        payload['data'] = data
    payload.update(extra)
    return web.json_response(payload)


def fail(message, status=400, **extra):
    return web.json_response({'ok': False, 'error': str(message), **extra}, status=status)


async def read_json(request) -> dict:
    try:
        data = await request.json()
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def get_int(request, name, default=None, minimum=None, maximum=None):
    raw = request.query.get(name)
    if raw in (None, ''):
        return default
    try:
        value = int(raw)
    except (TypeError, ValueError):
        raise ValueError(f'参数 {name} 需要是整数')
    if minimum is not None and value < minimum:
        value = minimum
    if maximum is not None and value > maximum:
        value = maximum
    return value


def pagination(request, default_per=20, max_per=100):
    page = get_int(request, 'page', 1, minimum=1) or 1
    per_page = get_int(request, 'per_page', default_per, minimum=1, maximum=max_per) or default_per
    return page, per_page, (page - 1) * per_page


def page_payload(items, total, page, per_page):
    total_pages = max(1, (total + per_page - 1) // per_page)
    return {
        'items': items,
        'total': total,
        'page': page,
        'per_page': per_page,
        'total_pages': total_pages,
    }


def audit(request, action, detail=''):
    """记录改状态操作。密钥类值不要放进 detail。"""
    from . import auth

    logger.info('Web面板操作 %s ip=%s %s', action, auth.client_ip(request), detail)
