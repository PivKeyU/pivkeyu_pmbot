import asyncio
from datetime import datetime
from typing import Optional

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes
from telegram.helpers import escape_markdown

from config import config
from database import models as db

_thread_locks: dict[int, asyncio.Lock] = {}


def _get_thread_lock(user_id: int) -> asyncio.Lock:
    lock = _thread_locks.get(user_id)
    if lock is None:
        if len(_thread_locks) >= 5000:
            for uid in [uid for uid, lck in _thread_locks.items() if not lck.locked()]:
                _thread_locks.pop(uid, None)
        lock = asyncio.Lock()
        _thread_locks[user_id] = lock
    return lock


def build_direct_contact_url(username: Optional[str]) -> Optional[str]:
    if not username:
        return None
    return f"tg://resolve?domain={username}"


def build_user_info_markdown(user) -> str:
    name_parts = [part.strip() for part in [user.first_name or "", user.last_name or ""] if part and part.strip()]
    display_name = " ".join(name_parts) if name_parts else str(user.id)
    username = f"@{user.username}" if user.username else "暂未登记"
    first_contact = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    escaped_display_name = escape_markdown(display_name, version=2)
    escaped_username = escape_markdown(username, version=2)
    escaped_first_contact = escape_markdown(first_contact, version=2)

    return (
        "*来访客人档案*\n\n"
        f"*称呼:* {escaped_display_name}\n"
        f"*TG ID:* `{user.id}`\n"
        f"*用户名:* {escaped_username}\n"
        f"*首次敲门:* {escaped_first_contact}"
    )


async def get_or_create_thread(update: Update, context: ContextTypes.DEFAULT_TYPE) -> tuple[Optional[int], bool]:
    user = update.effective_user
    async with _get_thread_lock(user.id):
        user_data = await db.get_user(user.id)

        if user_data and user_data.get('thread_id'):
            return user_data['thread_id'], False

        topic_name = f"{user.first_name} (ID: {user.id})"
        try:
            topic = await context.bot.create_forum_topic(
                chat_id=config.FORUM_GROUP_ID,
                name=topic_name
            )
            thread_id = topic.message_thread_id
            await db.update_user_thread_id(user.id, thread_id)
        except Exception as e:
            print(f"创建话题失败: {e}")
            return None, False

        try:
            await send_user_info_card(update, context, thread_id)
        except Exception as e:
            print(f"发送用户信息卡片失败: {e}")

        return thread_id, True


async def build_user_info_card_keyboard(
    user_id: int,
    username: Optional[str] = None
) -> InlineKeyboardMarkup:
    is_blocked, _ = await db.is_blacklisted(user_id)
    is_exempted = await db.is_exempted(user_id)

    if username is None:
        user_data = await db.get_user(user_id)
        username = user_data.get("username") if user_data else None

    row = [
        InlineKeyboardButton(
            "打开通道" if is_blocked else "锁上通道",
            callback_data=f"usercard_block_{user_id}"
        ),
        InlineKeyboardButton(
            "收回通行证" if is_exempted else "发通行证",
            callback_data=f"usercard_exempt_{user_id}"
        ),
    ]

    direct_contact_url = build_direct_contact_url(username)
    if direct_contact_url:
        row.append(InlineKeyboardButton("直接问候", url=direct_contact_url))

    return InlineKeyboardMarkup([
        row,
        [InlineKeyboardButton("用户分组", callback_data=f"usercard_groups_{user_id}")],
    ])


async def send_user_info_card(update: Update, context: ContextTypes.DEFAULT_TYPE, thread_id: int):
    user = update.effective_user
    reply_markup = await build_user_info_card_keyboard(user.id, username=user.username)
    info_text = build_user_info_markdown(user)

    photos = None
    try:
        photos = await context.bot.get_user_profile_photos(user.id, limit=1)
    except Exception as e:
        print(f"获取用户头像失败: {e}")

    if photos and photos.total_count > 0:
        try:
            await context.bot.send_photo(
                chat_id=config.FORUM_GROUP_ID,
                photo=photos.photos[0][0].file_id,
                caption=info_text,
                message_thread_id=thread_id,
                parse_mode=ParseMode.MARKDOWN_V2,
                reply_markup=reply_markup
            )
            return
        except Exception as e:
            print(f"发送头像卡片失败，改用文本卡片: {e}")

    await context.bot.send_message(
        chat_id=config.FORUM_GROUP_ID,
        text=info_text,
        message_thread_id=thread_id,
        parse_mode=ParseMode.MARKDOWN_V2,
        reply_markup=reply_markup
    )


async def ensure_user_thread(bot, user: dict) -> Optional[int]:
    """确保用户在论坛群中有对应的话题 thread_id；若尚未创建则尝试新建。"""
    if not config.FORUM_GROUP_ID or not hasattr(bot, 'create_forum_topic'):
        return user.get('thread_id')
    user_id = user['user_id']
    async with _get_thread_lock(user_id):
        fresh = await db.get_user(user_id)
        thread_id = (fresh or user).get('thread_id')
        if thread_id:
            user['thread_id'] = thread_id
            return thread_id
        first_name = user.get('first_name') or str(user_id)
        topic_name = f'{first_name} (ID: {user_id})'
        try:
            topic = await bot.create_forum_topic(
                chat_id=config.FORUM_GROUP_ID,
                name=topic_name,
            )
            thread_id = topic.message_thread_id
            await db.update_user_thread_id(user_id, thread_id)
            user['thread_id'] = thread_id
            return thread_id
        except Exception as exc:
            print(f'为用户 {user_id} 创建论坛话题失败: {exc}')
            return None
