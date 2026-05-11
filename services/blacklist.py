import time
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.helpers import escape_markdown
from database import models as db
from services.gemini_service import gemini_service
from config import config

pending_unblocks = {}

async def block_user(user_id: int, reason: str, admin_id: int, permanent: bool = False):
    await db.add_to_blacklist(user_id, reason, admin_id, permanent)
    return f"女仆已按管理员吩咐，将用户 {user_id}{'永久' if permanent else ''}请进黑名单小本本。\n登记理由: {reason}"

async def unblock_user(user_id: int):
    await db.remove_from_blacklist(user_id)
    await db.set_user_blacklist_strikes(user_id, 0)
    return f"女仆已按管理员吩咐，为用户 {user_id} 打开通道。"

def is_unblock_pending(user_id: int) -> tuple[bool, bool]:
    if user_id not in pending_unblocks:
        return False, True
    
    session = pending_unblocks[user_id]
    is_expired = time.time() - session['created_at'] > config.VERIFICATION_TIMEOUT
    
    if is_expired:
        del pending_unblocks[user_id]
        return False, True
    
    return True, False

def get_pending_unblock_message(user_id: int):
    if user_id not in pending_unblocks:
        return None
    
    session = pending_unblocks[user_id]
    
    if time.time() - session['created_at'] > config.VERIFICATION_TIMEOUT:
        del pending_unblocks[user_id]
        return None
    
    question = session['question']
    options = session['options']
    
    keyboard = [
        [InlineKeyboardButton(option, callback_data=f"unblock_{option}") for option in options]
    ]
    
    return question, InlineKeyboardMarkup(keyboard)

async def start_unblock_process(user_id: int):
    is_blocked, is_permanent = await db.is_blacklisted(user_id)
    
    if is_permanent:
        return "主人已被管理员永久锁门，这条自动申诉通道无法打开。", None

    has_pending, is_expired = is_unblock_pending(user_id)
    
    if has_pending and not is_expired:
        unblock_data = get_pending_unblock_message(user_id)
        if unblock_data:
            question, keyboard = unblock_data
            return (
                "主人还有解封小验证没完成，请先答完再继续发消息。\n\n"
                f"主人暂时被请到门外等候啦。\n\n"
                f"如果主人觉得这是误会，请回答下面的问题，女仆会帮您自动开门：\n\n{question}"
            ), keyboard
    
    challenge = await gemini_service.generate_unblock_question()
    question = challenge['question']
    correct_answer = challenge['correct_answer']
    options = challenge['options']
    
    pending_unblocks[user_id] = {
        'answer': correct_answer,
        'question': question,
        'options': options,
        'created_at': time.time()
    }
    
    keyboard = [
        [InlineKeyboardButton(option, callback_data=f"unblock_{option}") for option in options]
    ]
    
    return (
        "主人暂时被请到门外等候啦。\n\n"
        f"如果主人觉得这是误会，请回答下面的问题，女仆会帮您自动开门：\n\n{question}"
    ), InlineKeyboardMarkup(keyboard)

async def verify_unblock_answer(user_id: int, user_answer: str):
    if user_id not in pending_unblocks:
        return "解封小会话已经过期或不见啦。", False

    session = pending_unblocks[user_id]
    
    if time.time() - session['created_at'] > config.VERIFICATION_TIMEOUT:
        del pending_unblocks[user_id]
        return "解封验证超时啦，请重新发送消息领取新问题。", False

    if user_answer == session['answer']:
        del pending_unblocks[user_id]
        await db.remove_from_blacklist(user_id)
        await db.set_user_blacklist_strikes(user_id, 0)
        return "解封成功啦，主人现在可以正常发送消息。", True
    else:
        del pending_unblocks[user_id]
        await db.add_to_blacklist(user_id, reason="解封小验证失败", blocked_by=config.BOT_ID, permanent=True)
        return "答案不对，解封失败啦。女仆只能按规则永久锁上通道。", False

def _safe_text_for_markdown(text: str) -> str:
    if not text:
        return text
    
    dangerous_chars = r'_*[]()`'
    return "".join(f"\\{char}" if char in dangerous_chars else char for char in text)

