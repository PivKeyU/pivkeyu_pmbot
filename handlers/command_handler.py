from datetime import datetime, timedelta, timezone
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from database import models as db
from services.blacklist import block_user, unblock_user, get_blacklist_keyboard
from services import broadcast as broadcast_service
from utils.decorators import admin_only

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
        f"主人好呀，{user.first_name}！\n\n"
        "这里是随时待命的双向聊天女仆。\n"
        "主人可以直接把消息交给我，我会乖乖送到管理员那边。\n\n"
        "输入 /help 可以查看女仆小手册。"
    )
    
    await update.message.reply_text(welcome_message)

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    help_text = (
        "这里是双向聊天女仆机器人。\n\n"
        "女仆可代办的事:\n"
        "- 递送文本、图片、视频、音频和文档\n"
        "- 保留 Markdown 格式，让消息整整齐齐\n"
        "- 首次递送前会请主人完成一个小验证\n\n"
        "管理员女仆长命令:\n"
        "- `/block` - 在用户话题中把捣乱者请进黑名单小本本\n"
        "- `/blacklist` - 查看黑名单小本本\n"
        "- `/stats` - 查看宅邸统计\n"
        "- `/view_filtered` - 查看被女仆拦下的消息\n"
        "- `/exempt` - 给可信用户发放审查通行证（临时或永久）\n"
        "- `/group` - 管理私聊用户分组\n"
        "- `/broadcast` - 向全部用户或指定分组广播\n"
    )
    
    await update.message.reply_text(help_text, parse_mode='Markdown')

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
            await update.message.reply_text("主人，这个话题没有对应用户，女仆没法下手呢。")
        return

    if not context.args:
        await update.message.reply_text("主人，请给出用户 ID，或在用户话题里使用哦。女仆小抄: /block <user_id> [reason]")
        return
    
    try:
        user_id_to_block = int(context.args[0])
        reason = " ".join(context.args[1:]) if len(context.args) > 1 else "无"
        response = await block_user(user_id_to_block, reason, update.effective_user.id)
        await update.message.reply_text(response)
    except (ValueError, IndexError):
        await update.message.reply_text("这个用户 ID 看起来不对劲，主人再检查一下吧。")

@admin_only
async def unblock(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("主人，请告诉女仆要解封的用户 ID。女仆小抄: /unblock <user_id>")
        return
    
    try:
        user_id_to_unblock = int(context.args[0])
        response = await unblock_user(user_id_to_unblock)
        await update.message.reply_text(response)
    except (ValueError, IndexError):
        await update.message.reply_text("这个用户 ID 看起来不对劲，主人再检查一下吧。")

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
        [InlineKeyboardButton("所有主人名册", callback_data="stats_list_all_users_page_1")],
        [InlineKeyboardButton("黑名单小本本", callback_data="stats_list_blacklist_page_1")]
    ]
    
    await update.message.reply_text(
        stats_message, 
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode='Markdown'
    )

async def getid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_type = update.effective_chat.type
    user_id = update.effective_user.id

    if chat_type == 'private':
        message = f"主人，您的用户 ID 是: `{user_id}`"
    else:
        chat_id = update.effective_chat.id
        message = (
            f"主人，群组 ID 是: `{chat_id}`\n"
            f"主人，您的用户 ID 是: `{user_id}`"
        )
    
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
        [InlineKeyboardButton("黑名单小本本", callback_data="panel_blacklist_page_1"), InlineKeyboardButton("主人名册", callback_data="panel_stats")],
        [InlineKeyboardButton("拦截消息篮", callback_data="panel_filtered_page_1"), InlineKeyboardButton("自动回复女仆管理", callback_data="panel_autoreply")],
        [InlineKeyboardButton("通行证名单管理", callback_data="panel_exemptions_page_1"), InlineKeyboardButton("网络测试茶具管理", callback_data="panel_network_test")],
        [InlineKeyboardButton("广播与分组", callback_data="panel_broadcast"), InlineKeyboardButton("RSS 订阅茶点管理", callback_data="panel_rss")],
        [InlineKeyboardButton("AI 模型衣柜", callback_data="panel_ai_settings")],
    ]
    
    await update.message.reply_text(
        message,
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode='Markdown'
    )

