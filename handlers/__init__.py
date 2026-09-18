from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters
from .command_handler import (
    start, help_command, block, unblock, blacklist, stats, inbox, getid, autoreply,
    panel, exempt, group, broadcast, spamrules, tgmon, webmon, monitor_status, updatebot
)
from .user_handler import handle_message, handle_edited_private_message
from .callback_handler import handle_callback
from .admin_handler import handle_admin_reply, handle_edited_admin_message, view_filtered
from services.tg_monitor import handle_bot_group_message
from config import config
from network_test.commands import (
    ping_command, nexttrace_command, add_user_command, rm_user_command,
    add_server_command, rm_server_command, install_nexttrace_command
)

def _command_filters(extra=None):
    """命令处理器统一使用的 filters。

    CommandHandler 的默认 filters 是 filters.UpdateType.MESSAGES，它的实现是::

        update.message is not None or update.edited_message is not None

    也就是说【把一条已存在的消息编辑成命令】时同样会进入命令处理器；但那种 Update 里
    update.message 是 None（消息在 update.edited_message 上），而命令体里大量直接写
    update.message.reply_text(...)，会抛::

        AttributeError: 'NoneType' object has no attribute 'reply_text'

    并且这个异常会中断该 update 的后续处理器（PTB 在同一个 try 里遍历所有 handler）。

    这里统一用 UpdateType.MESSAGE 把编辑路径挡在命令处理器之外。编辑消息不会因此丢失：
    它由下方专门的 EDITED_MESSAGE 处理器接管（handle_edited_admin_message 同步论坛侧、
    handle_edited_private_message 同步私聊侧），那两个处理器都不依赖命令处理器。
    """

    if extra is None:
        return filters.UpdateType.MESSAGE
    return extra & filters.UpdateType.MESSAGE


def register_handlers(app: Application):
    app.add_handler(CommandHandler("getid", getid, filters=_command_filters()))
    app.add_handler(CommandHandler("start", start, filters=_command_filters(filters.ChatType.PRIVATE)))
    
    app.add_handler(CommandHandler("ping", ping_command, filters=_command_filters()))
    app.add_handler(CommandHandler("nexttrace", nexttrace_command, filters=_command_filters()))
    app.add_handler(CommandHandler("adduser", add_user_command, filters=_command_filters()))
    app.add_handler(CommandHandler("rmuser", rm_user_command, filters=_command_filters()))
    app.add_handler(CommandHandler("addserver", add_server_command, filters=_command_filters()))
    app.add_handler(CommandHandler("rmserver", rm_server_command, filters=_command_filters()))
    app.add_handler(CommandHandler("install_nexttrace", install_nexttrace_command, filters=_command_filters()))

    if config.FORUM_GROUP_ID and config.ADMIN_IDS:
        app.add_handler(CommandHandler("help", help_command, filters=_command_filters(filters.ChatType.PRIVATE)))
        app.add_handler(CommandHandler("block", block, filters=_command_filters()))
        app.add_handler(CommandHandler("unblock", unblock, filters=_command_filters()))
        app.add_handler(CommandHandler("panel", panel, filters=_command_filters()))
        app.add_handler(CommandHandler("blacklist", blacklist, filters=_command_filters()))
        app.add_handler(CommandHandler("stats", stats, filters=_command_filters()))
        app.add_handler(CommandHandler("inbox", inbox, filters=_command_filters()))
        app.add_handler(CommandHandler("view_filtered", view_filtered, filters=_command_filters()))
        app.add_handler(CommandHandler("autoreply", autoreply, filters=_command_filters()))
        app.add_handler(CommandHandler("exempt", exempt, filters=_command_filters()))
        app.add_handler(CommandHandler("group", group, filters=_command_filters()))
        app.add_handler(CommandHandler("broadcast", broadcast, filters=_command_filters()))
        app.add_handler(CommandHandler("spamrules", spamrules, filters=_command_filters()))
        app.add_handler(CommandHandler("tgmon", tgmon, filters=_command_filters()))
        app.add_handler(CommandHandler("webmon", webmon, filters=_command_filters()))
        app.add_handler(CommandHandler("monitor_status", monitor_status, filters=_command_filters()))
        app.add_handler(CommandHandler("updatebot", updatebot, filters=_command_filters()))
        
        app.add_handler(MessageHandler(
            filters.UpdateType.MESSAGE & filters.Chat(chat_id=config.FORUM_GROUP_ID) & filters.REPLY & ~filters.COMMAND,
            handle_admin_reply
        ))
        
        app.add_handler(MessageHandler(
            filters.Chat(chat_id=config.FORUM_GROUP_ID) & filters.UpdateType.EDITED_MESSAGE & ~filters.COMMAND,
            handle_edited_admin_message
        ))

        app.add_handler(MessageHandler(
            filters.UpdateType.MESSAGE & filters.ChatType.GROUPS & ~filters.COMMAND,
            handle_bot_group_message
        ))

        app.add_handler(MessageHandler(
            filters.UpdateType.CHANNEL_POST & ~filters.COMMAND,
            handle_bot_group_message
        ))

        app.add_handler(MessageHandler(
            (filters.TEXT | filters.PHOTO | filters.VIDEO | filters.AUDIO | filters.VOICE |
             filters.Document.ALL | filters.Sticker.ALL | filters.ANIMATION) &
            filters.UpdateType.MESSAGE & ~filters.COMMAND & filters.ChatType.PRIVATE,
            handle_message
        ))
        
        app.add_handler(MessageHandler(
            (filters.TEXT | filters.PHOTO | filters.VIDEO | filters.AUDIO | filters.VOICE |
             filters.Document.ALL | filters.ANIMATION) &
            filters.UpdateType.EDITED_MESSAGE & filters.ChatType.PRIVATE,
            handle_edited_private_message
        ))

        app.add_handler(CallbackQueryHandler(handle_callback))
    else:
        print("警告: FORUM_GROUP_ID 或 ADMIN_IDS 未设置。已禁用大部分功能。")
