import time
import ipaddress
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from .config import SERVERS, ADMIN_USERS, AUTHORIZED_USERS, save_config
from .state import user_data, last_ping_command_time, last_nexttrace_command_time, cleanup_cooldown
from .tasks import do_ping_in_background, do_nexttrace_in_background
from .utils import schedule_delete_message, check_authorization, check_is_admin, validate_target
from utils import copy as copy_text


def _who(user_id) -> str:
    """按称呼规则算这一位读者该被叫作什么（管理员 -> 主人，普通用户 -> 客人）。

    ping / nexttrace 这两条命令授权用户也能用，读者不一定是管理员。
    """
    return copy_text.address(check_is_admin(user_id, ADMIN_USERS))

async def start_command(update, context):
    user_id = update.effective_user.id
    if not check_authorization(user_id, AUTHORIZED_USERS, ADMIN_USERS):
        await update.message.reply_text(
            copy_text.nt_no_pass(user_id),
            parse_mode="Markdown"
        )
        return

    await update.message.reply_text(
        copy_text.with_deco_head(
            "欢迎使用网络测试女仆。\n\n"
            "女仆小抄：\n"
            "1）Ping 测试：/ping 后按提示进行\n"
            "2）路由追踪：/nexttrace 后按提示进行\n\n"
            "管理员女仆长命令：/adduser, /rmuser, /addserver, /rmserver",
            'GREET')
    )

async def ping_command(update, context):
    user_id = update.effective_user.id
    if not check_authorization(user_id, AUTHORIZED_USERS, ADMIN_USERS):
        await update.message.reply_text(
            copy_text.nt_no_pass(user_id),
            parse_mode="Markdown"
        )
        return

    now_ts = time.time()
    cleanup_cooldown(last_ping_command_time)
    if user_id in last_ping_command_time:
        elapsed = now_ts - last_ping_command_time[user_id]
        if elapsed < 15:
            await update.message.reply_text(
                copy_text.with_deco(
                    f"{_who(user_id)}还需要等 {15 - int(elapsed)} 秒才能再次使用 /ping（每 15 秒一次）。",
                    'WAIT'))
            return
    last_ping_command_time[user_id] = now_ts

    if not SERVERS:
        await update.message.reply_text(copy_text.nt_no_servers(_who(user_id)))
        return

    if user_id in user_data:
        del user_data[user_id]

    args = context.args
    if len(args) >= 1:
        ip_or_domain = args[0]
        # 校验目标地址，防止 SSH 命令注入
        ok, err = validate_target(ip_or_domain, _who(user_id))
        if not ok:
            await update.message.reply_text(err)
            return
        try:
            ping_count = int(args[1]) if len(args) >= 2 else 4
        except ValueError:
            await update.message.reply_text(
                copy_text.with_deco(f"Ping 次数要写成数字哦，{_who(user_id)}。", 'ERROR'))
            return
        if ping_count < 1 or ping_count > 50:
            await update.message.reply_text(
                copy_text.with_deco(f"Ping 次数要在 1 到 50 之间哦，{_who(user_id)}。", 'ERROR'))
            return

        keyboard = []
        for idx, server_info in enumerate(SERVERS):
            btn = InlineKeyboardButton(server_info['name'], callback_data=f"nt_server_{idx}")
            keyboard.append([btn])
        reply_markup = InlineKeyboardMarkup(keyboard)
        text = f"{_who(user_id)}指定了：目标= {ip_or_domain}，次数= {ping_count} 次\n请选择服务器："
        msg = await update.message.reply_text(text, reply_markup=reply_markup)
        user_data[user_id] = {
            "operation": "ping",
            "mode": "cmd",
            "server_info": None,
            "target": ip_or_domain,
            "count": ping_count,
            "chat_id": msg.chat_id,
            "message_id": msg.message_id
        }
    else:
        keyboard = []
        for idx, server_info in enumerate(SERVERS):
            btn = InlineKeyboardButton(server_info['name'], callback_data=f"nt_server_{idx}")
            keyboard.append([btn])
        reply_markup = InlineKeyboardMarkup(keyboard)
        text = f"{_who(user_id)}，请选择执行 Ping 测试的服务器："
        msg = await update.message.reply_text(text, reply_markup=reply_markup)
        user_data[user_id] = {
            "operation": "ping",
            "mode": "interactive",
            "server_info": None,
            "target": None,
            "count": None,
            "chat_id": msg.chat_id,
            "message_id": msg.message_id
        }

