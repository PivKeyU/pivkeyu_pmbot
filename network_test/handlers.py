import ipaddress
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from .state import user_data
from .tasks import do_ping_in_background, do_nexttrace_in_background
from .utils import schedule_delete_message, validate_target, check_is_admin
from .config import SERVERS, save_config, ADMIN_USERS
import asyncio
from utils import copy as copy_text


def _who(user_id) -> str:
    """按称呼规则算这一位读者该被叫作什么（管理员 -> 主人，普通用户 -> 客人）。

    ping / nexttrace 这类流程授权用户也能用，所以读者可能是客人。
    """
    return copy_text.address(check_is_admin(user_id, ADMIN_USERS))

async def callback_handler(update, context):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id

    if user_id not in user_data:
        return False  

    data = query.data
    
    
    if not data.startswith("nt_"):
        return False

    info = user_data[user_id]
    chat_id = info["chat_id"]
    message_id = info["message_id"]

    
    if data.startswith("nt_installnexttrace_"):
        if info.get("operation") != "installnexttrace":
            await context.bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=copy_text.nt_op_not_allowed(_who(user_id))
            )
            return True
            
        if data == "nt_installnexttrace_cancel":
            
            if info.get("from_panel"):
                del user_data[user_id]
                
                return False  
            else:
                await context.bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=message_id,
                    text=copy_text.nt_install_cancelled()
                )
                
                context.application.create_task(
                    schedule_delete_message(context, chat_id, message_id, delay=5)
                )
                del user_data[user_id]
                return True
            
        
        server_idx = int(data.split("_")[2])
        
        if server_idx < 0 or server_idx >= len(SERVERS):
            await context.bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=copy_text.with_deco("服务器索引不对劲，可能列表已更新，请主人重新执行 /install_nexttrace。", 'ERROR')
            )
            
            context.application.create_task(
                schedule_delete_message(context, chat_id, message_id, delay=5)
            )
            del user_data[user_id]
            return True
            
        server_info = SERVERS[server_idx]
        
        
        await context.bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=copy_text.nt_install_running(server_info['name'])
        )
        
        
        from .network import install_nexttrace_on_server
        try:
            result = await asyncio.to_thread(install_nexttrace_on_server, server_info)
            
            
            await context.bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=copy_text.nt_install_result(server_info['name'], result)
            )
        except Exception as e:
            # 安装失败原因属于 SSH 原始输出透传，照原样带给主人，便于自查
            await context.bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=copy_text.nt_install_error(server_info['name'], str(e))
            )
        
        
        context.application.create_task(
            schedule_delete_message(context, chat_id, message_id, delay=15)  
        )
        del user_data[user_id]
        return True

    
    if data.startswith("nt_rmserver_"):
        if info.get("operation") != "rmserver":
            await context.bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=copy_text.nt_op_not_allowed(_who(user_id))
            )
            return True
            
        if data == "nt_rmserver_cancel":
            
            if info.get("from_panel"):
                del user_data[user_id]
                
                return False  
            else:
                
                await context.bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=message_id,
                    text=copy_text.nt_remove_cancelled()
                )
                
                context.application.create_task(
                    schedule_delete_message(context, chat_id, message_id, delay=5)
                )
                del user_data[user_id]
                return True
            
        
        if info.get("confirm_delete"):
            
            server_idx = int(info["server_idx"])
            
            if server_idx < 0 or server_idx >= len(SERVERS):
                await context.bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=message_id,
                    text=copy_text.nt_server_index_stale()
                )
                
                context.application.create_task(
                    schedule_delete_message(context, chat_id, message_id, delay=5)
                )
                del user_data[user_id]
                return True
            
            # 核对所选服务器与选择时记录的一致，防止列表变化后误删其他服务器
            server_info = SERVERS[server_idx]
            if server_info['name'] != info.get('server_name') or server_info['host'] != info.get('server_host'):
                await context.bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=message_id,
                    text=copy_text.nt_server_list_stale()
                )
                context.application.create_task(
                    schedule_delete_message(context, chat_id, message_id, delay=5)
                )
                del user_data[user_id]
                return True
            
            removed_server = SERVERS.pop(server_idx)
            save_config()
            
            await context.bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=copy_text.nt_server_removed(removed_server['name'], removed_server['host'])
            )
            
            context.application.create_task(
                schedule_delete_message(context, chat_id, message_id, delay=5)
            )
            del user_data[user_id]
            return True
            
        
        if data.startswith("nt_rmserver_") and data != "nt_rmserver_cancel" and data != "nt_rmserver_confirm" and data != "nt_rmserver_abort":
            server_idx = int(data.split("_")[2])
            
            if server_idx < 0 or server_idx >= len(SERVERS):
                await context.bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=message_id,
                    text=copy_text.nt_server_index_stale()
                )
                
                context.application.create_task(
                    schedule_delete_message(context, chat_id, message_id, delay=5)
                )
                del user_data[user_id]
                return True
                
            server_info = SERVERS[server_idx]
            
            
            info["server_idx"] = server_idx
            info["server_name"] = server_info['name']
            info["server_host"] = server_info['host']
            
            
            keyboard = [
                [
                    InlineKeyboardButton("确认撤下", callback_data="nt_rmserver_confirm"),
                    InlineKeyboardButton(copy_text.BTN_CANCEL, callback_data="nt_rmserver_abort")
                ]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await context.bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=copy_text.nt_server_remove_confirm(server_info['name'], server_info['host'], server_info['port']),
                reply_markup=reply_markup
            )
            return True
        
        
        if data == "nt_rmserver_confirm":
            server_idx = info.get("server_idx", -1)
            
            if server_idx < 0 or server_idx >= len(SERVERS):
                await context.bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=message_id,
                    text=copy_text.nt_server_index_stale()
                )
                
                context.application.create_task(
                    schedule_delete_message(context, chat_id, message_id, delay=5)
                )
                del user_data[user_id]
                return True
                
            # 核对所选服务器与选择时记录的一致，防止列表变化后误删其他服务器
            server_info = SERVERS[server_idx]
            if server_info['name'] != info.get('server_name') or server_info['host'] != info.get('server_host'):
                await context.bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=message_id,
                    text=copy_text.nt_server_list_stale()
                )
                context.application.create_task(
                    schedule_delete_message(context, chat_id, message_id, delay=5)
                )
                del user_data[user_id]
                return True
            
            info["confirm_delete"] = True
            
            await context.bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=copy_text.with_deco(f"女仆正在替主人撤下服务器：{server_info['name']}... 稍等一下哦。", 'WAIT')
            )
            
            
            removed_server = SERVERS.pop(server_idx)
            save_config()
            
            await context.bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=copy_text.nt_server_removed(removed_server['name'], removed_server['host'])
            )
            
            context.application.create_task(
                schedule_delete_message(context, chat_id, message_id, delay=5)
            )
            del user_data[user_id]
            return True
            
        if data == "nt_rmserver_abort":
            await context.bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=copy_text.nt_remove_cancelled()
            )
            
            context.application.create_task(
                schedule_delete_message(context, chat_id, message_id, delay=5)
            )
            del user_data[user_id]
            return True

    
    if data.startswith("nt_trace_mode_"):
        if info.get("operation") != "nexttrace":
            await context.bot.edit_message_text(chat_id=chat_id, message_id=message_id,
                                              text=copy_text.nt_op_not_allowed(_who(user_id)))
            return True
        
        trace_mode = "icmp" if data == "nt_trace_mode_icmp" else "tcp"
        info["trace_mode"] = trace_mode
        
        
        keyboard = []
        for idx, server_info in enumerate(SERVERS):
            btn = InlineKeyboardButton(server_info['name'], callback_data=f"nt_server_{idx}")
            keyboard.append([btn])
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await context.bot.edit_message_text(
            chat_id=chat_id, 
            message_id=message_id,
            text=copy_text.with_deco(
                f"{_who(user_id)}选了{('ICMP' if trace_mode == 'icmp' else 'TCP')}模式追踪呢，接下来要挑哪台服务器？", 'ASK'),
            reply_markup=reply_markup
        )
        return True

    if data.startswith("nt_server_"):
        idx = int(data.split("_")[2])
        if idx < 0 or idx >= len(SERVERS):
            await context.bot.edit_message_text(
                copy_text.with_deco(f"这个服务器编号不对劲哦，{_who(user_id)}。", 'ERROR'),
                chat_id=chat_id, message_id=message_id)
            return True

        server_info = SERVERS[idx]
        info["server_info"] = server_info
        if info.get("operation") == "ping":
            mode = info["mode"]
            if mode == "cmd":
                await context.bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=message_id,
                    text=copy_text.nt_ping_running()
                )
                context.application.create_task(
                    do_ping_in_background(context, chat_id, server_info, info["target"], info["count"], user_id, message_id, _who(user_id))
                )
            elif mode == "interactive":
                await context.bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=message_id,
                    text=copy_text.with_deco(
                        f"{_who(user_id)}选了 {server_info['name']} 呢。接下来把目标 IP 或域名发给女仆吧（例如：8.8.8.8 或 google.com）。", 'ASK')
                )
        elif info.get("operation") == "nexttrace":
            mode = info["mode"]
            if mode == "cmd":
                try:
                    ipaddress.ip_address(info["target"])
                    trace_mode = info.get("trace_mode", "icmp")  
                    await context.bot.edit_message_text(
                        chat_id=chat_id, message_id=message_id,
                        text=copy_text.nt_trace_running(_who(user_id), server_info['name'], info['target'], trace_mode)
                    )
                    context.application.create_task(
                        do_nexttrace_in_background(context, chat_id, server_info, info["target"], "direct", user_id, message_id, trace_mode, _who(user_id))
                    )
                except ValueError:
                    keyboard = [
                        [
                            InlineKeyboardButton(copy_text.BTN_NT_IPV4, callback_data="nt_iptype_ipv4"),
                            InlineKeyboardButton(copy_text.BTN_NT_IPV6, callback_data="nt_iptype_ipv6")
                        ]
                    ]
                    reply_markup = InlineKeyboardMarkup(keyboard)
                    await context.bot.edit_message_text(
                        chat_id=chat_id, message_id=message_id,
                        text=copy_text.with_deco_head(
                            f"{_who(user_id)}选了 {server_info['name']} 呢。\n目标： {info['target']}\n这次要用哪一套 IP 协议呢？", 'ASK'),
                        reply_markup=reply_markup
                    )
            elif mode == "interactive":
                try:
                    ipaddress.ip_address(info["target"])
                    trace_mode = info.get("trace_mode", "icmp")  
                    await context.bot.edit_message_text(
                        chat_id=chat_id, message_id=message_id,
                        text=copy_text.nt_trace_running(_who(user_id), server_info['name'], info['target'], trace_mode)
                    )
                    context.application.create_task(
                        do_nexttrace_in_background(context, chat_id, server_info, info["target"], "direct", user_id, message_id, trace_mode, _who(user_id))
                    )
                except ValueError:
                    await context.bot.edit_message_text(
                        chat_id=chat_id,
                        message_id=message_id,
                        text=copy_text.with_deco(
                            f"{_who(user_id)}选了 {server_info['name']} 呢。接下来把目标 IP 或域名发给女仆吧。", 'ASK')
                    )
        return True
    elif data.startswith("nt_count_"):
        if info.get("operation") != "ping":
            await context.bot.edit_message_text(chat_id=chat_id, message_id=message_id,
                                                  text=copy_text.nt_op_not_allowed(_who(user_id)))
            return True

        count = int(data.split("_")[2])
        info["count"] = count
        if not info.get("server_info") or not info.get("target"):
            await context.bot.edit_message_text(chat_id=chat_id, message_id=message_id,
                                                  text=copy_text.nt_ping_target_missing(_who(user_id)))
            return True

        await context.bot.edit_message_text(chat_id=chat_id, message_id=message_id,
                                            text=copy_text.nt_ping_running())
        context.application.create_task(
            do_ping_in_background(context, chat_id, info["server_info"], info["target"], count, user_id, message_id, _who(user_id))
        )
        return True
    elif data.startswith("nt_iptype_"):
        if info.get("operation") != "nexttrace":
            await context.bot.edit_message_text(chat_id=chat_id, message_id=message_id,
                                                  text=copy_text.nt_op_not_allowed(_who(user_id)))
            return True
        ip_type = "IPv4" if data == "nt_iptype_ipv4" else "IPv6"
        info["ip_type"] = ip_type
        trace_mode = info.get("trace_mode", "icmp")  
        await context.bot.edit_message_text(
            chat_id=chat_id, message_id=message_id,
            text=copy_text.with_deco(
                f"女仆收到请求啦，正在后台执行{('ICMP' if trace_mode == 'icmp' else 'TCP')}模式路由追踪，请稍候...", 'WAIT')
        )
        context.application.create_task(
            do_nexttrace_in_background(context, chat_id, info["server_info"], info["target"], ip_type, user_id, message_id, trace_mode, _who(user_id))
        )
        return True
    
    return False  

