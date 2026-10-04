"""管理功能：/panel 各模块的 Web 迁移。

所有业务逻辑尽量复用现有函数（database/models.py、services/*、rss/*），
这里只做参数校验、调用与 JSON 序列化。
"""

import asyncio
import dataclasses
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

from aiohttp import web

from config import config
from database import models as db
from rss import data_manager as rss_data
from rss import settings as rss_settings
from rss.handlers import ensure_user_data, is_valid_url
from services import broadcast as broadcast_service
from services import image_update, safe_update
from services.ai_service import AUTOREPLY_PERSONALITY_MAX_CHARS, ai_service
from . import events, http_utils, keys

logger = logging.getLogger(__name__)

routes = web.RouteTableDef()

REPO_DIR = Path(__file__).resolve().parent.parent

AI_MODEL_KEYS = (
    'gemini_model_filter',
    'gemini_model_verification',
    'gemini_model_autoreply',
    'openai_model_filter',
    'openai_model_verification',
    'openai_model_autoreply',
)

TG_MONITOR_EDITABLE = {
    'name', 'chat_id', 'chat_title', 'listen_source', 'keywords', 'exclude_keywords',
    'enabled', 'notify_telegram', 'min_interval_seconds', 'dedupe_window_seconds',
}

WEB_MONITOR_EDITABLE = {
    'name', 'url', 'keywords', 'item_selector', 'title_selector', 'link_selector',
    'price_selector', 'stock_selector', 'enabled', 'interval_seconds', 'notify_telegram',
    'notify_on_keyword', 'notify_on_new_item', 'notify_on_change',
}


def _parse_keywords(raw) -> list:
    """把逗号/换行分隔的字符串或数组统一成去重列表。"""
    if isinstance(raw, (list, tuple)):
        items = list(raw)
    else:
        items = str(raw or '').replace('\n', ',').split(',')
    result = []
    for item in items:
        item = str(item).strip()
        if item and item not in result:
            result.append(item)
    return result


def _require_int(data: dict, key: str) -> int:
    if key not in data or data[key] in (None, ''):
        raise ValueError(f'缺少参数 {key}')
    try:
        return int(data[key])
    except (TypeError, ValueError):
        raise ValueError(f'参数 {key} 需要是整数')


def _pick(data: dict, allowed: set) -> dict:
    """只保留允许编辑的字段，并把列表类字段规范化。"""
    result = {}
    for key in allowed:
        if key not in data:
            continue
        value = data[key]
        if key in ('keywords', 'exclude_keywords'):
            value = _parse_keywords(value)
        result[key] = value
    return result


def _user_display(user: dict) -> str:
    name = user.get('first_name') or user.get('username') or str(user.get('user_id'))
    return name


# --------------------------------------------------------------------------- #
# 用户 / 黑名单 / 通行证 / 拦截篮
# --------------------------------------------------------------------------- #

@routes.get('/api/users')
async def list_users(request):
    page, per_page, offset = http_utils.pagination(request)
    items = await db.get_all_users_paginated(limit=per_page, offset=offset)
    total = await db.get_total_users_count()
    return http_utils.ok(http_utils.page_payload(items, total, page, per_page))


@routes.get('/api/blacklist')
async def list_blacklist(request):
    page, per_page, offset = http_utils.pagination(request)
    items = await db.get_blacklist_paginated(limit=per_page, offset=offset)
    total = await db.get_blacklist_count()
    return http_utils.ok(http_utils.page_payload(items, total, page, per_page))


@routes.post('/api/blacklist')
async def add_blacklist(request):
    from services import blacklist as blacklist_service

    data = await http_utils.read_json(request)
    user_id = _require_int(data, 'user_id')
    reason = str(data.get('reason') or '').strip() or '由 Web 面板封禁'
    permanent = bool(data.get('permanent'))
    message = await blacklist_service.block_user(user_id, reason, 0, permanent)
    events.publish('user_status', user_id=user_id, blacklisted=True)
    http_utils.audit(request, 'blacklist.add', f'user_id={user_id} permanent={permanent}')
    return http_utils.ok({'message': message})


@routes.delete('/api/blacklist/{user_id}')
async def remove_blacklist(request):
    from services import blacklist as blacklist_service

    user_id = int(request.match_info['user_id'])
    message = await blacklist_service.unblock_user(user_id)
    events.publish('user_status', user_id=user_id, blacklisted=False)
    http_utils.audit(request, 'blacklist.remove', f'user_id={user_id}')
    return http_utils.ok({'message': message})


