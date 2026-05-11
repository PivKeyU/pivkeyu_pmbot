from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import BadRequest, TelegramError
from telegram.ext import ContextTypes
from database import models as db
from utils.message_sender import edit_message_by_type, send_message_by_type

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

    sent = await send_message_by_type(
        context.bot,
        update.message,
        user_id,
        None,
        True,
        reply_to_message_id=reply_to_message_id,
    )
    if sent:
        await db.save_message_mapping(
            user_id=user_id,
            source_chat_id=update.message.chat_id,
            source_message_id=update.message.message_id,
            dest_chat_id=user_id,
            dest_message_id=sent.message_id,
            direction="admin_to_user",
            thread_id=update.message.message_thread_id,
        )
    return sent

async def handle_admin_reply(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.is_topic_message:
        return
    
    thread_id = update.message.message_thread_id
    
    user = await db.get_user_by_thread_id(thread_id)
    if not user:
        return
    
    user_id = user['user_id']
    
    await _send_reply_to_user(update, context, user_id)


async def handle_edited_admin_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.edited_message
    if not message:
        return

    mappings = await db.get_message_mappings_by_source(message.chat_id, message.message_id)
    if not mappings:
        return

    for mapping in mappings:
        try:
            await edit_message_by_type(
                context.bot,
                message,
                mapping['dest_chat_id'],
                mapping['dest_message_id'],
                disable_web_page_preview=True,
            )
        except BadRequest as exc:
            if "message is not modified" not in exc.message.lower():
                print(f"同步管理员编辑失败: {exc}")
        except TelegramError as exc:
            print(f"同步管理员编辑失败: {exc}")

async def _format_filtered_messages(messages, page: int, total_pages: int):
    response = f"女仆拦截篮 (第 {page}/{total_pages} 页):\n\n"
    
    for idx, msg in enumerate(messages, 1):
        first_name = msg.get('first_name') or 'N/A'
        username = msg.get('username') or 'N/A'
        reason = msg.get('reason') or 'N/A'
        content = msg.get('content') or 'N/A'
        filtered_at = msg.get('filtered_at') or 'N/A'

        if content and len(content) > 100:
            content = content[:100] + "..."
        
        response += (
            f"【{idx}】\n"
            f"主人: {first_name} (@{username})\n"
            f"拦截理由: {reason}\n"
            f"消息内容: {content}\n"
            f"拦截时间: {filtered_at}\n\n"
        )
    
    return response

async def _get_filtered_messages_keyboard(page: int, total_pages: int, callback_prefix: str = "filtered_page_"):
    keyboard = []
    
    if total_pages <= 1:
        return None
    
    buttons = []
    
    if page > 1:
        buttons.append(InlineKeyboardButton("上一页", callback_data=f"{callback_prefix}{page - 1}"))
    
    if page < total_pages:
        buttons.append(InlineKeyboardButton("下一页", callback_data=f"{callback_prefix}{page + 1}"))
    
    if buttons:
        keyboard.append(buttons)
    
    return InlineKeyboardMarkup(keyboard) if keyboard else None

async def view_filtered(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await db.is_admin(update.effective_user.id):
        await update.message.reply_text("主人没有权限吩咐这项工作哦。")
        return

    MESSAGES_PER_PAGE = 5
    page = 1

    total_count = await db.get_filtered_messages_count()
    
    if total_count == 0:
        await update.message.reply_text("没有找到女仆拦截篮。")
        return
    
    total_pages = (total_count + MESSAGES_PER_PAGE - 1) // MESSAGES_PER_PAGE

    offset = (page - 1) * MESSAGES_PER_PAGE

    messages = await db.get_filtered_messages(MESSAGES_PER_PAGE, offset)
    
    if not messages:
        await update.message.reply_text("没有找到女仆拦截篮。")
        return

    response = await _format_filtered_messages(messages, page, total_pages)

    keyboard = await _get_filtered_messages_keyboard(page, total_pages)

    if keyboard:
        await update.message.reply_text(response, reply_markup=keyboard)
    else:
        await update.message.reply_text(response)
