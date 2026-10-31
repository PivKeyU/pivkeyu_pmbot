"""机器人自重启：先验证保存的 Bot Token，再优雅停止，最后原地替换进程。

用 os.execv 而不是退出进程，这样 Docker 和源码部署都能用，不依赖容器的重启策略。
"""

import asyncio
import json
import logging
import os
import sys

from telegram import Bot
from telegram.error import InvalidToken, TelegramError

from config import config
from database import models as db
from services import broadcast
from utils import copy as copy_text

logger = logging.getLogger(__name__)

NOTIFY_META_KEY = 'restart_notify_chat_ids'
# 留出时间把「正在重启」的响应发出去，再停止事件循环
STOP_DELAY_SECONDS = 1

_restarting = False


class RestartRefused(Exception):
    """不满足重启条件，消息内容可以直接展示给管理员。"""


async def _check_token() -> None:
    """用当前保存的 Token 请求 Telegram；连不上时无法确认，同样拒绝重启。"""
    try:
        async with Bot(config.BOT_TOKEN or '') as bot:
            await bot.get_me()
    except InvalidToken:
        raise RestartRefused(copy_text.RESTART_BAD_TOKEN) from None
    except TelegramError as exc:
        logger.warning('重启前验证 Bot Token 失败: %s', type(exc).__name__)
        raise RestartRefused(copy_text.RESTART_TOKEN_UNREACHABLE) from None


async def _stop_after_delay(application) -> None:
    await asyncio.sleep(STOP_DELAY_SECONDS)
    application.stop_running()


async def request_restart(application, notify_chat_ids) -> None:
    """校验通过后安排重启；不满足条件时抛 RestartRefused。"""
    global _restarting
    if _restarting:
        raise RestartRefused(copy_text.RESTART_IN_PROGRESS)
    if broadcast.is_running():
        raise RestartRefused(copy_text.RESTART_BROADCAST_RUNNING)

    # 先占位再验证：验证要等网络，避免连续点击安排两次重启
    _restarting = True
    try:
        await _check_token()
        await db.set_app_meta(NOTIFY_META_KEY, json.dumps(list(notify_chat_ids)))
    except BaseException:
        _restarting = False
        raise

    logger.info('已安排重启机器人')
    application.create_task(_stop_after_delay(application))


async def announce_restart(application) -> None:
    """重启后告诉发起人已经回来了；通知失败不影响启动。"""
    raw = await db.get_app_meta(NOTIFY_META_KEY)
    if not raw:
        return
    await db.set_app_meta(NOTIFY_META_KEY, '')
    for chat_id in json.loads(raw):
        try:
            await application.bot.send_message(
                chat_id=chat_id, text=copy_text.with_deco(copy_text.RESTART_DONE, 'OK'))
        except TelegramError:
            logger.warning('发送重启完成通知失败 chat_id=%s', chat_id)


def exec_if_requested() -> None:
    """run_polling 返回后调用：资源已释放，用新进程替换当前进程。"""
    if not _restarting:
        return
    logger.info('机器人已停止，正在重新启动进程')
    sys.stdout.flush()
    sys.stderr.flush()
    os.execv(sys.executable, [sys.executable, *sys.orig_argv[1:]])