async def nexttrace_command(update, context):
    user_id = update.effective_user.id
    if not check_authorization(user_id, AUTHORIZED_USERS, ADMIN_USERS):
        await update.message.reply_text(
            copy_text.nt_no_pass(user_id),
            parse_mode="Markdown"
        )
        return

    now_ts = time.time()
    cleanup_cooldown(last_nexttrace_command_time)
    if user_id in last_nexttrace_command_time:
        elapsed = now_ts - last_nexttrace_command_time[user_id]
        if elapsed < 10:
            await update.message.reply_text(
                copy_text.with_deco(
                    f"{_who(user_id)}还需要等 {10 - int(elapsed)} 秒才能再次使用命令（每 10 秒一次）。",
                    'WAIT'))
            return
    last_nexttrace_command_time[user_id] = now_ts

    if not SERVERS:
        await update.message.reply_text(copy_text.nt_no_servers(_who(user_id)))
        return

    if user_id in user_data:
        del user_data[user_id]

    args = context.args
    if len(args) >= 1:
        target = args[0]
        # 校验目标地址，防止 SSH 命令注入
        ok, err = validate_target(target, _who(user_id))
        if not ok:
            await update.message.reply_text(err)
            return
        
        keyboard = [
            [
                InlineKeyboardButton(copy_text.BTN_NT_ICMP, callback_data="nt_trace_mode_icmp"),
                InlineKeyboardButton(copy_text.BTN_NT_TCP, callback_data="nt_trace_mode_tcp")
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        text = f"{_who(user_id)}指定了目标：{target}\n请选择追踪模式："
        msg = await update.message.reply_text(text, reply_markup=reply_markup)
        user_data[user_id] = {
            "operation": "nexttrace",
            "mode": "cmd",
            "server_info": None,
            "target": target,
            "ip_type": None,
            "trace_mode": None,
            "chat_id": msg.chat_id,
            "message_id": msg.message_id
        }
    else:
        keyboard = [
            [
                InlineKeyboardButton(copy_text.BTN_NT_ICMP, callback_data="nt_trace_mode_icmp"),
                InlineKeyboardButton(copy_text.BTN_NT_TCP, callback_data="nt_trace_mode_tcp")
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        text = f"{_who(user_id)}，请选择路由追踪模式："
        msg = await update.message.reply_text(text, reply_markup=reply_markup)
        user_data[user_id] = {
            "operation": "nexttrace",
            "mode": "interactive",
            "server_info": None,
            "target": None,
            "ip_type": None,
            "trace_mode": None,
            "chat_id": msg.chat_id,
            "message_id": msg.message_id
        }

async def add_user_command(update, context):
    user_id = update.effective_user.id
    if not check_is_admin(user_id, ADMIN_USERS):
        await update.message.reply_text(
            copy_text.nt_not_admin(user_id),
            parse_mode="Markdown"
        )
        return

    args = context.args
    if len(args) < 1:
        await update.message.reply_text(copy_text.with_deco("女仆小抄：/adduser <user_id>", 'ASK'))
        return

    try:
        new_user_id = int(args[0])
    except ValueError:
        await update.message.reply_text(copy_text.nt_user_id_invalid())
        return

    if new_user_id in AUTHORIZED_USERS:
        await update.message.reply_text(copy_text.auth_already(new_user_id, copy_text.AUTH_LIST_NETWORK_TEST))
    else:
        AUTHORIZED_USERS.append(new_user_id)
        save_config()
        await update.message.reply_text(copy_text.auth_granted(new_user_id, copy_text.AUTH_LIST_NETWORK_TEST))

async def rm_user_command(update, context):
    user_id = update.effective_user.id
    if not check_is_admin(user_id, ADMIN_USERS):
        await update.message.reply_text(
            copy_text.nt_not_admin(user_id),
            parse_mode="Markdown"
        )
        return

    args = context.args
    if len(args) < 1:
        await update.message.reply_text(copy_text.with_deco("女仆小抄：/rmuser <user_id>", 'ASK'))
        return

    try:
        del_user_id = int(args[0])
    except ValueError:
        await update.message.reply_text(copy_text.nt_user_id_invalid())
        return

    if del_user_id in AUTHORIZED_USERS:
        AUTHORIZED_USERS.remove(del_user_id)
        save_config()
        await update.message.reply_text(copy_text.auth_revoked(del_user_id, copy_text.AUTH_LIST_NETWORK_TEST))
    else:
        await update.message.reply_text(copy_text.auth_missing(del_user_id, copy_text.AUTH_LIST_NETWORK_TEST))

async def add_server_command(update, context):
    user_id = update.effective_user.id
    if not check_is_admin(user_id, ADMIN_USERS):
        await update.message.reply_text(
            copy_text.nt_not_admin(user_id),
            parse_mode="Markdown"
        )
        return

    message_text = update.message.text.strip()
    
    if message_text == "/addserver":
        msg = await update.message.reply_text(
            copy_text.with_deco_head(
                "欢迎使用服务器登记女仆向导。\n\n"
                "请主人按提示一步一步交代服务器信息。\n"
                "步骤 1/5: 请告诉女仆服务器名称（如：香港 - GCP）：\n\n"
                "主人可以随时输入 /cancel 取消登记流程",
                'GREET')
        )
        
        from .state import user_data
        user_data[user_id] = {
            "operation": "addserver",
            "step": 1,
            "server_data": {},
            "chat_id": msg.chat_id,
            "message_id": msg.message_id,
            "prompt_message_id": msg.message_id
        }
        
        try:
            await context.bot.delete_message(
                chat_id=update.message.chat_id,
                message_id=update.message.message_id
            )
        except Exception:
            pass
            
        return
    
    if message_text == "/cancel":
        from .state import user_data
        if user_id in user_data and user_data[user_id].get("operation") == "addserver":
            if user_data[user_id].get("prompt_message_id"):
                try:
                    await context.bot.delete_message(
                        chat_id=update.message.chat_id,
                        message_id=user_data[user_id]["prompt_message_id"]
                    )
                except Exception:
                    pass
                    
            del user_data[user_id]
            cancel_msg = await update.message.reply_text(copy_text.nt_server_reg_cancelled())
            
            context.application.create_task(schedule_delete_message(context, update.message.chat_id, cancel_msg.message_id, delay=5))
            
            try:
                await context.bot.delete_message(
                    chat_id=update.message.chat_id,
                    message_id=update.message.message_id
                )
            except Exception:
                pass
        else:
            await update.message.reply_text(copy_text.with_deco("当前没有正在进行的服务器登记。", 'EMPTY'))
        return
    
    if ' ' in message_text:
        args_text = message_text.split(' ', 1)[1]
    else:
        await update.message.reply_text(
            copy_text.nt_addserver_hint()
        )
        return
    
    try:
        import shlex
        args = shlex.split(args_text)
    except Exception as e:
        await update.message.reply_text(copy_text.nt_arg_parse_error(str(e)))
        return
    
    if len(args) < 5:
        await update.message.reply_text(
            copy_text.nt_addserver_hint()
        )
        return

    name = args[0]
    host = args[1]
    try:
        port = int(args[2])
    except ValueError:
        await update.message.reply_text(copy_text.with_deco("端口号必须是数字，请主人重新输入。", 'ERROR'))
        return
    username = args[3]
    password = args[4]

    new_server = {
        "name": name,
        "host": host,
        "port": port,
        "username": username,
        "password": password
    }

    SERVERS.append(new_server)
    save_config()

    await update.message.reply_text(copy_text.with_deco(f"服务器已登记：{name} ({host}:{port})", 'OK'))

async def rm_server_command(update, context):
    user_id = update.effective_user.id
    if not check_is_admin(user_id, ADMIN_USERS):
        await update.message.reply_text(
            copy_text.nt_not_admin(user_id),
            parse_mode="Markdown"
        )
        return

    message_text = update.message.text.strip()
    
    if message_text == "/rmserver":
        if not SERVERS:
            await update.message.reply_text(copy_text.nt_no_servers_registered())
            return
            
        keyboard = []
        for idx, server_info in enumerate(SERVERS):
            btn = InlineKeyboardButton(
                copy_text.nt_server_label(server_info['name'], server_info['host'], server_info['port']),
                callback_data=f"nt_rmserver_{idx}"
            )
            keyboard.append([btn])
        
        keyboard.append([InlineKeyboardButton(copy_text.BTN_CANCEL, callback_data="nt_rmserver_cancel")])
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        msg = await update.message.reply_text(
            copy_text.nt_pick_remove_target(),
            reply_markup=reply_markup
        )
        
        from .state import user_data
        user_data[user_id] = {
            "operation": "rmserver",
            "chat_id": msg.chat_id,
            "message_id": msg.message_id,
            "prompt_message_id": msg.message_id
        }
        
        try:
            await context.bot.delete_message(
                chat_id=update.message.chat_id,
                message_id=update.message.message_id
            )
        except Exception:
            pass
        
        return
    
    if ' ' in message_text:
        args_text = message_text.split(' ', 1)[1]
        try:
            import shlex
            args = shlex.split(args_text)
        except Exception as e:
            await update.message.reply_text(copy_text.nt_arg_parse_error(str(e)))
            return
    else:
        await update.message.reply_text(copy_text.nt_rmserver_hint())
        return
    
    if len(args) < 1:
        await update.message.reply_text(copy_text.nt_rmserver_hint())
        return

    target_name = args[0]
    found_index = None
    for i, s in enumerate(SERVERS):
        if s['name'] == target_name:
            found_index = i
            break

    if found_index is None:
        await update.message.reply_text(copy_text.with_deco(f"女仆没找到服务器名称：{target_name}，请主人确认输入。", 'ERROR'))
    else:
        removed_server = SERVERS.pop(found_index)
        save_config()
        result_msg = await update.message.reply_text(copy_text.nt_server_removed(removed_server['name'], removed_server['host']))
        
        context.application.create_task(schedule_delete_message(context, update.message.chat_id, result_msg.message_id, delay=5))
        
        try:
            await context.bot.delete_message(
                chat_id=update.message.chat_id,
                message_id=update.message.message_id
            )
        except Exception:
            pass

async def install_nexttrace_command(update, context):
    user_id = update.effective_user.id
    if not check_is_admin(user_id, ADMIN_USERS):
        await update.message.reply_text(
            copy_text.nt_not_admin(user_id),
            parse_mode="Markdown"
        )
        return

    if not SERVERS:
        await update.message.reply_text(
            copy_text.with_deco_head(
                "当前还没有登记任何服务器。\n请先使用 /addserver 登记服务器，女仆才好端出 NextTrace 茶具哦。",
                'EMPTY'))
        return
        
    try:
        await context.bot.delete_message(
            chat_id=update.message.chat_id,
            message_id=update.message.message_id
        )
    except Exception:
        pass
        
    keyboard = []
    for idx, server_info in enumerate(SERVERS):
        btn = InlineKeyboardButton(
            copy_text.nt_server_label(server_info['name'], server_info['host'], server_info['port']),
            callback_data=f"nt_installnexttrace_{idx}"
        )
        keyboard.append([btn])
    
    keyboard.append([InlineKeyboardButton(copy_text.BTN_CANCEL, callback_data="nt_installnexttrace_cancel")])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    msg = await update.message.reply_text(
        copy_text.nt_pick_install_target(),
        reply_markup=reply_markup
    )
    
    from .state import user_data
    user_data[user_id] = {
        "operation": "installnexttrace",
        "chat_id": msg.chat_id,
        "message_id": msg.message_id,
        "prompt_message_id": msg.message_id
    }
