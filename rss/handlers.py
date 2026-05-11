import logging
import asyncio
from urllib.parse import urlparse
from typing import Optional, Dict, Any
from telegram import Update
from telegram.ext import ContextTypes
from config import config
from . import data_manager, settings as rss_settings
from .auth import is_authorized

logger = logging.getLogger(__name__)


def _get_message(update: Update):
    return update.effective_message


def _get_data_file(context: ContextTypes.DEFAULT_TYPE) -> str:
    if context and context.application:
        return context.application.bot_data.get("rss_data_file", config.RSS_DATA_FILE)
    return config.RSS_DATA_FILE


async def _ensure_access(update: Update):
    message = _get_message(update)
    if not message:
        return None

    user = update.effective_user
    user_id = user.id if user else None

    if not is_authorized(user_id):
        await message.reply_text("主人还没有 RSS 茶点间的通行证哦。")
        return None

    if not rss_settings.is_enabled():
        await message.reply_text("RSS 女仆正在休息，请联系管理员女仆长在 /panel → RSS 订阅茶点管理 中唤醒。")
        return None

    return message


def is_valid_url(url_string: str) -> bool:
    try:
        result = urlparse(url_string)
        return all([result.scheme, result.netloc])
    except ValueError:
        return False


def get_chat_id(update: Update) -> str:
    chat = update.effective_chat
    return str(chat.id) if chat else ""


def ensure_user_data(chat_id: str, subscriptions_data: Dict[str, Any]) -> None:
    if chat_id not in subscriptions_data:
        subscriptions_data[chat_id] = {
            "rss_feeds": {},
            "custom_footer": None,
            "link_preview_enabled": True,
        }
    else:
        subscriptions_data[chat_id].setdefault("rss_feeds", {})
        subscriptions_data[chat_id].setdefault("custom_footer", None)
        subscriptions_data[chat_id].setdefault("link_preview_enabled", True)


def find_feed_by_identifier(
    feed_identifier: str,
    feeds: Dict[str, Any],
) -> Optional[str]:
    if feed_identifier.isdigit():
        feed_index = int(feed_identifier) - 1
        feed_list = list(feeds.keys())
        if 0 <= feed_index < len(feed_list):
            return feed_list[feed_index]

    if feed_identifier in feeds:
        return feed_identifier

    return None


