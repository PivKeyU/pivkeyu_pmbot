import logging

from telegram import (
    BotCommand,
    BotCommandScopeAllGroupChats,
    BotCommandScopeAllPrivateChats,
    BotCommandScopeChat,
    MenuButtonDefault,
    MenuButtonWebApp,
    WebAppInfo,
)
from telegram.error import TelegramError
from telegram.ext import Application

from config import config
from services.verification import miniapp_url
from utils import commands as command_catalog
from utils import copy as copy_text

logger = logging.getLogger(__name__)


def _to_bot_commands(commands) -> list[BotCommand]:
    return [BotCommand(command.name, command.description) for command in commands]


def _has_admin_features() -> bool:
    return bool(config.FORUM_GROUP_ID and config.ADMIN_IDS)


def get_private_chat_commands() -> list[BotCommand]:
    """普通用户的私聊菜单：只放人人都能用的命令。"""
    return _to_bot_commands(command_catalog.USER_COMMANDS)


def get_group_chat_commands() -> list[BotCommand]:
    return _to_bot_commands(command for command in command_catalog.USER_COMMANDS if not command.private_only)


def get_admin_private_commands() -> list[BotCommand]:
    return _to_bot_commands(command_catalog.all_commands())


def get_admin_group_commands() -> list[BotCommand]:
    return _to_bot_commands(command for command in command_catalog.all_commands() if not command.private_only)


async def register_miniapp_menu(app: Application) -> None:
    """给管理员的聊天框设置 Mini App 菜单按钮；地址清空后恢复默认按钮。"""
    url = miniapp_url()
    menu_button = (
        MenuButtonWebApp(text=copy_text.BTN_MINIAPP_MENU, web_app=WebAppInfo(url=url))
        if url else MenuButtonDefault()
    )
    for admin_id in config.ADMIN_IDS:
        try:
            await app.bot.set_chat_menu_button(chat_id=admin_id, menu_button=menu_button)
        except TelegramError as exc:
            # 最常见的原因是这位管理员还没和机器人说过话，不影响启动
            logger.warning("设置 Mini App 菜单按钮失败 admin_id=%s: %s", admin_id, exc)


async def _register_admin_commands(app: Application) -> int:
    """管理员的私聊和论坛群看到完整菜单，普通用户看不到管理命令。"""
    private_commands = get_admin_private_commands()
    targets = [(admin_id, private_commands) for admin_id in config.ADMIN_IDS]
    targets.append((config.FORUM_GROUP_ID, get_admin_group_commands()))
    for chat_id, commands in targets:
        try:
            await app.bot.set_my_commands(commands, scope=BotCommandScopeChat(chat_id))
        except TelegramError as exc:
            # 这位管理员还没和机器人说过话时会失败，不影响启动
            logger.warning("设置管理员命令菜单失败 chat_id=%s: %s", chat_id, exc)
    return len(private_commands)


async def register_bot_commands(app: Application) -> None:
    private_commands = get_private_chat_commands()
    group_commands = get_group_chat_commands()

    await app.bot.set_my_commands(
        private_commands,
        scope=BotCommandScopeAllPrivateChats(),
    )
    await app.bot.set_my_commands(
        group_commands,
        scope=BotCommandScopeAllGroupChats(),
    )

    admin_commands = 0
    if _has_admin_features():
        admin_commands = await _register_admin_commands(app)

    logger.info(
        "已同步 Telegram 命令菜单: 私聊 %s 条, 群聊 %s 条, 管理员 %s 条",
        len(private_commands),
        len(group_commands),
        admin_commands,
    )
    await register_miniapp_menu(app)
