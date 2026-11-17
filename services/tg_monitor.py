from __future__ import annotations

import asyncio
import hashlib
import html
import logging
import time
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any, Iterable
from urllib.parse import urlparse

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Application, ContextTypes

from config import config
from database import models as db
from services import monitor_push
from utils import copy as copy_text

try:
    from telethon import TelegramClient, events
    from telethon.sessions import StringSession
except Exception:  # pragma: no cover - optional dependency
    TelegramClient = None
    StringSession = None
    events = None

logger = logging.getLogger(__name__)

TG_MONITOR_TASK_KEY = "tg_monitor_user_session_task"
TG_MONITOR_CLIENT_KEY = "tg_monitor_user_session_client"
SUMMARY_MAX_CHARS = 900
_DISCOVERED_CACHE_TTL = 600.0
_discovered_chat_cache: dict[int, float] = {}


@dataclass
class MonitorHit:
    monitor: dict
    hits: list[str]
    text: str


def _split_words(raw_value: str) -> list[str]:
    return [item.strip() for item in (raw_value or "").replace("，", ",").replace(",", "\n").splitlines() if item.strip()]


async def _record_discovered_chat_cached(chat_id: int, title: str, username: str) -> None:
    if config.FORUM_GROUP_ID and int(chat_id) == config.FORUM_GROUP_ID:
        return
    now = time.monotonic()
    last_seen = _discovered_chat_cache.get(chat_id)
    if last_seen is not None and now - last_seen < _DISCOVERED_CACHE_TTL:
        return
    if len(_discovered_chat_cache) >= 2000:
        for cid in [k for k, ts in _discovered_chat_cache.items() if now - ts >= _DISCOVERED_CACHE_TTL][:1000]:
            _discovered_chat_cache.pop(cid, None)
        if len(_discovered_chat_cache) >= 2000:
            _discovered_chat_cache.clear()
    _discovered_chat_cache[chat_id] = now
    await db.record_discovered_tg_chat(chat_id, title, username)


def _keyword_hits(text: str, keywords: Iterable[str]) -> list[str]:
    normalized = (text or "").lower()
    if not normalized:
        return []
    return [keyword for keyword in keywords if keyword and keyword.lower() in normalized]


def _message_text(message) -> str:
    text = getattr(message, "text", None) or getattr(message, "caption", None) or ""
    if text:
        return str(text)
    return ""


def _message_link(chat_username: str | None, chat_id: int, message_id: int) -> str:
    if chat_username:
        return f"https://t.me/{chat_username}/{message_id}"
    chat_text = str(chat_id)
    if chat_text.startswith("-100") and len(chat_text) > 4:
        return f"https://t.me/c/{chat_text[4:]}/{message_id}"
    return f"chat_id={chat_id} message_id={message_id}"


def _fingerprint(message, monitor: dict, hits: list[str]) -> str:
    payload = "|".join(
        [
            str(monitor.get("id")),
            str(getattr(message.chat, "id", "")),
            str(getattr(getattr(message, "from_user", None), "id", "")),
            ",".join(sorted(hits)),
            _message_text(message)[:300],
        ]
    )
    return hashlib.sha256(payload.encode("utf-8", errors="ignore")).hexdigest()


def _format_sender(message) -> str:
    sender = getattr(message, "from_user", None)
    if not sender:
        return "未知"
    names = [
        str(getattr(sender, "first_name", "") or "").strip(),
        str(getattr(sender, "last_name", "") or "").strip(),
    ]
    full_name = " ".join(part for part in names if part) or str(getattr(sender, "id", "未知"))
    username = str(getattr(sender, "username", "") or "").strip()
    return f"{full_name} (@{username})" if username else full_name


def format_monitor_notification(message, monitor: dict, hits: list[str]) -> str:
    chat_title = str(getattr(message.chat, "title", "") or getattr(message.chat, "id", ""))
    chat_id = int(getattr(message.chat, "id"))
    message_id = int(getattr(message, "message_id", 0) or 0)
    text = _message_text(message).strip()
    if len(text) > SUMMARY_MAX_CHARS:
        text = text[:SUMMARY_MAX_CHARS] + "..."

    link = _message_link(
        str(getattr(message.chat, "username", "") or "") or None,
        chat_id,
        message_id,
    )
    return (
        f"<b>[TG 关键词命中] {html.escape(str(monitor.get('name') or chat_title))}</b>\n"
        f"群/频道：{html.escape(chat_title)} (<code>{chat_id}</code>)\n"
        f"发送者：{html.escape(_format_sender(message))}\n"
        f"命中：{html.escape(', '.join(hits))}\n"
        f"来源：{html.escape(str(monitor.get('listen_source') or 'user_session'))}\n"
        f"链接：{html.escape(link)}\n"
        f"内容：\n{html.escape(text or '(非文本消息)')}"
    )


