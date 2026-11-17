import asyncio
import html
import logging
import time
from urllib.parse import urlparse
import feedparser
from typing import Dict, Any, Optional
from telegram.ext import ContextTypes
from telegram import constants
from config import config
from database import models as db
from utils import copy as copy_text
from . import data_manager, retry_utils, settings

logger = logging.getLogger(__name__)

# 抓取订阅源超时与响应大小上限（复用 web_monitor 公网校验 + 异步 HTTP，不修改全局 socket 超时）
RSS_FETCH_TIMEOUT = 15
RSS_MAX_BYTES = 5 * 1024 * 1024
# 单个周期内每个订阅源最多发送的新条目数
MAX_SEND_PER_CYCLE = 5
MAX_SEEN_IDS = 200


def _escape_html(text: Any) -> str:
    """转义用户可控文本，防止破坏 Telegram HTML 消息（& < > 等需转义）。"""
    return html.escape(str(text), quote=True)


def _is_admin_chat(chat_id) -> bool:
    """判断这个订阅 chat_id 是否属于管理员。"""
    try:
        return int(chat_id) in config.ADMIN_IDS
    except (TypeError, ValueError):
        return False


def _is_valid_http_url(url: str) -> bool:
    """校验链接是否为 http(s) 协议，避免向消息中插入非法链接。"""
    try:
        result = urlparse(url)
    except ValueError:
        return False
    return result.scheme.lower() in ("http", "https") and bool(result.netloc)


async def _fetch_and_parse_feed(feed_url: str):
    """使用公网 SSRF 校验 + 流式上限异步抓取订阅字节流，并在线程池解析 RSS/Atom。"""
    from services.web_monitor import fetch_public_bytes
    raw_bytes, _status, _final_url, _charset = await fetch_public_bytes(
        feed_url,
        timeout_seconds=RSS_FETCH_TIMEOUT,
        max_bytes=RSS_MAX_BYTES,
    )
    return await asyncio.to_thread(feedparser.parse, raw_bytes)


async def send_telegram_message(
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: str,
    text: str,
) -> bool:
    try:
        subscriptions_data = data_manager.get_subscriptions()
        user_chat_id_str = str(chat_id)
        user_data = subscriptions_data.get(user_chat_id_str, {})
        custom_footer = user_data.get("custom_footer")
        link_preview_enabled = user_data.get("link_preview_enabled", True)

        if custom_footer:
            footer_html = f"\n---\n{_escape_html(custom_footer)}"
            if len(text) + len(footer_html) > 4096:
                budget = max(200, 4080 - len(footer_html))
                text = text[:budget]
            text += footer_html
        elif len(text) > 4096:
            text = text[:4080]

        await retry_utils.retry_telegram_api(
            context.bot.send_message,
            chat_id=chat_id,
            text=text,
            parse_mode=constants.ParseMode.HTML,
            disable_web_page_preview=not link_preview_enabled,
        )
        return True
    except Exception as exc:
        logger.error("向 %s 发送消息时出错: %s", chat_id, exc)
        return False


def _get_entry_id(entry: Dict[str, Any]) -> Optional[str]:
    return entry.get("id") or entry.get("link")


def _matches_keywords(entry: Dict[str, Any], keywords: list) -> bool:
    if not keywords:
        return True

    parts = [entry.get("title", "") or "", entry.get("summary", "") or ""]
    for block in entry.get("content") or []:
        if isinstance(block, dict) and block.get("value"):
            parts.append(str(block["value"]))
    for tag in entry.get("tags") or []:
        if isinstance(tag, dict) and tag.get("term"):
            parts.append(str(tag["term"]))
    content_to_check = " ".join(parts).lower()

    return any(keyword.lower() in content_to_check for keyword in keywords)


def _update_feed_progress(
    chat_id: str,
    feed_url: str,
    entry_id: Optional[str],
    seen_ids_to_add: list[str],
    data_file: str,
    *,
    resolved_title: Optional[str] = None,
    save_now: bool = True,
) -> bool:
    subscriptions_data = data_manager.get_subscriptions()
    feed_obj = subscriptions_data.get(chat_id, {}).get("rss_feeds", {}).get(feed_url)
    if not feed_obj:
        return False

    dirty = False
    if resolved_title and feed_obj.get("title") in (None, "", "未知标题", feed_url):
        feed_obj["title"] = resolved_title
        dirty = True
    if entry_id and feed_obj.get("last_entry_id") != entry_id:
        feed_obj["last_entry_id"] = entry_id
        dirty = True
    if seen_ids_to_add:
        existing = list(feed_obj.get("seen_entry_ids") or [])
        seen_set = set(existing)
        for item_id in seen_ids_to_add:
            if item_id and item_id not in seen_set:
                existing.append(item_id)
                seen_set.add(item_id)
                dirty = True
        if len(existing) > MAX_SEEN_IDS:
            existing = existing[-MAX_SEEN_IDS:]
            dirty = True
        feed_obj["seen_entry_ids"] = existing

    if dirty and save_now:
        data_manager.save_subscriptions(data_file)
    return dirty