async def get_blacklist_keyboard(page: int = 1, per_page: int = 5):
    total_count = await db.get_blacklist_count()
    
    if total_count == 0:
        return "黑名单小本本现在空空如也。", None

    total_pages = (total_count + per_page - 1) // per_page

    if page < 1:
        page = 1
    elif page > total_pages:
        page = total_pages

    offset = (page - 1) * per_page

    blacklist_users = await db.get_blacklist_paginated(limit=per_page, offset=offset)
    
    if not blacklist_users:
        return "黑名单小本本现在空空如也。", None

    keyboard = []
    message = f"黑名单小本本 (第 {page}/{total_pages} 页)\n\n"
    
    for idx, user in enumerate(blacklist_users, 1):
        user_id = user.get('user_id')
        first_name = user.get('first_name') or 'N/A'
        username = user.get('username')
        reason = user.get('reason') or '无'
        
        safe_first_name = _safe_text_for_markdown(first_name)
        safe_username = _safe_text_for_markdown(username) if username else None
        safe_reason = _safe_text_for_markdown(reason)
        
        user_info = f"{safe_first_name}"
        if safe_username:
            user_info += f" (@{safe_username})"
        
        message += f"{idx}. {user_info} (`{user_id}`)\n登记理由: {safe_reason}\n\n"
        
        keyboard.append([
            InlineKeyboardButton(f"为 {first_name} 开门", callback_data=f"admin_unblock_{user_id}")
        ])
    
    navigation_buttons = []
    if page > 1:
        navigation_buttons.append(InlineKeyboardButton("上一页", callback_data=f"blacklist_page_{page - 1}"))
    if page < total_pages:
        navigation_buttons.append(InlineKeyboardButton("下一页", callback_data=f"blacklist_page_{page + 1}"))
    
    if navigation_buttons:
        keyboard.append(navigation_buttons)

    return message, InlineKeyboardMarkup(keyboard)

async def get_all_users_keyboard(page: int = 1, per_page: int = 5, callback_prefix: str = "stats_list_all_users_page_", back_callback: str = "stats_back_to_menu", back_text: str = "返回统计小本本"):
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
    
    total_count = await db.get_total_users_count()
    
    if total_count == 0:
        return "主人名册现在还是空的。", None

    total_pages = (total_count + per_page - 1) // per_page

    if page < 1:
        page = 1
    elif page > total_pages:
        page = total_pages

    offset = (page - 1) * per_page

    users = await db.get_all_users_paginated(limit=per_page, offset=offset)
    
    if not users:
        return "主人名册现在还是空的。", None

    keyboard = []
    message = f"主人名册 (第 {page}/{total_pages} 页)\n\n"
    
    for idx, user in enumerate(users, 1):
        user_id = user.get('user_id')
        first_name = user.get('first_name') or 'N/A'
        username = user.get('username')
        is_blacklisted = user.get('is_blacklisted', 0)
        spam_count = user.get('spam_count', 0)
        
        safe_first_name = _safe_text_for_markdown(first_name)
        safe_username = _safe_text_for_markdown(username) if username else None
        
        user_info = f"{safe_first_name}"
        if safe_username:
            user_info += f" (@{safe_username})"
        
        blacklist_status = "是" if is_blacklisted else "否"
        has_spam = "是" if spam_count > 0 else "否"
        
        message += (
            f"{idx}. {user_info} (`{user_id}`)\n"
            f"   是否在黑名单小本本: {blacklist_status}\n"
            f"   是否递过可疑消息: {has_spam}\n"
            f"   可疑消息条数: {spam_count}\n\n"
        )
    
    navigation_buttons = []
    if page > 1:
        navigation_buttons.append(InlineKeyboardButton("上一页", callback_data=f"{callback_prefix}{page - 1}"))
    if page < total_pages:
        navigation_buttons.append(InlineKeyboardButton("下一页", callback_data=f"{callback_prefix}{page + 1}"))
    
    back_button = [InlineKeyboardButton(back_text, callback_data=back_callback)]
    keyboard.append(back_button)
    
    if navigation_buttons:
        keyboard.append(navigation_buttons)

    if not keyboard:
        keyboard = [[InlineKeyboardButton(back_text, callback_data=back_callback)]]
    
    return message, InlineKeyboardMarkup(keyboard)

