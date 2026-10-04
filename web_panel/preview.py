"""本地 UI 预览服务器：用假数据驱动面板前端，无需真实 BOT_TOKEN / 数据库。

仅供开发时查看界面使用，不影响生产部署（生产走 bot.py 内嵌的 web_panel）。

用法：
    python -m web_panel.preview                # 默认 http://127.0.0.1:18100
    python -m web_panel.preview --port 19000
    python -m web_panel.preview --no-browser   # 不自动打开浏览器

所有数据存在内存里，改了刷新即恢复；会话页每隔约 18 秒会收到一条
模拟新消息，可以体验实时推送与回复的效果。
"""

import argparse
import asyncio
import time
import webbrowser
from pathlib import Path

from aiohttp import WSMsgType, web

import config_meta

STATIC_DIR = Path(__file__).parent / 'static'

# --------------------------------------------------------------------------- #
# 假数据
# --------------------------------------------------------------------------- #

def _now():
    return time.strftime('%Y-%m-%d %H:%M:%S', time.localtime())


STORE = {
    'users': [
        {'user_id': 100001, 'username': 'yumemi', 'first_name': '星野梦美', 'is_verified': True,
         'thread_id': 11, 'last_active': _now(), 'message_count': 128, 'blacklisted': False,
         'last_content': '女仆小姐，明天的推荐菜是什么呀？', 'last_direction': 'user_to_admin',
         'last_media_type': None, 'last_message_at': _now()},
        {'user_id': 100002, 'username': 'alei', 'first_name': '阿蕾', 'is_verified': True,
         'thread_id': 12, 'last_active': _now(), 'message_count': 64, 'blacklisted': False,
         'last_content': '', 'last_direction': 'user_to_admin', 'last_media_type': 'photo',
         'last_message_at': _now()},
        {'user_id': 100003, 'username': 'xiaoguo', 'first_name': '小锅', 'is_verified': True,
         'thread_id': 13, 'last_active': _now(), 'message_count': 30, 'blacklisted': False,
         'last_content': '收到～谢谢女仆小姐！', 'last_direction': 'admin_to_user',
         'last_media_type': None, 'last_message_at': _now()},
        {'user_id': 100004, 'username': None, 'first_name': '路人乙', 'is_verified': False,
         'thread_id': 14, 'last_active': _now(), 'message_count': 2, 'blacklisted': False,
         'last_content': '请问怎么进群？', 'last_direction': 'user_to_admin',
         'last_media_type': None, 'last_message_at': _now()},
        {'user_id': 100005, 'username': 'ad_bot_996', 'first_name': '广告哥', 'is_verified': True,
         'thread_id': 15, 'last_active': _now(), 'message_count': 1, 'blacklisted': True,
         'last_content': '【低价代刷】加微信……', 'last_direction': 'user_to_admin',
         'last_media_type': None, 'last_message_at': _now()},
    ],
    'messages': {
        100001: [
            {'id': 1, 'message_id': 1, 'thread_id': 11, 'content': '女仆小姐，晚上好呀～',
             'media_type': None, 'media_file_id': None, 'direction': 'user_to_admin',
             'dest_message_id': 101, 'created_at': _now()},
            {'id': 2, 'message_id': 2, 'thread_id': 11, 'content': '（Web 面板回复）晚上好，主人～今晚有红烧肉哦。',
             'media_type': None, 'media_file_id': None, 'direction': 'admin_to_user',
             'dest_message_id': None, 'created_at': _now()},
            {'id': 3, 'message_id': 3, 'thread_id': 11, 'content': '哇！那我要吃两碗！',
             'media_type': None, 'media_file_id': None, 'direction': 'user_to_admin',
             'dest_message_id': 103, 'created_at': _now()},
        ],
        100002: [
            {'id': 4, 'message_id': 4, 'thread_id': 12, 'content': '看看这个截图',
             'media_type': None, 'media_file_id': None, 'direction': 'user_to_admin',
             'dest_message_id': 104, 'created_at': _now()},
            {'id': 5, 'message_id': 5, 'thread_id': 12, 'content': '',
             'media_type': 'photo', 'media_file_id': 'demo-photo-1', 'direction': 'user_to_admin',
             'dest_message_id': 105, 'created_at': _now()},
        ],
    },
    'knowledge': [
        {'id': 1, 'title': '入群须知', 'content': '进群先看公告，禁止刷屏与广告，有问题私聊女仆。'},
        {'id': 2, 'title': '价格表', 'content': '会员月卡 10 元，季卡 25 元，年卡 88 元。'},
    ],
    'keywords': ['加微信', '代购', '刷单'],
    'groups': [
        {'id': 1, 'name': '常驻客人', 'description': '老朋友们', 'member_count': 2, 'created_at': _now()},
    ],
    'group_members': {'常驻客人': [100001, 100003]},
    'broadcasts': [
        {'id': 1, 'scope': 'all', 'group_id': None, 'group_name': None, 'content_preview': '今晚 22:00 停机维护，别担心哦。',
         'created_by': 10001, 'total_count': 96, 'success_count': 96, 'failed_count': 0, 'created_at': _now()},
    ],
    'servers': [
        {'name': '东京小屋', 'host': 'tokyo.example.com', 'port': 22, 'username': 'maid'},
        {'name': '家里蹲', 'host': '192.168.1.3', 'port': 22, 'username': 'pi'},
    ],
    'rss_feeds': [
        {'chat_id': '100001', 'url': 'https://example.com/feed.xml', 'title': '示例博客',
         'keywords': ['Python'], 'last_entry_id': None},
    ],
    'rss_authorized': [100001],
    'tg_monitors': [
        {'id': 1, 'name': '资源群', 'chat_id': -1001234567890, 'chat_title': '资源分享群',
         'listen_source': 'user_session', 'keywords': ['更新', '发布'], 'exclude_keywords': [],
         'min_interval_seconds': 30, 'dedupe_window_seconds': 300, 'enabled': True},
    ],
    'discovered': [
        {'chat_id': -1001234567890, 'title': '资源分享群', 'username': None, 'discovered_at': _now()},
        {'chat_id': -1009876543210, 'title': '摸鱼咖啡厅', 'username': 'moyu_cafe', 'discovered_at': _now()},
    ],
    'web_monitors': [
        {'id': 1, 'name': '新番表', 'url': 'https://example.com/anime', 'keywords': ['更新'],
         'interval_seconds': 300, 'enabled': True, 'item_selector': 'article', 'title_selector': 'h2',
         'link_selector': 'a', 'price_selector': '', 'stock_selector': '', 'last_checked_at': _now()},
    ],
    'exemptions': [
        {'user_id': 100001, 'first_name': '星野梦美', 'username': 'yumemi', 'is_permanent': True,
         'expires_at': None, 'reason': '可信的老朋友', 'created_at': _now()},
    ],
    'filtered': [
        {'id': 1, 'user_id': 100005, 'first_name': '广告哥', 'username': 'ad_bot_996',
         'reason': '命中关键词「加微信」', 'content': '【低价代刷】加微信，全网最低价……', 'filtered_at': _now()},
        {'id': 2, 'user_id': 100006, 'first_name': '神秘人', 'username': None,
         'reason': 'AI 判定为垃圾消息', 'content': '兼职日结，点击链接立即报名……', 'filtered_at': _now()},
    ],
    'blacklist': [
        {'user_id': 100005, 'first_name': '广告哥', 'username': 'ad_bot_996',
         'reason': '发送广告消息', 'blocked_at': _now()},
    ],
    'autoreply': {'enabled': True, 'personality_md': ''},
    'spam_settings': {'enabled': True, 'auto_block': True},
    'ai': {
        'provider': 'gemini',
        'models': {
            'gemini_model_filter': 'gemini-2.5-flash', 'gemini_model_verification': 'gemini-2.5-flash-lite',
            'gemini_model_autoreply': 'gemini-2.5-flash', 'openai_model_filter': 'gpt-4.1',
            'openai_model_verification': 'gpt-4.1-mini', 'openai_model_autoreply': 'gpt-4.1',
        },
        'keys': {'gemini_configured': True, 'openai_configured': False,
                 'gemini_base_url': '', 'openai_base_url': 'https://api.openai.com/v1'},
    },
    'config_values': {
        'BOT_TOKEN': '123456:FAKE-TOKEN-example', 'FORUM_GROUP_ID': '-1001234567890',
        'ADMIN_IDS': '10001,10002', 'GEMINI_API_KEY': 'AIzaFAKEexampleKEY123',
        'ENABLE_AI_FILTER': 'true', 'AI_CONFIDENCE_THRESHOLD': '70',
        'MAX_MESSAGES_PER_MINUTE': '30', 'WEB_PANEL_PORT': '18080',
    },
    'config_overrides': {},
    'next_id': {'knowledge': 3, 'group': 2, 'tg': 2, 'web': 2, 'broadcast': 2, 'filtered': 3},
}

