import asyncio
from dataclasses import dataclass
from typing import Optional

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import BadRequest, Forbidden, TelegramError
from telegram.ext import ContextTypes

from config import config
from database import models as db
from utils.message_sender import send_message_by_type


@dataclass
class BroadcastResult:
    broadcast_id: int
    total: int
    success: int
    failed: int
    group_name: Optional[str] = None


def normalize_group_name(name: str) -> str:
    return name.strip().lstrip("#")


def get_message_preview(message, text: Optional[str] = None, limit: int = 120) -> str:
    if text is not None:
        preview = text
    elif message.text:
        preview = message.text
    elif message.caption:
        preview = message.caption
    elif message.photo:
        preview = "[图片]"
    elif message.animation:
        preview = "[动画]"
    elif message.video:
        preview = "[视频]"
    elif message.document:
        preview = f"[文件] {message.document.file_name or ''}".strip()
    elif message.audio:
        preview = f"[音频] {message.audio.file_name or message.audio.title or ''}".strip()
    elif message.voice:
        preview = "[语音]"
    elif message.sticker:
        preview = "[贴纸]"
    else:
        preview = "[暂不支持预览的消息]"

    preview = " ".join(preview.split())
    return preview[:limit]


def build_broadcast_panel_text(groups: list[dict]) -> str:
    lines = [
        "广播与分组管理",
        "",
        "广播命令：",
        "/broadcast all <内容>",
        "/broadcast group <分组名> <内容>",
        "也可以回复一条消息后使用 /broadcast all 或 /broadcast group <分组名>，这样会保留媒体与后续编辑同步。",
        "",
        "分组命令：",
        "/group create <分组名>",
        "/group list",
        "在用户话题中使用 /group add <分组名> 或点击用户卡片里的“用户分组”。",
    ]

    if groups:
        lines.extend(["", "当前分组："])
        for group in groups[:20]:
            lines.append(f"- {group['name']}：{group['member_count']} 人")
    else:
        lines.extend(["", "当前还没有分组。"])

    return "\n".join(lines)


def build_broadcast_panel_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("查看分组", callback_data="broadcast_groups")],
        [InlineKeyboardButton("回女仆长面板", callback_data="panel_back")],
    ])


def build_groups_keyboard(groups: list[dict]) -> InlineKeyboardMarkup:
    keyboard = []
    for group in groups[:40]:
        keyboard.append([
            InlineKeyboardButton(
                f"{group['name']} ({group['member_count']})",
                callback_data=f"broadcast_group_view_{group['id']}",
            )
        ])
    keyboard.append([InlineKeyboardButton("回广播与分组", callback_data="panel_broadcast")])
    keyboard.append([InlineKeyboardButton("回女仆长面板", callback_data="panel_back")])
    return InlineKeyboardMarkup(keyboard)


async def build_user_group_keyboard(user_id: int) -> tuple[str, InlineKeyboardMarkup]:
    groups = await db.get_all_user_groups()
    user_groups = await db.get_groups_for_user(user_id)
    user_group_ids = {group['id'] for group in user_groups}

    lines = [f"用户 {user_id} 的分组", ""]
    if user_groups:
        lines.append("已加入：" + "、".join(group['name'] for group in user_groups))
    else:
        lines.append("当前未加入任何分组。")

    if not groups:
        lines.extend([
            "",
            "还没有可选分组。请先使用：",
            "/group create <分组名>",
        ])
        return "\n".join(lines), InlineKeyboardMarkup([
            [InlineKeyboardButton("回女仆长面板", callback_data="panel_back")]
        ])

    keyboard = []
    for group in groups[:40]:
        marker = "✓" if group['id'] in user_group_ids else "+"
        keyboard.append([
            InlineKeyboardButton(
                f"{marker} {group['name']} ({group['member_count']})",
                callback_data=f"usergroup_toggle_{user_id}_{group['id']}",
            )
        ])
    keyboard.append([InlineKeyboardButton("刷新", callback_data=f"usercard_groups_{user_id}")])
    return "\n".join(lines), InlineKeyboardMarkup(keyboard)


async def send_text_broadcast(
    context: ContextTypes.DEFAULT_TYPE,
    recipients: list[dict],
    text: str,
    admin_id: int,
    scope: str,
    group_id: Optional[int] = None,
) -> BroadcastResult:
    broadcast_id = await db.create_broadcast(
        scope=scope,
        group_id=group_id,
        created_by=admin_id,
        content_preview=get_message_preview(None, text=text),
        total_count=len(recipients),
    )

    success = 0
    failed = 0
    for recipient in recipients:
        user_id = recipient['user_id']
        try:
            sent = await context.bot.send_message(chat_id=user_id, text=text)
            await db.save_broadcast_delivery(broadcast_id, user_id, "success", sent.message_id)
            mirror = await _send_text_mirror(context, recipient, text)
            if mirror:
                await db.save_message_mapping(
                    user_id=user_id,
                    source_chat_id=config.FORUM_GROUP_ID,
                    source_message_id=mirror.message_id,
                    dest_chat_id=user_id,
                    dest_message_id=sent.message_id,
                    direction="broadcast_to_user",
                    thread_id=mirror.message_thread_id,
                    broadcast_id=broadcast_id,
                )
            success += 1
        except (Forbidden, BadRequest, TelegramError) as exc:
            await db.save_broadcast_delivery(broadcast_id, user_id, "failed", error=str(exc)[:500])
            failed += 1
        await asyncio.sleep(0.05)

    await db.update_broadcast_counts(broadcast_id, success, failed)
    return BroadcastResult(broadcast_id, len(recipients), success, failed)


