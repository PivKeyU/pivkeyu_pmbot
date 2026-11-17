import asyncio
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import BadRequest, RetryAfter, TelegramError
from telegram.ext import ContextTypes
from database import models as db
from utils.message_sender import edit_message_by_type, get_message_media_info, send_message_by_type
from utils import copy as copy_text
from services import moderation
from web_panel import events as panel_events

logger = logging.getLogger(__name__)


async def _send_reply_to_user(update: Update, context: ContextTypes.DEFAULT_TYPE, user_id: int):
    reply_to_message_id = None
    if update.message.reply_to_message:
        mapping = await db.get_message_mapping_for_user(
            update.message.chat_id,
            update.message.reply_to_message.message_id,
            user_id,
        )
        if mapping and mapping.get('user_id') == user_id:
            if mapping['source_chat_id'] == user_id:
                reply_to_message_id = mapping['source_message_id']
            elif mapping['dest_chat_id'] == user_id:
                reply_to_message_id = mapping['dest_message_id']

    try:
        sent = await send_message_by_type(
            context.bot,
            update.message,
            user_id,
            None,
            True,
            reply_to_message_id=reply_to_message_id,
        )
    except TelegramError as exc:
        logger.warning("转发管理员回复到用户 %s 失败: %s", user_id, exc)
        try:
            await update.message.reply_text(copy_text.with_deco(f"消息没有送到用户那里：{exc}", 'ERROR'))
        except TelegramError:
            pass
        return None

    if not sent:
        try:
            await update.message.reply_text(copy_text.with_deco("女仆暂不支持转发这种消息格式呢。", 'ERROR'))
        except TelegramError:
            pass
        return None

    await db.save_message_mapping(
        user_id=user_id,
        source_chat_id=update.message.chat_id,
        source_message_id=update.message.message_id,
        dest_chat_id=user_id,
        dest_message_id=sent.message_id,
        direction="admin_to_user",
        thread_id=update.message.message_thread_id,
    )
    media_type, media_file_id = get_message_media_info(sent)
    await db.save_message(
        user_id=user_id,
        message_id=sent.message_id,
        content=update.message.text or update.message.caption or "",
        direction="admin_to_user",
        media_type=media_type,
        media_file_id=media_file_id,
        dest_message_id=update.message.message_id,
        thread_id=update.message.message_thread_id,
    )
    return sent


async def handle_admin_reply(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.is_topic_message:
        return
    if not update.effective_user or not await db.is_admin(update.effective_user.id):
        return

    # 回复的是「自定义暂停审查时长」提示：这是给女仆的指令，不转发给用户
    if await moderation.handle_custom_duration_reply(update.message, context.bot, context.application):
        return

    thread_id = update.message.message_thread_id

    user = await db.get_user_by_thread_id(thread_id)
    if not user:
        return

    user_id = user['user_id']

    sent = await _send_reply_to_user(update, context, user_id)
    if sent:
        media_type, _ = get_message_media_info(sent)
        # 推送给 Web 面板（没有面板订阅者时是空操作）
        panel_events.publish(
            'message',
            user_id=user_id,
            direction='admin_to_user',
            preview=(update.message.text or update.message.caption or '')[:120] or '[媒体]',
            message_id=sent.message_id,
            media_type=media_type,
        )


async def handle_edited_admin_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.edited_message
    if not message:
        return
    if not update.effective_user or not await db.is_admin(update.effective_user.id):
        return

    mappings = await db.get_message_mappings_by_source(message.chat_id, message.message_id)
    if not mappings:
        return

    for idx, mapping in enumerate(mappings):
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
                    await asyncio.sleep(min(float(exc.retry_after), 30.0) + 0.5)
                else:
                    logger.warning("同步管理员编辑遭遇限频: %s", exc)
            except BadRequest as exc:
                if "message is not modified" not in exc.message.lower():
                    logger.warning("同步管理员编辑失败: %s", exc)
                break
            except TelegramError as exc:
                logger.warning("同步管理员编辑失败: %s", exc)
                break
        if idx + 1 < len(mappings):
            await asyncio.sleep(0.05)


async def _format_filtered_messages(messages, page: int, total_pages: int):
    response = f"女仆拦截篮 (第 {page}/{total_pages} 页):\n\n"

    for idx, msg in enumerate(messages, 1):
        first_name = msg.get('first_name') or 'N/A'
        username = msg.get('username') or 'N/A'
        reason = msg.get('reason') or 'N/A'
        content = msg.get('content') or 'N/A'
        filtered_at = msg.get('filtered_at') or 'N/A'

        if len(reason) > 120:
            reason = reason[:120] + "..."
        if len(content) > 100:
            content = content[:100] + "..."

        entry = (
            f"【{idx}】\n"
            f"发送者: {first_name} (@{username})\n"
            f"拦截理由: {reason}\n"
            f"消息内容: {content}\n"
            f"拦截时间: {filtered_at}\n\n"
        )
        if len(response) + len(entry) > 4000:
            response += "...（内容过长，已截断）"
            break
        response += entry

    return response

async def _get_filtered_messages_keyboard(page: int, total_pages: int, callback_prefix: str = "filtered_page_"):
    keyboard = []
    
    if total_pages <= 1:
        return None
    
    buttons = []
    
    if page > 1:
        buttons.append(InlineKeyboardButton(copy_text.BTN_PREV_PAGE, callback_data=f"{callback_prefix}{page - 1}"))
    
    if page < total_pages:
        buttons.append(InlineKeyboardButton(copy_text.BTN_NEXT_PAGE, callback_data=f"{callback_prefix}{page + 1}"))
    
    if buttons:
        keyboard.append(buttons)
    
    return InlineKeyboardMarkup(keyboard) if keyboard else None

async def view_filtered(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await db.is_admin(update.effective_user.id):
        await update.message.reply_text(copy_text.perm_denied())
        return

    MESSAGES_PER_PAGE = 5
    page = 1

    total_count = await db.get_filtered_messages_count()
    
    if total_count == 0:
        await update.message.reply_text(copy_text.filtered_not_found())
        return
    
    total_pages = (total_count + MESSAGES_PER_PAGE - 1) // MESSAGES_PER_PAGE

    offset = (page - 1) * MESSAGES_PER_PAGE

    messages = await db.get_filtered_messages(MESSAGES_PER_PAGE, offset)
    
    if not messages:
        await update.message.reply_text(copy_text.filtered_not_found())
        return

    response = await _format_filtered_messages(messages, page, total_pages)

    keyboard = await _get_filtered_messages_keyboard(page, total_pages)

    if keyboard:
        await update.message.reply_text(response, reply_markup=keyboard)
    else:
        await update.message.reply_text(response)