FAKE_PING = (
    '【女仆 Ping 测试结果】\n\n'
    'PING 8.8.8.8 (8.8.8.8) 56(84) bytes of data.\n'
    '64 bytes from 8.8.8.8: icmp_seq=1 ttl=118 time=9.81 ms\n'
    '64 bytes from 8.8.8.8: icmp_seq=2 ttl=118 time=9.44 ms\n'
    '64 bytes from 8.8.8.8: icmp_seq=3 ttl=118 time=10.2 ms\n'
    '64 bytes from 8.8.8.8: icmp_seq=4 ttl=118 time=9.67 ms\n\n'
    '--- 8.8.8.8 ping statistics ---\n'
    '4 packets transmitted, 4 received, 0% packet loss, time 3004ms'
)

FAKE_TRACE = (
    '【女仆 NextTrace 路由追踪】\n\n'
    'IPV4 TRACE: 1.1.1.1\n'
    '1  192.168.1.1  1.02 ms  *  家庭网关\n'
    '2  10.0.0.1     3.55 ms  ISP\n'
    '3  203.0.113.1  8.90 ms  骨干网\n'
    '4  1.1.1.1      9.12 ms  Cloudflare'
)


def _page(items, page=1, per_page=20):
    total = len(items)
    total_pages = max(1, (total + per_page - 1) // per_page)
    return {'items': items, 'total': total, 'page': page,
            'per_page': per_page, 'total_pages': total_pages}


def _ok(data=None):
    return web.json_response({'ok': True, 'data': data})


def _user_by_id(user_id):
    return next((u for u in STORE['users'] if u['user_id'] == int(user_id)), None)


routes = web.RouteTableDef()


# --------------------------------------------------------------------------- #
# 基础与仪表盘
# --------------------------------------------------------------------------- #

@routes.get('/')
async def index(request):
    return web.FileResponse(STATIC_DIR / 'index.html')


@routes.get('/api/me')
async def me(request):
    return _ok({'authenticated': True, 'password_required': True,
                'bot_username': 'pivkeyu_maid', 'bot_id': 42})


@routes.post('/api/password')
async def change_password(request):
    body = await request.json()
    if len(str(body.get('new_password') or '')) < 8:
        return web.json_response({'ok': False, 'error': '新密码至少 8 位'}, status=400)
    return _ok({'password_changed': True})


@routes.get('/api/overview')
async def overview(request):
    return _ok({
        'bot': {'id': 42, 'username': 'pivkeyu_maid', 'forum_group_id': -1001234567890},
        'stats': {'total_users': 128, 'verified_users': 96, 'blocked_users': 3,
                  'filtered_messages': 12, 'pending_topics': 4, 'exemptions': 2,
                  'messages': {'total': 8421, 'today': 57}},
        'runtime_status': [
            {'name': 'RSS 订阅抓取', 'category': 'rss', 'last_run_at': _now(), 'last_success_at': _now(),
             'last_error_at': None, 'last_error': '', 'last_duration_ms': 812, 'last_sent_count': 2,
             'consecutive_failures': 0},
            {'name': 'TG 群监听', 'category': 'tg', 'last_run_at': _now(), 'last_success_at': _now(),
             'last_error_at': None, 'last_error': '', 'last_duration_ms': 96, 'last_sent_count': 0,
             'consecutive_failures': 0},
            {'name': '网页监控轮询', 'category': 'web', 'last_run_at': _now(), 'last_success_at': None,
             'last_error_at': _now(), 'last_error': '示例错误：目标站点超时（仅供预览展示）',
             'last_duration_ms': 30012, 'last_sent_count': 0, 'consecutive_failures': 2},
        ],
    })


@routes.get('/api/runtime-status')
async def runtime_status(request):
    return _ok({'items': []})


# --------------------------------------------------------------------------- #
# 配置中心
# --------------------------------------------------------------------------- #

def _config_item(meta):
    key = meta['key']
    value = STORE['config_overrides'].get(key, STORE['config_values'].get(key, meta.get('default', '')))
    source = 'override' if key in STORE['config_overrides'] else (
        'env' if key in STORE['config_values'] else 'default')
    if meta.get('secret') and value:
        shown = f"{value[:4]}••••••{value[-4:]}" if len(value) > 8 else '•' * len(value)
    else:
        shown = value
    return {'key': key, 'type': meta['type'], 'category': meta['category'],
            'category_label': config_meta.CATEGORY_LABELS.get(meta['category'], meta['category']),
            'label': meta['label'], 'hint': meta['hint'], 'restart': bool(meta.get('restart')),
            'secret': bool(meta.get('secret')), 'options': list(meta.get('options', [])),
            'source': source, 'has_value': bool(value), 'value': shown,
            'value_masked': bool(meta.get('secret')) and bool(value),
            'default': meta.get('default', '')}


@routes.get('/api/config')
async def get_config(request):
    return _ok({
        'categories': [{'key': k, 'label': v} for k, v in config_meta.CATEGORIES],
        'items': [_config_item(m) for m in config_meta.META],
    })


@routes.put('/api/config/{key}')
async def put_config(request):
    key = request.match_info['key']
    if key in ('WEB_PANEL_PASSWORD', 'WEB_PANEL_PASSWORD_SHA256'):
        return web.json_response({'ok': False, 'error': '请使用修改面板密码表单'}, status=400)
    meta = config_meta.META_BY_KEY.get(key)
    if not meta:
        return web.json_response({'ok': False, 'error': '未知配置项'}, status=404)
    body = await request.json()
    raw = body.get('value', '')
    STORE['config_overrides'][key] = '' if raw is None else str(raw)
    return _ok(_config_item(meta))


@routes.delete('/api/config/{key}')
async def reset_config(request):
    key = request.match_info['key']
    if key in ('WEB_PANEL_PASSWORD', 'WEB_PANEL_PASSWORD_SHA256'):
        return web.json_response({'ok': False, 'error': '请使用修改面板密码表单'}, status=400)
    STORE['config_overrides'].pop(key, None)
    return _ok(_config_item(config_meta.META_BY_KEY[key]))


# --------------------------------------------------------------------------- #
# 会话
# --------------------------------------------------------------------------- #

@routes.get('/api/conversations')
async def conversations(request):
    verified_users = [user for user in STORE['users'] if user.get('is_verified')]
    return _ok(_page(verified_users))


@routes.get('/api/conversations/{user_id}')
async def conversation_detail(request):
    user = _user_by_id(request.match_info['user_id'])
    if not user or not user.get('is_verified'):
        return web.json_response({'ok': False, 'error': '用户不存在'}, status=404)
    messages = STORE['messages'].get(user['user_id'], [])
    return _ok({
        'user': {'user_id': user['user_id'], 'username': user['username'], 'first_name': user['first_name'],
                 'last_name': None, 'is_verified': user['is_verified'], 'is_blacklisted': user['blacklisted'],
                 'blacklist_permanent': False, 'blacklist_strikes': 1 if user['blacklisted'] else 0,
                 'thread_id': user['thread_id'], 'created_at': _now(), 'last_active': user['last_active'],
                 'exempted': user['user_id'] == 100001},
        'messages': _page(messages, per_page=50),
    })


@routes.post('/api/conversations/{user_id}/reply')
async def reply(request):
    user_id = int(request.match_info['user_id'])
    body = await request.json()
    text = (body.get('text') or '').strip()
    if not text:
        return web.json_response({'ok': False, 'error': '回复内容不能为空'}, status=400)
    STORE['messages'].setdefault(user_id, []).append(
        {'id': int(time.time()), 'message_id': int(time.time()) % 100000, 'thread_id': 11,
         'content': f'（Web 面板回复）{text}', 'media_type': None, 'media_file_id': None,
         'direction': 'admin_to_user', 'dest_message_id': None, 'created_at': _now()})
    return _ok({'message_id': 1, 'mirrored': True})


@routes.post('/api/conversations/{user_id}/block')
@routes.post('/api/conversations/{user_id}/unblock')
async def block_unblock(request):
    return _ok({'message': '（预览模式）已模拟执行封禁/解封'})


@routes.get('/api/media/{file_id}')
async def media(request):
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" width="360" height="240">'
        '<rect width="100%" height="100%" fill="#fdeff1"/>'
        '<circle cx="180" cy="100" r="46" fill="#f9d5dd"/>'
        '<text x="50%" y="52%" text-anchor="middle" font-size="44">🎀</text>'
        '<text x="50%" y="200" text-anchor="middle" font-size="14" fill="#cf5a72">'
        '媒体预览占位（预览模式）</text></svg>'
    )
    return web.Response(text=svg, content_type='image/svg+xml')