@routes.get('/api/exemptions')
async def list_exemptions(request):
    page, per_page, offset = http_utils.pagination(request)
    items = await db.get_exemptions_paginated(limit=per_page, offset=offset)
    total = await db.get_exemptions_count()
    return http_utils.ok(http_utils.page_payload(items, total, page, per_page))


@routes.post('/api/exemptions')
async def add_exemption(request):
    data = await http_utils.read_json(request)
    user_id = _require_int(data, 'user_id')
    permanent = bool(data.get('permanent'))
    reason = str(data.get('reason') or '').strip() or None
    expires_at = None
    if not permanent:
        try:
            days = int(data.get('days') or 7)
        except (TypeError, ValueError):
            raise ValueError('有效天数需要是整数')
        if days < 1 or days > 3650:
            raise ValueError('有效天数需在 1-3650 之间')
        expires_at = (datetime.now(timezone.utc) + timedelta(days=days)).strftime('%Y-%m-%d %H:%M:%S')

    await db.add_exemption(user_id, permanent, 0, reason, expires_at)
    http_utils.audit(request, 'exemption.add', f'user_id={user_id} permanent={permanent} expires_at={expires_at}')
    return http_utils.ok()


@routes.delete('/api/exemptions/{user_id}')
async def remove_exemption(request):
    user_id = int(request.match_info['user_id'])
    await db.remove_exemption(user_id)
    http_utils.audit(request, 'exemption.remove', f'user_id={user_id}')
    return http_utils.ok()


@routes.get('/api/filtered')
async def list_filtered(request):
    page, per_page, offset = http_utils.pagination(request)
    items = await db.get_filtered_messages(limit=per_page, offset=offset)
    total = await db.get_filtered_messages_count()
    return http_utils.ok(http_utils.page_payload(items, total, page, per_page))


@routes.delete('/api/filtered/{message_id}')
async def delete_filtered(request):
    message_id = int(request.match_info['message_id'])
    deleted = await db.delete_filtered_message(message_id)
    if not deleted:
        return http_utils.fail('记录不存在', status=404)
    http_utils.audit(request, 'filtered.delete', f'id={message_id}')
    return http_utils.ok()


@routes.delete('/api/filtered')
async def clear_filtered(request):
    count = await db.clear_filtered_messages()
    http_utils.audit(request, 'filtered.clear', f'删除 {count} 条')
    return http_utils.ok({'deleted': count})


# --------------------------------------------------------------------------- #
# 自动回复 / 知识库 / 关键词
# --------------------------------------------------------------------------- #

@routes.get('/api/autoreply')
async def get_autoreply(request):
    return http_utils.ok({
        'enabled': await db.get_autoreply_enabled(),
        'personality_md': await db.get_setting('autoreply_personality_md', ''),
    })


@routes.put('/api/autoreply')
async def set_autoreply(request):
    data = await http_utils.read_json(request)
    personality_md = None
    if 'personality_md' in data:
        personality_md = data.get('personality_md')
        if not isinstance(personality_md, str):
            return http_utils.fail('人格.md 内容必须是文本')
        if len(personality_md) > AUTOREPLY_PERSONALITY_MAX_CHARS:
            return http_utils.fail(f'人格.md 最多 {AUTOREPLY_PERSONALITY_MAX_CHARS} 个字符')
        if not personality_md.strip():
            personality_md = ''

    if 'enabled' not in data and 'personality_md' not in data:
        return http_utils.fail('没有可更新的自动回复设置')

    if 'enabled' in data:
        enabled = bool(data.get('enabled'))
        await db.set_autoreply_enabled(enabled)
        http_utils.audit(request, 'autoreply.toggle', f'enabled={enabled}')

    if 'personality_md' in data:
        await db.set_setting(
            'autoreply_personality_md', personality_md, 'AI 自动回复人格 Markdown',
        )
        http_utils.audit(request, 'autoreply.personality.update', f'字符数={len(personality_md)}')

    return await get_autoreply(request)


@routes.get('/api/knowledge')
async def list_knowledge(request):
    entries = await db.get_all_knowledge_entries()
    return http_utils.ok({'items': entries})


@routes.post('/api/knowledge')
async def add_knowledge(request):
    data = await http_utils.read_json(request)
    title = str(data.get('title') or '').strip()
    content = str(data.get('content') or '').strip()
    if not title or not content:
        return http_utils.fail('标题与内容都不能为空')
    await db.add_knowledge_entry(title, content)
    http_utils.audit(request, 'knowledge.add', f'title={title}')
    return http_utils.ok()


