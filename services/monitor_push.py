"""监控结果的机器人推送：网页监控、补货监控和 TG 监听共用。"""

import html
import logging
from typing import Iterable

from telegram.constants import ParseMode
from telegram.error import BadRequest, TelegramError

from rss.retry_utils import retry_telegram_api

logger = logging.getLogger(__name__)


def _clamp_html_payload(text: str, max_len: int = 4000) -> str:
    """超过 Telegram 单条上限时安全降级为纯文本截断，避免未闭合标签引起 BadRequest。"""
    if len(text) <= 4096:
        return text
    # 从 HTML 通知安全切短（移除残留半截标签避免解析报错）
    plain = text.replace("<br>", "\n")
    import re
    plain = re.sub(r"<[^>]+>", "", plain)
    plain = html.unescape(plain)
    if len(plain) > max_len:
        plain = plain[:max_len - 12] + "\n...（已截断）"
    return html.escape(plain)


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
    """逐个发送，带 RetryAfter 退避与长度保护；返回成功条数。"""
    payload = _clamp_html_payload(text)
    sent = 0
    for chat_id in chat_ids:
        try:
            await retry_telegram_api(
                bot.send_message,
                chat_id=chat_id,
                text=payload,
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=True,
                reply_markup=reply_markup,
            )
            sent += 1
        except BadRequest as exc:
            logger.warning('发送监控通知 HTML 失败，降级纯文本重发 chat_id=%s: %s', chat_id, exc)
            try:
                await bot.send_message(
                    chat_id=chat_id,
                    text=payload[:4000],
                    disable_web_page_preview=True,
                    reply_markup=reply_markup,
                )
                sent += 1
            except TelegramError as exc2:
                logger.warning('发送监控通知纯文本失败 chat_id=%s: %s', chat_id, exc2)
        except TelegramError as exc:
            logger.warning('发送监控通知失败 chat_id=%s: %s', chat_id, exc)
    return sent