# --------------------------------------------------------------------------- #
# 用户管理
# --------------------------------------------------------------------------- #

@routes.get('/api/users')
async def users(request):
    items = [{'user_id': u['user_id'], 'first_name': u['first_name'], 'username': u['username'],
              'is_blacklisted': u['blacklisted'], 'spam_count': 3 if u['blacklisted'] else 0}
             for u in STORE['users']]
    return _ok(_page(items))


@routes.get('/api/blacklist')
async def blacklist(request):
    return _ok(_page(STORE['blacklist']))


@routes.post('/api/blacklist')
async def add_blacklist(request):
    body = await request.json()
    user_id = int(body.get('user_id') or 0)
    STORE['blacklist'].insert(0, {'user_id': user_id, 'first_name': f'用户 {user_id}',
                                  'username': None, 'reason': body.get('reason') or '（预览）手动拉黑',
                                  'blocked_at': _now()})
    return _ok({'message': f'已把 {user_id} 请进黑名单小本本（预览）'})


@routes.delete('/api/blacklist/{user_id}')
async def remove_blacklist(request):
    uid = int(request.match_info['user_id'])
    STORE['blacklist'] = [b for b in STORE['blacklist'] if b['user_id'] != uid]
    return _ok({'message': f'已替 {uid} 打开通道（预览）'})