@routes.put('/api/knowledge/{knowledge_id}')
async def update_knowledge(request):
    knowledge_id = int(request.match_info['knowledge_id'])
    data = await http_utils.read_json(request)
    title = str(data.get('title') or '').strip()
    content = str(data.get('content') or '').strip()
    if not title or not content:
        return http_utils.fail('标题与内容都不能为空')
    if not await db.get_knowledge_entry(knowledge_id):
        return http_utils.fail('知识条目不存在', status=404)
    await db.update_knowledge_entry(knowledge_id, title, content)
    http_utils.audit(request, 'knowledge.update', f'id={knowledge_id} title={title}')
    return http_utils.ok()


@routes.delete('/api/knowledge/{knowledge_id}')
async def delete_knowledge(request):
    knowledge_id = int(request.match_info['knowledge_id'])
    await db.delete_knowledge_entry(knowledge_id)
    http_utils.audit(request, 'knowledge.delete', f'id={knowledge_id}')
    return http_utils.ok()


@routes.get('/api/spam-keywords')
async def get_spam_keywords(request):
    settings = await db.get_spam_keyword_filter_settings()
    return http_utils.ok({
        'enabled': settings['enabled'],
        'auto_block': settings['auto_block'],
        'keywords': settings['keywords'],
    })


@routes.put('/api/spam-keywords/settings')
async def update_spam_settings(request):
    data = await http_utils.read_json(request)
    if 'enabled' in data:
        await db.set_spam_keyword_filter_enabled(bool(data['enabled']))
    if 'auto_block' in data:
        await db.set_spam_keyword_auto_block(bool(data['auto_block']))
    settings = await db.get_spam_keyword_filter_settings()
    http_utils.audit(
        request, 'spam_keywords.settings',
        f"enabled={settings['enabled']} auto_block={settings['auto_block']}",
    )
    return http_utils.ok({'enabled': settings['enabled'], 'auto_block': settings['auto_block']})


@routes.post('/api/spam-keywords')
async def add_spam_keyword(request):
    data = await http_utils.read_json(request)
    keyword = str(data.get('keyword') or '').strip()
    if not keyword:
        return http_utils.fail('关键词不能为空')
    created = await db.add_spam_keyword(keyword)
    if not created:
        return http_utils.fail('该关键词已存在', status=409)
    http_utils.audit(request, 'spam_keywords.add', keyword)
    return http_utils.ok()


@routes.delete('/api/spam-keywords/{keyword}')
async def remove_spam_keyword(request):
    keyword = request.match_info['keyword']
    removed = await db.remove_spam_keyword(keyword)
    if not removed:
        return http_utils.fail('关键词不存在', status=404)
    http_utils.audit(request, 'spam_keywords.remove', keyword)
    return http_utils.ok()


@routes.delete('/api/spam-keywords')
async def clear_spam_keywords(request):
    count = await db.clear_spam_keywords()
    http_utils.audit(request, 'spam_keywords.clear', f'删除 {count} 条')
    return http_utils.ok({'deleted': count})


# --------------------------------------------------------------------------- #
# AI 设置
# --------------------------------------------------------------------------- #

@routes.get('/api/ai-settings')
async def get_ai_settings(request):
    provider = await db.get_setting('ai_provider', 'gemini')
    models = {key: await db.get_setting(key, '') for key in AI_MODEL_KEYS}
    return http_utils.ok({
        'provider': provider,
        'models': models,
        'keys': {
            'gemini_configured': bool(config.GEMINI_API_KEY),
            'openai_configured': bool(config.OPENAI_API_KEY),
            'gemini_base_url': config.GEMINI_BASE_URL or '',
            'openai_base_url': config.OPENAI_BASE_URL or '',
        },
    })


@routes.get('/api/ai-models')
async def list_ai_models(request):
    provider = str(
        request.query.get('provider') or await db.get_setting('ai_provider', 'gemini') or 'gemini'
    ).strip().lower()
    if provider not in ('gemini', 'openai'):
        return http_utils.fail('provider 只允许 gemini 或 openai')

    api_key = config.GEMINI_API_KEY if provider == 'gemini' else config.OPENAI_API_KEY
    if not api_key:
        provider_label = 'Gemini' if provider == 'gemini' else 'OpenAI 兼容服务'
        return http_utils.fail(f'请先在配置中心设置 {provider_label} API Key')

    try:
        models = await ai_service.get_available_models(provider)
    except Exception:
        logger.exception('获取 AI 模型列表失败 provider=%s', provider)
        return http_utils.fail('获取模型列表失败，请检查 API Key 和 Base URL', status=502)
    return http_utils.ok({'provider': provider, 'models': models})