def _notify_chat_ids(monitor: dict) -> list[int]:
    default = config.TG_MONITOR_NOTIFY_CHAT_IDS or config.ADMIN_IDS
    return monitor_push.resolve_targets(monitor, default)


async def _notify_hit(application: Application, monitor: dict, message, hits: list[str]) -> int:
    """推送一次命中并记录结果；关闭了机器人推送时只记录。"""
    sent_count = 0
    if monitor.get("notify_telegram", True):
        sent_count = await monitor_push.push(
            application.bot,
            _notify_chat_ids(monitor),
            format_monitor_notification(message, monitor, hits),
        )
    await db.record_monitor_event(
        "tg",
        monitor.get("name") or "",
        ", ".join(hits),
        _message_link(
            str(getattr(message.chat, "username", "") or "") or None,
            int(getattr(message.chat, "id")),
            int(getattr(message, "message_id", 0) or 0),
        ),
        _message_text(message).strip()[:SUMMARY_MAX_CHARS],
        sent_count,
    )
    return sent_count


async def _match_monitors(message, listen_source: str) -> list[MonitorHit]:
    chat_id = int(getattr(message.chat, "id"))
    monitors = await db.get_tg_group_monitors_for_chat(chat_id, listen_source)
    if not monitors:
        return []

    text = _message_text(message)
    if not text:
        return []

    matched: list[MonitorHit] = []
    for monitor in monitors:
        exclude_hits = _keyword_hits(text, monitor.get("exclude_keywords") or [])
        if exclude_hits:
            continue
        keywords = monitor.get("keywords") or []
        if not keywords:
            # 关键词留空表示匹配该群/频道的全部文本消息（与 Web UI「留空表示全部」一致）
            hits = ["(全部消息)"]
        else:
            hits = _keyword_hits(text, keywords)
            if not hits:
                continue
        matched.append(MonitorHit(monitor=monitor, hits=hits, text=text))
    return matched


async def _match_monitor(message, listen_source: str) -> MonitorHit | None:
    hits = await _match_monitors(message, listen_source)
    return hits[0] if hits else None


async def handle_bot_group_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    message = update.effective_message
    if not message or not message.chat:
        return False

    # 跳过机器人自己的论坛话题群，且内存 TTL 去重以减少高频群聊 DB 写入
    await _record_discovered_chat_cached(
        int(message.chat.id),
        str(getattr(message.chat, "title", "") or message.chat.id),
        str(getattr(message.chat, "username", "") or ""),
    )

    hits = await _match_monitors(message, "bot")
    if not hits:
        return False

    handled_any = False
    for hit in hits:
        fp = _fingerprint(message, hit.monitor, hit.hits)
        allowed, reason = await db.tg_monitor_allow_send(
            hit.monitor["id"],
            fp,
            hit.monitor.get("min_interval_seconds") or 0,
            hit.monitor.get("dedupe_window_seconds") or 0,
            time.time(),
        )
        if not allowed:
            logger.info("TG bot 监听跳过 monitor=%s reason=%s", hit.monitor.get("name"), reason)
            continue

        sent_count = await _notify_hit(context.application, hit.monitor, message, hit.hits)
        await db.record_runtime_status(
            f"tg:{hit.monitor['name']}",
            "tg_monitor",
            True,
            sent_count=sent_count,
        )
        handled_any = True
    return handled_any or bool(hits)