async def check_single_feed(
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: str,
    feed_url: str,
    feed_config: Dict[str, Any],
    data_file: str,
    *,
    batch_save: bool = False,
) -> int:
    logger.info("正在为用户 %s 检查订阅源: %s", chat_id, feed_url)
    start = time.monotonic()
    status_name = f"rss:{chat_id}:{feed_config.get('title') or feed_url}"

    try:
        try:
            feed_content = await _fetch_and_parse_feed(feed_url)
        except asyncio.TimeoutError:
            logger.warning("抓取订阅源 %s 超时（超过 %s 秒），本轮跳过该订阅源。", feed_url, RSS_FETCH_TIMEOUT)
            await db.record_runtime_status(
                status_name,
                "rss",
                False,
                duration_ms=int((time.monotonic() - start) * 1000),
                error="fetch timeout",
            )
            return 0

        if feed_content.bozo:
            logger.warning(
                "用户 %s 的订阅源 %s 可能格式错误。Bozo 标记: %s",
                chat_id,
                feed_url,
                feed_content.bozo_exception,
            )
            if not getattr(feed_content, "entries", None):
                await db.record_runtime_status(
                    status_name,
                    "rss",
                    False,
                    duration_ms=int((time.monotonic() - start) * 1000),
                    error=f"feed parse error: {feed_content.bozo_exception}",
                )
                return 0

        parsed_feed_title = ""
        if getattr(feed_content, "feed", None):
            parsed_feed_title = str(feed_content.feed.get("title") or "").strip()

        last_known_entry_id = feed_config.get("last_entry_id")
        seen_entry_ids = list(feed_config.get("seen_entry_ids") or [])
        seen_set = set(seen_entry_ids)
        current_feed_latest_entry_id = None

        if feed_content.entries:
            latest_entry = feed_content.entries[0]
            current_feed_latest_entry_id = _get_entry_id(latest_entry)

        if last_known_entry_id is None and not seen_set:
            initial_seen = [eid for eid in (_get_entry_id(e) for e in reversed(feed_content.entries[:MAX_SEEN_IDS])) if eid]
            _update_feed_progress(
                chat_id,
                feed_url,
                current_feed_latest_entry_id,
                initial_seen,
                data_file,
                resolved_title=parsed_feed_title or None,
                save_now=not batch_save,
            )
            logger.info(
                "首次检查 %s (用户 %s)。初始化已见条目 %s 条，此周期不发送初始帖子。",
                feed_url,
                chat_id,
                len(initial_seen),
            )
            await db.record_runtime_status(
                status_name,
                "rss",
                True,
                duration_ms=int((time.monotonic() - start) * 1000),
                sent_count=0,
            )
            return 0

        temp_new_entries = []
        if seen_set:
            for entry in feed_content.entries:
                entry_id = _get_entry_id(entry)
                if not entry_id:
                    continue
                if entry_id not in seen_set:
                    temp_new_entries.append(entry)
            new_entries = list(reversed(temp_new_entries))
        else:
            # 升级兼容：老数据仅有 last_entry_id，从首轮起迁移到 seen_entry_ids（支持置顶条目）
            found_last_known = False
            for entry in feed_content.entries:
                entry_id = _get_entry_id(entry)
                if not entry_id:
                    logger.warning("%s 中的条目缺少 'id' 和 'link'。正在跳过。", feed_url)
                    continue
                if last_known_entry_id == entry_id:
                    found_last_known = True
                    break
                temp_new_entries.append(entry)

            if not found_last_known and last_known_entry_id is not None:
                logger.warning(
                    "在用户 %s 的 %s 当前获取中未找到最后已知条目 ID '%s'。如果存在，则最多发送 5 个最新项目。",
                    chat_id,
                    feed_url,
                    last_known_entry_id,
                )
                new_entries = list(reversed(temp_new_entries[:5]))
            else:
                new_entries = list(reversed(temp_new_entries))

        sent_count = 0
        send_attempted = 0
        send_failed = 0
        progressed_ids: list[str] = []
        latest_progressed_id = None
        keywords = feed_config.get("keywords", [])
        feed_title = feed_config.get("title") or parsed_feed_title or feed_url

        for index, entry in enumerate(new_entries):
            entry_id = _get_entry_id(entry)
            if not entry_id:
                continue

            if not _matches_keywords(entry, keywords):
                logger.debug(
                    "来自 %s 的条目 '%s' 因用户 %s 的关键词不匹配而被跳过。",
                    feed_url,
                    entry.get("title", "无标题"),
                    chat_id,
                )
                progressed_ids.append(entry_id)
                latest_progressed_id = entry_id
                continue

            title = str(entry.get("title", "无标题") or "无标题")
            link = str(entry.get("link", "") or "")
            safe_title = title[:200]
            if _is_valid_http_url(link):
                message = (
                    f"<b>{_escape_html(feed_title)}</b>\n"
                    f"<a href='{_escape_html(link)}'>{_escape_html(title[:800])}</a>"
                )
            else:
                logger.warning("订阅源 %s 的条目链接无效，仅发送标题: %s", feed_url, link)
                link = ""
                message = f"<b>{_escape_html(feed_title)}</b>\n{_escape_html(title[:800])}"

            send_attempted += 1
            delivered = await send_telegram_message(context, chat_id, message)
            if not delivered:
                # 推送失败时不推进该条及后续未送达条目进度，下次重试
                send_failed += 1
                break

            sent_count += 1
            progressed_ids.append(entry_id)
            latest_progressed_id = entry_id
            await db.record_monitor_event("rss", feed_title, safe_title, link, f"推送到 {chat_id}", 1)

            if sent_count >= MAX_SEND_PER_CYCLE:
                remaining = sum(
                    1 for remaining_entry in new_entries[index + 1:]
                    if _matches_keywords(remaining_entry, keywords)
                )
                if remaining > 0:
                    await send_telegram_message(
                        context,
                        chat_id,
                        copy_text.rss_more_entries(
                            copy_text.address(_is_admin_chat(chat_id)),
                            _escape_html(feed_title),
                            remaining,
                        ),
                    )
                    # 把剩余未逐条推送的旧批次 ID 一并记入 seen，维持“单周期最多 5 条”语义
                    for skipped in new_entries[index + 1:]:
                        skipped_id = _get_entry_id(skipped)
                        if skipped_id:
                            progressed_ids.append(skipped_id)
                    latest_progressed_id = progressed_ids[-1]
                break

        # 若无 seen_entry_ids 历史基线，把本页已存在条目一并记入基线以完成迁移
        if not seen_set and feed_content.entries and not send_failed:
            existing_ids = [_get_entry_id(e) for e in reversed(feed_content.entries[:MAX_SEEN_IDS])]
            progressed_ids = [eid for eid in existing_ids if eid] + progressed_ids

        _update_feed_progress(
            chat_id,
            feed_url,
            latest_progressed_id or current_feed_latest_entry_id,
            progressed_ids,
            data_file,
            resolved_title=parsed_feed_title or None,
            save_now=not batch_save,
        )

        ok = not (send_attempted > 0 and sent_count == 0 and send_failed > 0)
        await db.record_runtime_status(
            status_name,
            "rss",
            ok,
            duration_ms=int((time.monotonic() - start) * 1000),
            sent_count=sent_count,
            error="telegram push failed" if not ok else "",
        )
        return sent_count

    except Exception as exc:
        logger.error("处理用户 %s 的订阅源 %s 时出错: %s", chat_id, feed_url, exc, exc_info=True)
        await db.record_runtime_status(
            status_name,
            "rss",
            False,
            duration_ms=int((time.monotonic() - start) * 1000),
            error=str(exc),
        )
        return 0