@routes.put('/api/ai-settings')
async def update_ai_settings(request):
    data = await http_utils.read_json(request)
    provider = data.get('provider')
    if provider is not None:
        provider = str(provider).strip().lower()
        if provider not in ('gemini', 'openai'):
            return http_utils.fail('ai_provider 只允许 gemini 或 openai')
        await db.set_setting('ai_provider', provider, '当前使用的AI提供商 (gemini, openai)')

    models = data.get('models') or {}
    if not isinstance(models, dict):
        return http_utils.fail('models 需要是对象')
    for key, value in models.items():
        if key not in AI_MODEL_KEYS:
            return http_utils.fail(f'未知模型配置项: {key}')
        value = str(value or '').strip()
        if not value:
            return http_utils.fail(f'{key} 不能为空')
        await db.set_setting(key, value)

    http_utils.audit(request, 'ai_settings.update', f'provider={provider} 模型数={len(models)}')
    return await get_ai_settings(request)


# --------------------------------------------------------------------------- #
# 用户组 / 广播
# --------------------------------------------------------------------------- #

@routes.get('/api/user-groups')
async def list_user_groups(request):
    return http_utils.ok({'items': await db.get_all_user_groups()})


@routes.post('/api/user-groups')
async def create_user_group(request):
    data = await http_utils.read_json(request)
    name = str(data.get('name') or '').strip()
    description = str(data.get('description') or '').strip() or None
    if not name:
        return http_utils.fail('分组名不能为空')
    if await db.get_user_group_by_name(name):
        return http_utils.fail('同名分组已存在', status=409)
    await db.create_user_group(name, 0, description)
    http_utils.audit(request, 'user_groups.create', name)
    return http_utils.ok()


@routes.delete('/api/user-groups/{name}')
async def delete_user_group(request):
    name = request.match_info['name']
    deleted = await db.delete_user_group(name)
    if not deleted:
        return http_utils.fail('分组不存在', status=404)
    http_utils.audit(request, 'user_groups.delete', name)
    return http_utils.ok()


@routes.get('/api/user-groups/{name}/members')
async def list_group_members(request):
    name = request.match_info['name']
    if not await db.get_user_group_by_name(name):
        return http_utils.fail('分组不存在', status=404)
    members = await db.get_group_members(name, include_blacklisted=True)
    return http_utils.ok({'items': members})


@routes.post('/api/user-groups/{name}/members')
async def add_group_member(request):
    name = request.match_info['name']
    data = await http_utils.read_json(request)
    user_id = _require_int(data, 'user_id')
    if not await db.get_user_group_by_name(name):
        return http_utils.fail('分组不存在', status=404)
    _, _, added = await db.add_user_to_group(name, user_id, 0)
    if not added:
        return http_utils.fail('该用户已在分组中', status=409)
    http_utils.audit(request, 'user_groups.member_add', f'{name} <- {user_id}')
    return http_utils.ok()


@routes.delete('/api/user-groups/{name}/members/{user_id}')
async def remove_group_member(request):
    name = request.match_info['name']
    user_id = int(request.match_info['user_id'])
    removed = await db.remove_user_from_group(name, user_id)
    if not removed:
        return http_utils.fail('成员不存在', status=404)
    http_utils.audit(request, 'user_groups.member_remove', f'{name} -> {user_id}')
    return http_utils.ok()


class _BroadcastContext:
    """send_text_broadcast 只需要 context.bot，用最小替身接入现有服务。"""

    def __init__(self, bot):
        self.bot = bot


async def _run_broadcast(app, recipients, text, scope, group_id) -> None:
    try:
        context = _BroadcastContext(app[keys.BOT_KEY])
        result = await broadcast_service.send_text_broadcast(
            context, recipients, text, admin_id=0, scope=scope, group_id=group_id,
        )
        logger.info(
            'Web 面板广播完成 id=%s 总数=%s 成功=%s 失败=%s',
            result.broadcast_id, result.total, result.success, result.failed,
        )
        events.publish(
            'broadcast_done',
            broadcast_id=result.broadcast_id,
            total=result.total,
            success=result.success,
            failed=result.failed,
        )
    except Exception:
        logger.exception('Web 面板广播执行失败')


@routes.get('/api/broadcasts')
async def list_broadcasts(request):
    page, per_page, offset = http_utils.pagination(request)
    items = await db.list_broadcasts(limit=per_page, offset=offset)
    total = await db.get_broadcasts_count()
    return http_utils.ok(http_utils.page_payload(items, total, page, per_page))


