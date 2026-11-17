import asyncio
import time
import re
import ipaddress
import logging

import paramiko
from telegram.error import BadRequest, RetryAfter

from .config import get_admin_users


def check_authorization(user_id: int, authorized_users: list, admin_users: list = None) -> bool:
    admins = get_admin_users() if admin_users is None else admin_users
    if admins and user_id in admins:
        return True

    return user_id in authorized_users


def check_is_admin(user_id: int, admin_users: list = None) -> bool:
    admins = get_admin_users() if admin_users is None else admin_users
    return user_id in admins


def validate_port(raw_port) -> tuple:
    """校验 SSH 端口号必须在 1..65535 之间，返回 (ok, port_int_or_None, error_msg)。"""
    try:
        port = int(str(raw_port).strip())
    except (TypeError, ValueError):
        return False, None, "端口号得写成数字哦（1~65535）。"
    if port < 1 or port > 65535:
        return False, None, "端口号范围必须在 1 到 65535 之间哦。"
    return True, port, ""


def validate_target(target: str, display_name: str = None, allow_private: bool = True) -> tuple:
    """校验目标地址是否为合法的 IP 或域名（防 SSH 命令注入）。

    允许：IPv4 / IPv6（不含端口）或严格格式的域名（长度 <= 253）。
    当 ``allow_private=False`` 时，额外拒绝内网/回环/链路本地 IP 与本地域名（避免非管理员探测节点内网）。
    """
    who = display_name or '这一位'
    if not target or not target.strip():
        return False, "目标地址不能为空哦，{who}。".format(who=who)
    cleaned = target.strip()
    if len(cleaned) > 253:
        return False, "目标地址太长啦，{who}，请输入不超过 253 个字符的 IP 或域名。".format(who=who)
    try:
        ip_obj = ipaddress.ip_address(cleaned)
        if not allow_private and (
            ip_obj.is_private
            or ip_obj.is_loopback
            or ip_obj.is_link_local
            or ip_obj.is_reserved
            or ip_obj.is_multicast
            or ip_obj.is_unspecified
        ):
            return False, "普通授权用户不能对内网或保留 IP 执行测试哦，{who}。".format(who=who)
        return True, ""
    except ValueError:
        pass
    # 域名：字母数字开头结尾，每段 1~63 字符，可含连字符，点号分隔
    domain_pattern = re.compile(
        r"^[a-zA-Z0-9]([a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?(\.[a-zA-Z0-9]([a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?)*$"
    )
    if domain_pattern.match(cleaned):
        lowered = cleaned.lower().rstrip('.')
        if not allow_private and (
            lowered == 'localhost'
            or lowered.endswith('.localhost')
            or lowered.endswith('.local')
            or lowered.endswith('.internal')
        ):
            return False, "普通授权用户不能对本地或内网域名执行测试哦，{who}。".format(who=who)
        return True, ""
    return False, "目标地址格式不对哦，{who}，请输入合法的 IP 地址或域名（如 8.8.8.8 或 google.com）。".format(who=who)


async def schedule_delete_message(context, chat_id: int, message_id: int, delay: int = 10):
    await asyncio.sleep(delay)
    try:
        await context.bot.delete_message(chat_id=chat_id, message_id=message_id)
    except Exception as e:
        logging.debug(f"删除消息 {message_id} 跳过/失败: {e}")


async def progress_spinner(context, chat_id: int, message_id: int, base_text: str, done_event: asyncio.Event):
    spinner_states = [".", "..", "...", "...."]
    i = 0
    while not done_event.is_set():
        # 先等待 3.5 秒再编辑，减少 Telegram edit 频率并能在任务完成时立即唤醒退出
        try:
            await asyncio.wait_for(done_event.wait(), timeout=3.5)
            break
        except asyncio.TimeoutError:
            pass
        if done_event.is_set():
            break
        spinner = spinner_states[i % len(spinner_states)]
        try:
            await context.bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=f"{base_text}{spinner}",
                parse_mode="HTML"
            )
        except RetryAfter:
            logging.warning("progress_spinner 触发 Telegram 限流，停止进度动画")
            break
        except BadRequest as e:
            if "not modified" not in str(e).lower():
                logging.debug(f"更新进度消息失败: {e}")
        except Exception as e:
            logging.debug(f"更新进度消息失败: {e}")
        i += 1


def retry_operation(func, *args, retries=3, delay=2, **kwargs):
    last_exception = None

    for attempt in range(retries):
        try:
            return func(*args, **kwargs)
        except (paramiko.AuthenticationException, paramiko.BadHostKeyException) as e:
            logging.error(f"SSH 认证或主机密钥校验失败，停止重试: {e}")
            return f"SSH 认证或主机密钥校验失败: {e}"
        except Exception as e:
            last_exception = e
            logging.warning(f"操作失败 (尝试 {attempt+1}/{retries}): {str(e)}")
            if attempt < retries - 1:
                time.sleep(delay)
                delay *= 1.5

    return f"操作失败，女仆已重试{retries}次: {str(last_exception)}"