@admin_only
async def exempt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message
    admin_id = update.effective_user.id
    
    if message.is_topic_message:
        thread_id = message.message_thread_id
        user_to_exempt = await db.get_user_by_thread_id(thread_id)
        
        if not user_to_exempt:
            await update.message.reply_text("主人，这个话题没有对应用户，女仆没法下手呢。")
            return
        
        user_id_to_exempt = user_to_exempt['user_id']
        
        if not context.args:
            exemption_info = await db.get_exemption(user_id_to_exempt)
            if exemption_info:
                is_permanent = bool(exemption_info.get('is_permanent', 0))
                expires_at = exemption_info.get('expires_at')
                reason = exemption_info.get('reason', '无')
                
                status_text = "永久通行证" if is_permanent else f"临时通行证（到期时间: {expires_at}）"
                await update.message.reply_text(
                    f"主人，用户 {user_id_to_exempt} 当前拿着: {status_text}\n"
                    f"登记理由: {reason}\n\n"
                    f"女仆小抄:\n"
                    f"/exempt permanent [reason] - 发放永久通行证\n"
                    f"/exempt temp <小时数> [reason] - 发放临时通行证（例如: /exempt temp 24）\n"
                    f"/exempt remove - 收回通行证"
                )
            else:
                await update.message.reply_text(
                    f"主人，用户 {user_id_to_exempt} 目前还没有审查通行证。\n\n"
                    f"女仆小抄:\n"
                    f"/exempt permanent [reason] - 发放永久通行证\n"
                    f"/exempt temp <小时数> [reason] - 发放临时通行证（例如: /exempt temp 24）\n"
                    f"/exempt remove - 收回通行证"
                )
            return
        
        subcommand = context.args[0].lower()
        
        if subcommand == "remove":
            await db.remove_exemption(user_id_to_exempt)
            await update.message.reply_text(f"主人，女仆已收回用户 {user_id_to_exempt} 的审查通行证。")
            return
        
        reason = " ".join(context.args[1:]) if len(context.args) > 1 else "管理员发放通行证"
        
        if subcommand == "permanent":
            await db.add_exemption(user_id_to_exempt, is_permanent=True, exempted_by=admin_id, reason=reason)
            await update.message.reply_text(
                f"主人，用户 {user_id_to_exempt} 已拿到永久审查通行证。\n登记理由: {reason}"
            )
        elif subcommand == "temp":
            if len(context.args) < 2:
                await update.message.reply_text("主人，请告诉女仆临时通行证要生效几个小时。女仆小抄: /exempt temp <小时数> [reason]")
                return
            
            try:
                hours = int(context.args[1])
                expires_at = (datetime.now(timezone.utc) + timedelta(hours=hours)).isoformat()
                reason = " ".join(context.args[2:]) if len(context.args) > 2 else "管理员发放临时通行证"
                
                await db.add_exemption(user_id_to_exempt, is_permanent=False, exempted_by=admin_id, reason=reason, expires_at=expires_at)
                await update.message.reply_text(
                    f"主人，用户 {user_id_to_exempt} 已拿到 {hours} 小时临时审查通行证。\n登记理由: {reason}"
                )
            except ValueError:
                await update.message.reply_text("小时数要写成数字哦，主人。")
        else:
            await update.message.reply_text(
                "主人，这个子命令女仆看不懂呢。女仆小抄:\n"
                "/exempt permanent [reason] - 发放永久通行证\n"
                "/exempt temp <小时数> [reason] - 发放临时通行证\n"
                "/exempt remove - 收回通行证"
            )
        return
    
    if not context.args:
        await update.message.reply_text(
            "主人，请提供用户 ID，或在用户话题中吩咐女仆。\n\n"
            "女仆小抄:\n"
            "在话题中: /exempt [permanent|temp <小时数>|remove] [reason]\n"
            "直接使用: /exempt <user_id> [permanent|temp <小时数>|remove] [reason]"
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
                
                status_text = "永久通行证" if is_permanent else f"临时通行证（到期时间: {expires_at}）"
                await update.message.reply_text(
                    f"主人，用户 {user_id_to_exempt} 当前拿着: {status_text}\n登记理由: {reason}"
                )
            else:
                await update.message.reply_text(f"主人，用户 {user_id_to_exempt} 目前还没有审查通行证。")
            return
        
        subcommand = context.args[1].lower()
        reason = " ".join(context.args[2:]) if len(context.args) > 2 else "管理员发放通行证"
        
        if subcommand == "remove":
            await db.remove_exemption(user_id_to_exempt)
            await update.message.reply_text(f"主人，女仆已收回用户 {user_id_to_exempt} 的审查通行证。")
        elif subcommand == "permanent":
            await db.add_exemption(user_id_to_exempt, is_permanent=True, exempted_by=admin_id, reason=reason)
            await update.message.reply_text(
                f"主人，用户 {user_id_to_exempt} 已拿到永久审查通行证。\n登记理由: {reason}"
            )
        elif subcommand == "temp":
            if len(context.args) < 3:
                await update.message.reply_text("主人，请告诉女仆临时通行证要生效几个小时。女仆小抄: /exempt <user_id> temp <小时数> [reason]")
                return
            
            try:
                hours = int(context.args[2])
                expires_at = (datetime.now(timezone.utc) + timedelta(hours=hours)).isoformat()
                reason = " ".join(context.args[3:]) if len(context.args) > 3 else "管理员发放临时通行证"
                
                await db.add_exemption(user_id_to_exempt, is_permanent=False, exempted_by=admin_id, reason=reason, expires_at=expires_at)
                await update.message.reply_text(
                    f"主人，用户 {user_id_to_exempt} 已拿到 {hours} 小时临时审查通行证。\n登记理由: {reason}"
                )
            except ValueError:
                await update.message.reply_text("小时数要写成数字哦，主人。")
        else:
            await update.message.reply_text(
                "主人，这个子命令女仆看不懂呢。女仆小抄:\n"
                "/exempt <user_id> permanent [reason] - 发放永久通行证\n"
                "/exempt <user_id> temp <小时数> [reason] - 发放临时通行证\n"
                "/exempt <user_id> remove - 收回通行证"
            )
    except (ValueError, IndexError):
        await update.message.reply_text("这个用户 ID 看起来不对劲，主人再检查一下吧。")


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
            await update.message.reply_text("当前还没有任何分组。")
            return

        lines = ["用户分组列表", ""]
        for item in groups:
            lines.append(f"- {item['name']}：{item['member_count']} 人")
        await update.message.reply_text("\n".join(lines))
        return

    if subcommand == "create":
        if len(context.args) < 2:
            await update.message.reply_text("女仆小抄: /group create <分组名> [说明]")
            return
        name = broadcast_service.normalize_group_name(context.args[1])
        description = " ".join(context.args[2:]) if len(context.args) > 2 else None
        if not name:
            await update.message.reply_text("分组名不能为空。")
            return
        group_data, created = await db.get_or_create_user_group(name, admin_id, description)
        if created:
            await update.message.reply_text(f"已创建分组：{group_data['name']}")
        else:
            await update.message.reply_text(f"分组 {group_data['name']} 已经存在。")
        return

    if subcommand == "delete":
        if len(context.args) < 2:
            await update.message.reply_text("女仆小抄: /group delete <分组名>")
            return
        name = broadcast_service.normalize_group_name(context.args[1])
        deleted = await db.delete_user_group(name)
        await update.message.reply_text(f"已删除分组：{name}" if deleted else f"没有找到分组：{name}")
        return

    if subcommand == "members":
        if len(context.args) < 2:
            await update.message.reply_text("女仆小抄: /group members <分组名>")
            return
        name = broadcast_service.normalize_group_name(context.args[1])
        await update.message.reply_text(await broadcast_service.format_group_members(name))
        return

    if subcommand in {"add", "remove"}:
        if len(context.args) < 2:
            await update.message.reply_text(f"女仆小抄: /group {subcommand} <分组名> [user_id]")
            return

        name = broadcast_service.normalize_group_name(context.args[1])
        target_user_id = await _resolve_group_target_user_id(update, context, 2)
        if not target_user_id:
            await update.message.reply_text("请提供有效用户 ID，或在用户专属话题中使用这条命令。")
            return

        if not await db.get_user(target_user_id):
            await update.message.reply_text(f"用户 {target_user_id} 还没有和机器人建立过会话。")
            return

        if subcommand == "add":
            group_data, created, added = await db.add_user_to_group(name, target_user_id, admin_id)
            suffix = "（新分组已创建）" if created else ""
            if added:
                await update.message.reply_text(f"已将用户 {target_user_id} 加入分组 {group_data['name']}。{suffix}")
            else:
                await update.message.reply_text(f"用户 {target_user_id} 已经在分组 {group_data['name']} 里。")
        else:
            removed = await db.remove_user_from_group(name, target_user_id)
            await update.message.reply_text(
                f"已将用户 {target_user_id} 移出分组 {name}。" if removed else f"用户 {target_user_id} 不在分组 {name} 中。"
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
            await update.message.reply_text("女仆小抄: /broadcast group <分组名> [内容]")
            return
        group_name = broadcast_service.normalize_group_name(context.args[1])
        group_data, recipients = await db.get_broadcast_recipients(group_name)
        if not group_data:
            await update.message.reply_text(f"没有找到分组：{group_name}")
            return
        broadcast_scope = "group"
        text_start = 2
    else:
        await update.message.reply_text(_broadcast_help_text())
        return

    if not recipients:
        target = "全部用户" if scope == "all" else f"分组 {group_name}"
        await update.message.reply_text(f"{target} 中没有可广播的用户。")
        return

    source_message = update.message.reply_to_message
    text = " ".join(context.args[text_start:]).strip()
    status_message = await update.message.reply_text(f"开始广播，目标用户 {len(recipients)} 人，请稍等。")

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

    target = "全部用户" if scope == "all" else f"分组 {group_data['name']}"
    await status_message.edit_text(
        f"广播完成：{target}\n"
        f"任务 ID: {result.broadcast_id}\n"
        f"目标: {result.total}\n"
        f"成功: {result.success}\n"
        f"失败: {result.failed}"
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
                    "让自动回复女仆休息" if is_enabled else "让自动回复女仆值班",
                    callback_data=f"autoreply_toggle"
                )
            ],
            [InlineKeyboardButton("整理知识小本本", callback_data="autoreply_kb_list_page_1")],
            [InlineKeyboardButton("新增知识便签", callback_data="autoreply_kb_add")],
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
        await update.message.reply_text("自动回复女仆已开始值班啦。")
    elif subcommand == "off":
        await db.set_autoreply_enabled(False)
        await update.message.reply_text("自动回复女仆已去休息啦。")
    elif subcommand == "add":
        if len(context.args) < 3:
            await update.message.reply_text(
                "女仆小抄: /autoreply add <标题> <内容>\n\n"
                "示例: /autoreply add 常见问题 这是问题的答案"
            )
            return
        
        title = context.args[1]
        content = " ".join(context.args[2:])
        await db.add_knowledge_entry(title, content)
        await update.message.reply_text(f"已新增知识便签: {title}")
    elif subcommand == "list":
        entries = await db.get_all_knowledge_entries()
        if not entries:
            await update.message.reply_text("知识小本本还是空的，主人。")
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
                "女仆小抄: /autoreply edit <ID> <标题> <内容>\n\n"
                "示例: /autoreply edit 1 新标题 新内容"
            )
            return
        
        try:
            entry_id = int(context.args[1])
        except ValueError:
            await update.message.reply_text("这个条目 ID 不对劲，主人再看一眼吧。")
            return
        
        title = context.args[2]
        content = " ".join(context.args[3:])
        
        entry = await db.get_knowledge_entry(entry_id)
        if not entry:
            await update.message.reply_text(f"女仆翻遍小本本，也没找到条目 ID {entry_id}。")
            return
        
        await db.update_knowledge_entry(entry_id, title, content)
        await update.message.reply_text(f"知识便签已擦亮更新: {title}")
    elif subcommand == "delete":
        if len(context.args) < 2:
            await update.message.reply_text("女仆小抄: /autoreply delete <ID>")
            return
        
        try:
            entry_id = int(context.args[1])
        except ValueError:
            await update.message.reply_text("这个条目 ID 不对劲，主人再看一眼吧。")
            return
        
        entry = await db.get_knowledge_entry(entry_id)
        if not entry:
            await update.message.reply_text(f"女仆翻遍小本本，也没找到条目 ID {entry_id}。")
            return
        
        await db.delete_knowledge_entry(entry_id)
        await update.message.reply_text(f"知识便签已从小本本里取下: {entry['title']}")
    else:
        await update.message.reply_text(
            "女仆小抄:\n"
            "/autoreply - 打开管理菜单\n"
            "/autoreply on - 让自动回复女仆值班\n"
            "/autoreply off - 让自动回复女仆休息\n"
            "/autoreply add <标题> <内容> - 新增知识便签\n"
            "/autoreply edit <ID> <标题> <内容> - 修改知识便签\n"
            "/autoreply delete <ID> - 删除知识便签\n"
            "/autoreply list - 列出知识小本本"
        )
