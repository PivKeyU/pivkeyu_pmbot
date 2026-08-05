import asyncio
import time
import re
import ipaddress
import logging

def check_authorization(user_id: int, authorized_users: list, admin_users: list = None) -> bool:
    if admin_users and user_id in admin_users:
        return True
    
    return user_id in authorized_users

def check_is_admin(user_id: int, admin_users: list) -> bool:
    return user_id in admin_users

def validate_target(target: str) -> tuple:
    """校验目标地址是否为合法的 IP 或域名（防 SSH 命令注入）。

    允许：IPv4 / IPv6（不含端口）或严格格式的域名（长度 <= 253）。
    返回: (是否合法, 错误提示)，合法时错误提示为空字符串。
    """
    if not target or not target.strip():
        return False, "目标地址不能为空哦，主人。"
    if len(target) > 253:
        return False, "目标地址太长啦，主人，请输入不超过 253 个字符的 IP 或域名。"
    try:
        ipaddress.ip_address(target)
        return True, ""
    except ValueError:
        pass
    # 域名：字母数字开头结尾，每段 1~63 字符，可含连字符，点号分隔
    domain_pattern = re.compile(
        r"^[a-zA-Z0-9]([a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?(\.[a-zA-Z0-9]([a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?)*$"
    )
    if domain_pattern.match(target):
        return True, ""
    return False, "目标地址格式不对哦，主人，请输入合法的 IP 地址或域名（如 8.8.8.8 或 google.com）。"

async def schedule_delete_message(context, chat_id: int, message_id: int, delay: int = 10):
    await asyncio.sleep(delay)
    try:
        await context.bot.delete_message(chat_id=chat_id, message_id=message_id)
    except Exception as e:
        logging.error(f"删除消息 {message_id} 失败: {e}")

async def progress_spinner(context, chat_id: int, message_id: int, base_text: str, done_event: asyncio.Event):
    spinner_states = [".", "..", "...", "...."]
    i = 0
    while not done_event.is_set():
        spinner = spinner_states[i % len(spinner_states)]
        try:
            await context.bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=f"{base_text}{spinner}",
                parse_mode="HTML"
            )
        except Exception as e:
            logging.error(f"更新进度消息失败: {e}")
        await asyncio.sleep(1)
        i += 1

def retry_operation(func, *args, retries=3, delay=2, **kwargs):
    last_exception = None
    
    for attempt in range(retries):
        try:
            return func(*args, **kwargs)
        except Exception as e:
            last_exception = e
            logging.warning(f"操作失败 (尝试 {attempt+1}/{retries}): {str(e)}")
            if attempt < retries - 1:  
                time.sleep(delay)
                
                delay *= 1.5
    
    return f"操作失败，女仆已重试{retries}次: {str(last_exception)}"
