import ipaddress
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from .state import user_data
from .tasks import do_ping_in_background, do_nexttrace_in_background
from .utils import schedule_delete_message
from .config import SERVERS, save_config
import asyncio

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
                text="当前操作不能安装 NextTrace 哦。"
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
                    text="NextTrace 安装已取消。"
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
                text="服务器索引不对劲，可能列表已更新，请主人重新执行 /install_nexttrace。"
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
            text=f"女仆正在服务器 {server_info['name']} 上安装 NextTrace...\n请主人稍等，可能需要一点时间。"
        )
        
        
        from .network import install_nexttrace_on_server
        try:
            result = await asyncio.to_thread(install_nexttrace_on_server, server_info)
            
            
            await context.bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=f"服务器 {server_info['name']} 的 NextTrace 安装结果：\n\n{result}"
            )
        except Exception as e:
            await context.bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=f"服务器 {server_info['name']} 安装 NextTrace 时出错：\n\n{str(e)}"
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
                text="当前操作不能撤下服务器哦。"
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
                    text="撤下服务器操作已取消。"
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
                    text="服务器索引不对劲，可能列表已更新，请主人重新执行 /rmserver。"
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
                text=f"服务器已撤下：{removed_server['name']} (host={removed_server['host']})"
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
                    text="服务器索引不对劲，可能列表已更新，请主人重新执行 /rmserver。"
                )
                
                context.application.create_task(
                    schedule_delete_message(context, chat_id, message_id, delay=5)
                )
                del user_data[user_id]
                return True
                
            server_info = SERVERS[server_idx]
            
            
            info["server_idx"] = server_idx
            
            
            keyboard = [
                [
                    InlineKeyboardButton("确认撤下", callback_data="nt_rmserver_confirm"),
                    InlineKeyboardButton("取消", callback_data="nt_rmserver_abort")
                ]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await context.bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=f"主人确定要撤下这台服务器吗？\n\n名称: {server_info['name']}\nHost: {server_info['host']}:{server_info['port']}\n\n这个操作不可撤销哦！",
                reply_markup=reply_markup
            )
            return True
        
        
        if data == "nt_rmserver_confirm":
            info["confirm_delete"] = True
            
            server_idx = info["server_idx"]
            server_info = SERVERS[server_idx]
            
            await context.bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=f"女仆正在撤下服务器：{server_info['name']}..."
            )
            
            
            removed_server = SERVERS.pop(server_idx)
            save_config()
            
            await context.bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=f"服务器已撤下：{removed_server['name']} (host={removed_server['host']})"
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
                text="撤下服务器操作已取消。"
            )
            
            context.application.create_task(
                schedule_delete_message(context, chat_id, message_id, delay=5)
            )
            del user_data[user_id]
            return True

    
    if data.startswith("nt_trace_mode_"):
        if info.get("operation") != "nexttrace":
            await context.bot.edit_message_text(chat_id=chat_id, message_id=message_id,
                                              text="当前操作不能选择追踪模式哦。")
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
            text=f"主人选择了{('ICMP' if trace_mode == 'icmp' else 'TCP')}模式追踪，请选择服务器：",
            reply_markup=reply_markup
        )
        return True

    if data.startswith("nt_server_"):
        idx = int(data.split("_")[2])
        if idx < 0 or idx >= len(SERVERS):
            await context.bot.edit_message_text("服务器下标不对劲，主人。", chat_id=chat_id, message_id=message_id)
            return True

        server_info = SERVERS[idx]
        info["server_info"] = server_info
        if info.get("operation") == "ping":
            mode = info["mode"]
            if mode == "cmd":
                await context.bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=message_id,
                    text="女仆收到请求啦，正在后台执行 Ping，请稍候..."
                )
                context.application.create_task(
                    do_ping_in_background(context, chat_id, server_info, info["target"], info["count"], user_id)
                )
            elif mode == "interactive":
                await context.bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=message_id,
                    text=f"主人选择了 {server_info['name']}。\n请发送目标 IP 或域名（例如：8.8.8.8 或 google.com）。"
                )
        elif info.get("operation") == "nexttrace":
            mode = info["mode"]
            if mode == "cmd":
                try:
                    ipaddress.ip_address(info["target"])
                    trace_mode = info.get("trace_mode", "icmp")  
                    await context.bot.edit_message_text(
                        chat_id=chat_id, message_id=message_id,
                        text=f"主人选择了 {server_info['name']}。\n目标：{info['target']} 是 IP 地址，女仆正在后台执行{('ICMP' if trace_mode == 'icmp' else 'TCP')}模式路由追踪，请稍候..."
                    )
                    context.application.create_task(
                        do_nexttrace_in_background(context, chat_id, server_info, info["target"], "direct", user_id, trace_mode)
                    )
                except ValueError:
                    keyboard = [
                        [
                            InlineKeyboardButton("IPv4", callback_data="nt_iptype_ipv4"),
                            InlineKeyboardButton("IPv6", callback_data="nt_iptype_ipv6")
                        ]
                    ]
                    reply_markup = InlineKeyboardMarkup(keyboard)
                    await context.bot.edit_message_text(
                        chat_id=chat_id, message_id=message_id,
                        text=f"主人选择了 {server_info['name']}。\n目标： {info['target']}\n主人，请选择 IP 协议类型：",
                        reply_markup=reply_markup
                    )
            elif mode == "interactive":
                try:
                    ipaddress.ip_address(info["target"])
                    trace_mode = info.get("trace_mode", "icmp")  
                    await context.bot.edit_message_text(
                        chat_id=chat_id, message_id=message_id,
                        text=f"主人选择了 {server_info['name']}。\n目标：{info['target']} 是 IP 地址，女仆正在后台执行{('ICMP' if trace_mode == 'icmp' else 'TCP')}模式路由追踪，请稍候..."
                    )
                    context.application.create_task(
                        do_nexttrace_in_background(context, chat_id, server_info, info["target"], "direct", user_id, trace_mode)
                    )
                except ValueError:
                    await context.bot.edit_message_text(
                        chat_id=chat_id,
                        message_id=message_id,
                        text=f"主人选择了 {server_info['name']}。\n请发送目标 IP 或域名。"
                    )
        return True
    elif data.startswith("nt_count_"):
        if info.get("operation") != "ping":
            await context.bot.edit_message_text(chat_id=chat_id, message_id=message_id,
                                                  text="当前操作不能选择 Ping 次数哦。")
            return True

        count = int(data.split("_")[2])
        info["count"] = count
        if not info.get("server_info") or not info.get("target"):
            await context.bot.edit_message_text(chat_id=chat_id, message_id=message_id,
                                                  text="服务器或目标 IP 信息不完整，请主人重新开始 /ping 流程。")
            return True

        await context.bot.edit_message_text(chat_id=chat_id, message_id=message_id,
                                            text="女仆收到请求啦，正在后台执行 Ping，请稍候...")
        context.application.create_task(
            do_ping_in_background(context, chat_id, info["server_info"], info["target"], count, user_id)
        )
        return True
    elif data.startswith("nt_iptype_"):
        if info.get("operation") != "nexttrace":
            await context.bot.edit_message_text(chat_id=chat_id, message_id=message_id,
                                                  text="当前操作不能选择 IP 协议类型哦。")
            return True
        ip_type = "IPv4" if data == "nt_iptype_ipv4" else "IPv6"
        info["ip_type"] = ip_type
        trace_mode = info.get("trace_mode", "icmp")  
        await context.bot.edit_message_text(
            chat_id=chat_id, message_id=message_id,
            text=f"女仆收到请求啦，正在后台执行{('ICMP' if trace_mode == 'icmp' else 'TCP')}模式路由追踪，请稍候..."
        )
        context.application.create_task(
            do_nexttrace_in_background(context, chat_id, info["server_info"], info["target"], ip_type, user_id, trace_mode)
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
                    [InlineKeyboardButton("回网络测试茶具", callback_data="panel_network_test")]
                ])
                await context.bot.send_message(
                    chat_id=update.effective_chat.id,
                    text="服务器登记已取消。",
                    reply_markup=keyboard
                )
            else:
                await context.bot.send_message(
                    chat_id=update.effective_chat.id,
                    text="服务器登记已取消。"
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
                    [InlineKeyboardButton("回网络测试茶具", callback_data="panel_network_test")]
                ])
            msg = await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text=f"步骤 2/5: 服务器名称已登记为 \"{text}\"。\n\n请告诉女仆服务器 IP 地址：\n\n输入 /cancel 可随时取消",
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
                    [InlineKeyboardButton("回网络测试茶具", callback_data="panel_network_test")]
                ])
            msg = await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text=f"步骤 3/5: 服务器 IP 已登记为 \"{text}\"。\n\n请告诉女仆 SSH 端口号（通常为 22）：\n\n输入 /cancel 可随时取消",
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
                        [InlineKeyboardButton("回网络测试茶具", callback_data="panel_network_test")]
                    ])
                msg = await context.bot.send_message(
                    chat_id=update.effective_chat.id,
                    text=f"步骤 4/5: 端口号已登记为 {port}。\n\n请告诉女仆 SSH 用户名：\n\n输入 /cancel 可随时取消",
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
                        [InlineKeyboardButton("回网络测试茶具", callback_data="panel_network_test")]
                    ])
                msg = await context.bot.send_message(
                    chat_id=update.effective_chat.id,
                    text="端口号必须是数字，请主人重新输入端口号：\n\n输入 /cancel 可随时取消",
                    reply_markup=reply_markup
                )
                info["prompt_message_id"] = msg.message_id  
                
        elif step == 4:  
            server_data["username"] = text
            from_panel = info.get("from_panel", False)
            reply_markup = None
            if from_panel:
                reply_markup = InlineKeyboardMarkup([
                    [InlineKeyboardButton("回网络测试茶具", callback_data="panel_network_test")]
                ])
            msg = await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text=f"步骤 5/5: 用户名已登记为 \"{text}\"。\n\n请告诉女仆 SSH 密码：\n\n输入 /cancel 可随时取消",
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
                    [InlineKeyboardButton("回网络测试茶具", callback_data="panel_network_test")]
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
                    text=f"服务器登记成功！服务器 \"{server_data['name']}\" 已收进系统。"
                )
            else:
                await context.bot.send_message(
                    chat_id=update.effective_chat.id,
                    text="服务器登记已取消。"
                )
            
            
            del user_data[user_id]
        
        return True
    
    
    if info["mode"] != "interactive":
        if info.get("operation") == "ping":
            await update.message.reply_text("命令式模式不需要再输入 IP，如需重新测试，请主人使用 /ping。")
        elif info.get("operation") == "nexttrace":
            await update.message.reply_text("命令式模式不需要再输入 IP，如需重新测试，请主人使用 /nexttrace。")
        return True

    if not info.get("target"):
        target = update.message.text.strip()
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
                text="主人，请选择要 Ping 的次数：",
                reply_markup=reply_markup
            )
        elif info.get("operation") == "nexttrace":
            try:
                ipaddress.ip_address(target)
                trace_mode = info.get("trace_mode", "icmp")  
                await context.bot.edit_message_text(
                    chat_id=info["chat_id"],
                    message_id=info["message_id"],
                    text=f"目标：{target} 是 IP 地址，女仆正在后台执行{('ICMP' if trace_mode == 'icmp' else 'TCP')}模式路由追踪，请稍候..."
                )
                context.application.create_task(
                    do_nexttrace_in_background(context, info["chat_id"], info["server_info"], target, "direct", user_id, trace_mode)
                )
            except ValueError:
                keyboard = [
                    [
                        InlineKeyboardButton("IPv4", callback_data="nt_iptype_ipv4"),
                        InlineKeyboardButton("IPv6", callback_data="nt_iptype_ipv6")
                    ]
                ]
                reply_markup = InlineKeyboardMarkup(keyboard)
                await context.bot.edit_message_text(
                    chat_id=info["chat_id"],
                    message_id=info["message_id"],
                    text="主人，请选择 IP 协议类型：",
                    reply_markup=reply_markup
                )
    else:
        await update.message.reply_text("主人已经输入过目标 IP，如需重新测试，请使用对应命令。")
    
    return True
