import asyncio
import json
import logging
import os
import threading
import time
from typing import Any, Dict, Optional
import feedparser

logger = logging.getLogger(__name__)

# 抓取订阅源标题的超时（秒）与最大字节上限
FETCH_TITLE_TIMEOUT = 15
_MAX_TITLE_FETCH_BYTES = 2 * 1024 * 1024

subscriptions_data: Dict[str, Dict[str, Any]] = {}
_save_lock = threading.RLock()


async def fetch_feed_title(feed_url: str) -> Optional[str]:
    """异步安全抓取订阅源标题（复用 pinned-DNS 公网校验与大小限制，不修改全局 socket 超时）。"""
    try:
        from services.web_monitor import fetch_public_bytes
        raw_bytes, _status, _final_url, _charset = await fetch_public_bytes(
            feed_url,
            timeout_seconds=FETCH_TITLE_TIMEOUT,
            max_bytes=_MAX_TITLE_FETCH_BYTES,
        )
        feed = await asyncio.to_thread(feedparser.parse, raw_bytes)
        if feed.feed and feed.feed.get("title"):
            return str(feed.feed["title"]).strip()
        logger.warning("无法获取订阅源的标题: %s", feed_url)
    except Exception as exc:
        logger.error("获取订阅源 %s 标题时出错: %s", feed_url, exc)
    return None


def _ensure_feed_data_structure(
    feed_data: Dict[str, Any],
    feed_url: str,
    *,
    skip_title_fetch: bool = False,
) -> None:
    if "keywords" not in feed_data or not isinstance(feed_data["keywords"], list):
        feed_data["keywords"] = []
    if "last_entry_id" not in feed_data:
        feed_data["last_entry_id"] = None
    if "seen_entry_ids" not in feed_data or not isinstance(feed_data["seen_entry_ids"], list):
        feed_data["seen_entry_ids"] = []
    if "title" not in feed_data or not feed_data["title"]:
        feed_data["title"] = feed_url if skip_title_fetch else "未知标题"


def _ensure_user_data_structure(
    user_config: Dict[str, Any],
    *,
    skip_title_fetch: bool = False,
) -> Dict[str, Any]:
    if "rss_feeds" not in user_config:
        user_config["rss_feeds"] = {}
    else:
        for feed_url, feed_data in user_config["rss_feeds"].items():
            _ensure_feed_data_structure(feed_data, feed_url, skip_title_fetch=skip_title_fetch)

    user_config.setdefault("custom_footer", None)
    user_config.setdefault("link_preview_enabled", True)
    return user_config


def load_subscriptions(data_file: str, *, skip_title_fetch: bool = False) -> Dict[str, Dict[str, Any]]:
    global subscriptions_data

    if not os.path.exists(data_file):
        logger.info("未找到 %s。初始化为空订阅。", data_file)
        subscriptions_data = {}
        return subscriptions_data

    try:
        with open(data_file, "r", encoding="utf-8") as file:
            loaded_data = json.load(file)

        subscriptions_data = {}
        for chat_id_str, user_config in loaded_data.items():
            chat_id = str(chat_id_str)
            subscriptions_data[chat_id] = _ensure_user_data_structure(
                user_config.copy(),
                skip_title_fetch=skip_title_fetch,
            )

        logger.info("订阅已成功从 %s 加载", data_file)
    except json.JSONDecodeError as exc:
        # 解码失败时不静默清空订阅：先把损坏文件备份，便于后续排查恢复
        backup_file = f"{data_file}.corrupt-{int(time.time())}"
        try:
            os.replace(data_file, backup_file)
            logger.error(
                "解码 %s 出错: %s。已将损坏文件备份为 %s，初始化为空订阅。",
                data_file,
                exc,
                backup_file,
            )
        except OSError:
            logger.error(
                "解码 %s 出错: %s，且备份损坏文件失败。初始化为空订阅。",
                data_file,
                exc,
            )
        subscriptions_data = {}
    except Exception as exc:
        logger.error("从 %s 加载订阅出错: %s。初始化为空订阅。", data_file, exc)
        subscriptions_data = {}

    return subscriptions_data


def save_subscriptions(data_file: str) -> None:
    with _save_lock:
        temp_file = data_file + ".tmp"
        try:
            data_dir = os.path.dirname(data_file)
            if data_dir:
                os.makedirs(data_dir, exist_ok=True)

            # 先写入临时文件，再通过 os.replace 原子替换，
            # 避免进程中断或并发写入时留下损坏的 JSON
            snapshot = json.dumps(subscriptions_data, indent=4, ensure_ascii=False)
            with open(temp_file, "w", encoding="utf-8") as file:
                file.write(snapshot)
            os.replace(temp_file, data_file)
            logger.debug("订阅已成功保存到 %s", data_file)
        except Exception as exc:
            try:
                if os.path.exists(temp_file):
                    os.remove(temp_file)
            except OSError:
                pass
            logger.error("保存订阅到 %s 时出错: %s", data_file, exc)


def get_subscriptions() -> Dict[str, Dict[str, Any]]:
    return subscriptions_data


def remove_feed(chat_id: str, feed_url: str, data_file: str) -> bool:
    if chat_id not in subscriptions_data:
        return False

    feeds = subscriptions_data[chat_id].get("rss_feeds", {})
    if feed_url not in feeds:
        return False

    del feeds[feed_url]
    if not feeds:
        subscriptions_data.pop(chat_id, None)

    save_subscriptions(data_file)
    return True


def remove_keyword(chat_id: str, feed_url: str, keyword: str, data_file: str) -> bool:
    if chat_id not in subscriptions_data:
        return False

    feed_data = subscriptions_data[chat_id].get("rss_feeds", {}).get(feed_url)
    if not feed_data:
        return False

    keywords = feed_data.get("keywords", [])
    lowered = keyword.lower()
    for existing in keywords:
        if existing.lower() == lowered:
            keywords.remove(existing)
            save_subscriptions(data_file)
            return True
    return False
