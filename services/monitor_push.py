"""监控结果的机器人推送：网页监控、补货监控和 TG 监听共用。"""

import logging
from typing import Iterable

from telegram.constants import ParseMode
from telegram.error import TelegramError

logger = logging.getLogger(__name__)


def parse_chat_ids(raw) -> list[int]:
    """把逗号或换行分隔的 chat_id 解析成列表，填错抛 ValueError。"""
    tokens = str(raw or '').replace('，', ',').replace('\n', ',').split(',')
    chat_ids = []
    for token in tokens:
        token = token.strip()
        if not token:
            continue
        try:
            chat_ids.append(int(token))
        except ValueError:
            raise ValueError(f'推送目标需要是数字 chat_id：{token}')
    return chat_ids


def resolve_targets(monitor: dict, default: Iterable[int]) -> list[int]:
    return parse_chat_ids(monitor.get('notify_chat_ids')) or list(default)


async def push(bot, chat_ids: Iterable[int], text: str, reply_markup=None) -> int:
    """逐个发送，单个目标失败不影响其他目标；返回成功条数。"""
    sent = 0
    for chat_id in chat_ids:
        try:
            await bot.send_message(
                chat_id=chat_id,
                text=text,
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=True,
                reply_markup=reply_markup,
            )
            sent += 1
        except TelegramError as exc:
            logger.warning('发送监控通知失败 chat_id=%s: %s', chat_id, exc)
    return sent