@routes.get('/api/exemptions')
async def exemptions(request):
    return _ok(_page(STORE['exemptions']))


@routes.post('/api/exemptions')
async def add_exemption(request):
    body = await request.json()
    uid = int(body.get('user_id') or 0)
    STORE['exemptions'].append({'user_id': uid, 'first_name': f'用户 {uid}', 'username': None,
                                'is_permanent': bool(body.get('permanent')),
                                'expires_at': None if body.get('permanent') else _now(),
                                'reason': body.get('reason') or '', 'created_at': _now()})
    return _ok()


@routes.delete('/api/exemptions/{user_id}')
async def remove_exemption(request):
    uid = int(request.match_info['user_id'])
    STORE['exemptions'] = [e for e in STORE['exemptions'] if e['user_id'] != uid]
    return _ok()


@routes.get('/api/filtered')
async def filtered(request):
    return _ok(_page(STORE['filtered']))


@routes.delete('/api/filtered/{message_id}')
async def delete_filtered(request):
    mid = int(request.match_info['message_id'])
    STORE['filtered'] = [f for f in STORE['filtered'] if f['id'] != mid]
    return _ok()


@routes.delete('/api/filtered')
async def clear_filtered(request):
    count = len(STORE['filtered'])
    STORE['filtered'] = []
    return _ok({'deleted': count})