async def _build_pseudo_message(event: Any) -> Any | None:
    raw_message = getattr(event, "message", None)
    if raw_message is None:
        return None
    try:
        chat_id = int(getattr(event, "chat_id"))
    except Exception:
        return None

    text = str(getattr(raw_message, "text", "") or getattr(raw_message, "message", "") or "").strip()
    if not text:
        return None

    chat = getattr(event, "chat", None)
    if chat is None and callable(getattr(event, "get_chat", None)):
        try:
            chat = await event.get_chat()
        except Exception:
            chat = None

    sender = getattr(event, "sender", None)
    if sender is None and callable(getattr(event, "get_sender", None)):
        try:
            sender = await event.get_sender()
        except Exception:
            sender = None

    sender_id = getattr(event, "sender_id", None)
    if sender_id is None and sender is not None:
        sender_id = getattr(sender, "id", None)

    return SimpleNamespace(
        chat=SimpleNamespace(
            id=chat_id,
            title=str(getattr(chat, "title", "") or getattr(chat, "first_name", "") or chat_id),
            username=str(getattr(chat, "username", "") or ""),
        ),
        from_user=SimpleNamespace(
            id=int(sender_id) if sender_id is not None else 0,
            first_name=str(getattr(sender, "first_name", "") or ""),
            last_name=str(getattr(sender, "last_name", "") or ""),
            username=str(getattr(sender, "username", "") or ""),
        ),
        text=text,
        caption=None,
        message_id=int(getattr(raw_message, "id", 0) or 0),
    )


def _build_telethon_proxy(proxy_url: str):
    proxy_url = (proxy_url or "").strip()
    if not proxy_url:
        return None
    parsed = urlparse(proxy_url)
    if parsed.scheme not in {"socks5", "socks4", "http"}:
        return None
    try:
        import socks
    except ImportError:
        logger.warning("pysocks 未安装，TG_PROXY 将被忽略。")
        return None
    proxy_types = {
        "socks5": socks.SOCKS5,
        "socks4": socks.SOCKS4,
        "http": socks.HTTP,
    }
    proxy_type = parsed.scheme
    host = parsed.hostname
    port = parsed.port
    if not host or not port:
        return None
    username = parsed.username
    password = parsed.password
    return (proxy_types[proxy_type], host, int(port), True, username, password)


async def _handle_user_session_event(application: Application, event: Any) -> None:
    pseudo = await _build_pseudo_message(event)
    if pseudo is None:
        return

    # 跳过机器人自己的论坛话题群，且内存 TTL 去重避免高频写入 SQLite
    await _record_discovered_chat_cached(
        int(pseudo.chat.id),
        str(getattr(pseudo.chat, "title", "") or pseudo.chat.id),
        str(getattr(pseudo.chat, "username", "") or ""),
    )

    hits = await _match_monitors(pseudo, "user_session")
    if not hits:
        return

    for hit in hits:
        fp = _fingerprint(pseudo, hit.monitor, hit.hits)
        allowed, reason = await db.tg_monitor_allow_send(
            hit.monitor["id"],
            fp,
            hit.monitor.get("min_interval_seconds") or 0,
            hit.monitor.get("dedupe_window_seconds") or 0,
            time.time(),
        )
        if not allowed:
            logger.info("TG user_session 监听跳过 monitor=%s reason=%s", hit.monitor.get("name"), reason)
            continue

        start = time.monotonic()
        try:
            sent_count = await _notify_hit(application, hit.monitor, pseudo, hit.hits)
            duration_ms = int((time.monotonic() - start) * 1000)
            await db.record_runtime_status(
                f"tg:{hit.monitor['name']}",
                "tg_monitor",
                True,
                duration_ms=duration_ms,
                sent_count=sent_count,
            )
        except Exception as exc:
            duration_ms = int((time.monotonic() - start) * 1000)
            await db.record_runtime_status(
                f"tg:{hit.monitor['name']}",
                "tg_monitor",
                False,
                duration_ms=duration_ms,
                error=str(exc),
            )
            raise


async def _user_session_loop(application: Application) -> None:
    if TelegramClient is None or StringSession is None or events is None:
        logger.warning("Telethon 未安装，TG 用户会话监听不会启动。")
        return
    if not config.TG_API_ID or not config.TG_API_HASH or not config.TG_API_SESSION:
        logger.info("TG_API_ID/TG_API_HASH/TG_API_SESSION 未完整配置，TG 用户会话监听不会启动。")
        return
    try:
        api_id = int(config.TG_API_ID)
    except ValueError:
        logger.warning("TG_API_ID 必须是整数，TG 用户会话监听不会启动。")
        return

    backoff = 5
    while True:
        proxy = _build_telethon_proxy(config.TG_PROXY)
        client = TelegramClient(
            StringSession(config.TG_API_SESSION),
            api_id,
            config.TG_API_HASH,
            proxy=proxy,
        )
        application.bot_data[TG_MONITOR_CLIENT_KEY] = client

        @client.on(events.NewMessage)  # type: ignore[misc]
        async def on_new_message(event: Any) -> None:
            try:
                await _handle_user_session_event(application, event)
            except Exception:
                logger.exception("处理 TG 用户会话监听消息失败")

        try:
            await client.start()
            backoff = 5
            logger.info("TG 用户会话监听已启动。")
            await db.record_runtime_status("tg:user_session_listener", "tg_monitor", True)
            await client.run_until_disconnected()
            logger.warning("TG 用户会话监听连接已断开，%s 秒后尝试重连。", backoff)
        except asyncio.CancelledError:
            logger.info("TG 用户会话监听已取消。")
            raise
        except Exception as exc:
            logger.exception("TG 用户会话监听异常退出，%s 秒后重连。", backoff)
            await db.record_runtime_status("tg:user_session_listener", "tg_monitor", False, error=str(exc))
        finally:
            try:
                await client.disconnect()
            except Exception:
                logger.exception("断开 TG 用户会话监听失败。")
            if application.bot_data.get(TG_MONITOR_CLIENT_KEY) is client:
                application.bot_data.pop(TG_MONITOR_CLIENT_KEY, None)

        await asyncio.sleep(backoff)
        backoff = min(backoff * 2, 60)


