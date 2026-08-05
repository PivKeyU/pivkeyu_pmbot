from __future__ import annotations

import asyncio
import hashlib
import html
import logging
import re
import time
from dataclasses import dataclass
from datetime import datetime
from urllib.parse import urljoin

import aiohttp
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.error import TelegramError
from telegram.ext import Application, ContextTypes

from config import config
from database import models as db

try:
    from bs4 import BeautifulSoup
except Exception:  # pragma: no cover - optional dependency until requirements are installed
    BeautifulSoup = None

logger = logging.getLogger(__name__)

WEB_MONITOR_JOB_KEY = "web_monitor_job"
DEFAULT_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36 pivkeyu-bot/1.0"
)


def _now_sql() -> str:
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")


@dataclass
class WebItem:
    key: str
    title: str
    link: str
    text: str
    price: str = ""
    stock: str = ""

    @property
    def content_hash(self) -> str:
        payload = "|".join([self.title, self.link, self.text, self.price, self.stock])
        return hashlib.sha256(payload.encode("utf-8", errors="ignore")).hexdigest()


def parse_keywords(raw_keywords: str) -> list[str]:
    return [item.strip() for item in (raw_keywords or "").replace(",", "\n").splitlines() if item.strip()]


def _keyword_hits(text: str, keywords: list[str]) -> list[str]:
    normalized = (text or "").lower()
    if not normalized:
        return []
    return [keyword for keyword in keywords if keyword and keyword.lower() in normalized]


