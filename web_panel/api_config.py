"""配置中心：环境变量类配置的读取、覆盖（热生效）与重置。

覆盖层说明：修改写入 settings 表（key 即环境变量名），启动时由
config.load_overrides() 重新加载；运行中通过 config.apply_override()
即时生效。带「需重启」标记的项在重启进程后才真正生效。
"""

import logging
import os

from aiohttp import web

import config_meta
from config import config
from database import models as db
from . import http_utils

logger = logging.getLogger(__name__)

routes = web.RouteTableDef()

# 修改这些键后需要清空 AI 客户端缓存，让新密钥/地址立即生效
AI_CLIENT_KEYS = {'GEMINI_API_KEY', 'GEMINI_BASE_URL', 'OPENAI_API_KEY', 'OPENAI_BASE_URL'}
PASSWORD_CONFIG_KEYS = {'WEB_PANEL_PASSWORD', 'WEB_PANEL_PASSWORD_SHA256'}


def _mask(value: str) -> str:
    value = str(value or '')
    if not value:
        return ''
    if len(value) <= 8:
        return '•' * len(value)
    return f"{value[:4]}{'•' * 6}{value[-4:]}"


def _serialize(value) -> str:
    if isinstance(value, bool):
        return 'true' if value else 'false'
    if isinstance(value, (list, tuple)):
        return ','.join(str(item) for item in value)
    return '' if value is None else str(value)


def _item_state(meta: dict) -> dict:
    key = meta['key']
    current = getattr(config, key, '')
    overridden = config.has_override(key)
    if overridden:
        source = 'override'
    elif os.environ.get(key) not in (None, ''):
        source = 'env'
    else:
        source = 'default'

    secret = bool(meta.get('secret'))
    serialized = _serialize(current)
    item = {
        'key': key,
        'type': meta['type'],
        'category': meta['category'],
        'category_label': config_meta.CATEGORY_LABELS.get(meta['category'], meta['category']),
        'label': meta['label'],
        'hint': meta['hint'],
        'restart': bool(meta.get('restart')),
        'secret': secret,
        'options': list(meta.get('options', [])),
        'source': source,
        'has_value': bool(serialized),
        'value': _mask(serialized) if secret else serialized,
        'value_masked': secret and bool(serialized),
        'default': meta.get('default', ''),
    }
    if overridden:
        original = _serialize(config.get_env_value(key))
        item['original_value'] = _mask(original) if secret else original
    return item


def _clear_ai_cache_if_needed(key: str) -> None:
    if key in AI_CLIENT_KEYS:
        from services import ai_service

        ai_service.close_clients()


@routes.get('/api/config')
async def get_config(request):
    return http_utils.ok({
        'categories': [{'key': key, 'label': label} for key, label in config_meta.CATEGORIES],
        'items': [_item_state(meta) for meta in config_meta.META],
    })


@routes.put('/api/config/{key}')
async def update_config(request):
    key = request.match_info['key']
    if key in PASSWORD_CONFIG_KEYS:
        return http_utils.fail('请使用配置中心的「修改面板密码」表单更改密码')
    meta = config_meta.META_BY_KEY.get(key)
    if not meta:
        return http_utils.fail('未知配置项', status=404)

    data = await http_utils.read_json(request)
    if 'value' not in data:
        return http_utils.fail('缺少 value 字段')
    raw = data['value']
    if isinstance(raw, bool):
        raw = 'true' if raw else 'false'
    raw = '' if raw is None else str(raw)

    try:
        config.apply_override(key, raw)
    except (ValueError, TypeError) as exc:
        return http_utils.fail(str(exc))

    await db.set_setting(key, raw, f'Web 面板覆盖: {meta["label"]}')
    _clear_ai_cache_if_needed(key)
    http_utils.audit(request, 'config.update', f'{key}（敏感值不记录内容）' if meta.get('secret') else f'{key}={raw}')
    return http_utils.ok(_item_state(meta))


@routes.delete('/api/config/{key}')
async def reset_config(request):
    key = request.match_info['key']
    if key in PASSWORD_CONFIG_KEYS:
        return http_utils.fail('请使用配置中心的「修改面板密码」表单管理密码')
    meta = config_meta.META_BY_KEY.get(key)
    if not meta:
        return http_utils.fail('未知配置项', status=404)

    config.clear_override(key)
    await db.delete_setting(key)
    _clear_ai_cache_if_needed(key)
    http_utils.audit(request, 'config.reset', key)
    return http_utils.ok(_item_state(meta))