async def start_user_session_listener(application: Application) -> bool:
    if not config.TG_MONITOR_ENABLED:
        return False
    monitors = await db.list_tg_group_monitors(enabled_only=True)
    if not any(item.get("listen_source") == "user_session" for item in monitors):
        return False
    existing = application.bot_data.get(TG_MONITOR_TASK_KEY)
    if existing and not existing.done():
        return False
    task = asyncio.create_task(_user_session_loop(application))
    application.bot_data[TG_MONITOR_TASK_KEY] = task
    return True


async def stop_user_session_listener(application: Application) -> bool:
    task = application.bot_data.pop(TG_MONITOR_TASK_KEY, None)
    client = application.bot_data.pop(TG_MONITOR_CLIENT_KEY, None)
    stopped = False
    if client:
        try:
            await client.disconnect()
            stopped = True
        except Exception:
            logger.exception("断开 TG 用户会话客户端失败。")
    if task and not task.done():
        task.cancel()
        stopped = True
        try:
            await asyncio.wait_for(task, timeout=5.0)
        except (asyncio.CancelledError, asyncio.TimeoutError):
            pass
        except Exception:
            logger.exception("等待 TG 用户会话监听任务结束时出错。")
    return stopped


async def refresh_user_session_listener(application: Application) -> None:
    await stop_user_session_listener(application)
    await start_user_session_listener(application)


def parse_keywords(raw_keywords: str) -> list[str]:
    return _split_words(raw_keywords)


def build_panel_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("查看监听列表", callback_data="panel_tg_monitor_list")],
        [InlineKeyboardButton("发现的群/频道", callback_data="panel_tg_monitor_discovered")],
        [InlineKeyboardButton(copy_text.BTN_PANEL_STATUS, callback_data="panel_monitor_status")],
        [InlineKeyboardButton(copy_text.BTN_BACK_PANEL, callback_data="panel_back")],
    ])


async def build_panel_text() -> str:
    monitors = await db.list_tg_group_monitors()
    enabled = [item for item in monitors if item.get("enabled")]
    user_session_count = len([item for item in enabled if item.get("listen_source") == "user_session"])
    return (
        "TG 群/频道关键词监听\n\n"
        f"监听总数: {len(monitors)}\n"
        f"启用中: {len(enabled)}\n"
        f"用户会话监听: {user_session_count}\n\n"
        "命令小抄:\n"
        "/watch_tg add <名称> <chat_id> <关键词1,关键词2>\n"
        "/watch_tg list\n"
        "/watch_tg on <ID> /watch_tg off <ID>\n"
        "/watch_tg delete <ID>\n"
        "/watch_tg discovered"
    )


async def build_monitor_list_text() -> str:
    monitors = await db.list_tg_group_monitors()
    if not monitors:
        return "当前还没有 TG 群/频道监听。\n\n使用 /watch_tg add <名称> <chat_id> <关键词1,关键词2> 添加。"
    lines = ["TG 监听列表", ""]
    for item in monitors[:40]:
        status = "启用" if item.get("enabled") else "停用"
        keywords = ", ".join(item.get("keywords") or [])
        lines.append(
            f"#{item['id']} {item['name']} [{status}]\n"
            f"  chat_id: {item['chat_id']}\n"
            f"  来源: {item.get('listen_source')}\n"
            f"  关键词: {keywords or '无'}"
        )
    return "\n".join(lines)