# 防止周期任务重叠：上一轮检查未完成时直接跳过本轮
_running_check = False


async def check_feeds_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    global _running_check

    if _running_check:
        logger.warning("上一轮订阅源检查尚未完成，跳过本轮以避免重复发送。")
        return

    _running_check = True
    try:
        await _run_feeds_check(context)
    finally:
        _running_check = False


async def _run_feeds_check(context: ContextTypes.DEFAULT_TYPE) -> None:
    if not settings.is_enabled():
        logger.debug("RSS 功能已关闭，跳过本次检查任务。")
        return

    data_file = context.application.bot_data.get("rss_data_file", config.RSS_DATA_FILE)
    if not data_file:
        logger.warning("未配置 RSS 数据文件，跳过此次检查。")
        return

    logger.info("正在运行定期订阅源检查...")
    subscriptions_data = data_manager.get_subscriptions()

    if not subscriptions_data:
        logger.info("没有要检查的订阅。")
        return

    semaphore = asyncio.Semaphore(5)

    async def _guarded_check(cid: str, url: str, cfg: Dict[str, Any]) -> int:
        async with semaphore:
            return await check_single_feed(context, cid, url, cfg, data_file, batch_save=True)

    all_feed_checks = []
    for chat_id, user_data in list(subscriptions_data.items()):
        feeds = user_data.get("rss_feeds", {})
        for feed_url, feed_config in list(feeds.items()):
            all_feed_checks.append(_guarded_check(chat_id, feed_url, dict(feed_config)))

    if not all_feed_checks:
        logger.info("在 subscriptions_data 中未找到要检查的订阅源。")
        return

    logger.info("已计划 %s 个订阅源检查，将有界并发执行。", len(all_feed_checks))
    results = await asyncio.gather(*all_feed_checks, return_exceptions=True)
    # 周期结束统一落盘一次，避免每个订阅源独立序列化整个 JSON
    data_manager.save_subscriptions(data_file)

    error_count = 0
    for idx, result in enumerate(results):
        if isinstance(result, Exception):
            error_count += 1
            logger.error("订阅源检查任务 %s 失败: %s", idx, result)

    if error_count > 0:
        logger.warning("此周期有 %s/%s 个订阅源检查失败。", error_count, len(all_feed_checks))
    else:
        logger.info("此周期的所有订阅源检查已完成。")
