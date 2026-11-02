from __future__ import annotations

import asyncio
import hashlib
import html
import ipaddress
import json
import logging
import re
import socket
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import urljoin, urlsplit

import aiohttp
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, ContextTypes

from config import config
from database import models as db
from services import monitor_push
from utils import copy as copy_text

try:
    from bs4 import BeautifulSoup
except Exception:  # pragma: no cover - optional dependency until requirements are installed
    BeautifulSoup = None

logger = logging.getLogger(__name__)

WEB_MONITOR_JOB_KEY = "web_monitor_job"
MAX_REDIRECTS = 5
REDIRECT_STATUSES = {301, 302, 303, 307, 308}
DEFAULT_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36 pivkeyu-bot/1.0"
)


STOCK_STATE_KEY = "__stock__"
STOCK_IN = "in"
STOCK_OUT = "out"
STOCK_UNKNOWN = "unknown"
STOCK_SCOPE_MAX_CHARS = 1500
# 缺货词优先判断：「暂无货」里也有「有货」的字样不会误判，「not in stock」同理
DEFAULT_IN_STOCK_WORDS = (
    "有货", "现货", "有库存", "加入购物车", "立即购买", "立即下单", "马上抢",
    "in stock", "add to cart", "add to bag", "buy now",
)
DEFAULT_OUT_OF_STOCK_WORDS = (
    "缺货", "售罄", "无货", "已抢光", "已售完", "卖光了", "到货通知", "补货中", "暂时无法购买",
    "sold out", "out of stock", "not in stock", "currently unavailable", "notify me",
)
_PRICE_PATTERN = re.compile(r"(?:[¥￥$€£]|RMB|CNY|USD)\s?\d[\d,]*(?:\.\d+)?", re.IGNORECASE)
_SCHEMA_IN_STOCK = ("instock", "limitedavailability", "onlineonly", "instoreonly")
_SCHEMA_OUT_OF_STOCK = ("outofstock", "soldout", "discontinued")


def _now_sql() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


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


@dataclass
class StockResult:
    state: str
    title: str = ""
    price: str = ""
    evidence: str = ""


def _words(raw: str, defaults: tuple[str, ...]) -> list[str]:
    return [word.lower() for word in parse_keywords(raw)] or list(defaults)


def _classify_stock(text: str, in_words: list[str], out_words: list[str]) -> tuple[str, str]:
    normalized = (text or "").lower()
    for word in out_words:
        if word in normalized:
            return STOCK_OUT, word
    for word in in_words:
        if word in normalized:
            return STOCK_IN, word
    return STOCK_UNKNOWN, ""


