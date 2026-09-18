import time
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.helpers import escape_markdown
from database import models as db
from database.db_manager import db_manager
from services.gemini_service import gemini_service
from config import config
from utils import copy as copy_text

pending_unblocks = {}

# 解封小验证允许的答错次数（与主验证一致，达到上限才升级永久拉黑）
MAX_UNBLOCK_ATTEMPTS = config.MAX_VERIFICATION_ATTEMPTS


def _cleanup_expired_unblocks() -> None:
    """惰性清理过期的解封会话，防止内存 dict 无限膨胀。"""
    now = time.time()
    expired = [uid for uid, session in pending_unblocks.items() if now > session.get('expires_at', now)]
    for uid in expired:
        del pending_unblocks[uid]

async def block_user(user_id: int, reason: str, admin_id: int, permanent: bool = False):
    await db.add_to_blacklist(user_id, reason, admin_id, permanent)
    # 锁门是不可撤销的：用「别扭的关心」语气，同时完整保留 user_id / 永久与否 / reason
    return (
        f"哼，既然管理员开口了……已经把 {user_id} 请进黑名单小本本啦"
        f"{'，这次是永久锁门哦。' if permanent else '。'}\n登记理由: {reason}"
    )

async def unblock_user(user_id: int):
    await db.remove_from_blacklist(user_id)
    await db.set_user_blacklist_strikes(user_id, 0)
    return f"哼，既然管理员开口了……女仆已经替 {user_id} 把通道打开了，进去吧。"

def is_unblock_pending(user_id: int) -> tuple[bool, bool]:
    _cleanup_expired_unblocks()

    if user_id not in pending_unblocks:
        return False, True

    session = pending_unblocks[user_id]
    is_expired = time.time() > session['expires_at']

    if is_expired:
        del pending_unblocks[user_id]
        return False, True
    
    return True, False

def get_pending_unblock_message(user_id: int):
    _cleanup_expired_unblocks()

    if user_id not in pending_unblocks:
        return None

    session = pending_unblocks[user_id]

    if time.time() > session['expires_at']:
        del pending_unblocks[user_id]
        return None
    
    question = session['question']
    options = session['options']
    
    keyboard = [
        [InlineKeyboardButton(option, callback_data=f"unblock_{option}") for option in options]
    ]
    
    return question, InlineKeyboardMarkup(keyboard)

async def start_unblock_process(user_id: int):
    _cleanup_expired_unblocks()

    is_blocked, is_permanent = await db.is_blacklisted(user_id)
    
    if is_permanent:
        return "客人已被管理员永久锁门，这条自动申诉通道打不开呢，得请管理员女仆长亲自出手才行。", None

    has_pending, is_expired = is_unblock_pending(user_id)
    
    if has_pending and not is_expired:
        unblock_data = get_pending_unblock_message(user_id)
        if unblock_data:
            question, keyboard = unblock_data
            # 沿用会话里累计的错误次数：首次温柔邀请，第 2 次起升级为傲娇
            attempts = pending_unblocks.get(user_id, {}).get('attempts', 1)
            return (
                "客人还有解封小验证没完成，先答完再继续发消息嘛。\n\n"
                f"客人暂时被请到门外等候啦。\n\n"
                f"{copy_text.unblock_question(question, attempts)}"
            ), keyboard
    
    challenge = await gemini_service.generate_unblock_question()
    question = challenge['question']
    correct_answer = challenge['correct_answer']
    options = challenge['options']
    
    pending_unblocks[user_id] = {
        'answer': correct_answer,
        'question': question,
        'options': options,
        'attempts': 0,
        'created_at': time.time(),
        'expires_at': time.time() + config.VERIFICATION_TIMEOUT
    }
    
    keyboard = [
        [InlineKeyboardButton(option, callback_data=f"unblock_{option}") for option in options]
    ]
    
    # 首次提问统一用温柔邀请，不赌会话里是否残留旧计数
    return (
        "客人暂时被请到门外等候啦。\n\n"
        f"{copy_text.unblock_question(question, 1)}"
    ), InlineKeyboardMarkup(keyboard)

async def verify_unblock_answer(user_id: int, user_answer: str):
    _cleanup_expired_unblocks()

    if user_id not in pending_unblocks:
        return "解封小会话已经过期或不见啦，客人重新发一条消息再领一次题目嘛。", False

    session = pending_unblocks[user_id]

    if time.time() > session['expires_at']:
        del pending_unblocks[user_id]
        return "解封验证超时啦，客人重新发一条消息，女仆再给一份新题目。", False

    if user_answer == session['answer']:
        del pending_unblocks[user_id]
        await db.remove_from_blacklist(user_id)
        await db.set_user_blacklist_strikes(user_id, 0)
        return "哼，答对啦……才不是女仆担心客人在外面冻着呢。通道已经打开，客人进去吧。", True
    else:
        # 与主验证一致：累计答错次数，答错一次不直接永久拉黑
        session['attempts'] += 1
        if session['attempts'] >= MAX_UNBLOCK_ATTEMPTS:
            del pending_unblocks[user_id]
            await _escalate_to_permanent(user_id)
            return "不是女仆狠心……答案错太多次了，女仆只能按规则把通道永久锁上。", False
        # 保留会话，答错次数累计；客人重新发一条消息即可再试
        remaining = MAX_UNBLOCK_ATTEMPTS - session['attempts']
        return copy_text.unblock_wrong(remaining, session['attempts']), False