async def _ensure_thread_for_recipient(context: ContextTypes.DEFAULT_TYPE, recipient: dict) -> Optional[int]:
    thread_id = recipient.get('thread_id')
    if thread_id:
        return thread_id

    if not config.FORUM_GROUP_ID:
        return None

    first_name = recipient.get('first_name') or str(recipient['user_id'])
    topic_name = f"{first_name} (ID: {recipient['user_id']})"
    try:
        topic = await context.bot.create_forum_topic(
            chat_id=config.FORUM_GROUP_ID,
            name=topic_name,
        )
        await db.update_user_thread_id(recipient['user_id'], topic.message_thread_id)
        recipient['thread_id'] = topic.message_thread_id
        return topic.message_thread_id
    except TelegramError as exc:
        print(f"创建广播用户话题失败: {exc}")
        return None


async def _send_text_mirror(context: ContextTypes.DEFAULT_TYPE, recipient: dict, text: str):
    thread_id = await _ensure_thread_for_recipient(context, recipient)
    if not thread_id:
        return None
    try:
        return await context.bot.send_message(
            chat_id=config.FORUM_GROUP_ID,
            text=text,
            message_thread_id=thread_id,
            disable_web_page_preview=True,
        )
    except TelegramError as exc:
        print(f"发送广播话题镜像失败: {exc}")
        return None


async def _send_message_mirror(context: ContextTypes.DEFAULT_TYPE, recipient: dict, source_message):
    thread_id = await _ensure_thread_for_recipient(context, recipient)
    if not thread_id:
        return None
    try:
        return await send_message_by_type(
            context.bot,
            source_message,
            config.FORUM_GROUP_ID,
            thread_id=thread_id,
            disable_web_page_preview=True,
        )
    except TelegramError as exc:
        print(f"发送广播话题镜像失败: {exc}")
        return None


async def send_message_broadcast(
    context: ContextTypes.DEFAULT_TYPE,
    recipients: list[dict],
    source_message,
    admin_id: int,
    scope: str,
    group_id: Optional[int] = None,
) -> BroadcastResult:
    broadcast_id = await db.create_broadcast(
        scope=scope,
        group_id=group_id,
        source_chat_id=source_message.chat_id,
        source_message_id=source_message.message_id,
        created_by=admin_id,
        content_preview=get_message_preview(source_message),
        total_count=len(recipients),
    )

    success = 0
    failed = 0
    for recipient in recipients:
        user_id = recipient['user_id']
        try:
            sent = await send_message_by_type(
                context.bot,
                source_message,
                user_id,
                disable_web_page_preview=True,
            )
            if sent:
                await db.save_broadcast_delivery(broadcast_id, user_id, "success", sent.message_id)
                await db.save_message_mapping(
                    user_id=user_id,
                    source_chat_id=source_message.chat_id,
                    source_message_id=source_message.message_id,
                    dest_chat_id=user_id,
                    dest_message_id=sent.message_id,
                    direction="broadcast_to_user",
                    broadcast_id=broadcast_id,
                )
                mirror = await _send_message_mirror(context, recipient, source_message)
                if mirror:
                    await db.save_message_mapping(
                        user_id=user_id,
                        source_chat_id=source_message.chat_id,
                        source_message_id=source_message.message_id,
                        dest_chat_id=config.FORUM_GROUP_ID,
                        dest_message_id=mirror.message_id,
                        direction="broadcast_to_thread",
                        thread_id=mirror.message_thread_id,
                        broadcast_id=broadcast_id,
                    )
                    await db.save_message_mapping(
                        user_id=user_id,
                        source_chat_id=config.FORUM_GROUP_ID,
                        source_message_id=mirror.message_id,
                        dest_chat_id=user_id,
                        dest_message_id=sent.message_id,
                        direction="broadcast_to_user",
                        thread_id=mirror.message_thread_id,
                        broadcast_id=broadcast_id,
                    )
                success += 1
            else:
                await db.save_broadcast_delivery(broadcast_id, user_id, "failed", error="unsupported message type")
                failed += 1
        except (Forbidden, BadRequest, TelegramError) as exc:
            await db.save_broadcast_delivery(broadcast_id, user_id, "failed", error=str(exc)[:500])
            failed += 1
        await asyncio.sleep(0.05)

    await db.update_broadcast_counts(broadcast_id, success, failed)
    return BroadcastResult(broadcast_id, len(recipients), success, failed)


async def format_group_members(group_name: str) -> str:
    result = await db.get_group_members(group_name, include_blacklisted=True)
    if not result:
        return f"没有找到分组：{group_name}"

    group, members = result
    if not members:
        return f"分组 {group['name']} 暂时没有成员。"

    lines = [f"分组 {group['name']} 成员（{len(members)} 人）", ""]
    for member in members[:50]:
        name = member.get('first_name') or str(member['user_id'])
        username = f" @{member['username']}" if member.get('username') else ""
        blocked = " 已拉黑" if member.get('is_blacklisted') else ""
        lines.append(f"- {name}{username} ({member['user_id']}){blocked}")
    if len(members) > 50:
        lines.append(f"... 还有 {len(members) - 50} 人")
    return "\n".join(lines)