# --------------------------------------------------------------------------- #
# 自动回复 / 知识库 / 关键词
# --------------------------------------------------------------------------- #

@routes.get('/api/autoreply')
async def get_autoreply(request):
    return _ok(STORE['autoreply'])


@routes.put('/api/autoreply')
async def put_autoreply(request):
    body = await request.json()
    if 'enabled' not in body and 'personality_md' not in body:
        return web.json_response({'ok': False, 'error': '没有可更新的自动回复设置'}, status=400)
    if 'personality_md' in body:
        personality_md = body.get('personality_md')
        if not isinstance(personality_md, str):
            return web.json_response({'ok': False, 'error': '人格.md 内容必须是文本'}, status=400)
        if len(personality_md) > 20000:
            return web.json_response({'ok': False, 'error': '人格.md 最多 20000 个字符'}, status=400)
        personality_md = personality_md.strip() if personality_md.strip() else ''
    if 'enabled' in body:
        STORE['autoreply']['enabled'] = bool(body.get('enabled'))
    if 'personality_md' in body:
        STORE['autoreply']['personality_md'] = personality_md
    return _ok(STORE['autoreply'])


@routes.get('/api/knowledge')
async def knowledge(request):
    return _ok({'items': STORE['knowledge']})


@routes.post('/api/knowledge')
async def add_knowledge(request):
    body = await request.json()
    nid = STORE['next_id']['knowledge']
    STORE['next_id']['knowledge'] += 1
    STORE['knowledge'].append({'id': nid, 'title': body.get('title'), 'content': body.get('content')})
    return _ok()


@routes.put('/api/knowledge/{kid}')
async def put_knowledge(request):
    kid = int(request.match_info['kid'])
    body = await request.json()
    for entry in STORE['knowledge']:
        if entry['id'] == kid:
            entry['title'], entry['content'] = body.get('title'), body.get('content')
    return _ok()


@routes.delete('/api/knowledge/{kid}')
async def delete_knowledge(request):
    kid = int(request.match_info['kid'])
    STORE['knowledge'] = [k for k in STORE['knowledge'] if k['id'] != kid]
    return _ok()


@routes.get('/api/spam-keywords')
async def spam_keywords(request):
    return _ok({'enabled': STORE['spam_settings']['enabled'],
                'auto_block': STORE['spam_settings']['auto_block'],
                'keywords': list(STORE['keywords'])})


@routes.put('/api/spam-keywords/settings')
async def spam_settings(request):
    body = await request.json()
    STORE['spam_settings'].update({k: bool(v) for k, v in body.items() if k in ('enabled', 'auto_block')})
    return _ok(STORE['spam_settings'])


@routes.post('/api/spam-keywords')
async def add_keyword(request):
    body = await request.json()
    kw = (body.get('keyword') or '').strip()
    if not kw:
        return web.json_response({'ok': False, 'error': '关键词不能为空'}, status=400)
    if kw in STORE['keywords']:
        return web.json_response({'ok': False, 'error': '该关键词已存在'}, status=409)
    STORE['keywords'].append(kw)
    return _ok()


