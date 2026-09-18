from functools import wraps
from telegram import Update
from telegram.ext import ContextTypes
from database.models import is_admin
from config import config
from utils import copy as copy_text

def admin_only(func):
    @wraps(func)
    async def wrapped(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        user = update.effective_user
        # CommandHandler 默认 filters 是 UpdateType.MESSAGES，其实现为
        #     update.message is not None or update.edited_message is not None
        # 也就是说【编辑后的消息】同样会进来，而那种上下文里 update.message 是 None
        # （实测：私聊/群聊 Message 也会进来，channel_post 和 CallbackQuery 不会）——
        # 旧实现直接 `update.message.reply_text(...)`，在编辑路径上会 AttributeError 崩溃。
        # 用 effective_message 兼容；拿不到消息（如 CallbackQuery）时安静退出。
        # 另外频道帖子没有 effective_user，权限判断必须显式处理 None。
        message = update.effective_message
        if not config.ADMIN_IDS:
            if message:
                await message.reply_text(copy_text.perm_admin_disabled())
            return
        if user is None or not await is_admin(user.id):
            if message:
                await message.reply_text(copy_text.perm_admin_only())
            return
        return await func(update, context, *args, **kwargs)
    return wrapped
