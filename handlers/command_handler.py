import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from telegram.helpers import escape_markdown
from database import models as db
from services.blacklist import block_user, unblock_user, get_blacklist_keyboard
from services import broadcast as broadcast_service, safe_update, spam_filter, tg_monitor, web_monitor
from utils.decorators import admin_only
from config import config
from utils import copy as copy_text

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    
    if not await db.get_user(user.id):
        await db.add_user(
            user_id=user.id,
            username=user.username,
            first_name=user.first_name,
            last_name=user.last_name,
            language_code=user.language_code
        )
    
    welcome_message = (
        f"客人好呀，{user.first_name}！\n\n"
        "这里是随时待命的双向聊天女仆。\n"
        "客人可以直接把消息交给女仆，女仆会乖乖送到管理员那边。\n\n"
        "输入 /help 可以查看女仆小手册。"
    )
    
    await update.message.reply_text(copy_text.with_deco_head(welcome_message, 'GREET'))

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    help_text = (
        "这里是双向聊天女仆机器人。\n\n"
        "女仆可代办的事:\n"
        "- 递送文本、图片、视频、音频和文档\n"
        "- 保留 Markdown 格式，让消息整整齐齐\n"
        "- 首次递送前会请客人完成一个小验证\n\n"
        "管理员女仆长命令:\n"
        "- `/block` - 在用户话题中把捣乱者请进黑名单小本本\n"
        "- `/blacklist` - 查看黑名单小本本\n"
        "- `/stats` - 查看宅邸统计\n"
        "- `/inbox` - 查看待办小本本\n"
        "- `/view_filtered` - 查看被女仆拦下的消息\n"
        "- `/exempt` - 给可信用户发放审查通行证（临时或永久）\n"
        "- `/group` - 管理私聊用户分组\n"
        "- `/broadcast` - 向全部用户或指定分组广播\n"
        "- `/spamrules` - 管理关键词广告拦截\n"
        "- `/tgmon` - 管理 TG 群/频道关键词监听\n"
        "- `/webmon` - 管理网页关键词/变化监控\n"
        "- `/monitor_status` - 查看监听与 RSS 运行状态\n"
        "- `/updatebot` - 安全检查/更新/回滚本地代码\n"
    )
    
    await update.message.reply_text(copy_text.with_deco_head(help_text, 'GREET'), parse_mode='Markdown')