@routes.delete('/api/spam-keywords/{keyword}')
async def remove_keyword(request):
    STORE['keywords'] = [k for k in STORE['keywords'] if k != request.match_info['keyword']]
    return _ok()


@routes.delete('/api/spam-keywords')
async def clear_keywords(request):
    count = len(STORE['keywords'])
    STORE['keywords'] = []
    return _ok({'deleted': count})


# --------------------------------------------------------------------------- #
# AI 设置
# --------------------------------------------------------------------------- #

@routes.get('/api/ai-settings')
async def ai_settings(request):
    return _ok(STORE['ai'])


@routes.get('/api/ai-models')
async def ai_models(request):
    provider = (request.query.get('provider') or 'gemini').strip().lower()
    if provider == 'gemini':
        models = ['gemini-2.5-flash', 'gemini-2.5-flash-lite', 'gemini-2.5-pro']
    elif provider == 'openai':
        models = ['gpt-4.1', 'gpt-4.1-mini', 'gpt-4.1-nano']
    else:
        return web.json_response({'ok': False, 'error': 'provider 只允许 gemini 或 openai'}, status=400)
    return _ok({'provider': provider, 'models': models})


@routes.put('/api/ai-settings')
async def put_ai_settings(request):
    body = await request.json()
    if body.get('provider'):
        STORE['ai']['provider'] = body['provider']
    for key, value in (body.get('models') or {}).items():
        if key in STORE['ai']['models'] and value:
            STORE['ai']['models'][key] = value
    return _ok(STORE['ai'])


# --------------------------------------------------------------------------- #
# 用户组 / 广播
# --------------------------------------------------------------------------- #

@routes.get('/api/user-groups')
async def user_groups(request):
    items = [dict(g, member_count=len(STORE['group_members'].get(g['name'], []))) for g in STORE['groups']]
    return _ok({'items': items})


@routes.post('/api/user-groups')
async def add_group(request):
    body = await request.json()
    name = (body.get('name') or '').strip()
    if not name:
        return web.json_response({'ok': False, 'error': '分组名不能为空'}, status=400)
    nid = STORE['next_id']['group']
    STORE['next_id']['group'] += 1
    STORE['groups'].append({'id': nid, 'name': name, 'description': body.get('description') or '',
                            'member_count': 0, 'created_at': _now()})
    STORE['group_members'][name] = []
    return _ok()


@routes.delete('/api/user-groups/{name}')
async def delete_group(request):
    name = request.match_info['name']
    STORE['groups'] = [g for g in STORE['groups'] if g['name'] != name]
    STORE['group_members'].pop(name, None)
    return _ok()


@routes.get('/api/user-groups/{name}/members')
async def group_members(request):
    name = request.match_info['name']
    ids = STORE['group_members'].get(name, [])
    items = [{'user_id': uid, 'first_name': (u['first_name'] if (u := _user_by_id(uid)) else f'用户 {uid}'),
              'username': u['username'] if (u := _user_by_id(uid)) else None} for uid in ids]
    return _ok({'items': items})


@routes.post('/api/user-groups/{name}/members')
async def add_member(request):
    name = request.match_info['name']
    body = await request.json()
    STORE['group_members'].setdefault(name, []).append(int(body.get('user_id')))
    return _ok()


@routes.delete('/api/user-groups/{name}/members/{user_id}')
async def remove_member(request):
    name = request.match_info['name']
    uid = int(request.match_info['user_id'])
    STORE['group_members'][name] = [u for u in STORE['group_members'].get(name, []) if u != uid]
    return _ok()


@routes.get('/api/broadcasts')
async def broadcasts(request):
    return _ok(_page(STORE['broadcasts']))


@routes.post('/api/broadcast')
async def start_broadcast(request):
    body = await request.json()
    text = (body.get('text') or '').strip()
    if not text:
        return web.json_response({'ok': False, 'error': '广播内容不能为空'}, status=400)
    total = 96 if not body.get('group_name') else 2
    STORE['broadcasts'].insert(0, {'id': STORE['next_id']['broadcast'], 'scope': 'group' if body.get('group_name') else 'all',
                                   'group_id': None, 'group_name': body.get('group_name'),
                                   'content_preview': text[:100], 'created_by': 10001,
                                   'total_count': total, 'success_count': total, 'failed_count': 0,
                                   'created_at': _now()})
    STORE['next_id']['broadcast'] += 1
    return _ok({'accepted': True, 'total': total})


# --------------------------------------------------------------------------- #
# RSS / TG 监控 / 网页监控
# --------------------------------------------------------------------------- #