async def _escalate_to_permanent(user_id: int) -> None:
    """把用户升级为永久拉黑；已有黑名单记录时保留原 reason/blocked_by，不覆盖。"""
    async with db_manager.get_connection() as db:
        cursor = await db.execute(
            'UPDATE blacklist SET permanent = 1 WHERE user_id = ?',
            (user_id,)
        )
        await db.commit()
        if cursor.rowcount == 0:
            # 无历史记录（竞态兜底），此时不存在可覆盖的旧记录
            await db.add_to_blacklist(user_id, reason="解封小验证失败次数过多", blocked_by=config.BOT_ID, permanent=True)

def _safe_text_for_markdown(text: str) -> str:
    if not text:
        return text
    
    dangerous_chars = r'_*[]()`'
    return "".join(f"\\{char}" if char in dangerous_chars else char for char in text)

async def get_blacklist_keyboard(page: int = 1, per_page: int = 5):
    total_count = await db.get_blacklist_count()
    
    if total_count == 0:
        return "黑名单小本本现在空空如也，主人。", None

    total_pages = (total_count + per_page - 1) // per_page

    if page < 1:
        page = 1
    elif page > total_pages:
        page = total_pages

    offset = (page - 1) * per_page

    blacklist_users = await db.get_blacklist_paginated(limit=per_page, offset=offset)
    
    if not blacklist_users:
        return "黑名单小本本现在空空如也，主人。", None

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
            InlineKeyboardButton(copy_text.btn_admin_unblock(first_name), callback_data=f"admin_unblock_{user_id}")
        ])
    
    navigation_buttons = []
    if page > 1:
        navigation_buttons.append(InlineKeyboardButton(copy_text.BTN_PREV_PAGE, callback_data=f"blacklist_page_{page - 1}"))
    if page < total_pages:
        navigation_buttons.append(InlineKeyboardButton(copy_text.BTN_NEXT_PAGE, callback_data=f"blacklist_page_{page + 1}"))
    
    if navigation_buttons:
        keyboard.append(navigation_buttons)

    return message, InlineKeyboardMarkup(keyboard)

async def get_all_users_keyboard(page: int = 1, per_page: int = 5, callback_prefix: str = "stats_list_all_users_page_", back_callback: str = "stats_back_to_menu", back_text: str = "返回统计小本本"):
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
    
    total_count = await db.get_total_users_count()
    
    if total_count == 0:
        return "客人名册现在还是空的呢。", None

    total_pages = (total_count + per_page - 1) // per_page

    if page < 1:
        page = 1
    elif page > total_pages:
        page = total_pages

    offset = (page - 1) * per_page

    users = await db.get_all_users_paginated(limit=per_page, offset=offset)
    
    if not users:
        return "客人名册现在还是空的呢。", None

    keyboard = []
    message = f"客人名册 (第 {page}/{total_pages} 页)\n\n"
    
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
        navigation_buttons.append(InlineKeyboardButton(copy_text.BTN_PREV_PAGE, callback_data=f"{callback_prefix}{page - 1}"))
    if page < total_pages:
        navigation_buttons.append(InlineKeyboardButton(copy_text.BTN_NEXT_PAGE, callback_data=f"{callback_prefix}{page + 1}"))
    
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
        return "黑名单小本本现在空空如也，主人。", None

    total_pages = (total_count + per_page - 1) // per_page

    if page < 1:
        page = 1
    elif page > total_pages:
        page = total_pages

    offset = (page - 1) * per_page

    blacklist_users = await db.get_blacklist_paginated(limit=per_page, offset=offset)
    
    if not blacklist_users:
        return "黑名单小本本现在空空如也，主人。", None

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
            InlineKeyboardButton(copy_text.btn_admin_unblock(first_name), callback_data=f"admin_unblock_{user_id}")
        ])
    
    navigation_buttons = []
    if page > 1:
        navigation_buttons.append(InlineKeyboardButton(copy_text.BTN_PREV_PAGE, callback_data=f"stats_list_blacklist_page_{page - 1}"))
    if page < total_pages:
        navigation_buttons.append(InlineKeyboardButton(copy_text.BTN_NEXT_PAGE, callback_data=f"stats_list_blacklist_page_{page + 1}"))
    
    back_button = [InlineKeyboardButton(copy_text.BTN_BACK_STATS, callback_data="stats_back_to_menu")]
    keyboard.append(back_button)
    
    if navigation_buttons:
        keyboard.append(navigation_buttons)

    if not keyboard:
        keyboard = [[InlineKeyboardButton(copy_text.BTN_BACK_STATS, callback_data="stats_back_to_menu")]]
    
    return message, InlineKeyboardMarkup(keyboard)

async def get_exemptions_keyboard(page: int = 1, per_page: int = 5):
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
    from datetime import datetime, timezone
    
    total_count = await db.get_exemptions_count()
    
    if total_count == 0:
        return "通行证名单现在空空如也，主人。", None

    total_pages = (total_count + per_page - 1) // per_page

    if page < 1:
        page = 1
    elif page > total_pages:
        page = total_pages

    offset = (page - 1) * per_page

    exemptions = await db.get_exemptions_paginated(limit=per_page, offset=offset)
    
    if not exemptions:
        return "通行证名单现在空空如也，主人。", None

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
            InlineKeyboardButton(f"替 {first_name} 收回通行证", callback_data=f"admin_remove_exemption_{user_id}")
        ])
    
    navigation_buttons = []
    if page > 1:
        navigation_buttons.append(InlineKeyboardButton(copy_text.BTN_PREV_PAGE, callback_data=f"panel_exemptions_page_{page - 1}"))
    if page < total_pages:
        navigation_buttons.append(InlineKeyboardButton(copy_text.BTN_NEXT_PAGE, callback_data=f"panel_exemptions_page_{page + 1}"))
    
    if navigation_buttons:
        keyboard.append(navigation_buttons)

    return message, InlineKeyboardMarkup(keyboard)