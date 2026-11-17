import asyncio
from telegram import Update, constants
from telegram.error import BadRequest, RetryAfter, TelegramError
from telegram.ext import ContextTypes
from database import models as db
from services.verification import (
    create_verification, get_pending_verification_message, is_verification_pending, send_verification_prompt,
)
from services.thread_manager import get_or_create_thread
from services.gemini_service import gemini_service
from utils.media_converter import sticker_to_image
from utils.forward import forward_label
from utils.message_sender import edit_message_by_type, send_message_by_type
from services.rate_limiter import rate_limiter
from services import moderation, spam_filter
from config import config
from utils import copy as copy_text
from web_panel import events as panel_events


def _extract_media_meta(message) -> tuple[str | None, str | None]:
    """统一提取消息中的媒体类型与 file_id。"""
    if message.photo:
        return "photo", message.photo[-1].file_id
    if message.sticker:
        return "sticker", message.sticker.file_id
    if message.animation:
        return "animation", message.animation.file_id
    if message.video:
        return "video", message.video.file_id
    if message.document:
        return "document", message.document.file_id
    if message.audio:
        return "audio", message.audio.file_id
    if message.voice:
        return "voice", message.voice.file_id
    if message.video_note:
        return "video_note", message.video_note.file_id
    return None, None


def _extract_review_text(message) -> str:
    """提取用于关键词拦截/AI 审核的全部可见与隐藏文本（含链接实体和文档文件名）。"""
    parts = []
    base_text = message.text or message.caption or ""
    if base_text:
        parts.append(base_text)
    for entities_attr in ("entities", "caption_entities"):
        for ent in getattr(message, entities_attr, None) or []:
            url = getattr(ent, "url", None)
            if url and url not in base_text:
                parts.append(url)
    doc = getattr(message, "document", None)
    if doc and getattr(doc, "file_name", None):
        parts.append(f"[file:{doc.file_name}]")
    return "\n".join(parts).strip()


def _is_thread_missing_error(err: Exception) -> bool:
    msg = str(getattr(err, "message", "") or err).lower()
    return "thread not found" in msg or "topic not found" in msg


async def _download_review_image(message):
    """仅在需要 AI 视觉分析时下载图片或静态贴纸。"""
    if message.photo:
        photo_file = await message.photo[-1].get_file()
        return await photo_file.download_as_bytearray()
    if message.sticker and not message.sticker.is_animated and not message.sticker.is_video:
        sticker_file = await message.sticker.get_file()
        sticker_bytes = await sticker_file.download_as_bytearray()
        return await sticker_to_image(sticker_bytes)
    return None


async def _maybe_send_autoreply(message, context: ContextTypes.DEFAULT_TYPE, user_id: int, thread_id: int, forwarded_message_id: int | None):
    if not message.text or not await db.get_autoreply_enabled():
        return
    knowledge_base_content = await db.get_all_knowledge_content()
    if not knowledge_base_content:
        return

    autoreply_text = await gemini_service.generate_autoreply(message.text, knowledge_base_content)
    if not autoreply_text:
        return

    if len(autoreply_text) > 4000:
        autoreply_text = autoreply_text[:3990] + "\n...（已截断）"

    sent_autoreply = None
    try:
        sent_autoreply = await message.reply_text(autoreply_text, parse_mode='Markdown')
    except Exception as e:
        print(f"Markdown解析失败，使用纯文本: {e}")
        sent_autoreply = await message.reply_text(autoreply_text)

    mirror_autoreply = None
    if forwarded_message_id and thread_id:
        admin_notification = f"自动回复女仆内容:\n\n{autoreply_text}"
        if len(admin_notification) > 4096:
            admin_notification = admin_notification[:4085] + "..."
        try:
            mirror_autoreply = await context.bot.send_message(
                chat_id=config.FORUM_GROUP_ID,
                text=admin_notification,
                message_thread_id=thread_id,
                reply_to_message_id=forwarded_message_id,
                parse_mode='Markdown',
            )
        except Exception as e:
            print(f"发送自动回复通知给管理员失败（Markdown），尝试纯文本: {e}")
            try:
                mirror_autoreply = await context.bot.send_message(
                    chat_id=config.FORUM_GROUP_ID,
                    text=admin_notification,
                    message_thread_id=thread_id,
                    reply_to_message_id=forwarded_message_id,
                )
            except Exception as e2:
                print(f"发送自动回复通知给管理员失败: {e2}")

    if sent_autoreply:
        await db.save_message(
            user_id=user_id,
            message_id=sent_autoreply.message_id,
            content=f"（AI 自动回复）\n{autoreply_text}",
            direction="admin_to_user",
            dest_message_id=mirror_autoreply.message_id if mirror_autoreply else None,
            thread_id=thread_id,
        )
        panel_events.publish(
            'message',
            user_id=user_id,
            direction='admin_to_user',
            preview=f"（AI 自动回复）{autoreply_text}"[:120],
            message_id=sent_autoreply.message_id,
        )