async def handle_message(update, context):
    user_id = update.effective_user.id
    if user_id not in user_data:
        return False  
    
    info = user_data[user_id]
    
    
    if info.get("operation") == "addserver":
        text = update.message.text.strip()
        
        
        if text.lower() == "/cancel":
            
            if info.get("prompt_message_id"):
                try:
                    await context.bot.delete_message(
                        chat_id=update.effective_chat.id,
                        message_id=info["prompt_message_id"]
                    )
                except Exception:
                    pass  
                    
            from_panel = info.get("from_panel", False)
            del user_data[user_id]
            
            if from_panel:
                
                keyboard = InlineKeyboardMarkup([
                    [InlineKeyboardButton(copy_text.BTN_BACK_NETWORK_TEST, callback_data="panel_network_test")]
                ])
                await context.bot.send_message(
                    chat_id=update.effective_chat.id,
                    text=copy_text.nt_server_reg_cancelled(),
                    reply_markup=keyboard
                )
            else:
                await context.bot.send_message(
                    chat_id=update.effective_chat.id,
                    text=copy_text.nt_server_reg_cancelled()
                )
            return True
            
        step = info.get("step", 1)
        server_data = info.get("server_data", {})
        
        
        context.application.create_task(schedule_delete_message(context, update.message.chat_id, update.message.message_id, delay=2))
        
        
        if info.get("prompt_message_id"):
            try:
                await context.bot.delete_message(
                    chat_id=update.effective_chat.id,
                    message_id=info["prompt_message_id"]
                )
            except Exception:
                pass  
        
        if step == 1:  
            server_data["name"] = text
            from_panel = info.get("from_panel", False)
            reply_markup = None
            if from_panel:
                reply_markup = InlineKeyboardMarkup([
                    [InlineKeyboardButton(copy_text.BTN_BACK_NETWORK_TEST, callback_data="panel_network_test")]
                ])
            msg = await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text=copy_text.with_deco_head(
                    f"步骤 2/5: 服务器名称已经记下啦（\"{text}\"）。\n\n请把服务器 IP 地址告诉女仆哦：\n\n输入 /cancel 可随时取消", 'OK'),
                reply_markup=reply_markup
            )
            info["step"] = 2
            info["server_data"] = server_data
            info["prompt_message_id"] = msg.message_id  
            
        elif step == 2:  
            server_data["host"] = text
            from_panel = info.get("from_panel", False)
            reply_markup = None
            if from_panel:
                reply_markup = InlineKeyboardMarkup([
                    [InlineKeyboardButton(copy_text.BTN_BACK_NETWORK_TEST, callback_data="panel_network_test")]
                ])
            msg = await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text=copy_text.with_deco_head(
                    f"步骤 3/5: 服务器 IP 已经记下啦（\"{text}\"）。\n\n请把 SSH 端口号告诉女仆哦（通常为 22）：\n\n输入 /cancel 可随时取消", 'OK'),
                reply_markup=reply_markup
            )
            info["step"] = 3
            info["server_data"] = server_data
            info["prompt_message_id"] = msg.message_id  
            
        elif step == 3:  
            try:
                port = int(text)
                server_data["port"] = port
                from_panel = info.get("from_panel", False)
                reply_markup = None
                if from_panel:
                    reply_markup = InlineKeyboardMarkup([
                        [InlineKeyboardButton(copy_text.BTN_BACK_NETWORK_TEST, callback_data="panel_network_test")]
                    ])
                msg = await context.bot.send_message(
                    chat_id=update.effective_chat.id,
                    text=copy_text.with_deco_head(
                        f"步骤 4/5: 端口号已经记下啦（{port}）。\n\n请把 SSH 用户名告诉女仆哦：\n\n输入 /cancel 可随时取消", 'OK'),
                    reply_markup=reply_markup
                )
                info["step"] = 4
                info["server_data"] = server_data
                info["prompt_message_id"] = msg.message_id  
            except ValueError:
                from_panel = info.get("from_panel", False)
                reply_markup = None
                if from_panel:
                    reply_markup = InlineKeyboardMarkup([
                        [InlineKeyboardButton(copy_text.BTN_BACK_NETWORK_TEST, callback_data="panel_network_test")]
                    ])
                msg = await context.bot.send_message(
                    chat_id=update.effective_chat.id,
                    text=copy_text.with_deco_head(
                        "端口号得写成数字哦，主人重新输入一个：\n\n输入 /cancel 可随时取消", 'ERROR'),
                    reply_markup=reply_markup
                )
                info["prompt_message_id"] = msg.message_id  
                
        elif step == 4:  
            server_data["username"] = text
            from_panel = info.get("from_panel", False)
            reply_markup = None
            if from_panel:
                reply_markup = InlineKeyboardMarkup([
                    [InlineKeyboardButton(copy_text.BTN_BACK_NETWORK_TEST, callback_data="panel_network_test")]
                ])
            msg = await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text=copy_text.with_deco_head(
                    f"步骤 5/5: 用户名已经记下啦（\"{text}\"）。\n\n请把 SSH 密码告诉女仆哦：\n\n输入 /cancel 可随时取消", 'OK'),
                reply_markup=reply_markup
            )
            info["step"] = 5
            info["server_data"] = server_data
            info["prompt_message_id"] = msg.message_id  
            
        elif step == 5:  
            server_data["password"] = text
            
            
            summary = (
                f"主人，请确认以下服务器信息：\n\n"
                f"名称: {server_data['name']}\n"
                f"主机: {server_data['host']}\n"
                f"端口: {server_data['port']}\n"
                f"用户名: {server_data['username']}\n"
                f"密码: {'*' * len(server_data['password'])}\n\n"
                f"确认登记吗？（输入 yes 确认，输入其他内容取消）"
            )
            
            from_panel = info.get("from_panel", False)
            reply_markup = None
            if from_panel:
                reply_markup = InlineKeyboardMarkup([
                    [InlineKeyboardButton(copy_text.BTN_BACK_NETWORK_TEST, callback_data="panel_network_test")]
                ])
            
            msg = await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text=summary,
                reply_markup=reply_markup
            )
            
            info["step"] = 6
            info["server_data"] = server_data
            info["prompt_message_id"] = msg.message_id  
            
        elif step == 6:  
            
            if info.get("prompt_message_id"):
                try:
                    await context.bot.delete_message(
                        chat_id=update.effective_chat.id,
                        message_id=info["prompt_message_id"]
                    )
                except Exception:
                    pass
                    
            if text.lower() == "yes":
                
                SERVERS.append(server_data)
                save_config()
                
                await context.bot.send_message(
                    chat_id=update.effective_chat.id,
                    text=copy_text.with_deco(f"服务器登记成功啦！\"{server_data['name']}\" 已经收进女仆的系统。", 'OK')
                )
            else:
                await context.bot.send_message(
                    chat_id=update.effective_chat.id,
                    text=copy_text.nt_server_reg_cancelled()
                )
            
            
            del user_data[user_id]
        
        return True
    
    
    if info["mode"] != "interactive":
        if info.get("operation") == "ping":
            await update.message.reply_text(
                copy_text.with_deco("命令式模式不用再输入 IP 啦，想重新测试就用 /ping 吩咐女仆。", 'ASK'))
        elif info.get("operation") == "nexttrace":
            await update.message.reply_text(
                copy_text.with_deco("命令式模式不用再输入 IP 啦，想重新测试就用 /nexttrace 吩咐女仆。", 'ASK'))
        return True

    if not info.get("target"):
        target = update.message.text.strip()
        # 校验目标地址，防止 SSH 命令注入
        ok, err = validate_target(target)
        if not ok:
            await update.message.reply_text(err)
            return True
        info["target"] = target

        context.application.create_task(schedule_delete_message(context, update.message.chat_id, update.message.message_id, delay=5))

        if info.get("operation") == "ping":
            keyboard = [
                [
                    InlineKeyboardButton("Ping 5次", callback_data="nt_count_5"),
                    InlineKeyboardButton("Ping 10次", callback_data="nt_count_10"),
                    InlineKeyboardButton("Ping 30次", callback_data="nt_count_30")
                ]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await context.bot.edit_message_text(
                chat_id=info["chat_id"],
                message_id=info["message_id"],
                text=copy_text.with_deco(f"{_who(user_id)}，这次要 Ping 几次呢？", 'ASK'),
                reply_markup=reply_markup
            )
        elif info.get("operation") == "nexttrace":
            try:
                ipaddress.ip_address(target)
                trace_mode = info.get("trace_mode", "icmp")  
                await context.bot.edit_message_text(
                    chat_id=info["chat_id"],
                    message_id=info["message_id"],
                    text=copy_text.with_deco(
                        f"目标：{target} 是 IP 地址哦，女仆正在后台执行{('ICMP' if trace_mode == 'icmp' else 'TCP')}模式路由追踪，请稍候...", 'WAIT')
                )
                context.application.create_task(
                    do_nexttrace_in_background(context, info["chat_id"], info["server_info"], target, "direct", user_id, info["message_id"], trace_mode, _who(user_id))
                )
            except ValueError:
                keyboard = [
                    [
                        InlineKeyboardButton(copy_text.BTN_NT_IPV4, callback_data="nt_iptype_ipv4"),
                        InlineKeyboardButton(copy_text.BTN_NT_IPV6, callback_data="nt_iptype_ipv6")
                    ]
                ]
                reply_markup = InlineKeyboardMarkup(keyboard)
                await context.bot.edit_message_text(
                    chat_id=info["chat_id"],
                    message_id=info["message_id"],
                    text=copy_text.with_deco(f"{_who(user_id)}，这次要用哪一套 IP 协议呢？", 'ASK'),
                    reply_markup=reply_markup
                )
    else:
        await update.message.reply_text(copy_text.nt_ip_already_input(_who(user_id)))
    
    return True