@admin_only
async def blacklist(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message, keyboard = await get_blacklist_keyboard(page=1)
    if keyboard:
        await update.message.reply_text(message, reply_markup=keyboard, parse_mode='Markdown')
    else:
        await update.message.reply_text(message)

@admin_only
async def block(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message
    
    if message.is_topic_message and message.reply_to_message:
        thread_id = message.message_thread_id
        user_to_block = await db.get_user_by_thread_id(thread_id)
        
        if user_to_block:
            user_id_to_block = user_to_block['user_id']
            reason = " ".join(context.args) if context.args else "无"
            
            response = await block_user(user_id_to_block, reason, update.effective_user.id, permanent=True)
            await update.message.reply_text(response)
        else:
            await update.message.reply_text(copy_text.no_topic_user())
        return

    if not context.args:
        await update.message.reply_text(
            copy_text.with_deco_head(
                "主人，把主人的 user_id 一起给女仆吧，或者直接在主人的专属会客厅里下这条命令。\n"
                "女仆小抄：/block <user_id> [reason]",
                'ASK')
        )
        return
    
    try:
        user_id_to_block = int(context.args[0])
        reason = " ".join(context.args[1:]) if len(context.args) > 1 else "无"
        response = await block_user(user_id_to_block, reason, update.effective_user.id)
        await update.message.reply_text(response)
    except (ValueError, IndexError):
        await update.message.reply_text(copy_text.user_id_invalid())

@admin_only
async def unblock(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text(
            copy_text.with_deco("主人，请告诉女仆要解封的用户 ID。女仆小抄：/unblock <user_id>", 'ASK')
        )
        return
    
    try:
        user_id_to_unblock = int(context.args[0])
        response = await unblock_user(user_id_to_unblock)
        await update.message.reply_text(response)
    except (ValueError, IndexError):
        await update.message.reply_text(copy_text.user_id_invalid())

@admin_only
async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    total_users = await db.get_total_users_count()
    blocked_users = await db.get_blocked_users_count()
    
    stats_message = (
        f"女仆统计小本本\n"
        f"---------------------\n"
        f"接待过的主人: {total_users}\n"
        f"黑名单里的捣乱者: {blocked_users}\n\n"
        f"主人想翻哪一本记录呢："
    )
    
    keyboard = [
        [InlineKeyboardButton(copy_text.BTN_STATS_ALL_USERS, callback_data="stats_list_all_users_page_1")],
        [InlineKeyboardButton(copy_text.BTN_PANEL_BLACKLIST, callback_data="stats_list_blacklist_page_1")]
    ]
    
    await update.message.reply_text(
        stats_message, 
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode='Markdown'
    )


def _forum_message_link(thread_id: int, message_id: int) -> str:
    """生成论坛话题内消息的深链（t.me/c/ 形式，剥掉超群 -100 前缀）。"""
    chat_id_str = str(abs(config.FORUM_GROUP_ID))
    if chat_id_str.startswith("100") and len(chat_id_str) > 3:
        chat_id_str = chat_id_str[3:]
    return f"https://t.me/c/{chat_id_str}/{thread_id}/{message_id}"


def _format_local_time(created_at) -> str:
    """把 SQLite 的 UTC 时间戳转成本地时间显示。"""
    if not created_at:
        return "-"
    try:
        dt = datetime.fromisoformat(str(created_at).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone().strftime("%Y-%m-%d %H:%M")
    except ValueError:
        return str(created_at)


@admin_only
async def inbox(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """待办小本本：列出最近发来消息、管理员尚未回复的用户话题，附深链跳转。"""
    rows = await db.get_pending_reply_topics(limit=15)
    total = await db.count_pending_reply_topics()

    if not rows:
        await update.message.reply_text(
            copy_text.with_deco("主人的待办小本本干干净净，没有积压的消息哦～", 'EMPTY')
        )
        return

    if total > len(rows):
        title = f"女仆长待办小本本（共 {total} 条，先端上前 {len(rows)} 条）"
    else:
        title = f"女仆长待办小本本（共 {total} 条）"

    lines = [title, ""]
    for idx, row in enumerate(rows, 1):
        first_name = escape_markdown(str(row.get('first_name') or row.get('user_id') or '?'), version=1)
        username = row.get('username')
        name_part = (
            f"{first_name} (@{escape_markdown(str(username), version=1)})"
            if username else first_name
        )
        created_at = _format_local_time(row.get('created_at'))
        lines.append(f"{idx}. {name_part} · {created_at}")

        content = (row.get('content') or '').strip()
        if content:
            if len(content) > 50:
                content = content[:50] + "..."
            preview = f"内容: {escape_markdown(content, version=1)}"
        elif row.get('media_type'):
            preview = f"内容: [{escape_markdown(str(row['media_type']), version=1)}]"
        else:
            preview = "内容: （无文本内容）"
        lines.append(f"   {preview}")

        if row.get('thread_id') is not None and row.get('dest_message_id'):
            link = _forum_message_link(row['thread_id'], row['dest_message_id'])
            lines.append(f"   [跳转话题]({link})")
        lines.append("")

    await update.message.reply_text("\n".join(lines), parse_mode='Markdown')

async def getid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_type = update.effective_chat.type
    user_id = update.effective_user.id

    who = copy_text.address(await db.is_admin(user_id))

    if chat_type == 'private':
        message = copy_text.getid_self(who, user_id)
    else:
        chat_id = update.effective_chat.id
        message = "\n".join([
            copy_text.getid_group(who, chat_id),
            copy_text.getid_self(who, user_id),
        ])
    
    await update.message.reply_text(message, parse_mode='Markdown')

@admin_only
async def panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    total_users = await db.get_total_users_count()
    blocked_users = await db.get_blocked_users_count()
    exempted_users = await db.get_exemptions_count()
    is_enabled = await db.get_autoreply_enabled()
    
    message = (
        f"女仆长管理面板\n\n"
        f"宅邸统计:\n\n"
        f"接待过的主人: {total_users}\n"
        f"黑名单里的捣乱者: {blocked_users}\n"
        f"持有通行证的主人: {exempted_users}\n"
        f"自动回复女仆: {'正在值班' if is_enabled else '正在休息'}\n\n"
        f"主人，请挑选要打理的事项："
    )
    
    keyboard = [
        [InlineKeyboardButton(copy_text.BTN_PANEL_BLACKLIST, callback_data="panel_blacklist_page_1"), InlineKeyboardButton(copy_text.BTN_PANEL_STATS, callback_data="panel_stats")],
        [InlineKeyboardButton(copy_text.BTN_PANEL_FILTERED, callback_data="panel_filtered_page_1"), InlineKeyboardButton(copy_text.BTN_PANEL_AUTOREPLY, callback_data="panel_autoreply")],
        [InlineKeyboardButton(copy_text.BTN_PANEL_EXEMPTIONS, callback_data="panel_exemptions_page_1"), InlineKeyboardButton(copy_text.BTN_PANEL_NETWORK_TEST, callback_data="panel_network_test")],
        [InlineKeyboardButton(copy_text.BTN_PANEL_BROADCAST, callback_data="panel_broadcast"), InlineKeyboardButton(copy_text.BTN_PANEL_RSS, callback_data="panel_rss")],
        [InlineKeyboardButton(copy_text.BTN_PANEL_TG_MONITOR, callback_data="panel_tg_monitor"), InlineKeyboardButton(copy_text.BTN_PANEL_WEB_MONITOR, callback_data="panel_web_monitor")],
        [InlineKeyboardButton(copy_text.BTN_PANEL_SPAMRULES, callback_data="panel_spamrules")],
        [InlineKeyboardButton(copy_text.BTN_PANEL_STATUS, callback_data="panel_monitor_status"), InlineKeyboardButton(copy_text.BTN_PANEL_UPDATEBOT, callback_data="panel_updatebot")],
        [InlineKeyboardButton(copy_text.BTN_PANEL_AI_SETTINGS, callback_data="panel_ai_settings")],
    ]
    
    await update.message.reply_text(
        message,
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode='Markdown'
    )


def _format_runtime_statuses(statuses: list[dict]) -> str:
    if not statuses:
        return "暂时还没有运行状态记录。"

    lines = ["运行状态", ""]
    for item in statuses[:30]:
        failures = int(item.get('consecutive_failures') or 0)
        badge = "正常" if failures == 0 else f"异常 x{failures}"
        lines.extend([
            f"{item.get('name')} [{badge}]",
            f"  类别: {item.get('category')}",
            f"  最近运行: {item.get('last_run_at') or '-'}",
            f"  最近成功: {item.get('last_success_at') or '-'}",
            f"  最近失败: {item.get('last_error_at') or '-'}",
            f"  耗时: {item.get('last_duration_ms') or 0} ms, 推送: {item.get('last_sent_count') or 0}",
        ])
        if item.get('last_error'):
            error_text = str(item['last_error'])
            lines.append(f"  错误: {error_text[:180]}")
        lines.append("")
    return "\n".join(lines).strip()


@admin_only
async def monitor_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    category = context.args[0].lower() if context.args else None
    if category in {"tg", "tgmon"}:
        category = "tg_monitor"
    elif category not in {None, "rss", "web", "tg_monitor"}:
        await update.message.reply_text(
            copy_text.with_deco("女仆小抄：/monitor_status [rss|tg|web]", 'ASK')
        )
        return

    statuses = await db.get_runtime_statuses(category=category)
    await update.message.reply_text(_format_runtime_statuses(statuses))

@admin_only
async def exempt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message
    admin_id = update.effective_user.id
    
    if message.is_topic_message:
        thread_id = message.message_thread_id
        user_to_exempt = await db.get_user_by_thread_id(thread_id)
        
        if not user_to_exempt:
            await update.message.reply_text(copy_text.no_topic_user())
            return
        
        user_id_to_exempt = user_to_exempt['user_id']
        
        if not context.args:
            exemption_info = await db.get_exemption(user_id_to_exempt)
            if exemption_info:
                is_permanent = bool(exemption_info.get('is_permanent', 0))
                expires_at = exemption_info.get('expires_at')
                reason = exemption_info.get('reason', '无')
                
                status_text = "永久通行证" if is_permanent else "临时通行证（到期时间: {}）".format(expires_at)
                await update.message.reply_text(
                    copy_text.exempt_title(user_id_to_exempt, status_text, reason)
                    + "\n\n"
                    + "女仆小抄：\n"
                    + "/exempt permanent [reason] - 发放永久通行证\n"
                    + "/exempt temp <小时数> [reason] - 发放临时通行证（例如: /exempt temp 24）\n"
                    + "/exempt remove - 收回通行证"
                )
            else:
                await update.message.reply_text(
                    copy_text.exempt_check_none(user_id_to_exempt)
                    + "\n\n"
                    + "女仆小抄：\n"
                    + "/exempt permanent [reason] - 发放永久通行证\n"
                    + "/exempt temp <小时数> [reason] - 发放临时通行证（例如: /exempt temp 24）\n"
                    + "/exempt remove - 收回通行证"
                )
            return
        
        subcommand = context.args[0].lower()
        
        if subcommand == "remove":
            await db.remove_exemption(user_id_to_exempt)
            await update.message.reply_text(copy_text.exempt_revoked(user_id_to_exempt))
            return
        
        reason = " ".join(context.args[1:]) if len(context.args) > 1 else "管理员发放通行证"
        
        if subcommand == "permanent":
            await db.add_exemption(user_id_to_exempt, is_permanent=True, exempted_by=admin_id, reason=reason)
            await update.message.reply_text(
                copy_text.exempt_perm_granted(user_id_to_exempt, reason)
            )
        elif subcommand == "temp":
            if len(context.args) < 2:
                await update.message.reply_text(
                    copy_text.with_deco("主人，请告诉女仆临时通行证要生效几个小时。女仆小抄：/exempt temp <小时数> [reason]", 'ASK')
                )
                return
            
            try:
                hours = int(context.args[1])
                expires_at = (datetime.now(timezone.utc) + timedelta(hours=hours)).isoformat()
                reason = " ".join(context.args[2:]) if len(context.args) > 2 else "管理员发放临时通行证"
                
                await db.add_exemption(user_id_to_exempt, is_permanent=False, exempted_by=admin_id, reason=reason, expires_at=expires_at)
                await update.message.reply_text(
                    copy_text.exempt_temp_granted(user_id_to_exempt, hours, reason)
                )
            except ValueError:
                await update.message.reply_text(copy_text.exempt_hours_invalid())
        else:
            await update.message.reply_text(
                copy_text.with_deco(
                    "主人，这个子命令女仆看不懂呢。女仆小抄：\n"
                    "/exempt permanent [reason] - 发放永久通行证\n"
                    "/exempt temp <小时数> [reason] - 发放临时通行证\n"
                    "/exempt remove - 收回通行证",
                    'ERROR')
            )
        return
    
    if not context.args:
        await update.message.reply_text(
            copy_text.with_deco_head(
                "主人，把主人的 ID 一起给女仆吧，或者在主人的专属会客厅里直接吩咐。\n\n"
                "女仆小抄：\n"
                "在话题中: /exempt [permanent|temp <小时数>|remove] [reason]\n"
                "直接使用: /exempt <user_id> [permanent|temp <小时数>|remove] [reason]",
                'ASK')
        )
        return
    
    try:
        user_id_to_exempt = int(context.args[0])
        
        if len(context.args) < 2:
            exemption_info = await db.get_exemption(user_id_to_exempt)
            if exemption_info:
                is_permanent = bool(exemption_info.get('is_permanent', 0))
                expires_at = exemption_info.get('expires_at')
                reason = exemption_info.get('reason', '无')
                
                if is_permanent:
                    status_text = "永久通行证"
                else:
                    status_text = "临时通行证（到期时间: {}）".format(expires_at)
                await update.message.reply_text(
                    copy_text.exempt_check_holding(user_id_to_exempt, status_text, reason)
                )
            else:
                await update.message.reply_text(copy_text.exempt_check_none(user_id_to_exempt))
            return
        
        subcommand = context.args[1].lower()
        reason = " ".join(context.args[2:]) if len(context.args) > 2 else "管理员发放通行证"
        
        if subcommand == "remove":
            await db.remove_exemption(user_id_to_exempt)
            await update.message.reply_text(copy_text.exempt_revoked(user_id_to_exempt))
        elif subcommand == "permanent":
            await db.add_exemption(user_id_to_exempt, is_permanent=True, exempted_by=admin_id, reason=reason)
            await update.message.reply_text(
                copy_text.exempt_perm_granted(user_id_to_exempt, reason)
            )
        elif subcommand == "temp":
            if len(context.args) < 3:
                await update.message.reply_text(
                    copy_text.with_deco("主人，请告诉女仆临时通行证要生效几个小时。女仆小抄：/exempt <user_id> temp <小时数> [reason]", 'ASK')
                )
                return
            
            try:
                hours = int(context.args[2])
                expires_at = (datetime.now(timezone.utc) + timedelta(hours=hours)).isoformat()
                reason = " ".join(context.args[3:]) if len(context.args) > 3 else "管理员发放临时通行证"
                
                await db.add_exemption(user_id_to_exempt, is_permanent=False, exempted_by=admin_id, reason=reason, expires_at=expires_at)
                await update.message.reply_text(
                    copy_text.exempt_temp_granted(user_id_to_exempt, hours, reason)
                )
            except ValueError:
                await update.message.reply_text(copy_text.exempt_hours_invalid())
        else:
            await update.message.reply_text(
                copy_text.with_deco(
                    "主人，这个子命令女仆看不懂呢。女仆小抄：\n"
                    "/exempt <user_id> permanent [reason] - 发放永久通行证\n"
                    "/exempt <user_id> temp <小时数> [reason] - 发放临时通行证\n"
                    "/exempt <user_id> remove - 收回通行证",
                    'ERROR')
            )
    except (ValueError, IndexError):
        await update.message.reply_text(copy_text.user_id_invalid())


def _group_help_text() -> str:
    return (
        "分组管理小抄:\n"
        "/group list - 查看全部分组\n"
        "/group create <分组名> [说明] - 创建分组\n"
        "/group delete <分组名> - 删除分组\n"
        "/group members <分组名> - 查看成员\n"
        "/group add <分组名> [user_id] - 添加用户；在用户话题里可省略 user_id\n"
        "/group remove <分组名> [user_id] - 移除用户；在用户话题里可省略 user_id"
    )


async def _resolve_group_target_user_id(update: Update, context: ContextTypes.DEFAULT_TYPE, arg_index: int):
    if len(context.args) > arg_index:
        try:
            return int(context.args[arg_index])
        except ValueError:
            return None

    message = update.message
    if message and message.is_topic_message:
        user = await db.get_user_by_thread_id(message.message_thread_id)
        if user:
            return user['user_id']
    return None


@admin_only
async def group(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text(_group_help_text())
        return

    subcommand = context.args[0].lower()
    admin_id = update.effective_user.id

    if subcommand == "list":
        groups = await db.get_all_user_groups()
        if not groups:
            await update.message.reply_text(
                copy_text.with_deco("主人还没有建过任何分组呢。", 'EMPTY')
            )
            return

        lines = ["分组小本本", ""]
        for item in groups:
            lines.append(f"- {item['name']}：{item['member_count']} 人")
        await update.message.reply_text("\n".join(lines))
        return

    if subcommand == "create":
        if len(context.args) < 2:
            await update.message.reply_text(
                copy_text.with_deco("女仆小抄：/group create <分组名> [说明]", 'ASK')
            )
            return
        name = broadcast_service.normalize_group_name(context.args[1])
        description = " ".join(context.args[2:]) if len(context.args) > 2 else None
        if not name:
            await update.message.reply_text(
                copy_text.with_deco("分组名总得给女仆一个嘛，空着的话小本本上写不下呢。", 'ASK')
            )
            return
        group_data, created = await db.get_or_create_user_group(name, admin_id, description)
        if created:
            await update.message.reply_text(
                copy_text.with_deco(f"分组 {group_data['name']} 已经替主人建好啦。", 'OK')
            )
        else:
            await update.message.reply_text(
                copy_text.with_deco(f"分组 {group_data['name']} 本来就在小本本上呢。", 'OK')
            )
        return

    if subcommand == "delete":
        if len(context.args) < 2:
            await update.message.reply_text(
                copy_text.with_deco("女仆小抄：/group delete <分组名>", 'ASK')
            )
            return
        name = broadcast_service.normalize_group_name(context.args[1])
        deleted = await db.delete_user_group(name)
        await update.message.reply_text(
            copy_text.with_deco(f"分组 {name} 已经替主人划掉啦。", 'OK') if deleted
            else copy_text.with_deco(f"女仆翻遍分组小本本，也没找到 {name}。", 'ERROR')
        )
        return

    if subcommand == "members":
        if len(context.args) < 2:
            await update.message.reply_text(
                copy_text.with_deco("女仆小抄：/group members <分组名>", 'ASK')
            )
            return
        name = broadcast_service.normalize_group_name(context.args[1])
        await update.message.reply_text(await broadcast_service.format_group_members(name))
        return

    if subcommand in {"add", "remove"}:
        if len(context.args) < 2:
            await update.message.reply_text(
                copy_text.with_deco(f"女仆小抄：/group {subcommand} <分组名> [user_id]", 'ASK')
            )
            return

        name = broadcast_service.normalize_group_name(context.args[1])
        target_user_id = await _resolve_group_target_user_id(update, context, 2)
        if not target_user_id:
            await update.message.reply_text(
                copy_text.with_deco("主人得给女仆一个有效的用户 ID，或者直接在主人的专属会客厅里下这条命令嘛。", 'ASK')
            )
            return

        if not await db.get_user(target_user_id):
            await update.message.reply_text(
                copy_text.with_deco(f"{target_user_id} 还没和女仆打过招呼呢，暂时进不了分组。", 'ERROR')
            )
            return

        if subcommand == "add":
            group_data, created, added = await db.add_user_to_group(name, target_user_id, admin_id)
            suffix = "（新分组已创建）" if created else ""
            if added:
                await update.message.reply_text(
                    copy_text.with_deco(f"已经把 {target_user_id} 收进分组 {group_data['name']} 啦。{suffix}", 'OK')
                )
            else:
                await update.message.reply_text(
                    copy_text.with_deco(f"{target_user_id} 本来就在分组 {group_data['name']} 里啦。", 'OK')
                )
        else:
            removed = await db.remove_user_from_group(name, target_user_id)
            await update.message.reply_text(
                copy_text.with_deco(f"已经把 {target_user_id} 移出分组 {name} 啦。", 'OK') if removed
                else copy_text.with_deco(f"{target_user_id} 本来就不在分组 {name} 里呢。", 'OK')
            )
        return

    await update.message.reply_text(_group_help_text())


def _broadcast_help_text() -> str:
    return (
        "广播小抄:\n"
        "/broadcast all <内容> - 广播给全部未拉黑用户\n"
        "/broadcast group <分组名> <内容> - 广播给指定分组\n"
        "也可以回复一条文本、图片、视频、文件、音频、语音、贴纸消息后使用：\n"
        "/broadcast all\n"
        "/broadcast group <分组名>\n"
        "回复消息方式支持后续编辑同步。"
    )


@admin_only
async def broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text(_broadcast_help_text())
        return

    scope = context.args[0].lower()
    admin_id = update.effective_user.id
    group_data = None
    group_name = None
    text_start = 1

    if scope == "all":
        _, recipients = await db.get_broadcast_recipients()
        broadcast_scope = "all"
    elif scope == "group":
        if len(context.args) < 2:
            await update.message.reply_text(
                copy_text.with_deco("女仆小抄：/broadcast group <分组名> [内容]", 'ASK')
            )
            return
        group_name = broadcast_service.normalize_group_name(context.args[1])
        group_data, recipients = await db.get_broadcast_recipients(group_name)
        if not group_data:
            await update.message.reply_text(
                copy_text.with_deco(f"没有找到分组：{group_name}", 'ERROR')
            )
            return
        broadcast_scope = "group"
        text_start = 2
    else:
        await update.message.reply_text(_broadcast_help_text())
        return

    if not recipients:
        target = "全部主人" if scope == "all" else f"分组 {group_name}"
        await update.message.reply_text(
            copy_text.with_deco(f"{target} 里还没有可以广播的人呢。", 'EMPTY')
        )
        return

    source_message = update.message.reply_to_message
    text = " ".join(context.args[text_start:]).strip()
    status_message = await update.message.reply_text(
        copy_text.with_deco(f"广播开始啦，目标 {len(recipients)} 人，请主人稍等。", 'WAIT')
    )

    if source_message:
        result = await broadcast_service.send_message_broadcast(
            context=context,
            recipients=recipients,
            source_message=source_message,
            admin_id=admin_id,
            scope=broadcast_scope,
            group_id=group_data['id'] if group_data else None,
        )
    elif text:
        result = await broadcast_service.send_text_broadcast(
            context=context,
            recipients=recipients,
            text=text,
            admin_id=admin_id,
            scope=broadcast_scope,
            group_id=group_data['id'] if group_data else None,
        )
    else:
        await status_message.edit_text(_broadcast_help_text())
        return

    target = "全部主人" if scope == "all" else f"分组 {group_data['name']}"
    await status_message.edit_text(
        copy_text.with_deco_head(
            f"广播完成：{target}\n"
            f"任务 ID: {result.broadcast_id}\n"
            f"目标: {result.total}\n"
            f"成功: {result.success}\n"
            f"失败: {result.failed}",
            'OK')
    )


def _spamrules_help_text() -> str:
    return (
        "关键词广告拦截小抄:\n"
        "/spamrules - 查看当前设置\n"
        "/spamrules on|off - 开关拦截\n"
        "/spamrules autoblock on|off - 开关命中后自动拉黑\n"
        "/spamrules add <关键词> - 添加关键词，多个用逗号分隔\n"
        "/spamrules del <关键词> - 删除关键词\n"
        "/spamrules clear - 清空全部关键词"
    )


def _parse_keyword_args(raw_text: str) -> list[str]:
    return [item.strip() for item in raw_text.replace(",", "\n").splitlines() if item.strip()]


@admin_only
async def spamrules(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        text = await spam_filter.format_settings()
        await update.message.reply_text(
            copy_text.with_deco_head(f"{text}\n\n{_spamrules_help_text()}", 'CAT')
        )
        return

    subcommand = context.args[0].lower()
    admin_id = update.effective_user.id

    if subcommand in {"on", "off"}:
        await db.set_spam_keyword_filter_enabled(subcommand == "on")
        await update.message.reply_text(
            copy_text.with_deco(f"关键词拦截女仆已经{'上岗值班啦' if subcommand == 'on' else '回屋休息啦'}。", 'CAT')
        )
        return

    if subcommand == "autoblock":
        if len(context.args) < 2 or context.args[1].lower() not in {"on", "off"}:
            await update.message.reply_text(
                copy_text.with_deco("女仆小抄：/spamrules autoblock on|off", 'ASK')
            )
            return
        enabled = context.args[1].lower() == "on"
        await db.set_spam_keyword_auto_block(enabled)
        await update.message.reply_text(
            copy_text.with_deco(f"自动拉黑女仆已经{'上岗值班啦' if enabled else '回屋休息啦'}。", 'CAT')
        )
        return

    if subcommand in {"add", "del", "delete", "remove"}:
        if len(context.args) < 2:
            await update.message.reply_text(
                copy_text.with_deco("主人还没告诉女仆要拦什么词呢。", 'ASK')
            )
            return
        keywords = _parse_keyword_args(" ".join(context.args[1:]))
        if not keywords:
            await update.message.reply_text(
                copy_text.with_deco("这些词女仆抄不下来呢，主人换一组试试嘛。", 'ERROR')
            )
            return

        changed = 0
        if subcommand == "add":
            for keyword in keywords:
                if await db.add_spam_keyword(keyword, created_by=admin_id):
                    changed += 1
            await update.message.reply_text(
                copy_text.with_deco(f"已经把 {changed} 个关键词抄进拦截小本本啦。", 'OK')
            )
        else:
            for keyword in keywords:
                if await db.remove_spam_keyword(keyword):
                    changed += 1
            await update.message.reply_text(
                copy_text.with_deco(f"已经从拦截小本本上划掉 {changed} 个关键词啦。", 'OK')
            )
        return

    if subcommand == "clear":
        count = await db.clear_spam_keywords()
        await update.message.reply_text(
            copy_text.with_deco(
                f"哼，真的要全清吗？好吧，既然主人说了——拦截小本本已经擦干净，{count} 个关键词都划掉了。",
                'TSUNDERE')
        )
        return

    await update.message.reply_text(_spamrules_help_text())


def _tgmon_help_text() -> str:
    return (
        "TG 群/频道监听小抄:\n"
        "/tgmon - 打开监听面板\n"
        "/tgmon list - 查看监听列表\n"
        "/tgmon discovered - 查看用户会话发现的群/频道\n"
        "/tgmon add <名称> <chat_id> <关键词1,关键词2> [bot|user_session]\n"
        "/tgmon on <ID> /tgmon off <ID>\n"
        "/tgmon delete <ID>\n"
        "/tgmon keywords <ID> <关键词1,关键词2>\n"
        "/tgmon exclude <ID> <排除词1,排除词2>\n"
        "/tgmon interval <ID> <秒数>"
    )


async def _refresh_tg_monitor_after_change(context: ContextTypes.DEFAULT_TYPE):
    await tg_monitor.refresh_user_session_listener(context.application)


@admin_only
async def tgmon(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text(
            await tg_monitor.build_panel_text(),
            reply_markup=tg_monitor.build_panel_keyboard(),
        )
        return

    subcommand = context.args[0].lower()
    admin_id = update.effective_user.id

    if subcommand == "list":
        await update.message.reply_text(await tg_monitor.build_monitor_list_text())
        return

    if subcommand == "discovered":
        chats = await db.list_discovered_tg_chats(limit=50)
        if not chats:
            await update.message.reply_text(
                copy_text.with_deco(
                    "女仆还没探到任何群或频道呢。等主人配好 TG_API_SESSION、让监听女仆上岗，她自己就会记下来。",
                    'EMPTY')
            )
            return
        lines = ["发现的 TG 群/频道", ""]
        for chat in chats:
            username = f" @{chat['username']}" if chat.get('username') else ""
            lines.append(
                f"- {chat.get('title') or chat['chat_id']}{username}\n"
                f"  chat_id: {chat['chat_id']}\n"
                f"  最近看到: {chat.get('last_seen_at') or '-'}"
            )
        await update.message.reply_text("\n".join(lines))
        return

    if subcommand == "add":
        if len(context.args) < 4:
            await update.message.reply_text(
                copy_text.with_deco("女仆小抄：/tgmon add <名称> <chat_id> <关键词1,关键词2> [bot|user_session]", 'ASK')
            )
            return
        name = context.args[1].strip()
        try:
            chat_id = int(context.args[2])
        except ValueError:
            await update.message.reply_text(copy_text.tgmon_add_id_invalid())
            return

        source = config.TG_MONITOR_DEFAULT_SOURCE
        keyword_args = context.args[3:]
        if keyword_args and keyword_args[-1].lower() in {"bot", "user_session"}:
            source = keyword_args[-1].lower()
            keyword_args = keyword_args[:-1]
        if source not in {"bot", "user_session"}:
            source = "user_session"

        keywords = tg_monitor.parse_keywords(" ".join(keyword_args))
        if not keywords:
            await update.message.reply_text(copy_text.tgmon_no_keywords())
            return
        try:
            monitor_id = await db.create_tg_group_monitor(
                name=name,
                chat_id=chat_id,
                keywords=keywords,
                created_by=admin_id,
                listen_source=source,
            )
        except Exception:
            # 异常详情只进日志，给主人的是简短说明
            logging.exception("创建 TG 监听失败")
            await update.message.reply_text(
                copy_text.with_deco("监听没能建起来呢，主人稍后再试一次嘛。", 'ERROR')
            )
            return
        await _refresh_tg_monitor_after_change(context)
        await update.message.reply_text(
            copy_text.with_deco(f"TG 监听 #{monitor_id} 已经替主人建好啦：{name}", 'OK')
        )
        return

    if subcommand in {"on", "off", "delete", "del", "rm"}:
        if len(context.args) < 2:
            await update.message.reply_text(copy_text.tgmon_pick_id())
            return
        try:
            monitor_id = int(context.args[1])
        except ValueError:
            await update.message.reply_text(copy_text.tgmon_id_invalid())
            return
        if subcommand in {"delete", "del", "rm"}:
            changed = await db.delete_tg_group_monitor(monitor_id)
            await update.message.reply_text(
                copy_text.with_deco(f"TG 监听 #{monitor_id} 已经替主人撤掉啦。", 'OK') if changed
                else copy_text.tgmon_not_found()
            )
        else:
            changed = await db.update_tg_group_monitor(monitor_id, enabled=(subcommand == "on"))
            await update.message.reply_text(
                copy_text.with_deco(
                    f"TG 监听 #{monitor_id} 已经{'上岗值班啦' if subcommand == 'on' else '回屋休息啦'}。",
                    'OK')
                if changed else copy_text.tgmon_not_found()
            )
        await _refresh_tg_monitor_after_change(context)
        return

    if subcommand in {"keywords", "exclude"}:
        if len(context.args) < 3:
            await update.message.reply_text(
                copy_text.with_deco(f"女仆小抄：/tgmon {subcommand} <ID> <关键词1,关键词2>", 'ASK')
            )
            return
        try:
            monitor_id = int(context.args[1])
        except ValueError:
            await update.message.reply_text(copy_text.tgmon_id_invalid())
            return
        words = tg_monitor.parse_keywords(" ".join(context.args[2:]))
        field = "keywords" if subcommand == "keywords" else "exclude_keywords"
        changed = await db.update_tg_group_monitor(monitor_id, **{field: words})
        await update.message.reply_text(copy_text.tgmon_keywords_updated() if changed else copy_text.tgmon_not_found())
        await _refresh_tg_monitor_after_change(context)
        return

    if subcommand == "interval":
        if len(context.args) < 3:
            await update.message.reply_text(
                copy_text.with_deco("女仆小抄：/tgmon interval <ID> <秒数>", 'ASK')
            )
            return
        try:
            monitor_id = int(context.args[1])
            seconds = max(0, int(context.args[2]))
        except ValueError:
            await update.message.reply_text(copy_text.tgmon_id_and_seconds_invalid())
            return
        changed = await db.update_tg_group_monitor(monitor_id, min_interval_seconds=seconds)
        await update.message.reply_text(copy_text.tgmon_interval_updated() if changed else copy_text.tgmon_not_found())
        return

    await update.message.reply_text(_tgmon_help_text())


def _webmon_help_text() -> str:
    return (
        "网页监控小抄:\n"
        "/webmon - 打开网页监控面板\n"
        "/webmon list - 查看监控列表\n"
        "/webmon add <名称> <url> [关键词1,关键词2]\n"
        "/webmon on <ID> /webmon off <ID>\n"
        "/webmon run <ID> - 立即检查\n"
        "/webmon delete <ID>\n"
        "/webmon keywords <ID> <关键词1,关键词2>\n"
        "/webmon interval <ID> <秒数>"
    )


@admin_only
async def webmon(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text(
            await web_monitor.build_panel_text(),
            reply_markup=web_monitor.build_panel_keyboard(),
        )
        return

    subcommand = context.args[0].lower()
    admin_id = update.effective_user.id

    if subcommand == "list":
        await update.message.reply_text(await web_monitor.build_monitor_list_text())
        return

    if subcommand == "add":
        if len(context.args) < 3:
            await update.message.reply_text(
                copy_text.with_deco("女仆小抄：/webmon add <名称> <url> [关键词1,关键词2]", 'ASK')
            )
            return
        name = context.args[1].strip()
        url = context.args[2].strip()
        keywords = web_monitor.parse_keywords(" ".join(context.args[3:])) if len(context.args) > 3 else []
        try:
            monitor_id = await db.create_web_monitor(
                name=name,
                url=url,
                keywords=keywords,
                created_by=admin_id,
            )
        except Exception:
            # 异常详情只进日志，给主人的是简短说明
            logging.exception("创建网页监控失败")
            await update.message.reply_text(
                copy_text.with_deco("网页监控没能建起来呢，主人稍后再试一次嘛。", 'ERROR')
            )
            return
        await update.message.reply_text(
            copy_text.with_deco(f"网页监控 #{monitor_id} 已经替主人建好啦：{name}", 'OK')
        )
        return

    if subcommand in {"on", "off", "delete", "del", "rm", "run"}:
        if len(context.args) < 2:
            await update.message.reply_text(copy_text.webmon_pick_id())
            return
        try:
            monitor_id = int(context.args[1])
        except ValueError:
            await update.message.reply_text(copy_text.webmon_id_invalid())
            return

        if subcommand == "run":
            monitor = await db.get_web_monitor(monitor_id)
            if not monitor:
                await update.message.reply_text(copy_text.webmon_not_found())
                return
            status_message = await update.message.reply_text(copy_text.webmon_running())
            sent = await web_monitor.run_monitor(context.application, monitor)
            await status_message.edit_text(copy_text.webmon_run_done(sent))
            return

        if subcommand in {"delete", "del", "rm"}:
            changed = await db.delete_web_monitor(monitor_id)
            await update.message.reply_text(
                copy_text.with_deco(f"网页监控 #{monitor_id} 已经替主人撤掉啦。", 'OK') if changed
                else copy_text.webmon_not_found()
            )
        else:
            changed = await db.update_web_monitor(monitor_id, enabled=(subcommand == "on"))
            await update.message.reply_text(
                copy_text.with_deco(
                    f"网页监控 #{monitor_id} 已经{'上岗值班啦' if subcommand == 'on' else '回屋休息啦'}。",
                    'OK')
                if changed else copy_text.webmon_not_found()
            )
        return

    if subcommand == "keywords":
        if len(context.args) < 3:
            await update.message.reply_text(
                copy_text.with_deco("女仆小抄：/webmon keywords <ID> <关键词1,关键词2>", 'ASK')
            )
            return
        try:
            monitor_id = int(context.args[1])
        except ValueError:
            await update.message.reply_text(copy_text.webmon_id_invalid())
            return
        keywords = web_monitor.parse_keywords(" ".join(context.args[2:]))
        changed = await db.update_web_monitor(monitor_id, keywords=keywords)
        await update.message.reply_text(copy_text.webmon_keywords_updated() if changed else copy_text.webmon_not_found())
        return

    if subcommand == "interval":
        if len(context.args) < 3:
            await update.message.reply_text(
                copy_text.with_deco("女仆小抄：/webmon interval <ID> <秒数>", 'ASK')
            )
            return
        try:
            monitor_id = int(context.args[1])
            seconds = max(60, int(context.args[2]))
        except ValueError:
            await update.message.reply_text(copy_text.webmon_id_and_seconds_invalid())
            return
        changed = await db.update_web_monitor(monitor_id, interval_seconds=seconds)
        await update.message.reply_text(copy_text.webmon_interval_updated() if changed else copy_text.webmon_not_found())
        return

    await update.message.reply_text(_webmon_help_text())


@admin_only
async def updatebot(update: Update, context: ContextTypes.DEFAULT_TYPE):
    repo_dir = Path(__file__).resolve().parent.parent
    action = context.args[0].lower() if context.args else "status"
    status_message = await update.message.reply_text(
        copy_text.with_deco("女仆正在检查 Git 状态，请稍等。", 'WAIT')
    )

    try:
        if action == "status":
            status = await safe_update.get_status(repo_dir, fetch_remote=True)
            rollback = await db.get_app_meta("last_update_rollback")
            await status_message.edit_text(safe_update.format_status(status, rollback))
            return

        if action in {"apply", "run", "update"}:
            status = await safe_update.apply_update(repo_dir)
            rollback = await db.get_app_meta("last_update_rollback")
            await status_message.edit_text(
                copy_text.with_deco_head(
                    safe_update.format_status(status, rollback) + "\n\n更新流程已经跑完啦。主人记得按部署方式重启 Bot。",
                    'OK')
            )
            return

        if action == "rollback":
            commit = await safe_update.rollback_last_update(repo_dir)
            await status_message.edit_text(
                copy_text.with_deco(f"已经替主人回滚到 {commit[:12]}。重启 Bot 之后才会生效哦。", 'OK')
            )
            return

        await status_message.edit_text(
            copy_text.with_deco("女仆小抄：/updatebot [status|apply|rollback]", 'ASK')
        )
    except safe_update.SafeUpdateError as exc:
        # 拒绝原因（未提交改动 / 无回滚点 等）是主人自查所需信息，照原样给出
        await status_message.edit_text(
            copy_text.with_deco_head(
                f"女仆不敢动这个操作呢：{exc}\n\n不是担心主人才拦下的……只是弄坏了不好收场嘛。",
                'DENY')
        )
    except Exception:
        # 异常详情只进日志，给主人的是简短说明
        logging.exception("安全更新失败")
        await status_message.edit_text(
            copy_text.with_deco("安全更新失败啦，女仆没能走完流程，主人稍后再试一次嘛。", 'ERROR')
        )


@admin_only
async def autoreply(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        is_enabled = await db.get_autoreply_enabled()
        status_text = "正在值班" if is_enabled else "正在休息"
        
        message = (
            f"自动回复女仆管理\n\n"
            f"当前状态: {status_text}\n\n"
            f"主人，请选择要安排的工作："
        )
        
        keyboard = [
            [
                InlineKeyboardButton(
                    copy_text.BTN_AUTOREPLY_REST if is_enabled else copy_text.BTN_AUTOREPLY_ON_DUTY,
                    callback_data=f"autoreply_toggle"
                )
            ],
            [InlineKeyboardButton(copy_text.BTN_KB_LIST, callback_data="autoreply_kb_list_page_1")],
            [InlineKeyboardButton(copy_text.BTN_KB_ADD, callback_data="autoreply_kb_add")],
        ]
        
        await update.message.reply_text(
            message,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='Markdown'
        )
        return
    
    subcommand = context.args[0].lower()
    
    if subcommand == "on":
        await db.set_autoreply_enabled(True)
        await update.message.reply_text(
            copy_text.with_deco("自动回复女仆已开始值班啦。", 'OK')
        )
    elif subcommand == "off":
        await db.set_autoreply_enabled(False)
        await update.message.reply_text(
            copy_text.with_deco("自动回复女仆已去休息啦。", 'OK')
        )
    elif subcommand == "add":
        if len(context.args) < 3:
            await update.message.reply_text(
                copy_text.with_deco(
                    "女仆小抄：/autoreply add <标题> <内容>\n\n"
                    "示例: /autoreply add 常见问题 这是问题的答案",
                    'ASK')
            )
            return
        
        title = context.args[1]
        content = " ".join(context.args[2:])
        await db.add_knowledge_entry(title, content)
        await update.message.reply_text(
            copy_text.with_deco(f"新便签已经替主人收进小本本啦：{title}", 'OK')
        )
    elif subcommand == "list":
        entries = await db.get_all_knowledge_entries()
        if not entries:
            await update.message.reply_text(copy_text.kb_empty())
            return
        
        message = "知识小本本条目:\n\n"
        for entry in entries:
            message += f"ID: {entry['id']}\n"
            message += f"标题: {entry['title']}\n"
            message += f"内容摘要: {entry['content'][:50]}...\n\n"
        
        await update.message.reply_text(message)
    elif subcommand == "edit":
        if len(context.args) < 4:
            await update.message.reply_text(
                copy_text.with_deco(
                    "女仆小抄：/autoreply edit <ID> <标题> <内容>\n\n"
                    "示例: /autoreply edit 1 新标题 新内容",
                    'ASK')
            )
            return
        
        try:
            entry_id = int(context.args[1])
        except ValueError:
            await update.message.reply_text(copy_text.kb_entry_id_invalid())
            return
        
        title = context.args[2]
        content = " ".join(context.args[3:])
        
        entry = await db.get_knowledge_entry(entry_id)
        if not entry:
            await update.message.reply_text(copy_text.kb_entry_not_found(entry_id))
            return
        
        await db.update_knowledge_entry(entry_id, title, content)
        await update.message.reply_text(
            copy_text.with_deco(f"便签已经替主人擦亮更新啦：{title}", 'OK')
        )
    elif subcommand == "delete":
        if len(context.args) < 2:
            await update.message.reply_text(
                copy_text.with_deco("女仆小抄：/autoreply delete <ID>", 'ASK')
            )
            return
        
        try:
            entry_id = int(context.args[1])
        except ValueError:
            await update.message.reply_text(copy_text.kb_entry_id_invalid())
            return
        
        entry = await db.get_knowledge_entry(entry_id)
        if not entry:
            await update.message.reply_text(copy_text.kb_entry_not_found(entry_id))
            return
        
        await db.delete_knowledge_entry(entry_id)
        await update.message.reply_text(
            copy_text.with_deco(f"便签已经替主人从小本本里取下来啦：{entry['title']}", 'OK')
        )
    else:
        await update.message.reply_text(
            copy_text.with_deco(
                "女仆小抄：\n"
                "/autoreply - 打开管理菜单\n"
                "/autoreply on - 让自动回复女仆值班\n"
                "/autoreply off - 让自动回复女仆休息\n"
                "/autoreply add <标题> <内容> - 新增知识便签\n"
                "/autoreply edit <ID> <标题> <内容> - 修改知识便签\n"
                "/autoreply delete <ID> - 删除知识便签\n"
                "/autoreply list - 列出知识小本本",
                'ASK')
        )