def _clean_text(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def _first_text(root, selector: str) -> str:
    if not selector:
        return ""
    try:
        element = root.select_one(selector)
    except Exception:
        return ""
    if not element:
        return ""
    return _clean_text(element.get_text(" ", strip=True))


def _first_link(root, selector: str, base_url: str) -> str:
    if not selector:
        return ""
    try:
        element = root.select_one(selector)
    except Exception:
        return ""
    if not element:
        return ""
    href = element.get("href")
    return urljoin(base_url, href) if href else ""


def _extract_items(html_text: str, monitor: dict) -> list[WebItem]:
    if BeautifulSoup is None:
        raise RuntimeError("beautifulsoup4 未安装，无法解析网页监控内容。")

    soup = BeautifulSoup(html_text, "html.parser")
    item_selector = monitor.get("item_selector") or "article, .thread, .post, li"
    title_selector = monitor.get("title_selector") or "h1, h2, h3, a"
    link_selector = monitor.get("link_selector") or "a"
    price_selector = monitor.get("price_selector") or ""
    stock_selector = monitor.get("stock_selector") or ""
    url = monitor["url"]

    try:
        nodes = soup.select(item_selector)
    except Exception:
        nodes = []
    if not nodes:
        nodes = [soup]

    items = []
    for idx, node in enumerate(nodes[:30]):
        title = _first_text(node, title_selector) or _clean_text(node.get_text(" ", strip=True))[:120] or monitor["name"]
        link = _first_link(node, link_selector, url) or url
        text = _clean_text(node.get_text(" ", strip=True))
        price = _first_text(node, price_selector)
        stock = _first_text(node, stock_selector)
        key_source = link or f"{title}:{idx}"
        key = hashlib.sha256(key_source.encode("utf-8", errors="ignore")).hexdigest()
        items.append(WebItem(key=key, title=title[:200], link=link, text=text[:2000], price=price, stock=stock))
    return items


async def _fetch_html(url: str) -> str:
    timeout = aiohttp.ClientTimeout(total=20)
    headers = {"User-Agent": DEFAULT_UA}
    async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
        async with session.get(url) as response:
            response.raise_for_status()
            return await response.text()


def _build_notification(monitor: dict, item: WebItem, reasons: list[str], hits: list[str]) -> str:
    lines = [
        f"<b>[网页监控] {html.escape(monitor['name'])}</b>",
        f"原因：{html.escape('、'.join(reasons))}",
    ]
    if hits:
        lines.append(f"命中：{html.escape(', '.join(hits))}")
    lines.extend([
        f"标题：{html.escape(item.title)}",
        f"链接：{html.escape(item.link)}",
    ])
    if item.price:
        lines.append(f"价格：{html.escape(item.price)}")
    if item.stock:
        lines.append(f"库存：{html.escape(item.stock)}")
    summary = item.text[:500]
    if summary:
        lines.append("内容：\n" + html.escape(summary))
    return "\n".join(lines)


async def _send_admins(application: Application, text: str) -> int:
    sent = 0
    for chat_id in config.ADMIN_IDS:
        try:
            await application.bot.send_message(
                chat_id=chat_id,
                text=text,
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=True,
            )
            sent += 1
        except TelegramError as exc:
            logger.warning("发送网页监控通知失败 chat_id=%s: %s", chat_id, exc)
    return sent


# 按 monitor_id 区分的运行锁：防止定时轮询与手动触发并发执行同一监控时重复推送
_run_locks: dict[int, asyncio.Lock] = {}
_RUN_LOCK_MAX = 256


def _get_run_lock(monitor_id: int) -> asyncio.Lock:
    """获取指定监控的运行锁；锁表过大时清空重建，防止字典无限增长。"""
    lock = _run_locks.get(monitor_id)
    if lock is None:
        if len(_run_locks) >= _RUN_LOCK_MAX:
            _run_locks.clear()
        lock = asyncio.Lock()
        _run_locks[monitor_id] = lock
    return lock


async def run_monitor(application: Application, monitor: dict) -> int:
    # 非阻塞获取本监控的运行锁：获取失败说明已有实例在跑（定时轮询或手动触发），直接跳过本轮。
    # 注意 timeout 不能为 0：Python 3.11 的 wait_for 对 timeout<=0 会立即判定超时（锁空闲也如此）
    lock = _get_run_lock(monitor["id"])
    try:
        await asyncio.wait_for(lock.acquire(), timeout=0.05)
    except asyncio.TimeoutError:
        logger.info("网页监控 %s 已有实例在运行，跳过本轮。", monitor.get("name"))
        return 0

    start = time.monotonic()
    sent_count = 0
    notified_items = 0  # 按“条目命中数”计数（区别于 sent_count 的按管理员消息数）
    try:
        html_text = await _fetch_html(monitor["url"])
        items = _extract_items(html_text, monitor)
        keywords = monitor.get("keywords") or []
        has_baseline = await db.has_web_monitor_state(monitor["id"])

        for item in items:
            previous = await db.get_web_monitor_state(monitor["id"], item.key)
            hits = _keyword_hits(f"{item.title} {item.text}", keywords)
            reasons = []
            if has_baseline and previous is None and monitor.get("notify_on_new_item"):
                reasons.append("新条目")
            if has_baseline and hits and monitor.get("notify_on_keyword"):
                reasons.append("关键词命中")
            if previous and monitor.get("notify_on_change"):
                if previous.get("content_hash") != item.content_hash:
                    reasons.append("内容变化")
                if item.price and previous.get("price") and previous.get("price") != item.price:
                    reasons.append("价格变化")
                if item.stock and previous.get("stock") and previous.get("stock") != item.stock:
                    reasons.append("库存变化")

            await db.save_web_monitor_state(
                monitor["id"],
                item.key,
                item.title,
                item.link,
                item.content_hash,
                item.price,
                item.stock,
            )

            if reasons and monitor.get("notify_telegram"):
                sent_count += await _send_admins(application, _build_notification(monitor, item, reasons, hits))
                notified_items += 1
                if notified_items >= 5:
                    break

        await db.update_web_monitor(monitor["id"], last_checked_at=_now_sql())
        await db.record_runtime_status(
            f"web:{monitor['name']}",
            "web",
            True,
            duration_ms=int((time.monotonic() - start) * 1000),
            sent_count=sent_count,
        )
        return sent_count
    except Exception as exc:
        logger.exception("网页监控失败 monitor=%s", monitor.get("name"))
        await db.record_runtime_status(
            f"web:{monitor.get('name') or monitor.get('id')}",
            "web",
            False,
            duration_ms=int((time.monotonic() - start) * 1000),
            error=str(exc),
        )
        return 0
    finally:
        lock.release()


async def check_due_monitors(context: ContextTypes.DEFAULT_TYPE) -> None:
    monitors = await db.list_due_web_monitors()
    for monitor in monitors:
        await db.update_web_monitor(monitor["id"], last_checked_at=_now_sql())
        await run_monitor(context.application, monitor)


def setup(app: Application) -> None:
    job = app.bot_data.get(WEB_MONITOR_JOB_KEY)
    if job:
        job.schedule_removal()
    app.bot_data[WEB_MONITOR_JOB_KEY] = app.job_queue.run_repeating(
        check_due_monitors,
        interval=60,
        first=20,
        name="web_monitor_checker",
    )
    logger.info("网页监控检查任务已调度。")


async def build_panel_text() -> str:
    monitors = await db.list_web_monitors()
    enabled = [item for item in monitors if item.get("enabled")]
    return (
        "网页监控\n\n"
        f"监控总数: {len(monitors)}\n"
        f"启用中: {len(enabled)}\n\n"
        "命令小抄:\n"
        "/webmon add <名称> <url> [关键词1,关键词2]\n"
        "/webmon list\n"
        "/webmon on <ID> /webmon off <ID>\n"
        "/webmon run <ID>\n"
        "/webmon delete <ID>"
    )


def build_panel_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("查看网页监控", callback_data="panel_web_monitor_list")],
        [InlineKeyboardButton("运行状态", callback_data="panel_monitor_status_web")],
        [InlineKeyboardButton("回女仆长面板", callback_data="panel_back")],
    ])


async def build_monitor_list_text() -> str:
    monitors = await db.list_web_monitors()
    if not monitors:
        return "当前还没有网页监控。\n\n使用 /webmon add <名称> <url> [关键词1,关键词2] 添加。"
    lines = ["网页监控列表", ""]
    for item in monitors[:40]:
        status = "启用" if item.get("enabled") else "停用"
        keywords = ", ".join(item.get("keywords") or [])
        lines.append(
            f"#{item['id']} {item['name']} [{status}]\n"
            f"  URL: {item['url']}\n"
            f"  间隔: {item['interval_seconds']} 秒\n"
            f"  关键词: {keywords or '无'}"
        )
    return "\n".join(lines)
