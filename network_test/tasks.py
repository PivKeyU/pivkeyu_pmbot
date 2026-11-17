import asyncio
import html
import logging

from .network import ping_on_server, nexttrace_on_server, format_nexttrace_result
from .utils import progress_spinner
from .state import user_data

_TELEGRAM_HTML_LIMIT = 4000


def _cleanup_user_state(user_id: int, message_id: int):
    # 仅当会话仍是本次任务对应的会话时才清理，避免误删用户新发起的会话
    current = user_data.get(user_id)
    if current is not None and current.get("message_id") == message_id:
        user_data.pop(user_id, None)


def _build_truncated_html(prefix: str, raw_body: str) -> str:
    safe_body = html.escape(raw_body)
    budget = max(120, _TELEGRAM_HTML_LIMIT - len(prefix))
    if len(safe_body) > budget:
        safe_body = safe_body[:budget - 20] + "\n... (输出过长已截断)"
    return prefix + safe_body


async def do_ping_in_background(context, chat_id: int, server_info: dict, target: str, ping_count: int, user_id: int, message_id: int, display_name: str = None):
    done_event = asyncio.Event()
    safe_server = html.escape(str(server_info.get('name', '')))
    safe_target = html.escape(str(target))
    base_text = (
        "<b>【女仆 Ping 测试结果】</b>\n\n"
        f"测试节点: {safe_server}\n"
        f"目标: {safe_target}\n"
        f"Ping 次数: {ping_count}\n\n女仆正在执行 Ping，请稍候"
    )
    spinner_task = asyncio.create_task(progress_spinner(context, chat_id, message_id, base_text, done_event))

    try:
        try:
            ping_raw_result = await asyncio.to_thread(ping_on_server, server_info, target, ping_count, display_name)
        finally:
            done_event.set()
            await spinner_task

        retry_info = ""
        if "操作失败，女仆已重试" in ping_raw_result:
            logging.warning(f"Ping 测试重试后完成: {server_info.get('name')} -> {target}")
            retry_info = "<i>小提示: 测试途中遇到连接问题，女仆已经尽力重试。</i>\n\n"

        prefix = (
            "<b>【女仆 Ping 测试结果】</b>\n\n"
            f"测试节点: {safe_server}\n"
            f"目标: {safe_target}\n"
            f"Ping 次数: {ping_count}\n\n"
            f"{retry_info}"
        )
        final_text = _build_truncated_html(prefix, ping_raw_result)
        await context.bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=final_text,
            parse_mode="HTML"
        )
    except Exception as exc:
        logging.error(f"do_ping_in_background 异常: {exc}", exc_info=True)
        try:
            await context.bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=f"Ping 执行或回报失败: {html.escape(str(exc))[:300]}"
            )
        except Exception:
            pass
    finally:
        _cleanup_user_state(user_id, message_id)


async def do_nexttrace_in_background(context, chat_id: int, server_info: dict, target: str, ip_type: str, user_id: int, message_id: int, trace_mode: str = "icmp", display_name: str = None):
    done_event = asyncio.Event()
    mode_norm = (trace_mode or "icmp").strip().lower()
    trace_mode_text = "TCP 模式" if mode_norm == "tcp" else ("UDP 模式" if mode_norm == "udp" else "ICMP 模式")
    safe_server = html.escape(str(server_info.get('name', '')))
    safe_target = html.escape(str(target))
    mode_label = "直接执行" if str(ip_type).strip().lower() == 'direct' else html.escape(str(ip_type))
    base_text = (
        "<b>【女仆 NextTrace 路由追踪结果】</b>\n\n"
        f"测试节点: {safe_server}\n"
        f"目标: {safe_target}\n"
        f"执行模式: {mode_label} ({trace_mode_text})\n\n女仆正在执行路由追踪，请稍候"
    )
    spinner_task = asyncio.create_task(progress_spinner(context, chat_id, message_id, base_text, done_event))

    try:
        try:
            result = await asyncio.to_thread(nexttrace_on_server, server_info, target, ip_type, trace_mode, display_name)
        finally:
            done_event.set()
            await spinner_task

        if "操作失败，女仆已重试" in result or result.startswith(("SSH 认证", "命令执行出错", "路由追踪服务暂时不可用", "目标地址")):
            if "操作失败，女仆已重试" in result:
                logging.warning(f"NextTrace 测试重试后完成: {server_info.get('name')} -> {target}")
                retry_info = "<i>小提示: 测试途中遇到连接问题，女仆已经尽力重试。</i>\n\n"
            else:
                retry_info = ""
            prefix = (
                "<b>【女仆 NextTrace 路由追踪结果】</b>\n\n"
                f"测试节点: {safe_server}\n"
                f"目标: {safe_target}\n"
                f"执行模式: {mode_label} ({trace_mode_text})\n\n"
                f"{retry_info}"
            )
            final_text = _build_truncated_html(prefix, result)
        else:
            final_text = format_nexttrace_result(result, server_info.get('name', ''), target, ip_type, trace_mode)

        await context.bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=final_text,
            parse_mode="HTML"
        )
    except Exception as exc:
        logging.error(f"do_nexttrace_in_background 异常: {exc}", exc_info=True)
        try:
            await context.bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=f"NextTrace 执行或回报失败: {html.escape(str(exc))[:300]}"
            )
        except Exception:
            pass
    finally:
        _cleanup_user_state(user_id, message_id)
