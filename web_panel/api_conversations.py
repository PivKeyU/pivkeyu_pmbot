"""会话中心：用户会话列表、消息历史、回复、封禁与媒体预览。"""

import logging
import mimetypes

from aiohttp import web
from telegram.error import BadRequest, TelegramError

from config import config
from database import models as db
from services import blacklist as blacklist_service
from services import moderation
from services.thread_manager import ensure_user_thread
from . import events, http_utils, keys

logger = logging.getLogger(__name__)

routes = web.RouteTableDef()

HISTORY_PAGE_SIZE = 50
INLINE_MEDIA_TYPES = {
    'image/jpeg', 'image/png', 'image/gif', 'image/webp',
    'video/mp4',
    'audio/mpeg', 'audio/mp4', 'audio/ogg', 'audio/wav',
}


async def _get_user_or_404(user_id: int):
    user = await db.get_user(user_id)
    if not user:
        raise web.HTTPNotFound(text='用户不存在')
    return user


@routes.get('/api/conversations')
async def list_conversations(request):
    page, per_page, offset = http_utils.pagination(request, default_per=20)
    query = (request.query.get('q') or '').strip() or None
    items = await db.get_conversation_summaries(limit=per_page, offset=offset, query=query)
    total = await db.get_conversation_summaries_count(query=query)
    return http_utils.ok(http_utils.page_payload(items, total, page, per_page))


@routes.get('/api/conversations/{user_id}')
async def conversation_detail(request):
    user_id = int(request.match_info['user_id'])
    user = await _get_user_or_404(user_id)
    if not await db.is_conversation_visible(user_id):
        raise web.HTTPNotFound(text='用户不存在')
    page, per_page, offset = http_utils.pagination(request, default_per=HISTORY_PAGE_SIZE, max_per=200)

    messages = await db.get_conversation_history(user_id, limit=per_page, offset=offset)
    total = await db.get_conversation_message_count(user_id)
    is_blocked, permanent = await db.is_blacklisted(user_id)

    return http_utils.ok({
        'user': {
            'user_id': user['user_id'],
            'username': user.get('username'),
            'first_name': user.get('first_name'),
            'last_name': user.get('last_name'),
            'is_verified': bool(user.get('is_verified')),
            'is_blacklisted': bool(is_blocked),
            'blacklist_permanent': bool(permanent),
            'blacklist_strikes': user.get('blacklist_strikes', 0),
            'thread_id': user.get('thread_id'),
            'created_at': user.get('created_at'),
            'last_active': user.get('last_active'),
            'exempted': await db.is_exempted(user_id),
            'exemption': await db.get_exemption(user_id),
            'pending_appeal': await db.get_pending_review_appeal(user_id),
        },
        'messages': http_utils.page_payload(messages, total, page, per_page),
    })


# 建话题的逻辑在 services/thread_manager.py，申诉流程也要用
_ensure_user_thread = ensure_user_thread