async def handle_invalid_thread(update: Update, context: ContextTypes.DEFAULT_TYPE, user_id: int):
    await db.update_user_thread_id(user_id, None)
    if not config.VERIFICATION_ENABLED or await db.is_admin(user_id):
        # 免验证模式下会客厅丢失时直接重建话题并重发，不强迫重新答题
        thread_id, _ = await get_or_create_thread(update, context)
        if not thread_id:
            await update.message.reply_text(
                copy_text.topic_create_failed(copy_text.address(await db.is_admin(user_id)))
            )
            return
        sent_msg = await _resend_message(update, context, thread_id)
        if sent_msg:
            await _maybe_send_autoreply(update.message, context, user_id, thread_id, sent_msg.message_id)
        return

    await db.update_user_verification(user_id, False)
    context.user_data['pending_update'] = update
    prompt = await create_verification(user_id)
    full_message = copy_text.with_deco_head(
        f"{copy_text.THREAD_CLOSED_REVERIFY}{prompt.text}", 'ASK')
    await send_verification_prompt(update.message, prompt, full_message)

async def _get_private_reply_target(message, user_id: int, thread_id: int):
    if not message.reply_to_message:
        return None

    mapping = await db.get_message_mapping(message.chat_id, message.reply_to_message.message_id)
    if not mapping or mapping.get('user_id') != user_id:
        return None

    if mapping['source_chat_id'] == config.FORUM_GROUP_ID or mapping['dest_chat_id'] == config.FORUM_GROUP_ID:
        if not mapping.get('thread_id') or mapping['thread_id'] != thread_id:
            return None

    if mapping.get('thread_id') and mapping['thread_id'] != thread_id:
        return None

    if mapping['source_chat_id'] == config.FORUM_GROUP_ID:
        return mapping['source_message_id']
    if mapping['dest_chat_id'] == config.FORUM_GROUP_ID:
        return mapping['dest_message_id']
    return None


async def _resend_message(update: Update, context: ContextTypes.DEFAULT_TYPE, thread_id: int):
    message = update.message
    reply_to_message_id = await _get_private_reply_target(message, update.effective_user.id, thread_id)
    sent = await send_message_by_type(
        context.bot,
        message,
        config.FORUM_GROUP_ID,
        thread_id,
        True,
        reply_to_message_id=reply_to_message_id,
    )
    if sent:
        await db.save_message_mapping(
            user_id=update.effective_user.id,
            source_chat_id=message.chat_id,
            source_message_id=message.message_id,
            dest_chat_id=config.FORUM_GROUP_ID,
            dest_message_id=sent.message_id,
            direction="user_to_admin",
            thread_id=thread_id,
        )
        media_type, media_file_id = _extract_media_meta(message)
        # 记录消息预览（含论坛侧 message_id），供 /todo 待办聚合展示内容
        await db.save_message(
            user_id=update.effective_user.id,
            message_id=message.message_id,
            content=message.text or message.caption or "",
            direction="user_to_admin",
            media_type=media_type,
            media_file_id=media_file_id,
            dest_message_id=sent.message_id,
            thread_id=thread_id,
            forward_source=forward_label(message),
        )
        # 推送给 Web 面板（没有面板订阅者时是空操作）
        panel_events.publish(
            'message',
            user_id=update.effective_user.id,
            direction='user_to_admin',
            preview=(message.text or message.caption or '')[:120] or f'[{media_type or "媒体"}]',
            message_id=message.message_id,
            media_type=media_type,
        )
    return sent