async def add_feed(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = await _ensure_access(update)
    if not message:
        return

    chat_id = get_chat_id(update)
    if not context.args:
        await message.reply_text("主人，请给女仆 RSS 链接。女仆小抄: /rss_add <链接>")
        return

    feed_url = context.args[0]
    if not is_valid_url(feed_url):
        await message.reply_text(f"这条链接 '{feed_url}' 看起来不太对，主人检查一下再交给女仆吧。")
        return

    subscriptions_data = data_manager.get_subscriptions()
    ensure_user_data(chat_id, subscriptions_data)

    if feed_url in subscriptions_data[chat_id]["rss_feeds"]:
        await message.reply_text(f"这份茶点 {feed_url} 已经在主人的订阅单里啦。")
        return

    if hasattr(asyncio, "to_thread"):
        feed_title = await asyncio.to_thread(data_manager.get_feed_title, feed_url) or "未命名茶点"
    else:
        loop = asyncio.get_event_loop()
        feed_title = await loop.run_in_executor(None, data_manager.get_feed_title, feed_url) or "未命名茶点"

    subscriptions_data[chat_id]["rss_feeds"][feed_url] = {
        "title": feed_title,
        "keywords": [],
        "last_entry_id": None,
    }
    data_manager.save_subscriptions(_get_data_file(context))

    reply_message_text = f"茶点 '{feed_title}' ({feed_url}) 已端上订阅单啦。"
    await message.reply_text(reply_message_text)
    logger.info("用户 %s 添加了订阅源: %s", chat_id, feed_url)


async def list_feeds(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = await _ensure_access(update)
    if not message:
        return

    chat_id = get_chat_id(update)
    subscriptions_data = data_manager.get_subscriptions()

    feeds = subscriptions_data.get(chat_id, {}).get("rss_feeds", {})
    if not feeds:
        await message.reply_text("主人还没有任何 RSS 茶点。使用 /rss_add <链接> 添加一份吧。")
        return

    message_content = "主人当前的 RSS 茶点单:\n"
    for idx, (url, data) in enumerate(feeds.items(), 1):
        title = data.get("title", "N/A")
        keywords_list = data.get("keywords", [])
        keywords_str = f" (口味词: {', '.join(keywords_list)})" if keywords_list else ""
        message_content += f"{idx}. {title} - {url}{keywords_str}\n"

    await message.reply_text(message_content)


async def remove_feed(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = await _ensure_access(update)
    if not message:
        return

    chat_id = get_chat_id(update)

    if not context.args:
        await message.reply_text("主人，请告诉女仆要撤下的 RSS 链接或 ID（来自 /rss_list）。女仆小抄: /rss_remove <链接或ID>")
        return

    identifier = context.args[0]
    subscriptions_data = data_manager.get_subscriptions()
    feeds = subscriptions_data.get(chat_id, {}).get("rss_feeds", {})

    if not feeds:
        await message.reply_text("主人还没有可以撤下的 RSS 茶点。")
        return

    feed_to_remove = find_feed_by_identifier(identifier, feeds)

    if feed_to_remove:
        removed_title = feeds[feed_to_remove].get("title", feed_to_remove)
        del subscriptions_data[chat_id]["rss_feeds"][feed_to_remove]

        if not subscriptions_data[chat_id]["rss_feeds"]:
            del subscriptions_data[chat_id]

        data_manager.save_subscriptions(_get_data_file(context))
        reply_message_text = f"茶点 '{removed_title}' 已经撤下啦。"
        logger.info("用户 %s 移除了订阅源: %s", chat_id, feed_to_remove)
    else:
        reply_message_text = f"女仆没找到标识符为 '{identifier}' 的茶点。请用 /rss_list 查看茶点 ID/链接。"

    await message.reply_text(reply_message_text)


async def add_keyword(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = await _ensure_access(update)
    if not message:
        return

    chat_id = get_chat_id(update)

    if len(context.args) < 2:
        await message.reply_text("女仆小抄: /rss_addkeyword <RSS链接或ID> <口味词>")
        return

    feed_identifier = context.args[0]
    keyword_to_add = " ".join(context.args[1:]).lower()
    subscriptions_data = data_manager.get_subscriptions()
    feeds = subscriptions_data.get(chat_id, {}).get("rss_feeds", {})

    if not feeds:
        await message.reply_text("主人还没有能添加口味词的 RSS 茶点。")
        return

    target_feed_url = find_feed_by_identifier(feed_identifier, feeds)

    if not target_feed_url:
        await message.reply_text(f"女仆没找到标识符为 '{feed_identifier}' 的茶点。请用 /rss_list 查看。")
        return

    feed_data = subscriptions_data[chat_id]["rss_feeds"][target_feed_url]
    feed_data.setdefault("keywords", [])

    if keyword_to_add in feed_data["keywords"]:
        feed_title = feed_data.get("title", target_feed_url)
        await message.reply_text(f"口味词 '{keyword_to_add}' 已经在 '{feed_title}' 里啦。")
    else:
        feed_data["keywords"].append(keyword_to_add)
        data_manager.save_subscriptions(_get_data_file(context))
        feed_title = feed_data.get("title", target_feed_url)
        await message.reply_text(f"口味词 '{keyword_to_add}' 已加入 '{feed_title}'。")
        logger.info("用户 %s 向订阅源 %s 添加了关键词 '%s'", chat_id, target_feed_url, keyword_to_add)


async def remove_keyword(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = await _ensure_access(update)
    if not message:
        return

    chat_id = get_chat_id(update)

    if len(context.args) < 2:
        await message.reply_text("女仆小抄: /rss_removekeyword <RSS链接或ID> <口味词>")
        return

    feed_identifier = context.args[0]
    keyword_to_remove = " ".join(context.args[1:]).lower()
    subscriptions_data = data_manager.get_subscriptions()
    feeds = subscriptions_data.get(chat_id, {}).get("rss_feeds", {})

    if not feeds:
        await message.reply_text("主人还没有能删除口味词的 RSS 茶点。")
        return

    target_feed_url = find_feed_by_identifier(feed_identifier, feeds)

    if not target_feed_url:
        await message.reply_text(f"女仆没找到标识符为 '{feed_identifier}' 的茶点。请用 /rss_list 查看。")
        return

    feed_data = subscriptions_data[chat_id]["rss_feeds"][target_feed_url]
    feed_title = feed_data.get("title", target_feed_url)

    if keyword_to_remove in feed_data.get("keywords", []):
        feed_data["keywords"].remove(keyword_to_remove)
        data_manager.save_subscriptions(_get_data_file(context))
        await message.reply_text(f"口味词 '{keyword_to_remove}' 已从 '{feed_title}' 删除。")
        logger.info("用户 %s 从订阅源 %s 移除了关键词 '%s'", chat_id, target_feed_url, keyword_to_remove)
    else:
        await message.reply_text(f"女仆没在 '{feed_title}' 里找到口味词 '{keyword_to_remove}'。")


async def list_keywords(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = await _ensure_access(update)
    if not message:
        return

    chat_id = get_chat_id(update)

    if not context.args:
        await message.reply_text("女仆小抄: /rss_listkeywords <RSS链接或ID>")
        return

    feed_identifier = context.args[0]
    subscriptions_data = data_manager.get_subscriptions()
    feeds = subscriptions_data.get(chat_id, {}).get("rss_feeds", {})

    if not feeds:
        await message.reply_text("主人还没有任何 RSS 茶点。")
        return

    target_feed_url = find_feed_by_identifier(feed_identifier, feeds)

    if not target_feed_url:
        await message.reply_text(f"女仆没找到标识符为 '{feed_identifier}' 的茶点。请用 /rss_list 查看。")
        return

    feed_data = subscriptions_data[chat_id]["rss_feeds"][target_feed_url]
    keywords = feed_data.get("keywords", [])
    title = feed_data.get("title", target_feed_url)

    if keywords:
        reply_message_text = f"'{title}' 的口味词:\n- " + "\n- ".join(keywords)
    else:
        reply_message_text = f"'{title}' 还没设置口味词，会端上所有新条目。"

    await message.reply_text(reply_message_text)


async def remove_all_keywords(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = await _ensure_access(update)
    if not message:
        return

    chat_id = get_chat_id(update)

    if not context.args:
        await message.reply_text("女仆小抄: /rss_removeallkeywords <RSS链接或ID>")
        return

    feed_identifier = context.args[0]
    subscriptions_data = data_manager.get_subscriptions()
    feeds = subscriptions_data.get(chat_id, {}).get("rss_feeds", {})

    if not feeds:
        await message.reply_text("主人还没有任何 RSS 茶点。")
        return

    target_feed_url = find_feed_by_identifier(feed_identifier, feeds)

    if not target_feed_url:
        await message.reply_text(f"女仆没找到标识符为 '{feed_identifier}' 的茶点。请用 /rss_list 查看。")
        return

    feed_data = subscriptions_data[chat_id]["rss_feeds"][target_feed_url]
    feed_title = feed_data.get("title", target_feed_url)

    if feed_data.get("keywords"):
        feed_data["keywords"] = []
        data_manager.save_subscriptions(_get_data_file(context))
        await message.reply_text(f"茶点 '{feed_title}' 的所有口味词都已清空。")
        logger.info("用户 %s 移除了订阅源 %s 的所有关键词。", chat_id, target_feed_url)
    else:
        await message.reply_text(f"茶点 '{feed_title}' 原本就没有口味词。")


async def set_custom_footer(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = await _ensure_access(update)
    if not message:
        return

    chat_id = get_chat_id(update)
    subscriptions_data = data_manager.get_subscriptions()
    ensure_user_data(chat_id, subscriptions_data)

    footer_text = " ".join(context.args) if context.args else None
    subscriptions_data[chat_id]["custom_footer"] = footer_text
    data_manager.save_subscriptions(_get_data_file(context))

    if footer_text:
        reply_message_text = f"RSS 小尾巴已系好:\n{footer_text}"
    else:
        reply_message_text = "RSS 小尾巴已解下。"

    logger.info("用户 %s 将自定义页脚设置为: '%s'", chat_id, footer_text)
    await message.reply_text(reply_message_text)


async def toggle_link_preview(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = await _ensure_access(update)
    if not message:
        return

    chat_id = get_chat_id(update)
    subscriptions_data = data_manager.get_subscriptions()
    ensure_user_data(chat_id, subscriptions_data)

    current_status = subscriptions_data[chat_id].get("link_preview_enabled", True)
    new_status = not current_status
    subscriptions_data[chat_id]["link_preview_enabled"] = new_status
    data_manager.save_subscriptions(_get_data_file(context))

    status_text = "开启" if new_status else "关闭"
    reply_message_text = f"链接预览已切换为: {status_text}。"

    logger.info("用户 %s 将链接预览切换为: %s", chat_id, status_text)
    await message.reply_text(reply_message_text)


async def add_authorized_user(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = _get_message(update)
    if not message:
        return

    user = update.effective_user
    if not user or user.id not in config.ADMIN_IDS:
        await message.reply_text("只有管理员女仆长可以登记 RSS 授权主人。")
        return

    if not context.args:
        await message.reply_text("女仆小抄: /rss_add_user <user_id>")
        return

    try:
        target_id = int(context.args[0])
    except ValueError:
        await message.reply_text("主人，请输入有效的用户 ID（整数）。")
        return

    added = rss_settings.add_authorized_user(target_id)
    if added:
        await message.reply_text(f"已把用户 {target_id} 登记进 RSS 授权名单。")
        logger.info("管理员 %s 添加了 RSS 授权用户 %s", user.id, target_id)
    else:
        await message.reply_text(f"用户 {target_id} 已经在 RSS 授权名单里啦。")


async def remove_authorized_user(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = _get_message(update)
    if not message:
        return

    user = update.effective_user
    if not user or user.id not in config.ADMIN_IDS:
        await message.reply_text("只有管理员女仆长可以移除 RSS 授权主人。")
        return

    if not context.args:
        await message.reply_text("女仆小抄: /rss_rm_user <user_id>")
        return

    try:
        target_id = int(context.args[0])
    except ValueError:
        await message.reply_text("主人，请输入有效的用户 ID（整数）。")
        return

    removed = rss_settings.remove_authorized_user(target_id)
    if removed:
        await message.reply_text(f"已把用户 {target_id} 从 RSS 授权名单移除。")
        logger.info("管理员 %s 移除了 RSS 授权用户 %s", user.id, target_id)
    else:
        await message.reply_text(f"用户 {target_id} 不在 RSS 授权名单里。")


COMMAND_MAP = {
    "rss_add": add_feed,
    "rss_remove": remove_feed,
    "rss_list": list_feeds,
    "rss_addkeyword": add_keyword,
    "rss_removekeyword": remove_keyword,
    "rss_listkeywords": list_keywords,
    "rss_removeallkeywords": remove_all_keywords,
    "rss_setfooter": set_custom_footer,
    "rss_togglepreview": toggle_link_preview,
    "rss_add_user": add_authorized_user,
    "rss_rm_user": remove_authorized_user,
}