@routes.post('/api/broadcast')
async def start_broadcast(request):
    data = await http_utils.read_json(request)
    text = str(data.get('text') or '').strip()
    if not text:
        return http_utils.fail('广播内容不能为空')
    if len(text) > 4096:
        return http_utils.fail('广播内容超过 4096 字上限')

    group_name = str(data.get('group_name') or '').strip() or None
    group_id = None
    if group_name:
        group = await db.get_user_group_by_name(group_name)
        if not group:
            return http_utils.fail('分组不存在', status=404)
        group_id = group['id']

    _, recipients = await db.get_broadcast_recipients(group_name)
    if not recipients:
        return http_utils.fail('没有可投递的对象（该分组暂无成员或已全部拉黑）')

    tasks = request.app[keys.BROADCAST_TASKS_KEY]
    if tasks:
        return http_utils.fail('已有广播任务进行中，请等待完成', status=409)

    task = asyncio.create_task(_run_broadcast(request.app, recipients, text, 'group' if group_name else 'all', group_id))
    tasks.add(task)

    def _cleanup(finished):
        tasks.discard(finished)
        if finished.cancelled():
            return
        exc = finished.exception()
        if exc:
            logger.error('广播任务异常退出: %s', exc)

    task.add_done_callback(_cleanup)
    http_utils.audit(request, 'broadcast.start', f'scope={"group:" + group_name if group_name else "all"} 人数={len(recipients)}')
    return http_utils.ok({'accepted': True, 'total': len(recipients)})


# --------------------------------------------------------------------------- #
# RSS
# --------------------------------------------------------------------------- #

@routes.get('/api/rss')
async def get_rss(request):
    feeds = []
    for chat_id, user_config in rss_data.get_subscriptions().items():
        for feed_url, feed in (user_config.get('rss_feeds') or {}).items():
            feeds.append({
                'chat_id': chat_id,
                'url': feed_url,
                'title': feed.get('title') or feed_url,
                'keywords': feed.get('keywords') or [],
                'last_entry_id': feed.get('last_entry_id'),
            })
    return http_utils.ok({
        'enabled': rss_settings.is_enabled(),
        'check_interval': rss_settings.get_check_interval(),
        'authorized_users': rss_settings.get_authorized_users(),
        'feeds': feeds,
    })


@routes.put('/api/rss/settings')
async def update_rss_settings(request):
    data = await http_utils.read_json(request)
    if 'enabled' in data:
        rss_settings.set_enabled(bool(data['enabled']))
    if 'check_interval' in data:
        try:
            interval = int(data['check_interval'])
        except (TypeError, ValueError):
            raise ValueError('检查间隔需要是整数秒')
        if interval < 60 or interval > 86400:
            raise ValueError('检查间隔需在 60-86400 秒之间')
        rss_settings.set_check_interval(interval)
    http_utils.audit(
        request, 'rss.settings',
        f'enabled={rss_settings.is_enabled()} interval={rss_settings.get_check_interval()}',
    )
    return http_utils.ok({
        'enabled': rss_settings.is_enabled(),
        'check_interval': rss_settings.get_check_interval(),
    })


@routes.post('/api/rss/feeds')
async def add_rss_feed(request):
    data = await http_utils.read_json(request)
    chat_id = str(data.get('chat_id') or '').strip()
    feed_url = str(data.get('url') or '').strip()
    if not chat_id:
        return http_utils.fail('chat_id 不能为空')
    if not is_valid_url(feed_url):
        return http_utils.fail('订阅链接格式不正确')

    subscriptions = rss_data.get_subscriptions()
    ensure_user_data(chat_id, subscriptions)
    if feed_url in subscriptions[chat_id]['rss_feeds']:
        return http_utils.fail('该订阅已存在', status=409)

    title = await asyncio.to_thread(rss_data.get_feed_title, feed_url) or '未命名茶点'
    subscriptions[chat_id]['rss_feeds'][feed_url] = {
        'title': title,
        'keywords': [],
        'last_entry_id': None,
    }
    rss_data.save_subscriptions(rss_settings.get_data_file())
    http_utils.audit(request, 'rss.feed_add', f'chat={chat_id} url={feed_url}')
    return http_utils.ok({'title': title})


@routes.delete('/api/rss/feeds')
async def remove_rss_feed(request):
    data = await http_utils.read_json(request)
    chat_id = str(data.get('chat_id') or '').strip()
    feed_url = str(data.get('url') or '').strip()
    removed = rss_data.remove_feed(chat_id, feed_url, rss_settings.get_data_file())
    if not removed:
        return http_utils.fail('订阅不存在', status=404)
    http_utils.audit(request, 'rss.feed_remove', f'chat={chat_id} url={feed_url}')
    return http_utils.ok()