async def deliver_pending_update(context: ContextTypes.DEFAULT_TYPE, user_id: int) -> bool:
    """验证通过后补发暂存的那条消息；没有暂存消息时返回 False。"""
    if 'pending_update' not in context.user_data:
        return False

    pending_update = context.user_data.pop('pending_update')
    message = pending_update.message

    is_blocked, _ = await db.is_blacklisted(user_id)
    if is_blocked:
        return False

    is_admin_user = await db.is_admin(user_id)
    review_text = _extract_review_text(message)
    if not is_admin_user and review_text:
        spam_result = await spam_filter.check_message_text(review_text)
        if spam_result.matched:
            media_type, media_file_id = _extract_media_meta(message)
            reason = "关键词广告拦截命中: " + ", ".join(spam_result.hits)
            await db.save_filtered_message(
                user_id=user_id,
                message_id=message.message_id,
                content=review_text,
                reason=reason,
                media_type=media_type,
                media_file_id=media_file_id,
                forward_source=forward_label(message),
            )
            if spam_result.auto_block:
                await db.add_to_blacklist(
                    user_id, reason=reason, blocked_by=config.BOT_ID, permanent=True,
                )
                await db.set_user_blacklist_strikes(user_id, 99)
                await message.reply_text(copy_text.with_deco("这条消息女仆拦下啦，通道也按规则锁上。", 'CAT'))
            else:
                await message.reply_text(copy_text.with_deco("这条消息女仆拦下啦，没有继续递送。", 'CAT'))
            return True

    should_forward = True
    is_exempted = await db.is_exempted(user_id)
    skip_untexted_video = (message.video or message.animation) and not review_text
    if config.ENABLE_AI_FILTER and not is_exempted and not is_admin_user and not skip_untexted_video:
        image_bytes = None if (message.video or message.animation) else await _download_review_image(message)
        analyzing_message = await context.bot.send_message(
            chat_id=message.chat_id,
            text=copy_text.scan_message(copy_text.address(is_admin_user)),
            reply_to_message_id=message.message_id
        )
        analysis_result = await gemini_service.analyze_message(message, image_bytes)
        if analysis_result.get("is_spam"):
            should_forward = False
            media_type, media_file_id = _extract_media_meta(message)
            await db.save_filtered_message(
                user_id=user_id,
                message_id=message.message_id,
                content=review_text or message.text or message.caption,
                reason=analysis_result.get("reason"),
                media_type=media_type,
                media_file_id=media_file_id,
                forward_source=forward_label(message),
            )
            reason = analysis_result.get("reason", "暂时没有写明理由")
            await analyzing_message.edit_text(
                copy_text.msg_blocked(reason),
                reply_markup=await moderation.blocked_reply_markup(reason),
            )
            if await moderation.maybe_auto_revoke(context.bot, user_id):
                await message.reply_text(copy_text.with_deco(
                    '客人最近被拦下的次数有点多，女仆先收回验证结果啦，下次发消息要重新答题哦。', 'BLOCK'))
        else:
            await analyzing_message.delete()

    if should_forward:
        thread_id, _ = await get_or_create_thread(pending_update, context)
        if not thread_id:
            await pending_update.message.reply_text(copy_text.topic_create_failed(copy_text.address(is_admin_user)))
            return True

        try:
            sent_msg = await _resend_message(pending_update, context, thread_id)
            if sent_msg:
                await _maybe_send_autoreply(message, context, user_id, thread_id, sent_msg.message_id)
            else:
                await pending_update.message.reply_text(
                    copy_text.with_deco("女仆看不懂这种格式呢，客人换一种交给女仆吧。", 'ERROR')
                )
        except BadRequest as e:
            if _is_thread_missing_error(e):
                await handle_invalid_thread(pending_update, context, user_id)
            else:
                print(f"发送消息时发生未知错误: {e}")
                await pending_update.message.reply_text(copy_text.delivery_failed(copy_text.address(is_admin_user)))
    return True


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    from network_test.handlers import handle_message as network_handle_message
    handled = await network_handle_message(update, context)
    if handled:
        return

    user = update.effective_user
    is_admin_user = await db.is_admin(user.id)

    if not is_admin_user:
        is_over_limit, was_warned = await rate_limiter.check_user_rate_limit(user.id)

        if is_over_limit:
            if was_warned:
                context.user_data.pop('pending_update', None)
                await db.add_to_blacklist(
                    user.id,
                    reason="忽略速率限制警告，多次超出限制",
                    blocked_by=config.BOT_ID,
                    permanent=True
                )
                await db.set_user_blacklist_strikes(user.id, 99)
                await update.message.reply_text(
                    copy_text.with_deco_head(
                        "不是女仆想锁客人的门……提醒过之后消息还是这么密集，女仆只能把通道永久锁上。\n\n"
                        "有疑问就找管理员女仆长吧。",
                        'BLOCK',
                    )
                )
                return
            else:
                await rate_limiter.mark_user_warned(user.id)
                await update.message.reply_text(
                    copy_text.with_deco_head(
                        f"提醒客人：消息发送得太快啦，女仆的小托盘快端不稳了。\n\n"
                        f"当前宅邸规则：每分钟最多 {config.MAX_MESSAGES_PER_MINUTE} 条消息。\n\n"
                        f"请稍等片刻再试；如果继续超速，女仆会永久锁上通道哦。",
                        'BLOCK',
                    )
                )
                return

    if 'pending_update' in context.user_data:
        if context.user_data['pending_update'].update_id == update.update_id:
            context.user_data.pop('pending_update')

    is_blocked, is_permanent = await db.is_blacklisted(user.id)
    if is_blocked and not is_admin_user:
        context.user_data.pop('pending_update', None)
        if is_permanent:
            await update.message.reply_text(
                copy_text.with_deco(
                    "客人的通道已经被永久锁上啦，有事就请找管理员女仆长申诉吧。", 'BLOCK')
            )
            return

        if not config.AUTO_UNBLOCK_ENABLED:
            await update.message.reply_text(
                copy_text.with_deco("自动解封女仆现在在休息，客人直接找管理员女仆长申诉嘛。", 'BLOCK'))
            return

        from services.blacklist import start_unblock_process
        message, keyboard = await start_unblock_process(user.id)
        if message and keyboard:
            await update.message.reply_text(message, reply_markup=keyboard, parse_mode='Markdown')
        elif message:
            await update.message.reply_text(message)
        return

    user_data = await db.get_user(user.id)
    is_new_user = False

    if not user_data:
        await db.add_user(
            user_id=user.id,
            username=user.username,
            first_name=user.first_name,
            last_name=user.last_name,
            language_code=user.language_code
        )
        user_data = await db.get_user(user.id)
        is_new_user = True
    else:
        await db.update_user_profile(
            user_id=user.id,
            username=user.username,
            first_name=user.first_name,
            last_name=user.last_name,
            language_code=user.language_code
        )

    message = update.message
    review_text = _extract_review_text(message)
    if not is_admin_user:
        spam_result = await spam_filter.check_message_text(review_text)
        if spam_result.matched:
            media_type, media_file_id = _extract_media_meta(message)
            reason = "关键词广告拦截命中: " + ", ".join(spam_result.hits)
            await db.save_filtered_message(
                user_id=user.id,
                message_id=message.message_id,
                content=review_text,
                reason=reason,
                media_type=media_type,
                media_file_id=media_file_id,
                forward_source=forward_label(message),
            )
            if spam_result.auto_block:
                context.user_data.pop('pending_update', None)
                await db.add_to_blacklist(
                    user.id,
                    reason=reason,
                    blocked_by=config.BOT_ID,
                    permanent=True,
                )
                await db.set_user_blacklist_strikes(user.id, 99)
                await update.message.reply_text(
                    copy_text.with_deco("这条消息女仆拦下啦，通道也按规则锁上。", 'CAT'))
            else:
                await update.message.reply_text(
                    copy_text.with_deco("这条消息女仆拦下啦，没有继续递送。", 'CAT'))
            return

    if is_new_user and not is_admin_user and config.VERIFICATION_ENABLED:
        welcome_message = (
            f"客人好呀，{user.first_name}！\n\n"
            "这里是随时待命的双向聊天女仆。\n"
            "客人可以直接把消息交给女仆，女仆会乖乖送到管理员那边。\n\n"
            "不过递送第一条消息前，请先完成一个小验证哦。"
        )
        await update.message.reply_text(copy_text.with_deco_head(welcome_message, 'GREET'))

    if not user_data.get('is_verified'):
        if not config.VERIFICATION_ENABLED or is_admin_user:
            await db.update_user_verification(user.id, is_verified=True)
        else:
            has_pending, is_expired = is_verification_pending(user.id)

            if has_pending and not is_expired:
                prompt = get_pending_verification_message(user.id)
                if prompt:
                    context.user_data['pending_update'] = update
                    await send_verification_prompt(
                        update.message,
                        prompt,
                        copy_text.with_deco_head(
                            f"{copy_text.VERIFY_INVITE_PENDING}\n\n{prompt.question}", 'ASK'),
                    )
                    return
            context.user_data['pending_update'] = update
            prompt = await create_verification(user.id)
            await send_verification_prompt(
                update.message, prompt, copy_text.with_deco_head(prompt.text, 'ASK'))
            return

    is_exempted = await db.is_exempted(user.id)
    skip_untexted_video = (message.video or message.animation) and not review_text
    if config.ENABLE_AI_FILTER and not is_exempted and not is_admin_user and not skip_untexted_video:
        image_bytes = None if (message.video or message.animation) else await _download_review_image(message)
        analyzing_message = await context.bot.send_message(
            chat_id=message.chat_id,
            text=copy_text.scan_message(copy_text.address(is_admin_user)),
            reply_to_message_id=message.message_id
        )

        analysis_result = await gemini_service.analyze_message(message, image_bytes)
        if analysis_result.get("is_spam"):
            media_type, media_file_id = _extract_media_meta(message)
            await db.save_filtered_message(
                user_id=user.id,
                message_id=message.message_id,
                content=review_text or message.text or message.caption,
                reason=analysis_result.get("reason"),
                media_type=media_type,
                media_file_id=media_file_id,
                forward_source=forward_label(message),
            )
            reason = analysis_result.get("reason", "暂时没有写明理由")
            await analyzing_message.edit_text(
                copy_text.msg_blocked(reason),
                reply_markup=await moderation.blocked_reply_markup(reason),
            )
            # 开启「AI 自动撤销验证」时，短时间内多次违规就收回验证结果
            if await moderation.maybe_auto_revoke(context.bot, user.id):
                await update.message.reply_text(copy_text.with_deco(
                    '客人最近被拦下的次数有点多，女仆先收回验证结果啦，下次发消息要重新答题哦。', 'BLOCK'))
            return
        else:
            await analyzing_message.delete()

    thread_id, _ = await get_or_create_thread(update, context)
    if not thread_id:
        await update.message.reply_text(copy_text.topic_create_failed(copy_text.address(is_admin_user)))
        return

    forwarded_message_id = None
    try:
        sent_msg = await _resend_message(update, context, thread_id)
        if not sent_msg:
            await update.message.reply_text(
                copy_text.with_deco("女仆看不懂这种格式呢，客人换一种交给女仆吧。", 'ERROR'))
            return
        forwarded_message_id = sent_msg.message_id
    except BadRequest as e:
        if _is_thread_missing_error(e):
            await handle_invalid_thread(update, context, user.id)
            return
        else:
            print(f"发送消息时发生未知错误: {e}")
            await update.message.reply_text(copy_text.delivery_failed(copy_text.address(is_admin_user)))
            return

    await _maybe_send_autoreply(message, context, user.id, thread_id, forwarded_message_id)