@routes.post('/api/conversations/{user_id}/reply')
async def reply_to_user(request):
    user_id = int(request.match_info['user_id'])
    user = await _get_user_or_404(user_id)
    data = await http_utils.read_json(request)
    text = str(data.get('text') or '').strip()
    if not text:
        return http_utils.fail('回复内容不能为空')
    if len(text) > 4096:
        return http_utils.fail('回复内容超过 4096 字上限')

    bot = request.app[keys.BOT_KEY]
    try:
        sent = await bot.send_message(chat_id=user_id, text=text, disable_web_page_preview=True)
    except TelegramError as exc:
        logger.warning('Web 面板回复用户 %s 失败: %s', user_id, exc)
        return http_utils.fail(f'发送失败: {exc}')

    thread_id = await _ensure_user_thread(bot, user)
    mirror = None
    # 镜像到论坛话题时原样转发，不加任何来源前缀
    mirror_text = text

    if thread_id and config.FORUM_GROUP_ID:
        try:
            mirror = await bot.send_message(
                chat_id=config.FORUM_GROUP_ID,
                text=mirror_text,
                message_thread_id=thread_id,
                disable_web_page_preview=True,
            )
        except BadRequest as exc:
            err_lower = str(exc).lower()
            if 'thread not found' in err_lower or 'topic not found' in err_lower:
                logger.info('用户 %s 原话题 %s 已失效，尝试重建话题', user_id, thread_id)
                await db.update_user_thread_id(user_id, None)
                user['thread_id'] = None
                thread_id = await _ensure_user_thread(bot, user)
                if thread_id:
                    try:
                        mirror = await bot.send_message(
                            chat_id=config.FORUM_GROUP_ID,
                            text=mirror_text,
                            message_thread_id=thread_id,
                            disable_web_page_preview=True,
                        )
                    except TelegramError as retry_exc:
                        logger.warning('Web 面板回复重建话题 %s 后镜像仍失败: %s', thread_id, retry_exc)
            else:
                logger.warning('Web 面板回复镜像到话题 %s 失败: %s', thread_id, exc)
        except TelegramError as exc:
            logger.warning('Web 面板回复镜像到话题 %s 失败: %s', thread_id, exc)

    # 始终写入 message_mappings：即使论坛镜像失败，也要记录本次 admin_to_user 回复，
    # 否则 get_pending_reply_topics 会认为该用户一直处于「待回复」状态。
    await db.save_message_mapping(
        user_id=user_id,
        source_chat_id=config.FORUM_GROUP_ID if mirror else user_id,
        source_message_id=mirror.message_id if mirror else sent.message_id,
        dest_chat_id=user_id,
        dest_message_id=sent.message_id,
        direction='admin_to_user',
        thread_id=thread_id,
    )

    await db.save_message(
        user_id=user_id,
        message_id=sent.message_id,
        content=text,
        direction='admin_to_user',
        dest_message_id=mirror.message_id if mirror else None,
        thread_id=thread_id,
    )
    events.publish(
        'message',
        user_id=user_id,
        direction='admin_to_user',
        preview=text[:120],
        message_id=sent.message_id,
    )
    http_utils.audit(request, 'conversation.reply', f'user_id={user_id} 长度={len(text)}')
    return http_utils.ok({'message_id': sent.message_id, 'mirrored': bool(mirror)})


@routes.post('/api/conversations/read-all')
async def mark_all_read(request):
    count = await db.mark_all_conversations_read()
    events.publish('user_status', change='read_all')
    http_utils.audit(request, 'conversation.read_all', f'会话数={count}')
    return http_utils.ok({'count': count})


@routes.post('/api/conversations/{user_id}/read')
async def mark_read(request):
    user_id = int(request.match_info['user_id'])
    await _get_user_or_404(user_id)
    await db.mark_conversation_read(user_id)
    return http_utils.ok({'user_id': user_id})


@routes.post('/api/conversations/{user_id}/unverify')
async def unverify_user(request):
    user_id = int(request.match_info['user_id'])
    await _get_user_or_404(user_id)
    await moderation.revoke_verification(user_id)
    http_utils.audit(request, 'conversation.unverify', f'user_id={user_id}')
    return http_utils.ok({'user_id': user_id, 'is_verified': False})


@routes.post('/api/conversations/{user_id}/verify')
async def verify_user(request):
    user_id = int(request.match_info['user_id'])
    await _get_user_or_404(user_id)
    await moderation.restore_verification(user_id)
    http_utils.audit(request, 'conversation.verify', f'user_id={user_id}')
    return http_utils.ok({'user_id': user_id, 'is_verified': True})


