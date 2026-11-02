from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters
from .command_handler import (
    start, help_command, block, unblock, blacklist, stats, inbox, getid, autoreply,
    panel, exempt, group, broadcast, spamrules, tgmon, webmon, monitor_status, updatebot,
    unverify, verify, miniapp, restart_bot, restock, handle_unknown_command,
)
from .user_handler import handle_message, handle_edited_private_message
from .callback_handler import handle_callback
from .admin_handler import handle_admin_reply, handle_edited_admin_message, view_filtered
from services.tg_monitor import handle_bot_group_message
from config import config
from utils.commands import names_for
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
    app.add_handler(CommandHandler(names_for("getid"), getid, filters=_command_filters()))
    app.add_handler(CommandHandler(names_for("start"), start, filters=_command_filters(filters.ChatType.PRIVATE)))
    
    app.add_handler(CommandHandler(names_for("ping"), ping_command, filters=_command_filters()))
    app.add_handler(CommandHandler(names_for("nexttrace"), nexttrace_command, filters=_command_filters()))
    app.add_handler(CommandHandler(names_for("adduser"), add_user_command, filters=_command_filters()))
    app.add_handler(CommandHandler(names_for("rmuser"), rm_user_command, filters=_command_filters()))
    app.add_handler(CommandHandler(names_for("addserver"), add_server_command, filters=_command_filters()))
    app.add_handler(CommandHandler(names_for("rmserver"), rm_server_command, filters=_command_filters()))
    app.add_handler(CommandHandler(names_for("install_nexttrace"), install_nexttrace_command, filters=_command_filters()))

    if config.FORUM_GROUP_ID and config.ADMIN_IDS:
        app.add_handler(CommandHandler(names_for("help"), help_command, filters=_command_filters(filters.ChatType.PRIVATE)))
        app.add_handler(CommandHandler(names_for("block"), block, filters=_command_filters()))
        app.add_handler(CommandHandler(names_for("unblock"), unblock, filters=_command_filters()))
        app.add_handler(CommandHandler(names_for("panel"), panel, filters=_command_filters()))
        # Web App 按钮只能发在私聊里
        app.add_handler(CommandHandler(names_for("app"), miniapp, filters=_command_filters(filters.ChatType.PRIVATE)))
        app.add_handler(CommandHandler(names_for("blacklist"), blacklist, filters=_command_filters()))
        app.add_handler(CommandHandler(names_for("stats"), stats, filters=_command_filters()))
        app.add_handler(CommandHandler(names_for("inbox"), inbox, filters=_command_filters()))
        app.add_handler(CommandHandler(names_for("view_filtered"), view_filtered, filters=_command_filters()))
        app.add_handler(CommandHandler(names_for("autoreply"), autoreply, filters=_command_filters()))
        app.add_handler(CommandHandler(names_for("exempt"), exempt, filters=_command_filters()))
        app.add_handler(CommandHandler(names_for("unverify"), unverify, filters=_command_filters()))
        app.add_handler(CommandHandler(names_for("verify"), verify, filters=_command_filters()))
        app.add_handler(CommandHandler(names_for("group"), group, filters=_command_filters()))
        app.add_handler(CommandHandler(names_for("broadcast"), broadcast, filters=_command_filters()))
        app.add_handler(CommandHandler(names_for("spamrules"), spamrules, filters=_command_filters()))
        app.add_handler(CommandHandler(names_for("tgmon"), tgmon, filters=_command_filters()))
        app.add_handler(CommandHandler(names_for("webmon"), webmon, filters=_command_filters()))
        app.add_handler(CommandHandler(names_for("monitor_status"), monitor_status, filters=_command_filters()))
        app.add_handler(CommandHandler(names_for("updatebot"), updatebot, filters=_command_filters()))
        app.add_handler(CommandHandler(names_for("restart"), restart_bot, filters=_command_filters()))
        app.add_handler(CommandHandler(names_for("restock"), restock, filters=_command_filters()))
        # 放在单独的分组里：只处理目录里没有的命令，已有命令继续走上面的处理器
        app.add_handler(MessageHandler(
            filters.UpdateType.MESSAGE & filters.ChatType.PRIVATE & filters.COMMAND,
            handle_unknown_command,
        ), group=1)
        
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