async def get_blacklist_keyboard_detailed(page: int = 1, per_page: int = 5):
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
    
    total_count = await db.get_blacklist_count()
    
    if total_count == 0:
        return "黑名单小本本现在空空如也。", None

    total_pages = (total_count + per_page - 1) // per_page

    if page < 1:
        page = 1
    elif page > total_pages:
        page = total_pages

    offset = (page - 1) * per_page

    blacklist_users = await db.get_blacklist_paginated(limit=per_page, offset=offset)
    
    if not blacklist_users:
        return "黑名单小本本现在空空如也。", None

    keyboard = []
    message = f"黑名单小本本 (第 {page}/{total_pages} 页)\n\n"
    
    for idx, user in enumerate(blacklist_users, 1):
        user_id = user.get('user_id')
        user_details = await db.get_blacklist_user_details(user_id)
        
        if user_details:
            first_name = user_details.get('first_name') or 'N/A'
            username = user_details.get('username')
            last_name = user_details.get('last_name')
            reason = user_details.get('reason') or '无'
            blocked_at = user_details.get('blocked_at')
            permanent = user_details.get('permanent', 0)
            blacklist_strikes = user_details.get('blacklist_strikes', 0)
            spam_count = user_details.get('spam_count', 0)
            
            safe_first_name = _safe_text_for_markdown(first_name)
            safe_username = _safe_text_for_markdown(username) if username else None
            safe_reason = _safe_text_for_markdown(reason)
            
            user_info = f"{safe_first_name}"
            if last_name:
                user_info += f" {_safe_text_for_markdown(last_name)}"
            if safe_username:
                user_info += f" (@{safe_username})"
            
            permanent_text = "永久锁门" if permanent else "临时锁门"
            
            display_strikes = blacklist_strikes
            
            message += (
                f"{idx}. {user_info} (`{user_id}`)\n"
                f"   锁门类型: {permanent_text}\n"
                f"   锁门理由: {safe_reason}\n"
                f"   锁门次数: {display_strikes}\n"
                f"   可疑消息条数: {spam_count}\n"
            )
            if blocked_at:
                message += f"   锁门时间: {blocked_at}\n"
            message += "\n"
        else:
            first_name = user.get('first_name') or 'N/A'
            username = user.get('username')
            reason = user.get('reason') or '无'
            
            safe_first_name = _safe_text_for_markdown(first_name)
            safe_username = _safe_text_for_markdown(username) if username else None
            safe_reason = _safe_text_for_markdown(reason)
            
            user_info = f"{safe_first_name}"
            if safe_username:
                user_info += f" (@{safe_username})"
            
            message += f"{idx}. {user_info} (`{user_id}`)\n登记理由: {safe_reason}\n\n"
        
        keyboard.append([
            InlineKeyboardButton(f"为 {first_name} 开门", callback_data=f"admin_unblock_{user_id}")
        ])
    
    navigation_buttons = []
    if page > 1:
        navigation_buttons.append(InlineKeyboardButton("上一页", callback_data=f"stats_list_blacklist_page_{page - 1}"))
    if page < total_pages:
        navigation_buttons.append(InlineKeyboardButton("下一页", callback_data=f"stats_list_blacklist_page_{page + 1}"))
    
    back_button = [InlineKeyboardButton("返回统计小本本", callback_data="stats_back_to_menu")]
    keyboard.append(back_button)
    
    if navigation_buttons:
        keyboard.append(navigation_buttons)

    if not keyboard:
        keyboard = [[InlineKeyboardButton("返回统计小本本", callback_data="stats_back_to_menu")]]
    
    return message, InlineKeyboardMarkup(keyboard)

async def get_exemptions_keyboard(page: int = 1, per_page: int = 5):
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
    from datetime import datetime, timezone
    
    total_count = await db.get_exemptions_count()
    
    if total_count == 0:
        return "通行证名单现在空空如也。", None

    total_pages = (total_count + per_page - 1) // per_page

    if page < 1:
        page = 1
    elif page > total_pages:
        page = total_pages

    offset = (page - 1) * per_page

    exemptions = await db.get_exemptions_paginated(limit=per_page, offset=offset)
    
    if not exemptions:
        return "通行证名单现在空空如也。", None

    keyboard = []
    message = f"通行证名单 (第 {page}/{total_pages} 页)\n\n"
    
    for idx, exemption in enumerate(exemptions, 1):
        user_id = exemption.get('user_id')
        first_name = exemption.get('first_name') or 'N/A'
        username = exemption.get('username')
        is_permanent = bool(exemption.get('is_permanent', 0))
        expires_at = exemption.get('expires_at')
        reason = exemption.get('reason') or '无'
        
        safe_first_name = _safe_text_for_markdown(first_name)
        safe_username = _safe_text_for_markdown(username) if username else None
        safe_reason = _safe_text_for_markdown(reason)
        
        user_info = f"{safe_first_name}"
        if safe_username:
            user_info += f" (@{safe_username})"
        
        exemption_type = "永久通行证" if is_permanent else "临时通行证"
        expires_info = ""
        if not is_permanent and expires_at:
            try:
                expires_datetime = datetime.fromisoformat(expires_at.replace('Z', '+00:00'))
                if expires_datetime.tzinfo is None:
                    expires_datetime = expires_datetime.replace(tzinfo=timezone.utc)
                now = datetime.now(timezone.utc)
                if expires_datetime > now:
                    expires_info = f"\n到期时间: {expires_at}"
                else:
                    expires_info = "\n已过期"
            except Exception:
                expires_info = f"\n到期时间: {expires_at}"
        
        message += (
            f"{idx}. {user_info} (`{user_id}`)\n"
            f"   通行证类型: {exemption_type}\n"
            f"   登记理由: {safe_reason}{expires_info}\n\n"
        )
        
        keyboard.append([
            InlineKeyboardButton(f"收回 {first_name} 的通行证", callback_data=f"admin_remove_exemption_{user_id}")
        ])
    
    navigation_buttons = []
    if page > 1:
        navigation_buttons.append(InlineKeyboardButton("上一页", callback_data=f"panel_exemptions_page_{page - 1}"))
    if page < total_pages:
        navigation_buttons.append(InlineKeyboardButton("下一页", callback_data=f"panel_exemptions_page_{page + 1}"))
    
    if navigation_buttons:
        keyboard.append(navigation_buttons)

    return message, InlineKeyboardMarkup(keyboard)