@routes.post('/api/rss/keywords')
async def add_rss_keyword(request):
    data = await http_utils.read_json(request)
    chat_id = str(data.get('chat_id') or '').strip()
    feed_url = str(data.get('url') or '').strip()
    keyword = str(data.get('keyword') or '').strip()
    if not keyword:
        return http_utils.fail('关键词不能为空')

    subscriptions = rss_data.get_subscriptions()
    feed = (subscriptions.get(chat_id, {}).get('rss_feeds') or {}).get(feed_url)
    if not feed:
        return http_utils.fail('订阅不存在', status=404)
    keywords = feed.setdefault('keywords', [])
    if keyword in keywords:
        return http_utils.fail('关键词已存在', status=409)
    keywords.append(keyword)
    rss_data.save_subscriptions(rss_settings.get_data_file())
    http_utils.audit(request, 'rss.keyword_add', f'{chat_id} {feed_url} <- {keyword}')
    return http_utils.ok()


@routes.delete('/api/rss/keywords')
async def remove_rss_keyword(request):
    data = await http_utils.read_json(request)
    chat_id = str(data.get('chat_id') or '').strip()
    feed_url = str(data.get('url') or '').strip()
    keyword = str(data.get('keyword') or '').strip()
    removed = rss_data.remove_keyword(chat_id, feed_url, keyword, rss_settings.get_data_file())
    if not removed:
        return http_utils.fail('关键词不存在', status=404)
    http_utils.audit(request, 'rss.keyword_remove', f'{chat_id} {feed_url} -> {keyword}')
    return http_utils.ok()


@routes.post('/api/rss/authorized')
async def add_rss_authorized(request):
    data = await http_utils.read_json(request)
    user_id = _require_int(data, 'user_id')
    rss_settings.add_authorized_user(user_id)
    http_utils.audit(request, 'rss.authorized_add', f'user_id={user_id}')
    return http_utils.ok({'items': rss_settings.get_authorized_users()})


@routes.delete('/api/rss/authorized/{user_id}')
async def remove_rss_authorized(request):
    user_id = int(request.match_info['user_id'])
    rss_settings.remove_authorized_user(user_id)
    http_utils.audit(request, 'rss.authorized_remove', f'user_id={user_id}')
    return http_utils.ok({'items': rss_settings.get_authorized_users()})


# --------------------------------------------------------------------------- #
# TG 监控 / 网页监控
# --------------------------------------------------------------------------- #

@routes.get('/api/tg-monitors')
async def list_tg_monitors(request):
    return http_utils.ok({'items': await db.list_tg_group_monitors()})


@routes.post('/api/tg-monitors')
async def create_tg_monitor(request):
    data = await http_utils.read_json(request)
    name = str(data.get('name') or '').strip()
    if not name:
        return http_utils.fail('监控名不能为空')
    if await db.get_tg_group_monitor_by_name(name):
        return http_utils.fail('同名监控已存在', status=409)
    monitor_id = await db.create_tg_group_monitor(
        name=name,
        chat_id=_require_int(data, 'chat_id'),
        keywords=_parse_keywords(data.get('keywords')),
        created_by=0,
        chat_title=str(data.get('chat_title') or '').strip() or None,
        listen_source=str(data.get('listen_source') or 'user_session').strip().lower(),
        exclude_keywords=_parse_keywords(data.get('exclude_keywords')),
        min_interval_seconds=int(data.get('min_interval_seconds') or 30),
        dedupe_window_seconds=int(data.get('dedupe_window_seconds') or 300),
    )
    http_utils.audit(request, 'tg_monitor.create', f'{name} chat_id={data.get("chat_id")}')
    return http_utils.ok({'id': monitor_id})


@routes.put('/api/tg-monitors/{monitor_id}')
async def update_tg_monitor(request):
    monitor_id = int(request.match_info['monitor_id'])
    if not await db.get_tg_group_monitor(monitor_id):
        return http_utils.fail('监控不存在', status=404)
    data = await http_utils.read_json(request)
    updates = _pick(data, TG_MONITOR_EDITABLE)
    if not updates:
        return http_utils.fail('没有可更新的字段')
    await db.update_tg_group_monitor(monitor_id, **updates)
    http_utils.audit(request, 'tg_monitor.update', f'id={monitor_id} 字段={",".join(updates)}')
    return http_utils.ok()


@routes.delete('/api/tg-monitors/{monitor_id}')
async def delete_tg_monitor(request):
    monitor_id = int(request.match_info['monitor_id'])
    deleted = await db.delete_tg_group_monitor(monitor_id)
    if not deleted:
        return http_utils.fail('监控不存在', status=404)
    http_utils.audit(request, 'tg_monitor.delete', f'id={monitor_id}')
    return http_utils.ok()