@routes.post('/api/conversations/{user_id}/review-pause')
async def pause_review(request):
    """暂停 AI 审查：minutes 为分钟数，permanent=true 为永久。会一并批准待处理的申请并通知用户。"""
    user_id = int(request.match_info['user_id'])
    await _get_user_or_404(user_id)
    data = await http_utils.read_json(request)
    if data.get('permanent'):
        minutes = None
    else:
        raw = str(data.get('minutes') or data.get('duration') or '').strip()
        minutes = moderation.parse_duration(raw)
        if minutes is None:
            return http_utils.fail('请填写暂停时长，例如 30、3h、2d')
    try:
        result = await moderation.grant_review_pause(request.app[keys.BOT_KEY], user_id, minutes, 0, 'Web 面板')
    except ValueError as exc:
        return http_utils.fail(str(exc))
    http_utils.audit(request, 'conversation.review_pause', f"user_id={user_id} 时长={result['duration_text']}")
    return http_utils.ok(result)


@routes.delete('/api/conversations/{user_id}/review-pause')
async def resume_review(request):
    user_id = int(request.match_info['user_id'])
    await _get_user_or_404(user_id)
    await moderation.resume_review(user_id)
    http_utils.audit(request, 'conversation.review_resume', f'user_id={user_id}')
    return http_utils.ok({'user_id': user_id})


@routes.post('/api/conversations/{user_id}/appeal/reject')
async def reject_appeal(request):
    user_id = int(request.match_info['user_id'])
    await _get_user_or_404(user_id)
    if not await moderation.reject_appeal(request.app[keys.BOT_KEY], user_id, 0, 'Web 面板'):
        return http_utils.fail('这个用户没有待处理的申请')
    http_utils.audit(request, 'conversation.appeal_reject', f'user_id={user_id}')
    return http_utils.ok({'user_id': user_id})


@routes.post('/api/conversations/{user_id}/block')
async def block_user(request):
    user_id = int(request.match_info['user_id'])
    await _get_user_or_404(user_id)
    data = await http_utils.read_json(request)
    reason = str(data.get('reason') or '').strip() or '由 Web 面板封禁'
    permanent = bool(data.get('permanent'))
    message = await blacklist_service.block_user(user_id, reason, 0, permanent)
    events.publish('user_status', user_id=user_id, blacklisted=True)
    http_utils.audit(request, 'conversation.block', f'user_id={user_id} permanent={permanent} reason={reason}')
    return http_utils.ok({'message': message})


@routes.post('/api/conversations/{user_id}/unblock')
async def unblock_user(request):
    user_id = int(request.match_info['user_id'])
    message = await blacklist_service.unblock_user(user_id)
    events.publish('user_status', user_id=user_id, blacklisted=False)
    http_utils.audit(request, 'conversation.unblock', f'user_id={user_id}')
    return http_utils.ok({'message': message})


@routes.get('/api/media/{file_id}')
async def media_proxy(request):
    file_id = request.match_info['file_id']
    bot = request.app[keys.BOT_KEY]
    session = request.app[keys.HTTP_SESSION_KEY]
    try:
        tg_file = await bot.get_file(file_id)
    except TelegramError as exc:
        return http_utils.fail(f'获取文件失败: {exc}', status=404)

    base_file_url = getattr(bot, 'base_file_url', None) or f"{bot.base_url}bot{bot.token}/"
    url = f"{base_file_url}{tg_file.file_path}"
    guessed_type = mimetypes.guess_type(tg_file.file_path or '')[0]
    content_type = guessed_type if guessed_type in INLINE_MEDIA_TYPES else 'application/octet-stream'

    try:
        upstream = await session.get(url)
    except Exception as exc:
        logger.warning('媒体代理请求失败: %s', exc)
        return http_utils.fail('拉取媒体失败', status=502)

    if upstream.status != 200:
        upstream.release()
        return http_utils.fail('拉取媒体失败', status=502)

    response = web.StreamResponse(
        status=200,
        headers={
            'Content-Type': content_type,
            'Content-Disposition': 'inline' if content_type in INLINE_MEDIA_TYPES else 'attachment',
            'Cache-Control': 'private, max-age=3600',
        },
    )
    await response.prepare(request)
    try:
        async for chunk in upstream.content.iter_chunked(64 * 1024):
            await response.write(chunk)
    finally:
        upstream.release()
    await response.write_eof()
    return response
