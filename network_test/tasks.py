import asyncio
from .network import ping_on_server, nexttrace_on_server, format_nexttrace_result
from .utils import progress_spinner
from .state import user_data
import logging

async def do_ping_in_background(context, chat_id: int, server_info: dict, target: str, ping_count: int, user_id: int, message_id: int):
    done_event = asyncio.Event()
    base_text = (
        "<b>【女仆 Ping 测试结果】</b>\n\n"
        f"测试节点: {server_info['name']}\n"
        f"目标: {target}\n"
        f"Ping 次数: {ping_count}\n\n女仆正在执行 Ping，请稍候"
    )
    spinner_task = asyncio.create_task(progress_spinner(context, chat_id, message_id, base_text, done_event))
    
    # 无论执行是否抛异常，都要结束 spinner，避免协程泄漏
    try:
        ping_raw_result = await asyncio.to_thread(ping_on_server, server_info, target, ping_count)
    finally:
        done_event.set()
        await spinner_task

    
    retry_info = ""
    if "操作失败，女仆已重试" in ping_raw_result:
        
        logging.warning(f"Ping 测试重试后完成: {server_info['name']} -> {target}")
        retry_info = "<i>小提示: 测试途中遇到连接问题，女仆已经尽力重试。</i>\n\n"
    
    final_text = (
        "<b>【女仆 Ping 测试结果】</b>\n\n"
        f"测试节点: {server_info['name']}\n"
        f"目标: {target}\n"
        f"Ping 次数: {ping_count}\n\n"
        f"{retry_info}"
        f"{ping_raw_result}"
    )
    await context.bot.edit_message_text(
        chat_id=chat_id,
        message_id=message_id,
        text=final_text,
        parse_mode="HTML"
    )
    # 仅当会话仍是本次任务对应的会话时才清理，避免误删用户新发起的会话
    current = user_data.get(user_id)
    if current is not None and current.get("message_id") == message_id:
        del user_data[user_id]

async def do_nexttrace_in_background(context, chat_id: int, server_info: dict, target: str, ip_type: str, user_id: int, message_id: int, trace_mode: str = "icmp"):
    done_event = asyncio.Event()
    trace_mode_text = "TCP 模式" if trace_mode == "tcp" else "ICMP 模式"
    base_text = (
        "<b>【女仆 NextTrace 路由追踪结果】</b>\n\n"
        f"测试节点: {server_info['name']}\n"
        f"目标: {target}\n"
        f"执行模式: {'直接执行' if ip_type=='direct' else ip_type} ({trace_mode_text})\n\n女仆正在执行路由追踪，请稍候"
    )
    spinner_task = asyncio.create_task(progress_spinner(context, chat_id, message_id, base_text, done_event))
    
    # 无论执行是否抛异常，都要结束 spinner，避免协程泄漏
    try:
        result = await asyncio.to_thread(nexttrace_on_server, server_info, target, ip_type, trace_mode)
    finally:
        done_event.set()
        await spinner_task

    
    retry_info = ""
    if "操作失败，女仆已重试" in result:
        
        logging.warning(f"NextTrace 测试重试后完成: {server_info['name']} -> {target}")
        retry_info = "<i>小提示: 测试途中遇到连接问题，女仆已经尽力重试。</i>\n\n"
        # 重试彻底失败：透传真实错误文本，绝不伪装成成功
        final_text = (
            "<b>【女仆 NextTrace 路由追踪结果】</b>\n\n"
            f"测试节点: {server_info['name']}\n"
            f"目标: {target}\n"
            f"执行模式: {'直接执行' if ip_type=='direct' else ip_type} ({trace_mode_text})\n\n"
            f"{retry_info}"
            f"{result}"
        )
    else:
        final_text = format_nexttrace_result(result, server_info['name'], target, ip_type, trace_mode)
    
    await context.bot.edit_message_text(
        chat_id=chat_id,
        message_id=message_id,
        text=final_text,
        parse_mode="HTML"
    )
    # 仅当会话仍是本次任务对应的会话时才清理，避免误删用户新发起的会话
    current = user_data.get(user_id)
    if current is not None and current.get("message_id") == message_id:
        del user_data[user_id]