@routes.get('/api/tg-discovered')
async def list_tg_discovered(request):
    limit = http_utils.get_int(request, 'limit', 30, minimum=1, maximum=100) or 30
    return http_utils.ok({'items': await db.list_discovered_tg_chats(limit=limit)})


@routes.get('/api/web-monitors')
async def list_web_monitors(request):
    return http_utils.ok({'items': await db.list_web_monitors()})


@routes.post('/api/web-monitors')
async def create_web_monitor(request):
    data = await http_utils.read_json(request)
    name = str(data.get('name') or '').strip()
    url = str(data.get('url') or '').strip()
    if not name or not url:
        return http_utils.fail('名称与网址都不能为空')
    if not url.lower().startswith(('http://', 'https://')):
        return http_utils.fail('网址需要以 http:// 或 https:// 开头')
    monitor_id = await db.create_web_monitor(
        name=name,
        url=url,
        keywords=_parse_keywords(data.get('keywords')),
        created_by=0,
        interval_seconds=int(data.get('interval_seconds') or 300),
        item_selector=str(data.get('item_selector') or '').strip() or None,
        title_selector=str(data.get('title_selector') or '').strip() or None,
        link_selector=str(data.get('link_selector') or '').strip() or None,
        price_selector=str(data.get('price_selector') or '').strip() or None,
        stock_selector=str(data.get('stock_selector') or '').strip() or None,
    )
    http_utils.audit(request, 'web_monitor.create', f'{name} {url}')
    return http_utils.ok({'id': monitor_id})


@routes.put('/api/web-monitors/{monitor_id}')
async def update_web_monitor(request):
    monitor_id = int(request.match_info['monitor_id'])
    if not await db.get_web_monitor(monitor_id):
        return http_utils.fail('监控不存在', status=404)
    data = await http_utils.read_json(request)
    updates = _pick(data, WEB_MONITOR_EDITABLE)
    if not updates:
        return http_utils.fail('没有可更新的字段')
    await db.update_web_monitor(monitor_id, **updates)
    http_utils.audit(request, 'web_monitor.update', f'id={monitor_id} 字段={",".join(updates)}')
    return http_utils.ok()


@routes.delete('/api/web-monitors/{monitor_id}')
async def delete_web_monitor(request):
    monitor_id = int(request.match_info['monitor_id'])
    deleted = await db.delete_web_monitor(monitor_id)
    if not deleted:
        return http_utils.fail('监控不存在', status=404)
    http_utils.audit(request, 'web_monitor.delete', f'id={monitor_id}')
    return http_utils.ok()


# --------------------------------------------------------------------------- #
# 网络工具
# --------------------------------------------------------------------------- #

def _find_server(name: str):
    from network_test import config as nt_config

    for server in nt_config.SERVERS:
        if server.get('name') == name:
            return server
    return None


@routes.get('/api/network/servers')
async def list_network_servers(request):
    from network_test import config as nt_config

    items = [
        {
            'name': server.get('name'),
            'host': server.get('host'),
            'port': server.get('port'),
            'username': server.get('username'),
        }
        for server in nt_config.SERVERS
    ]
    return http_utils.ok({'items': items})


@routes.post('/api/network/servers')
async def add_network_server(request):
    from network_test import config as nt_config

    data = await http_utils.read_json(request)
    name = str(data.get('name') or '').strip()
    host = str(data.get('host') or '').strip()
    username = str(data.get('username') or '').strip()
    password = str(data.get('password') or '')
    if not name or not host or not username:
        return http_utils.fail('名称、主机、用户名都不能为空')
    if _find_server(name):
        return http_utils.fail('同名服务器已存在', status=409)
    try:
        port = int(data.get('port') or 22)
    except (TypeError, ValueError):
        raise ValueError('端口需要是整数')
    nt_config.SERVERS.append({
        'name': name, 'host': host, 'port': port, 'username': username, 'password': password,
    })
    nt_config.save_config()
    http_utils.audit(request, 'network.server_add', f'{name} {host}:{port}')
    return http_utils.ok()


@routes.delete('/api/network/servers/{name}')
async def remove_network_server(request):
    from network_test import config as nt_config

    name = request.match_info['name']
    server = _find_server(name)
    if not server:
        return http_utils.fail('服务器不存在', status=404)
    nt_config.SERVERS.remove(server)
    nt_config.save_config()
    http_utils.audit(request, 'network.server_remove', name)
    return http_utils.ok()