async def handle_edited_private_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.edited_message
    if not message:
        return

    user_id = message.from_user.id if message.from_user else message.chat_id
    is_admin_user = await db.is_admin(user_id)
    if not is_admin_user:
        is_blocked, _ = await db.is_blacklisted(user_id)
        if is_blocked:
            return

    mappings = await db.get_message_mappings_by_source(message.chat_id, message.message_id)
    if not mappings:
        return

    review_text = _extract_review_text(message)
    if not is_admin_user and review_text:
        spam_result = await spam_filter.check_message_text(review_text)
        if spam_result.matched:
            media_type, media_file_id = _extract_media_meta(message)
            reason = "编辑后命中关键词广告拦截: " + ", ".join(spam_result.hits)
            await db.save_filtered_message(
                user_id=user_id,
                message_id=message.message_id,
                content=review_text,
                reason=reason,
                media_type=media_type,
                media_file_id=media_file_id,
                forward_source=forward_label(message),
            )
            if spam_result.auto_block:
                await db.add_to_blacklist(
                    user_id, reason=reason, blocked_by=config.BOT_ID, permanent=True,
                )
                await db.set_user_blacklist_strikes(user_id, 99)
                await message.reply_text(copy_text.with_deco("编辑后的消息命中拦截规则，通道已按规则锁上。", 'CAT'))
            else:
                await message.reply_text(copy_text.with_deco("编辑后的消息命中拦截规则，未同步给管理员。", 'CAT'))
            return

        if config.ENABLE_AI_FILTER and not await db.is_exempted(user_id):
            analysis_result = await gemini_service.analyze_message(message, None)
            if analysis_result.get("is_spam"):
                media_type, media_file_id = _extract_media_meta(message)
                reason = analysis_result.get("reason", "编辑内容未通过内容审核")
                await db.save_filtered_message(
                    user_id=user_id,
                    message_id=message.message_id,
                    content=review_text,
                    reason=f"编辑消息拦截: {reason}",
                    media_type=media_type,
                    media_file_id=media_file_id,
                    forward_source=forward_label(message),
                )
                await message.reply_text(copy_text.msg_blocked(reason))
                return

    for idx, mapping in enumerate(mappings):
        if idx > 0:
            await asyncio.sleep(0.05)
        for attempt in range(2):
            try:
                await edit_message_by_type(
                    context.bot,
                    message,
                    mapping['dest_chat_id'],
                    mapping['dest_message_id'],
                    disable_web_page_preview=True,
                )
                break
            except RetryAfter as exc:
                if attempt == 0:
                    await asyncio.sleep(min(float(exc.retry_after) + 1.0, 30.0))
                    continue
                print(f"同步用户编辑触发限流: {exc}")
            except BadRequest as exc:
                if "message is not modified" not in exc.message.lower():
                    print(f"同步用户编辑失败: {exc}")
                break
            except TelegramError as exc:
                print(f"同步用户编辑失败: {exc}")
                break