@routes.get('/api/rss')
async def rss(request):
    return _ok({'enabled': True, 'check_interval': 300,
                'authorized_users': STORE['rss_authorized'], 'feeds': STORE['rss_feeds']})


@routes.put('/api/rss/settings')
async def rss_settings(request):
    return _ok({'enabled': True, 'check_interval': int((await request.json()).get('check_interval') or 300)})


@routes.post('/api/rss/feeds')
async def rss_add_feed(request):
    body = await request.json()
    STORE['rss_feeds'].append({'chat_id': str(body.get('chat_id')), 'url': body.get('url'),
                               'title': '新订阅（预览）', 'keywords': [], 'last_entry_id': None})
    return _ok({'title': '新订阅（预览）'})


@routes.delete('/api/rss/feeds')
async def rss_remove_feed(request):
    body = await request.json()
    STORE['rss_feeds'] = [f for f in STORE['rss_feeds'] if f['url'] != body.get('url')]
    return _ok()


@routes.post('/api/rss/keywords')
async def rss_add_keyword(request):
    body = await request.json()
    for feed in STORE['rss_feeds']:
        if feed['url'] == body.get('url'):
            feed.setdefault('keywords', []).append(body.get('keyword'))
    return _ok()


@routes.delete('/api/rss/keywords')
async def rss_remove_keyword(request):
    body = await request.json()
    for feed in STORE['rss_feeds']:
        if feed['url'] == body.get('url'):
            feed['keywords'] = [k for k in feed['keywords'] if k != body.get('keyword')]
    return _ok()


@routes.post('/api/rss/authorized')
async def rss_add_auth(request):
    body = await request.json()
    STORE['rss_authorized'].append(int(body.get('user_id')))
    return _ok({'items': STORE['rss_authorized']})


@routes.delete('/api/rss/authorized/{user_id}')
async def rss_remove_auth(request):
    uid = int(request.match_info['user_id'])
    STORE['rss_authorized'] = [u for u in STORE['rss_authorized'] if u != uid]
    return _ok({'items': STORE['rss_authorized']})


@routes.get('/api/tg-monitors')
async def tg_monitors(request):
    return _ok({'items': STORE['tg_monitors']})


@routes.post('/api/tg-monitors')
async def tg_add(request):
    body = await request.json()
    nid = STORE['next_id']['tg']
    STORE['next_id']['tg'] += 1
    STORE['tg_monitors'].append(dict(body, id=nid, enabled=True,
                                     keywords=[k for k in (body.get('keywords') or '').replace('，', ',').split(',') if k],
                                     exclude_keywords=[]))
    return _ok({'id': nid})


@routes.put('/api/tg-monitors/{mid}')
async def tg_update(request):
    mid = int(request.match_info['mid'])
    body = await request.json()
    for monitor in STORE['tg_monitors']:
        if monitor['id'] == mid:
            monitor.update({k: v for k, v in body.items() if k in monitor or k == 'enabled'})
    return _ok()


@routes.delete('/api/tg-monitors/{mid}')
async def tg_delete(request):
    mid = int(request.match_info['mid'])
    STORE['tg_monitors'] = [m for m in STORE['tg_monitors'] if m['id'] != mid]
    return _ok()


@routes.get('/api/tg-discovered')
async def tg_discovered(request):
    return _ok({'items': STORE['discovered']})


@routes.get('/api/web-monitors')
async def web_monitors(request):
    return _ok({'items': STORE['web_monitors']})


@routes.post('/api/web-monitors')
async def web_add(request):
    body = await request.json()
    nid = STORE['next_id']['web']
    STORE['next_id']['web'] += 1
    STORE['web_monitors'].append(dict(body, id=nid, enabled=True, last_checked_at=None,
                                      keywords=[k for k in (body.get('keywords') or '').replace('，', ',').split(',') if k]))
    return _ok({'id': nid})


@routes.put('/api/web-monitors/{mid}')
async def web_update(request):
    mid = int(request.match_info['mid'])
    body = await request.json()
    for monitor in STORE['web_monitors']:
        if monitor['id'] == mid:
            monitor.update({k: v for k, v in body.items() if k in monitor or k == 'enabled'})
    return _ok()


@routes.delete('/api/web-monitors/{mid}')
async def web_delete(request):
    mid = int(request.match_info['mid'])
    STORE['web_monitors'] = [m for m in STORE['web_monitors'] if m['id'] != mid]
    return _ok()


# --------------------------------------------------------------------------- #
# 网络工具 / 更新
# --------------------------------------------------------------------------- #

@routes.get('/api/network/servers')
async def servers(request):
    return _ok({'items': [{k: s[k] for k in ('name', 'host', 'port', 'username')} for s in STORE['servers']]})