def _validated_target(target: str) -> str:
    from network_test.utils import validate_target

    target = (target or '').strip()
    ok, message = validate_target(target)
    if not ok:
        raise ValueError(message)
    return target


@routes.post('/api/network/ping')
async def run_ping(request):
    from network_test import network as nt_network

    data = await http_utils.read_json(request)
    server = _find_server(str(data.get('server') or ''))
    if not server:
        return http_utils.fail('服务器不存在', status=404)
    target = _validated_target(data.get('target'))
    try:
        count = int(data.get('count') or 4)
    except (TypeError, ValueError):
        raise ValueError('Ping 次数需要是整数')
    count = max(1, min(count, 10))

    try:
        result = await asyncio.wait_for(
            asyncio.to_thread(nt_network.ping_on_server, server, target, count, '这一位'),
            timeout=180,
        )
    except asyncio.TimeoutError:
        return http_utils.fail('执行超时（180 秒），请检查服务器连通性', status=504)
    http_utils.audit(request, 'network.ping', f'{server.get("name")} -> {target} x{count}')
    return http_utils.ok({'result': result})


@routes.post('/api/network/nexttrace')
async def run_nexttrace(request):
    from network_test import network as nt_network

    data = await http_utils.read_json(request)
    server = _find_server(str(data.get('server') or ''))
    if not server:
        return http_utils.fail('服务器不存在', status=404)
    target = _validated_target(data.get('target'))
    ip_type = str(data.get('ip_type') or 'ipv4').strip().lower()
    if ip_type not in ('ipv4', 'ipv6'):
        return http_utils.fail('ip_type 只允许 ipv4 或 ipv6')
    mode = str(data.get('mode') or 'icmp').strip().lower()
    if mode not in ('icmp', 'tcp', 'udp'):
        return http_utils.fail('mode 只允许 icmp / tcp / udp')

    try:
        result = await asyncio.wait_for(
            asyncio.to_thread(nt_network.nexttrace_on_server, server, target, ip_type, mode, '这一位'),
            timeout=300,
        )
    except asyncio.TimeoutError:
        return http_utils.fail('执行超时（300 秒），请检查服务器连通性', status=504)
    http_utils.audit(request, 'network.nexttrace', f'{server.get("name")} -> {target} {ip_type}/{mode}')
    return http_utils.ok({'result': result})


@routes.post('/api/network/install')
async def install_nexttrace(request):
    from network_test import network as nt_network

    data = await http_utils.read_json(request)
    server = _find_server(str(data.get('server') or ''))
    if not server:
        return http_utils.fail('服务器不存在', status=404)
    try:
        result = await asyncio.wait_for(
            asyncio.to_thread(nt_network.install_nexttrace_on_server, server),
            timeout=600,
        )
    except asyncio.TimeoutError:
        return http_utils.fail('安装超时（600 秒）', status=504)
    http_utils.audit(request, 'network.install_nexttrace', str(server.get('name')))
    return http_utils.ok({'result': result})


# --------------------------------------------------------------------------- #
# 更新
# --------------------------------------------------------------------------- #

@routes.get('/api/update/git')
async def git_update_status(request):
    status = await safe_update.get_status(REPO_DIR, fetch_remote=True)
    rollback = await db.get_app_meta('last_update_rollback')
    return http_utils.ok({'status': dataclasses.asdict(status), 'rollback': rollback})


@routes.post('/api/update/git')
async def git_update_apply(request):
    try:
        status = await safe_update.apply_update(REPO_DIR)
    except safe_update.SafeUpdateError as exc:
        return http_utils.fail(f'更新失败: {exc}')
    http_utils.audit(request, 'update.git_apply', f'head={status.head[:12]}')
    return http_utils.ok({'status': dataclasses.asdict(status)})


@routes.post('/api/update/git/rollback')
async def git_update_rollback(request):
    try:
        message = await safe_update.rollback_last_update(REPO_DIR)
    except safe_update.SafeUpdateError as exc:
        return http_utils.fail(f'回滚失败: {exc}')
    http_utils.audit(request, 'update.git_rollback', message[:120])
    return http_utils.ok({'message': message})


@routes.get('/api/update/image')
async def image_update_status(request):
    status = await image_update.check_for_update()
    return http_utils.ok({'status': dataclasses.asdict(status)})


@routes.post('/api/update/image')
async def image_update_apply(request):
    triggered, message = await image_update.trigger_watchtower_update()
    if not triggered:
        return http_utils.fail(message)
    http_utils.audit(request, 'update.image_apply', message[:120])
    return http_utils.ok({'message': message})
