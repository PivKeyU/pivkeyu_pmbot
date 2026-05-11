import logging
from typing import Iterable, Sequence

from telegram import (
    BotCommand,
    BotCommandScopeAllGroupChats,
    BotCommandScopeAllPrivateChats,
)
from telegram.ext import Application

from config import config

logger = logging.getLogger(__name__)

CommandSpec = tuple[str, str]


COMMON_PRIVATE_COMMANDS: tuple[CommandSpec, ...] = (
    ("start", "唤醒女仆"),
    ("getid", "查看主人 ID"),
    ("ping", "端来 Ping 测试"),
    ("nexttrace", "端来路由追踪"),
    ("adduser", "登记授权主人"),
    ("rmuser", "移除授权主人"),
    ("addserver", "登记测试服务器"),
    ("rmserver", "撤下测试服务器"),
    ("install_nexttrace", "安装追踪工具"),
)

ADMIN_PRIVATE_COMMANDS: tuple[CommandSpec, ...] = (
    ("help", "查看女仆小手册"),
    ("block", "记入黑名单"),
    ("unblock", "移出黑名单"),
    ("panel", "打开女仆长面板"),
    ("blacklist", "查看黑名单小本本"),
    ("stats", "查看宅邸统计"),
    ("view_filtered", "查看拦截篮"),
    ("autoreply", "安排自动回复女仆"),
    ("exempt", "管理审查通行证"),
    ("group", "管理用户分组"),
    ("broadcast", "发送用户广播"),
)

RSS_PRIVATE_COMMANDS: tuple[CommandSpec, ...] = (
    ("rss_add", "添加 RSS 茶点"),
    ("rss_remove", "撤下 RSS 茶点"),
    ("rss_list", "查看 RSS 茶点"),
    ("rss_addkeyword", "添加 RSS 口味词"),
    ("rss_removekeyword", "删除 RSS 口味词"),
    ("rss_listkeywords", "查看 RSS 口味词"),
    ("rss_removeallkeywords", "清空 RSS 口味词"),
    ("rss_setfooter", "设置 RSS 小尾巴"),
    ("rss_togglepreview", "切换链接预览"),
    ("rss_add_user", "登记 RSS 授权"),
    ("rss_rm_user", "移除 RSS 授权"),
)

COMMON_GROUP_COMMANDS: tuple[CommandSpec, ...] = (
    ("getid", "查看群组 ID"),
    ("ping", "端来 Ping 测试"),
    ("nexttrace", "端来路由追踪"),
    ("adduser", "登记授权主人"),
    ("rmuser", "移除授权主人"),
    ("addserver", "登记测试服务器"),
    ("rmserver", "撤下测试服务器"),
    ("install_nexttrace", "安装追踪工具"),
)

ADMIN_GROUP_COMMANDS: tuple[CommandSpec, ...] = (
    ("block", "记入黑名单"),
    ("unblock", "移出黑名单"),
    ("panel", "打开女仆长面板"),
    ("blacklist", "查看黑名单小本本"),
    ("stats", "查看宅邸统计"),
    ("view_filtered", "查看拦截篮"),
    ("autoreply", "安排自动回复女仆"),
    ("exempt", "管理审查通行证"),
    ("group", "管理用户分组"),
    ("broadcast", "发送用户广播"),
)


def _to_bot_commands(command_specs: Sequence[CommandSpec]) -> list[BotCommand]:
    return [BotCommand(command, description) for command, description in command_specs]


def _has_admin_features() -> bool:
    return bool(config.FORUM_GROUP_ID and config.ADMIN_IDS)


def _extend_commands(
    commands: list[CommandSpec],
    extra_commands: Iterable[CommandSpec],
) -> list[CommandSpec]:
    commands.extend(extra_commands)
    return commands


def get_private_chat_commands() -> list[BotCommand]:
    commands = list(COMMON_PRIVATE_COMMANDS)
    if _has_admin_features():
        _extend_commands(commands, ADMIN_PRIVATE_COMMANDS)
    _extend_commands(commands, RSS_PRIVATE_COMMANDS)
    return _to_bot_commands(commands)


def get_group_chat_commands() -> list[BotCommand]:
    commands = list(COMMON_GROUP_COMMANDS)
    if _has_admin_features():
        _extend_commands(commands, ADMIN_GROUP_COMMANDS)
    return _to_bot_commands(commands)


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

    logger.info(
        "已同步 Telegram 命令菜单: 私聊 %s 条, 群聊 %s 条",
        len(private_commands),
        len(group_commands),
    )