@routes.post('/api/network/servers')
async def add_server(request):
    body = await request.json()
    STORE['servers'].append({'name': body.get('name'), 'host': body.get('host'),
                             'port': int(body.get('port') or 22), 'username': body.get('username'),
                             'password': body.get('password')})
    return _ok()


@routes.delete('/api/network/servers/{name}')
async def remove_server(request):
    name = request.match_info['name']
    STORE['servers'] = [s for s in STORE['servers'] if s['name'] != name]
    return _ok()


@routes.post('/api/network/ping')
async def ping(request):
    body = await request.json()
    return _ok({'result': FAKE_PING.replace('8.8.8.8', body.get('target') or '8.8.8.8')})


@routes.post('/api/network/nexttrace')
async def nexttrace(request):
    body = await request.json()
    return _ok({'result': FAKE_TRACE.replace('1.1.1.1', body.get('target') or '1.1.1.1')})


@routes.post('/api/network/install')
async def install(request):
    await asyncio.sleep(1)
    return _ok({'result': '（预览）NextTrace 已是最新版本，无需安装。'})


@routes.get('/api/update/git')
async def git_status(request):
    return _ok({'status': {'branch': 'main', 'head': 'a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2',
                           'remote_head': 'a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2',
                           'ahead': 0, 'behind': 0, 'dirty': False}, 'rollback': ''})


@routes.post('/api/update/git')
async def git_apply(request):
    return _ok({'status': {'branch': 'main', 'head': 'b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3',
                           'remote_head': 'b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3',
                           'ahead': 0, 'behind': 0, 'dirty': False}})


@routes.post('/api/update/git/rollback')
async def git_rollback(request):
    return _ok({'message': '（预览）已回滚到上一次更新前的提交。'})


@routes.get('/api/update/image')
async def image_status(request):
    return _ok({'status': {'in_container': True, 'image_repo': 'pivkeyu/pivkeyu_pmbot',
                           'image_tag': 'latest', 'local_sha': 'a1b2c3d4e5f6',
                           'remote_digest': 'sha256:deadbeef', 'known_digest': 'sha256:deadbeef',
                           'update_available': False, 'error': ''}})


@routes.post('/api/update/image')
async def image_apply(request):
    return _ok({'message': '（预览）已触发 Watchtower 更新，容器即将重建。'})


# --------------------------------------------------------------------------- #
# WebSocket：模拟实时新消息
# --------------------------------------------------------------------------- #

FAKE_INCOMING = [
    (100001, '女仆小姐，我回来啦～'),
    (100003, '帮忙查一下天气呗'),
    (100001, '对了，明天提醒我取快递！'),
]


@routes.get('/ws')
async def ws_handler(request):
    ws = web.WebSocketResponse(heartbeat=30)
    await ws.prepare(request)
    await ws.send_json({'type': 'hello', 'ts': time.time()})

    async def push_fake_messages():
        for index in range(60):
            await asyncio.sleep(18)
            user_id, text = FAKE_INCOMING[index % len(FAKE_INCOMING)]
            await ws.send_json({'type': 'message', 'user_id': user_id, 'direction': 'user_to_admin',
                                'preview': text, 'message_id': index, 'ts': time.time()})

    pusher = asyncio.create_task(push_fake_messages())
    try:
        async for message in ws:
            if message.type == WSMsgType.TEXT and message.data == 'ping':
                await ws.send_json({'type': 'pong', 'ts': time.time()})
    finally:
        pusher.cancel()
    return ws


# --------------------------------------------------------------------------- #
# 入口
# --------------------------------------------------------------------------- #

def build_app() -> web.Application:
    @web.middleware
    async def no_store_cache(request, handler):
        """预览模式禁用缓存：改完前端代码刷新页面即可看到效果。"""
        response = await handler(request)
        if request.path.startswith('/static') or request.path == '/':
            response.headers['Cache-Control'] = 'no-store'
        return response

    app = web.Application(middlewares=[no_store_cache])
    app.add_routes(routes)
    app.router.add_static('/static/', STATIC_DIR, name='static')
    return app


def main():
    parser = argparse.ArgumentParser(description='女仆房间 UI 本地预览（假数据）')
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=18100)
    parser.add_argument('--no-browser', action='store_true', help='不自动打开浏览器')
    args = parser.parse_args()

    app = build_app()
    url = f'http://{args.host}:{args.port}'
    print('=' * 56)
    print('  女仆的房间 · UI 预览模式（全部为演示数据）')
    print(f'  地址: {url}')
    print('  停止: Ctrl+C')
    print('=' * 56)
    if not args.no_browser:
        webbrowser.open(url)
    web.run_app(app, host=args.host, port=args.port, print=None)


if __name__ == '__main__':
    main()