def _walk_json(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk_json(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_json(child)


def _offer_price(offer: dict) -> str:
    price = offer.get("price") or offer.get("lowPrice")
    if price in (None, ""):
        return ""
    return f"{offer.get('priceCurrency') or ''} {price}".strip()


def _stock_from_structured_data(soup, keyword: str) -> StockResult | None:
    """电商页面常带 schema.org 的商品数据，比页面文字可靠。"""
    states = []
    title = price = ""
    for script in soup.select('script[type="application/ld+json"]'):
        try:
            data = json.loads(script.string or "")
        except ValueError:
            continue
        for node in _walk_json(data):
            offers = node.get("offers")
            if not offers:
                continue
            name = str(node.get("name") or "")
            if keyword and keyword not in name.lower():
                continue
            for offer in offers if isinstance(offers, list) else [offers]:
                if not isinstance(offer, dict):
                    continue
                availability = str(offer.get("availability") or "").lower()
                if any(word in availability for word in _SCHEMA_IN_STOCK):
                    states.append(STOCK_IN)
                elif any(word in availability for word in _SCHEMA_OUT_OF_STOCK):
                    states.append(STOCK_OUT)
                else:
                    continue
                title = title or name
                price = price or _offer_price(offer)
    if not states:
        return None
    state = STOCK_IN if STOCK_IN in states else STOCK_OUT
    return StockResult(state, title, price, "页面的商品结构化数据")


def _product_scope(root, keyword: str, in_words: list[str], out_words: list[str]) -> str | None:
    """找到商品名所在的区域：从商品名往外扩，直到碰到库存字样；太大就停。"""
    fallback = None
    for text_node in root.find_all(string=True):
        if keyword not in str(text_node).lower():
            continue
        element = text_node.parent
        for _ in range(8):
            if element is None:
                break
            text = _clean_text(element.get_text(" ", strip=True))
            if len(text) > STOCK_SCOPE_MAX_CHARS:
                break
            fallback = fallback or text
            if _classify_stock(text, in_words, out_words)[0] != STOCK_UNKNOWN:
                return text
            element = element.parent
    return fallback


def detect_stock(html_text: str, monitor: dict) -> StockResult:
    if BeautifulSoup is None:
        raise RuntimeError("beautifulsoup4 未安装，无法解析网页监控内容。")

    soup = BeautifulSoup(html_text, "html.parser")
    keyword = (monitor.get("product_keyword") or "").strip().lower()
    selector = (monitor.get("stock_selector") or "").strip()
    in_words = _words(monitor.get("in_stock_words"), DEFAULT_IN_STOCK_WORDS)
    out_words = _words(monitor.get("out_of_stock_words"), DEFAULT_OUT_OF_STOCK_WORDS)

    structured = None if selector else _stock_from_structured_data(soup, keyword)
    for tag in soup(["script", "style", "noscript", "template"]):
        tag.decompose()
    root = soup.body or soup
    title = (structured.title if structured else "") or _first_text(root, "h1") or monitor.get("name", "")
    price = _first_text(root, monitor.get("price_selector") or "")

    if structured:
        structured.title = title
        structured.price = price or structured.price
        return structured

    if selector:
        scope = _first_text(root, selector)
        if not scope:
            return StockResult(STOCK_UNKNOWN, title, price, f"页面里找不到库存选择器 {selector}")
    elif keyword:
        scope = _product_scope(root, keyword, in_words, out_words)
        if not scope:
            return StockResult(STOCK_UNKNOWN, title, price, f"页面里没有商品关键词「{keyword}」")
    else:
        scope = _clean_text(root.get_text(" ", strip=True))

    state, word = _classify_stock(scope, in_words, out_words)
    if not price:
        found = _PRICE_PATTERN.search(scope)
        price = found.group(0) if found else ""
    evidence = f"页面文字「{word}」" if word else "页面里没有有货或缺货字样，可以填库存选择器或自定义字样"
    return StockResult(state, title, price, evidence)


async def _assert_public_url(url: str) -> None:
    """拒绝内网、回环等非公网地址，防止监控地址被用来探测服务器内部服务。"""
    parsed = urlsplit(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ValueError(f"网页监控地址不合法: {url}")
    loop = asyncio.get_running_loop()
    infos = await loop.getaddrinfo(parsed.hostname, parsed.port or 443, type=socket.SOCK_STREAM)
    for info in infos:
        address = ipaddress.ip_address(info[4][0].split("%")[0])
        address = getattr(address, "ipv4_mapped", None) or address
        if not address.is_global:
            raise ValueError(f"网页监控不允许访问内网地址: {parsed.hostname}")


async def _fetch_html(url: str) -> str:
    timeout = aiohttp.ClientTimeout(total=20)
    headers = {"User-Agent": DEFAULT_UA}
    async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
        # 手动跟随重定向：公网地址跳到内网时，每一跳都要重新检查
        for _ in range(MAX_REDIRECTS + 1):
            await _assert_public_url(url)
            async with session.get(url, allow_redirects=False) as response:
                location = response.headers.get("Location")
                if response.status in REDIRECT_STATUSES and location:
                    url = urljoin(str(response.url), location)
                    continue
                response.raise_for_status()
                return await response.text(errors="replace")
    raise ValueError(f"网页监控重定向次数过多: {url}")


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


def _build_restock_notification(monitor: dict, result: StockResult) -> str:
    lines = [
        f"<b>[补货提醒] {html.escape(monitor['name'])}</b>",
        f"商品：{html.escape(result.title or monitor['name'])}",
    ]
    if result.price:
        lines.append(f"价格：{html.escape(result.price)}")
    lines.append(f"判断依据：{html.escape(result.evidence)}")
    lines.append(f"链接：{html.escape(monitor['url'])}")
    return "\n".join(lines)


async def preview_stock(monitor: dict) -> StockResult:
    """保存前试一次，看配置能不能判断出库存状态。"""
    return detect_stock(await _fetch_html(monitor["url"]), monitor)


async def _check_stock(application: Application, monitor: dict, html_text: str) -> int:
    """只有从缺货变成有货才推送；看不出状态时保留上次结果，防止页面抖动造成误报。"""
    result = detect_stock(html_text, monitor)
    if result.state == STOCK_UNKNOWN:
        await db.update_web_monitor(monitor["id"], last_error=result.evidence)
        return 0

    previous = await db.get_web_monitor_state(monitor["id"], STOCK_STATE_KEY)
    last_state = previous.get("stock") if previous else None
    await db.save_web_monitor_state(
        monitor["id"], STOCK_STATE_KEY, result.title, monitor["url"], "", result.price, result.state,
    )
    await db.update_web_monitor(monitor["id"], last_error="")
    if last_state is None or last_state == result.state:
        return 0

    sent_count = 0
    restocked = result.state == STOCK_IN
    if restocked and monitor.get("notify_telegram"):
        keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("去看看", url=monitor["url"])]])
        sent_count = await monitor_push.push(
            application.bot,
            monitor_push.resolve_targets(monitor, config.ADMIN_IDS),
            _build_restock_notification(monitor, result),
            keyboard,
        )
    await db.record_monitor_event(
        "stock",
        monitor["name"],
        "已补货" if restocked else "已售罄",
        monitor["url"],
        " · ".join(part for part in (result.title, result.price, result.evidence) if part),
        sent_count,
    )
    return sent_count


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
        if monitor.get("monitor_type") == "stock":
            sent_count = await _check_stock(application, monitor, html_text)
            await db.update_web_monitor(monitor["id"], last_checked_at=_now_sql())
            await db.record_runtime_status(
                f"stock:{monitor['name']}",
                "web",
                True,
                duration_ms=int((time.monotonic() - start) * 1000),
                sent_count=sent_count,
            )
            return sent_count

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

            if reasons:
                pushed = 0
                if monitor.get("notify_telegram"):
                    pushed = await monitor_push.push(
                        application.bot,
                        monitor_push.resolve_targets(monitor, config.ADMIN_IDS),
                        _build_notification(monitor, item, reasons, hits),
                    )
                await db.record_monitor_event(
                    "web", monitor["name"], item.title, item.link, "、".join(reasons), pushed,
                )
                sent_count += pushed
                notified_items += 1
                if notified_items >= 5:
                    break

        await db.update_web_monitor(monitor["id"], last_checked_at=_now_sql(), last_error="")
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
        await db.update_web_monitor(monitor["id"], last_error=str(exc)[:300])
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
        "/watch_web add <名称> <url> [关键词1,关键词2]\n"
        "/watch_web list\n"
        "/watch_web on <ID> /watch_web off <ID>\n"
        "/watch_web run <ID>\n"
        "/watch_web delete <ID>"
    )


def build_panel_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("查看网页监控", callback_data="panel_web_monitor_list")],
        [InlineKeyboardButton(copy_text.BTN_PANEL_STATUS, callback_data="panel_monitor_status_web")],
        [InlineKeyboardButton(copy_text.BTN_BACK_PANEL, callback_data="panel_back")],
    ])


async def build_monitor_list_text() -> str:
    monitors = await db.list_web_monitors()
    if not monitors:
        return "当前还没有网页监控。\n\n使用 /watch_web add <名称> <url> [关键词1,关键词2] 添加。"
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
