import logging
import re
import secrets
from pathlib import Path
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import BadRequest
from telegram.ext import ContextTypes
from services.verification import verify_answer, create_verification
from services.gemini_service import gemini_service
from database import models as db
from utils.media_converter import sticker_to_image
from services.thread_manager import get_or_create_thread, build_user_info_card_keyboard
from services import broadcast as broadcast_service, safe_update, spam_filter, tg_monitor, web_monitor
from services import image_update
from .user_handler import _resend_message
from config import config
from rss import data_manager as rss_data_manager, settings as rss_settings
from rss import enable_feature as rss_enable_feature, disable_feature as rss_disable_feature
from utils import copy as copy_text

RSS_PANEL_CACHE_KEY = "rss_panel_cache"
RSS_FEEDS_PER_PAGE = 4
AI_MODELS_PER_PAGE = 5
RSS_DOC_URL = "https://github.com/Hamster-Prime/Telegram_Anti-harassment_two-way_chatbot#-rss-%E8%AE%A2%E9%98%85%E5%8A%9F%E8%83%BD"


def _cache_rss_reference(application, kind, payload):
    token = secrets.token_hex(6)
    cache = application.bot_data.setdefault(RSS_PANEL_CACHE_KEY, {})
    if len(cache) >= 500:
        cache.clear()
    cache[token] = (kind, payload)
    return token


def _resolve_rss_reference(application, token, expected_kind):
    cache = application.bot_data.get(RSS_PANEL_CACHE_KEY, {})
    value = cache.get(token)
    if not value:
        return None
    kind, payload = value
    if kind != expected_kind:
        return None
    return payload


async def _refresh_usercard_keyboard(query, target_user_id: int):
    keyboard = await build_user_info_card_keyboard(target_user_id)

    try:
        await query.edit_message_reply_markup(reply_markup=keyboard)
    except BadRequest as exc:
        if "message is not modified" not in exc.message.lower():
            raise


async def _build_panel_back_view():
    total_users = await db.get_total_users_count()
    blocked_users = await db.get_blocked_users_count()
    exempted_users = await db.get_exemptions_count()
    is_enabled = await db.get_autoreply_enabled()

    message = (
        f"女仆长管理面板\n\n"
        f"宅邸统计:\n\n"
        f"接待过的主人: {total_users}\n"
        f"黑名单里的捣乱者: {blocked_users}\n"
        f"持有通行证的主人: {exempted_users}\n"
        f"自动回复女仆: {'正在值班' if is_enabled else '正在休息'}\n\n"
        f"主人，请挑选要打理的事项："
    )

    keyboard = [
        [InlineKeyboardButton(copy_text.BTN_PANEL_BLACKLIST, callback_data="panel_blacklist_page_1"), InlineKeyboardButton(copy_text.BTN_PANEL_STATS, callback_data="panel_stats")],
        [InlineKeyboardButton(copy_text.BTN_PANEL_FILTERED, callback_data="panel_filtered_page_1"), InlineKeyboardButton(copy_text.BTN_PANEL_AUTOREPLY, callback_data="panel_autoreply")],
        [InlineKeyboardButton(copy_text.BTN_PANEL_EXEMPTIONS, callback_data="panel_exemptions_page_1"), InlineKeyboardButton(copy_text.BTN_PANEL_NETWORK_TEST, callback_data="panel_network_test")],
        [InlineKeyboardButton(copy_text.BTN_PANEL_BROADCAST, callback_data="panel_broadcast"), InlineKeyboardButton(copy_text.BTN_PANEL_RSS, callback_data="panel_rss")],
        [InlineKeyboardButton(copy_text.BTN_PANEL_TG_MONITOR, callback_data="panel_tg_monitor"), InlineKeyboardButton(copy_text.BTN_PANEL_WEB_MONITOR, callback_data="panel_web_monitor")],
        [InlineKeyboardButton(copy_text.BTN_PANEL_SPAMRULES, callback_data="panel_spamrules")],
        [InlineKeyboardButton(copy_text.BTN_PANEL_STATUS, callback_data="panel_monitor_status"), InlineKeyboardButton(copy_text.BTN_PANEL_UPDATEBOT, callback_data="panel_updatebot")],
        [InlineKeyboardButton(copy_text.BTN_PANEL_AI_SETTINGS, callback_data="panel_ai_settings")],
    ]
    return message, InlineKeyboardMarkup(keyboard)


def _collect_rss_feeds():
    entries = []
    subscriptions = rss_data_manager.get_subscriptions()
    for chat_id, user_data in subscriptions.items():
        feeds = user_data.get("rss_feeds", {})
        for feed_url, feed_data in feeds.items():
            entries.append((chat_id, feed_url, feed_data))
    entries.sort(key=lambda item: (item[0], item[2].get("title", "")))
    return entries


def _build_rss_panel_view():
    enabled = rss_settings.is_enabled()
    status_text = "正在值班" if enabled else "正在休息"
    lines = [
        "RSS 订阅茶点控制台",
        "",
        f"当前状态: {status_text}",
        f"数据小柜: {rss_settings.get_data_file()}",
        f"巡查间隔: {rss_settings.get_check_interval()} 秒",
        "",
        "常用命令（请在私聊吩咐女仆）：",
        "/rss_add <url>",
        "/rss_remove <url|ID>",
        "/rss_list",
        "/rss_addkeyword <ID> <关键词>",
        "/rss_removekeyword <ID> <关键词>",
        "/rss_listkeywords <ID>",
        "/rss_removeallkeywords <ID>",
        "/rss_setfooter [文本]",
        "/rss_togglepreview",
    ]

    keyboard = [
        [
            InlineKeyboardButton(
                "让 RSS 女仆休息" if enabled else "让 RSS 女仆值班",
                callback_data="panel_rss_toggle",
            )
        ],
        [InlineKeyboardButton("查看订阅茶点单", callback_data="panel_rss_list_page_1")],
        [InlineKeyboardButton("查看 RSS 小手册", url=RSS_DOC_URL)],
        [InlineKeyboardButton(copy_text.BTN_BACK_PANEL, callback_data="panel_back")],
    ]

    return "\n".join(lines), InlineKeyboardMarkup(keyboard)


def _build_rss_list_view(application, page: int):
    feeds = _collect_rss_feeds()
    total = len(feeds)

    if total == 0:
        keyboard = [
            [InlineKeyboardButton(copy_text.BTN_BACK_RSS, callback_data="panel_rss")],
            [InlineKeyboardButton(copy_text.BTN_BACK_PANEL, callback_data="panel_back")],
        ]
        return "当前还没有 RSS 茶点。", InlineKeyboardMarkup(keyboard)

    per_page = RSS_FEEDS_PER_PAGE
    total_pages = (total + per_page - 1) // per_page
    page = max(1, min(page, total_pages))
    start = (page - 1) * per_page
    subset = feeds[start : start + per_page]

    lines = [f"RSS 茶点单 (第 {page}/{total_pages} 页)", ""]
    keyboard_rows = []

    for idx, (chat_id, feed_url, feed_data) in enumerate(subset, start=start + 1):
        title = feed_data.get("title", "未命名茶点")
        keywords = feed_data.get("keywords", [])
        keywords_text = ", ".join(keywords) if keywords else "无"
        lines.extend(
            [
                f"{idx}. 主人 {chat_id}",
                f"   标题: {title}",
                f"   链接: {feed_url}",
                f"   口味词: {keywords_text}",
                "",
            ]
        )
        token = _cache_rss_reference(
            application,
            "feed",
            {"chat_id": chat_id, "feed_url": feed_url},
        )
        keyboard_rows.append(
            [
                InlineKeyboardButton(
                    f"打理 #{idx}",
                    callback_data=f"panel_rss_feed_{token}",
                )
            ]
        )

    nav_buttons = []
    if page > 1:
        nav_buttons.append(
            InlineKeyboardButton(copy_text.BTN_PREV_PAGE, callback_data=f"panel_rss_list_page_{page-1}")
        )
    if page < total_pages:
        nav_buttons.append(
            InlineKeyboardButton(copy_text.BTN_NEXT_PAGE, callback_data=f"panel_rss_list_page_{page+1}")
        )
    if nav_buttons:
        keyboard_rows.append(nav_buttons)

    keyboard_rows.append([InlineKeyboardButton(copy_text.BTN_BACK_RSS, callback_data="panel_rss")])
    keyboard_rows.append([InlineKeyboardButton(copy_text.BTN_BACK_PANEL, callback_data="panel_back")])

    return "\n".join(lines).strip(), InlineKeyboardMarkup(keyboard_rows)


def _build_rss_feed_detail(application, chat_id: str, feed_url: str):
    subscriptions = rss_data_manager.get_subscriptions()
    feed_data = (
        subscriptions.get(chat_id, {})
        .get("rss_feeds", {})
        .get(feed_url)
    )
    if not feed_data:
        return None, None

    title = feed_data.get("title", "未命名茶点")
    keywords = feed_data.get("keywords", [])

    lines = [
        "茶点详情",
        "",
        f"主人 ID: {chat_id}",
        f"标题: {title}",
        f"链接: {feed_url}",
    ]

    if keywords:
        lines.append("口味词：")
        lines.extend([f"- {kw}" for kw in keywords])
    else:
        lines.append("口味词：无（会端上所有更新）")

    keyboard_rows = []
    remove_token = _cache_rss_reference(
        application,
        "feed",
        {"chat_id": chat_id, "feed_url": feed_url},
    )
    keyboard_rows.append(
        [InlineKeyboardButton("撤下这份茶点", callback_data=f"panel_rss_remove_{remove_token}")]
    )

    for kw in keywords:
        kw_token = _cache_rss_reference(
            application,
            "keyword",
            {"chat_id": chat_id, "feed_url": feed_url, "keyword": kw},
        )
        keyboard_rows.append(
            [
                InlineKeyboardButton(
                    f"取下口味词：{kw}",
                    callback_data=f"panel_rss_kwrm_{kw_token}",
                )
            ]
        )

    keyboard_rows.append([InlineKeyboardButton("回订阅茶点单", callback_data="panel_rss_list_page_1")])
    keyboard_rows.append([InlineKeyboardButton(copy_text.BTN_BACK_RSS, callback_data="panel_rss")])

    return "\n".join(lines), InlineKeyboardMarkup(keyboard_rows)


def _format_runtime_statuses(statuses: list[dict]) -> str:
    if not statuses:
        return "暂时还没有运行状态记录。"

    lines = ["运行状态", ""]
    for item in statuses[:30]:
        failures = int(item.get("consecutive_failures") or 0)
        badge = "正常" if failures == 0 else f"异常 x{failures}"
        lines.extend([
            f"{item.get('name')} [{badge}]",
            f"  类别: {item.get('category')}",
            f"  最近运行: {item.get('last_run_at') or '-'}",
            f"  最近成功: {item.get('last_success_at') or '-'}",
            f"  最近失败: {item.get('last_error_at') or '-'}",
            f"  耗时: {item.get('last_duration_ms') or 0} ms, 推送: {item.get('last_sent_count') or 0}",
        ])
        if item.get("last_error"):
            lines.append(f"  错误: {str(item['last_error'])[:180]}")
        lines.append("")
    return "\n".join(lines).strip()


async def _build_spamrules_view():
    text = await spam_filter.format_settings()
    settings = await db.get_spam_keyword_filter_settings()
    keyboard = [
        [
            InlineKeyboardButton(
                "关闭关键词拦截" if settings["enabled"] else "启用关键词拦截",
                callback_data="panel_spamrules_toggle",
            )
        ],
        [
            InlineKeyboardButton(
                "关闭自动拉黑" if settings["auto_block"] else "启用自动拉黑",
                callback_data="panel_spamrules_autoblock",
            )
        ],
        [InlineKeyboardButton(copy_text.BTN_BACK_PANEL, callback_data="panel_back")],
    ]
    return text + "\n\n命令管理: /spamrules", InlineKeyboardMarkup(keyboard)


def _git_updatebot_view_text(status, rollback, note: str = "") -> str:
    """Git 更新面板的正文。note 用于附加一句说明（例如镜像检查失败的原因）。"""
    text = (
        safe_update.format_status(status, rollback)
        + "\n\n执行更新: /updatebot apply\n执行回滚: /updatebot rollback"
    )
    if note:
        text += "\n\n" + note
    return text


def _git_updatebot_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("刷新状态", callback_data="panel_updatebot")],
        [InlineKeyboardButton(copy_text.BTN_BACK_PANEL, callback_data="panel_back")],
    ])


async def _build_git_updatebot_view(note: str = ""):
    """容器内 Git 更新面板（原有行为，作为镜像更新不可用时的回退）。"""
    repo_dir = Path(__file__).resolve().parent.parent
    status = await safe_update.get_status(repo_dir, fetch_remote=True)
    rollback = await db.get_app_meta("last_update_rollback")
    return _git_updatebot_view_text(status, rollback, note), _git_updatebot_keyboard()


def _image_update_keyboard(status=None) -> InlineKeyboardMarkup:
    """镜像更新面板的按钮。有可用更新时才给「确认更新」。"""
    rows = []
    if status is not None and getattr(status, "update_available", False):
        rows.append([InlineKeyboardButton(copy_text.BTN_IMAGE_APPLY,
                                          callback_data="panel_image_apply")])
    rows.append([InlineKeyboardButton(copy_text.BTN_IMAGE_CHECK,
                                     callback_data="panel_image_check")])
    rows.append([InlineKeyboardButton(copy_text.BTN_BACK_PANEL, callback_data="panel_back")])
    return InlineKeyboardMarkup(rows)


async def _build_image_update_view(status=None):
    """镜像更新面板。status 为 None 时现场查一次。"""
    if status is None:
        status = await image_update.check_for_update()
    text = image_update.format_image_status(status)
    if not status.in_container:
        text += "\n\n" + copy_text.IMAGE_UPDATE_NOT_IN_DOCKER
    return text, _image_update_keyboard(status)


async def _build_updatebot_view():
    """安全更新面板：容器里给镜像更新，其它环境沿用 Git 更新。

    镜像这条路只在容器里才有意义（非容器没有容器可重建）；任何异常都退回 Git 面板，
    绝不让新功能把整个面板弄打不开。
    """
    try:
        if image_update.detect_container():
            status = await image_update.check_for_update()
            text, keyboard = await _build_image_update_view(status)
            text += "\n\nGit 更新: /updatebot status|apply|rollback"
            return text, keyboard
    except Exception:
        # 异常详情只进日志，给主人的是一句简短说明 + 可用的 Git 面板
        logging.exception("镜像更新面板构建失败，回退到 Git 更新面板")
        note = "女仆没能读到镜像更新的状态呢，先给主人看 Git 更新的情况吧。"
        return await _build_git_updatebot_view(note)
    return await _build_git_updatebot_view()


def _build_ai_model_selection_view(application, provider_type: str, feature_type: str, models: list, page: int = 1):
    total = len(models)
    total_pages = max(1, (total + AI_MODELS_PER_PAGE - 1) // AI_MODELS_PER_PAGE)
    page = max(1, min(page, total_pages))
    start = (page - 1) * AI_MODELS_PER_PAGE
    page_models = models[start:start + AI_MODELS_PER_PAGE]

    feature_name_map = {
        'filter': '内容审查',
        'verification': '验证码生成',
        'autoreply': '自动回复'
    }
    feature_name = feature_name_map.get(feature_type, feature_type)

    keyboard = []
    for model in page_models:
        token = _cache_rss_reference(
            application,
            "ai_model",
            {
                "provider_type": provider_type,
                "feature_type": feature_type,
                "model_name": model,
            },
        )
        keyboard.append([InlineKeyboardButton(model, callback_data=f"setmref:{token}")])

    nav_buttons = []
    callback_prefix = f"ai_select_model_{provider_type}_{feature_type}"
    if page > 1:
        nav_buttons.append(InlineKeyboardButton(copy_text.BTN_PREV_PAGE, callback_data=f"{callback_prefix}_{page - 1}"))
    if page < total_pages:
        nav_buttons.append(InlineKeyboardButton(copy_text.BTN_NEXT_PAGE, callback_data=f"{callback_prefix}_{page + 1}"))
    if nav_buttons:
        keyboard.append(nav_buttons)

    keyboard.append([InlineKeyboardButton("回上一层", callback_data=f"ai_config_models_{provider_type}")])

    message = (
        f"主人，请挑选 {provider_type.upper()} {feature_name} 模型:\n"
        f"第 {page}/{total_pages} 页，共 {total} 个模型"
    )
    return message, InlineKeyboardMarkup(keyboard)


async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data
    user_id = query.from_user.id
    
    if data.startswith("nt_"):
        # nt_* 回调由 network_test 内部的 callback_handler 自行 answer，
        # 外层再 answer 一次会对已应答的 query 触发 Telegram 400 错误
        # （并产生错误日志噪音，掩盖真实异常），因此这里跳过外层应答。
        from network_test.handlers import callback_handler as network_callback_handler
        handled = await network_callback_handler(update, context)
        if not handled:
            if data in ["nt_rmserver_cancel", "nt_installnexttrace_cancel"]:
                from network_test.state import user_data
                if user_id in user_data and user_data[user_id].get("from_panel"):
                    del user_data[user_id]
                    data = "panel_network_test"
        else:
            return
    else:
        await query.answer()

    if data.startswith("verify_"):
        answer = data.split("_", 1)[1]
        success, message, is_banned, new_question = await verify_answer(user_id, answer)
        
        if is_banned:
            await query.edit_message_text(text=message, reply_markup=None)
            return
        
        if new_question:
            new_question_text, new_keyboard = new_question
            await query.edit_message_text(
                text=f"{message}\n\n{new_question_text}",
                reply_markup=new_keyboard
            )
            return
        
        await query.edit_message_text(text=message)

        if success:
            if 'pending_update' in context.user_data:
                pending_update = context.user_data.pop('pending_update')
                message = pending_update.message
                image_bytes = None

                if message.photo:
                    photo_file = await message.photo[-1].get_file()
                    image_bytes = await photo_file.download_as_bytearray()
                elif message.sticker and not message.sticker.is_animated and not message.sticker.is_video:
                    sticker_file = await message.sticker.get_file()
                    sticker_bytes = await sticker_file.download_as_bytearray()
                    image_bytes = await sticker_to_image(sticker_bytes)

                should_forward = True
                if message.video or message.animation:
                    pass
                else:
                    analyzing_message = await context.bot.send_message(
                        chat_id=message.chat_id,
                        text=copy_text.scan_message(copy_text.address(await db.is_admin(user_id))),
                        reply_to_message_id=message.message_id
                    )
                    analysis_result = await gemini_service.analyze_message(message, image_bytes)
                    if analysis_result.get("is_spam"):
                        should_forward = False
                        media_type = None
                        media_file_id = None
                        if message.photo:
                            media_type = "photo"
                            media_file_id = message.photo[-1].file_id
                        elif message.sticker:
                            media_type = "sticker"
                            media_file_id = message.sticker.file_id

                        await db.save_filtered_message(
                            user_id=user_id,
                            message_id=message.message_id,
                            content=message.text or message.caption,
                            reason=analysis_result.get("reason"),
                            media_type=media_type,
                            media_file_id=media_file_id,
                        )
                        reason = analysis_result.get("reason", "暂时没有写明理由")
                        await analyzing_message.edit_text(copy_text.msg_blocked(reason))
                    else:
                        await analyzing_message.delete()

                if should_forward:
                    thread_id, is_new = await get_or_create_thread(pending_update, context)
                    if not thread_id:
                        await pending_update.message.reply_text(copy_text.topic_create_failed(copy_text.address(await db.is_admin(user_id))))
                        return
                    
                    try:
                        if not is_new:
                            await _resend_message(pending_update, context, thread_id)
                    except BadRequest as e:
                        if "Message thread not found" in e.message:
                            await db.update_user_thread_id(user_id, None)
                            await db.update_user_verification(user_id, False)
                            
                            context.user_data['pending_update'] = pending_update
                            question, keyboard = await create_verification(user_id)
                            
                            # 会客厅被关掉后重新验证：装饰落在首行（那句话）末尾，
                            # 不去碰下面接的命令/题目文本
                            full_message = copy_text.with_deco_head(
                                f"{copy_text.THREAD_CLOSED_REVERIFY}{question}", 'ERROR')

                            await pending_update.message.reply_text(
                                text=full_message,
                                reply_markup=keyboard
                            )
                        else:
                            print(f"发送消息时发生未知错误: {e}")
                            await pending_update.message.reply_text(copy_text.delivery_failed(copy_text.address(await db.is_admin(user_id))))
            else:
                await query.message.reply_text(
                    copy_text.with_deco("门已经打开啦，客人现在可以发送消息。", 'OK')
                )
    
    elif data == "panel_back":
        if not await db.is_admin(user_id):
            await query.answer(copy_text.perm_denied(), show_alert=True)
            return
        
        message, keyboard = await _build_panel_back_view()
        
        await query.edit_message_text(
            message,
            reply_markup=keyboard,
            parse_mode='Markdown'
        )
    
    elif data == "panel_broadcast":
        if not await db.is_admin(user_id):
            await query.answer(copy_text.perm_denied(), show_alert=True)
            return

        groups = await db.get_all_user_groups()
        message = broadcast_service.build_broadcast_panel_text(groups)
        keyboard = broadcast_service.build_broadcast_panel_keyboard()
        await query.edit_message_text(message, reply_markup=keyboard)

    elif data == "broadcast_groups":
        if not await db.is_admin(user_id):
            await query.answer(copy_text.perm_denied(), show_alert=True)
            return

        groups = await db.get_all_user_groups()
        if not groups:
            await query.edit_message_text(
                copy_text.with_deco(
                    "主人还没有建过任何分组呢。用 /group create <分组名> 现建一个吧。",
                    'EMPTY',
                ),
                reply_markup=broadcast_service.build_broadcast_panel_keyboard(),
            )
            return
        await query.edit_message_text(
            copy_text.with_deco("主人想翻哪一本分组小本本呢？", 'ASK'),
            reply_markup=broadcast_service.build_groups_keyboard(groups),
        )

    elif data.startswith("broadcast_group_view_"):
        if not await db.is_admin(user_id):
            await query.answer(copy_text.perm_denied(), show_alert=True)
            return

        try:
            group_id = int(data.split("_")[-1])
        except (ValueError, IndexError):
            await query.answer(copy_text.with_deco("这个分组编号不对劲哦，主人。", 'ERROR'), show_alert=True)
            return

        group = await db.get_user_group_by_id(group_id)
        if not group:
            await query.answer(copy_text.with_deco("女仆翻遍分组小本本，也没找到这一条呢。", 'ERROR'), show_alert=True)
            return

        message = await broadcast_service.format_group_members(group['name'])
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("回分组列表", callback_data="broadcast_groups")],
            [InlineKeyboardButton(copy_text.BTN_BACK_BROADCAST, callback_data="panel_broadcast")],
        ])
        await query.edit_message_text(message, reply_markup=keyboard)

    elif data.startswith("usercard_groups_"):
        if not await db.is_admin(user_id):
            await query.answer(copy_text.perm_denied(), show_alert=True)
            return

        try:
            target_user_id = int(data.split("_")[-1])
        except (ValueError, IndexError):
            await query.answer(copy_text.user_id_invalid(), show_alert=True)
            return

        message, keyboard = await broadcast_service.build_user_group_keyboard(target_user_id)
        await query.message.reply_text(message, reply_markup=keyboard)

    elif data.startswith("usergroup_toggle_"):
        if not await db.is_admin(user_id):
            await query.answer(copy_text.perm_denied(), show_alert=True)
            return

        try:
            _, _, target_user_id_text, group_id_text = data.split("_", 3)
            target_user_id = int(target_user_id_text)
            group_id = int(group_id_text)
        except (ValueError, IndexError):
            await query.answer(copy_text.with_deco("这份分组请求不对劲哦，主人。", 'ERROR'), show_alert=True)
            return

        group = await db.get_user_group_by_id(group_id)
        if not group:
            await query.answer(copy_text.with_deco("女仆翻遍分组小本本，也没找到这一条呢。", 'ERROR'), show_alert=True)
            return

        user_groups = await db.get_groups_for_user(target_user_id)
        user_group_ids = {item['id'] for item in user_groups}
        if group_id in user_group_ids:
            await db.remove_user_from_group(group['name'], target_user_id)
            await query.answer(copy_text.with_deco(f"已经把这一位移出 {group['name']} 啦。", 'OK'))
        else:
            await db.add_user_to_group(group['name'], target_user_id, user_id)
            await query.answer(copy_text.with_deco(f"已经把这一位收进 {group['name']} 啦。", 'OK'))

        message, keyboard = await broadcast_service.build_user_group_keyboard(target_user_id)
        try:
            await query.edit_message_text(message, reply_markup=keyboard)
        except BadRequest as exc:
            if "message is not modified" not in exc.message.lower():
                raise

    elif data.startswith("panel_blacklist_page_"):
        from services import blacklist
        
        if not await db.is_admin(user_id):
            await query.answer(copy_text.perm_denied(), show_alert=True)
            return
        
        try:
            page = int(data.split("_")[3])
        except (ValueError, IndexError):
            await query.answer(copy_text.page_invalid(), show_alert=True)
            return
        
        message, keyboard = await blacklist.get_blacklist_keyboard(page=page)
        
        if keyboard:
            keyboard_buttons = list(keyboard.inline_keyboard)
            keyboard_buttons.append([InlineKeyboardButton(copy_text.BTN_BACK_PANEL, callback_data="panel_back")])
            keyboard = InlineKeyboardMarkup(keyboard_buttons)
        
        if keyboard:
            await query.edit_message_text(
                text=message,
                reply_markup=keyboard,
                parse_mode='Markdown'
            )
        else:
            back_keyboard = InlineKeyboardMarkup([[InlineKeyboardButton(copy_text.BTN_BACK_PANEL, callback_data="panel_back")]])
            await query.edit_message_text(text=message, reply_markup=back_keyboard)
    
    elif data == "panel_stats":
        if not await db.is_admin(user_id):
            await query.answer(copy_text.perm_denied(), show_alert=True)
            return
        
        from services.blacklist import get_all_users_keyboard
        
        page = 1
        message, keyboard = await get_all_users_keyboard(
            page=page,
            callback_prefix="panel_stats_all_users_page_",
            back_callback="panel_back",
            back_text=copy_text.BTN_BACK_PANEL
        )
        
        if keyboard:
            await query.edit_message_text(
                text=message,
                reply_markup=keyboard,
                parse_mode='Markdown'
            )
        else:
            back_keyboard = InlineKeyboardMarkup([[InlineKeyboardButton(copy_text.BTN_BACK_PANEL, callback_data="panel_back")]])
            await query.edit_message_text(text=message, reply_markup=back_keyboard, parse_mode='Markdown')
    
    elif data.startswith("panel_stats_all_users_page_"):
        from services.blacklist import get_all_users_keyboard
        
        if not await db.is_admin(user_id):
            await query.answer(copy_text.perm_denied(), show_alert=True)
            return
        
        try:
            page = int(data.split("_")[5])
        except (ValueError, IndexError):
            await query.answer(copy_text.page_invalid(), show_alert=True)
            return
        
        message, keyboard = await get_all_users_keyboard(
            page=page,
            callback_prefix="panel_stats_all_users_page_",
            back_callback="panel_back",
            back_text=copy_text.BTN_BACK_PANEL
        )
        
        if keyboard:
            await query.edit_message_text(
                text=message,
                reply_markup=keyboard,
                parse_mode='Markdown'
            )
        else:
            keyboard = InlineKeyboardMarkup([[InlineKeyboardButton(copy_text.BTN_BACK_PANEL, callback_data="panel_back")]])
            await query.edit_message_text(
                text=message,
                reply_markup=keyboard,
                parse_mode='Markdown'
            )
    
    elif data.startswith("panel_stats_blacklist_page_"):
        from services.blacklist import get_blacklist_keyboard_detailed
        
        if not await db.is_admin(user_id):
            await query.answer(copy_text.perm_denied(), show_alert=True)
            return
        
        try:
            page = int(data.split("_")[4])
        except (ValueError, IndexError):
            await query.answer(copy_text.page_invalid(), show_alert=True)
            return
        
        message, keyboard = await get_blacklist_keyboard_detailed(page=page)
        
        if keyboard:
            keyboard_buttons = [list(row) for row in keyboard.inline_keyboard]
            for i, row in enumerate(keyboard_buttons):
                for j, button in enumerate(row):
                    if button.callback_data == "stats_back_to_menu":
                        keyboard_buttons[i][j] = InlineKeyboardButton(copy_text.BTN_BACK_PANEL, callback_data="panel_back")
                        break
            keyboard = InlineKeyboardMarkup(keyboard_buttons)
        
        if keyboard:
            await query.edit_message_text(
                text=message,
                reply_markup=keyboard,
                parse_mode='Markdown'
            )
        else:
            back_keyboard = InlineKeyboardMarkup([[InlineKeyboardButton(copy_text.BTN_BACK_PANEL, callback_data="panel_back")]])
            await query.edit_message_text(text=message, reply_markup=back_keyboard, parse_mode='Markdown')
    
    elif data.startswith("panel_filtered_page_"):
        from .admin_handler import _format_filtered_messages, _get_filtered_messages_keyboard
        
        if not await db.is_admin(user_id):
            await query.answer(copy_text.perm_denied(), show_alert=True)
            return
        
        try:
            page = int(data.split("_")[3])
        except (ValueError, IndexError):
            await query.answer(copy_text.page_invalid(), show_alert=True)
            return
        
        MESSAGES_PER_PAGE = 5

        total_count = await db.get_filtered_messages_count()
        
        if total_count == 0:
            back_keyboard = InlineKeyboardMarkup([[InlineKeyboardButton(copy_text.BTN_BACK_PANEL, callback_data="panel_back")]])
            await query.edit_message_text(copy_text.filtered_empty(), reply_markup=back_keyboard)
            return
        
        total_pages = (total_count + MESSAGES_PER_PAGE - 1) // MESSAGES_PER_PAGE

        if page < 1:
            page = 1
        elif page > total_pages:
            page = total_pages

        offset = (page - 1) * MESSAGES_PER_PAGE

        messages = await db.get_filtered_messages(MESSAGES_PER_PAGE, offset)
        
        if not messages:
            back_keyboard = InlineKeyboardMarkup([[InlineKeyboardButton(copy_text.BTN_BACK_PANEL, callback_data="panel_back")]])
            await query.edit_message_text(copy_text.filtered_empty(), reply_markup=back_keyboard)
            return

        response = await _format_filtered_messages(messages, page, total_pages)

        keyboard = await _get_filtered_messages_keyboard(page, total_pages, callback_prefix="panel_filtered_page_")
        
        if keyboard:
            keyboard_buttons = [list(row) for row in keyboard.inline_keyboard]
            keyboard_buttons.append([InlineKeyboardButton(copy_text.BTN_BACK_PANEL, callback_data="panel_back")])
            keyboard = InlineKeyboardMarkup(keyboard_buttons)
        else:
            keyboard = InlineKeyboardMarkup([[InlineKeyboardButton(copy_text.BTN_BACK_PANEL, callback_data="panel_back")]])

        await query.edit_message_text(response, reply_markup=keyboard)
    
    elif data == "panel_autoreply":
        if not await db.is_admin(user_id):
            await query.answer(copy_text.perm_denied(), show_alert=True)
            return
        
        is_enabled = await db.get_autoreply_enabled()
        status_text = "正在值班" if is_enabled else "正在休息"
        
        message = (
            f"自动回复女仆管理\n\n"
            f"当前状态: {status_text}\n\n"
            f"主人，请选择要安排的工作："
        )
        
        keyboard = [
            [
                InlineKeyboardButton(
                    copy_text.BTN_AUTOREPLY_REST if is_enabled else copy_text.BTN_AUTOREPLY_ON_DUTY,
                    callback_data="panel_autoreply_toggle"
                )
            ],
            [InlineKeyboardButton(copy_text.BTN_KB_LIST, callback_data="panel_autoreply_kb_list_page_1")],
            [InlineKeyboardButton(copy_text.BTN_KB_ADD, callback_data="panel_autoreply_kb_add")],
            [InlineKeyboardButton(copy_text.BTN_BACK_PANEL, callback_data="panel_back")],
        ]
        
        await query.edit_message_text(
            message,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='Markdown'
        )
    
    elif data == "panel_rss":
        if not await db.is_admin(user_id):
            await query.answer(copy_text.perm_denied(), show_alert=True)
            return

        message, keyboard = _build_rss_panel_view()
        await query.edit_message_text(message, reply_markup=keyboard)

    elif data == "panel_tg_monitor":
        if not await db.is_admin(user_id):
            await query.answer(copy_text.perm_denied(), show_alert=True)
            return

        message = await tg_monitor.build_panel_text()
        await query.edit_message_text(message, reply_markup=tg_monitor.build_panel_keyboard())

    elif data == "panel_tg_monitor_list":
        if not await db.is_admin(user_id):
            await query.answer(copy_text.perm_denied(), show_alert=True)
            return

        message = await tg_monitor.build_monitor_list_text()
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton(copy_text.BTN_BACK_TG_MONITOR, callback_data="panel_tg_monitor")],
            [InlineKeyboardButton(copy_text.BTN_BACK_PANEL, callback_data="panel_back")],
        ])
        await query.edit_message_text(message, reply_markup=keyboard)

    elif data == "panel_tg_monitor_discovered":
        if not await db.is_admin(user_id):
            await query.answer(copy_text.perm_denied(), show_alert=True)
            return

        chats = await db.list_discovered_tg_chats(limit=40)
        if not chats:
            message = "暂时还没有发现的群/频道。"
        else:
            lines = ["发现的 TG 群/频道", ""]
            for chat in chats:
                username = f" @{chat['username']}" if chat.get("username") else ""
                lines.append(
                    f"- {chat.get('title') or chat['chat_id']}{username}\n"
                    f"  chat_id: {chat['chat_id']}\n"
                    f"  最近看到: {chat.get('last_seen_at') or '-'}"
                )
            message = "\n".join(lines)
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton(copy_text.BTN_BACK_TG_MONITOR, callback_data="panel_tg_monitor")],
            [InlineKeyboardButton(copy_text.BTN_BACK_PANEL, callback_data="panel_back")],
        ])
        await query.edit_message_text(message, reply_markup=keyboard)

    elif data == "panel_web_monitor":
        if not await db.is_admin(user_id):
            await query.answer(copy_text.perm_denied(), show_alert=True)
            return

        message = await web_monitor.build_panel_text()
        await query.edit_message_text(message, reply_markup=web_monitor.build_panel_keyboard())

    elif data == "panel_web_monitor_list":
        if not await db.is_admin(user_id):
            await query.answer(copy_text.perm_denied(), show_alert=True)
            return

        message = await web_monitor.build_monitor_list_text()
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("回网页监控", callback_data="panel_web_monitor")],
            [InlineKeyboardButton(copy_text.BTN_BACK_PANEL, callback_data="panel_back")],
        ])
        await query.edit_message_text(message, reply_markup=keyboard)

    elif data == "panel_spamrules":
        if not await db.is_admin(user_id):
            await query.answer(copy_text.perm_denied(), show_alert=True)
            return

        message, keyboard = await _build_spamrules_view()
        await query.edit_message_text(message, reply_markup=keyboard)

    elif data == "panel_spamrules_toggle":
        if not await db.is_admin(user_id):
            await query.answer(copy_text.perm_denied(), show_alert=True)
            return

        settings = await db.get_spam_keyword_filter_settings()
        await db.set_spam_keyword_filter_enabled(not settings["enabled"])
        await query.answer(
            copy_text.with_deco(
                "关键词拦截女仆已经上岗值班啦。" if settings["enabled"] is False else "关键词拦截女仆已经回屋休息啦。",
                'CAT'),
            show_alert=True)
        message, keyboard = await _build_spamrules_view()
        await query.edit_message_text(message, reply_markup=keyboard)

    elif data == "panel_spamrules_autoblock":
        if not await db.is_admin(user_id):
            await query.answer(copy_text.perm_denied(), show_alert=True)
            return

        settings = await db.get_spam_keyword_filter_settings()
        await db.set_spam_keyword_auto_block(not settings["auto_block"])
        await query.answer(
            copy_text.with_deco(
                "自动拉黑女仆已经上岗值班啦。" if settings["auto_block"] is False else "自动拉黑女仆已经回屋休息啦。",
                'CAT'),
            show_alert=True)
        message, keyboard = await _build_spamrules_view()
        await query.edit_message_text(message, reply_markup=keyboard)

    elif data == "panel_monitor_status":
        if not await db.is_admin(user_id):
            await query.answer(copy_text.perm_denied(), show_alert=True)
            return

        statuses = await db.get_runtime_statuses()
        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("只看 RSS", callback_data="panel_monitor_status_rss"),
                InlineKeyboardButton("只看 TG", callback_data="panel_monitor_status_tg"),
            ],
            [InlineKeyboardButton("只看网页监控", callback_data="panel_monitor_status_web")],
            [InlineKeyboardButton(copy_text.BTN_REFRESH, callback_data="panel_monitor_status")],
            [InlineKeyboardButton(copy_text.BTN_BACK_PANEL, callback_data="panel_back")],
        ])
        await query.edit_message_text(_format_runtime_statuses(statuses), reply_markup=keyboard)

    elif data in {"panel_monitor_status_rss", "panel_monitor_status_tg", "panel_monitor_status_web"}:
        if not await db.is_admin(user_id):
            await query.answer(copy_text.perm_denied(), show_alert=True)
            return

        if data.endswith("_rss"):
            category = "rss"
        elif data.endswith("_web"):
            category = "web"
        else:
            category = "tg_monitor"
        statuses = await db.get_runtime_statuses(category=category)
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("全部状态", callback_data="panel_monitor_status")],
            [InlineKeyboardButton(copy_text.BTN_BACK_PANEL, callback_data="panel_back")],
        ])
        await query.edit_message_text(_format_runtime_statuses(statuses), reply_markup=keyboard)

    elif data == "panel_updatebot":
        if not await db.is_admin(user_id):
            await query.answer(copy_text.perm_denied(), show_alert=True)
            return

        try:
            message, keyboard = await _build_updatebot_view()
        except Exception:
            # 异常详情只进日志，给主人的是简短说明
            logging.exception("读取安全更新状态失败")
            message = "女仆这次没读到安全更新的状态呢，主人稍后再试一次吧。"
            keyboard = InlineKeyboardMarkup([[InlineKeyboardButton(copy_text.BTN_BACK_PANEL, callback_data="panel_back")]])
        await query.edit_message_text(message, reply_markup=keyboard)

    elif data == "panel_image_check":
        if not await db.is_admin(user_id):
            await query.answer(copy_text.perm_denied(), show_alert=True)
            return

        # 先应答，免得 Telegram 那边的「转转转」一直挂着
        await query.answer()
        try:
            # 网络查询可能要几秒，先给主人一个「正在查」的反馈
            await query.edit_message_text(copy_text.with_deco(
                "女仆正在问 Docker Hub 有没有新镜像，稍等一下哦。", 'WAIT'))
            message, keyboard = await _build_image_update_view()
        except Exception:
            # 异常详情只进日志，给主人的是简短说明
            logging.exception("检查镜像更新失败")
            message = ("女仆这次没查到镜像的更新情况呢，主人稍后再试一次吧。"
                       "\n\nGit 更新: /updatebot status|apply|rollback")
            keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton(copy_text.BTN_BACK_PANEL, callback_data="panel_back")],
            ])
        await query.edit_message_text(message, reply_markup=keyboard)

    elif data == "panel_image_apply":
        # 这一步**只显示二次确认**，绝不直接更新 —— 更新会重建容器，
        # 一次误点就把机器人踢下线了，必须让主人再确认一次。
        if not await db.is_admin(user_id):
            await query.answer(copy_text.perm_denied(), show_alert=True)
            return

        await query.answer()
        repo, tag = image_update.resolve_image()
        image = "{repo}:{tag}".format(repo=repo, tag=tag)
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton(copy_text.BTN_IMAGE_APPLY,
                                      callback_data="panel_image_apply_confirm")],
            [InlineKeyboardButton(copy_text.BTN_CANCEL, callback_data="panel_updatebot")],
        ])
        await query.edit_message_text(
            copy_text.image_update_confirm_prompt(image), reply_markup=keyboard)

    elif data == "panel_image_apply_confirm":
        if not await db.is_admin(user_id):
            await query.answer(copy_text.perm_denied(), show_alert=True)
            return

        await query.answer()
        # ⚠️ 顺序极重要：更新会重启容器、把机器人自己杀死。
        # 必须**先把「已开始」的消息发出去**，再触发 watchtower；
        # 反过来的话主人什么都收不到，只会看到机器人凭空消失。
        await query.edit_message_text(copy_text.image_update_started())
        try:
            ok, detail = await image_update.trigger_watchtower_update()
        except Exception as exc:
            logging.exception("触发 watchtower 更新失败")
            ok, detail = False, str(exc)[:160]
        if not ok:
            # 失败时把「已开始」改成失败说明，避免主人以为更新在跑
            await query.edit_message_text(copy_text.image_update_failed(detail))
            return
        # 触发成功：把远端 digest 记为已知基线，免得重建后又报一次「有更新」
        try:
            repo, tag = image_update.resolve_image()
            digest = await image_update.fetch_remote_digest(repo, tag)
            await image_update.remember_current_digest(digest)
        except Exception:
            logging.exception("更新后刷新镜像指纹基线失败（不影响本次更新）")
    elif data == "panel_ai_settings":
        if not await db.is_admin(user_id):
            await query.answer(copy_text.perm_denied(), show_alert=True)
            return
            
        async with db.db_manager.get_connection() as conn:
             cursor = await conn.execute("""
                SELECT key, value FROM settings 
                WHERE key IN (
                    'ai_provider', 
                    'gemini_model_filter', 'gemini_model_verification', 'gemini_model_autoreply',
                    'openai_model_filter', 'openai_model_verification', 'openai_model_autoreply'
                )
             """)
             settings = {row[0]: row[1] for row in await cursor.fetchall()}
             
        current_provider = settings.get('ai_provider', 'gemini')
        
        provider_name = "Gemini" if current_provider == 'gemini' else "OpenAI"
        
        message = (
            f"**AI 模型衣柜**\n\n"
            f"当前侍奉提供商: `{provider_name}`\n\n"
            f"**Gemini 衣架**:\n"
            f"• 审查: `{settings.get('gemini_model_filter', 'N/A')}`\n"
            f"• 验证: `{settings.get('gemini_model_verification', 'N/A')}`\n"
            f"• 回复: `{settings.get('gemini_model_autoreply', 'N/A')}`\n\n"
            f"**OpenAI 衣架**:\n"
            f"• 审查: `{settings.get('openai_model_filter', 'N/A')}`\n"
            f"• 验证: `{settings.get('openai_model_verification', 'N/A')}`\n"
            f"• 回复: `{settings.get('openai_model_autoreply', 'N/A')}`\n\n"
            f"主人，请挑选要整理的项目:"
        )
        
        keyboard = [
            [
                InlineKeyboardButton(copy_text.btn_ai_enable_gemini(current_provider == 'gemini'), callback_data="ai_set_provider_gemini"),
                InlineKeyboardButton(copy_text.btn_ai_enable_openai(current_provider == 'openai'), callback_data="ai_set_provider_openai")
            ],
            [
                InlineKeyboardButton(copy_text.BTN_AI_MODELS_GEMINI, callback_data="ai_config_models_gemini"),
                InlineKeyboardButton(copy_text.BTN_AI_MODELS_OPENAI, callback_data="ai_config_models_openai")
            ],
            [InlineKeyboardButton(copy_text.BTN_BACK_PANEL, callback_data="panel_back")]
        ]
        
        await query.edit_message_text(message, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')

    elif data.startswith("ai_set_provider_"):
        if not await db.is_admin(user_id): return
        
        new_provider = data.split("_")[3]
        async with db.db_manager.get_connection() as conn:
            await conn.execute("UPDATE settings SET value = ? WHERE key = 'ai_provider'", (new_provider,))
            await conn.commit()
            
        await query.answer(copy_text.with_deco(f"AI 提供商已经替主人换成 {new_provider.upper()} 啦。", 'OK'))
        
        async with db.db_manager.get_connection() as conn:
             cursor = await conn.execute("""
                SELECT key, value FROM settings 
                WHERE key IN (
                    'ai_provider', 
                    'gemini_model_filter', 'gemini_model_verification', 'gemini_model_autoreply',
                    'openai_model_filter', 'openai_model_verification', 'openai_model_autoreply'
                )
             """)
             settings = {row[0]: row[1] for row in await cursor.fetchall()}
             
        current_provider = settings.get('ai_provider', 'gemini')
        provider_name = "Gemini" if current_provider == 'gemini' else "OpenAI"
        
        message = (
            f"**AI 模型衣柜**\n\n"
            f"当前侍奉提供商: `{provider_name}`\n\n"
            f"**Gemini 衣架**:\n"
            f"• 审查: `{settings.get('gemini_model_filter', 'N/A')}`\n"
            f"• 验证: `{settings.get('gemini_model_verification', 'N/A')}`\n"
            f"• 回复: `{settings.get('gemini_model_autoreply', 'N/A')}`\n\n"
            f"**OpenAI 衣架**:\n"
            f"• 审查: `{settings.get('openai_model_filter', 'N/A')}`\n"
            f"• 验证: `{settings.get('openai_model_verification', 'N/A')}`\n"
            f"• 回复: `{settings.get('openai_model_autoreply', 'N/A')}`\n\n"
            f"主人，请挑选要整理的项目:"
        )
        
        keyboard = [
            [
                InlineKeyboardButton(copy_text.btn_ai_enable_gemini(current_provider == 'gemini'), callback_data="ai_set_provider_gemini"),
                InlineKeyboardButton(copy_text.btn_ai_enable_openai(current_provider == 'openai'), callback_data="ai_set_provider_openai")
            ],
            [
                InlineKeyboardButton(copy_text.BTN_AI_MODELS_GEMINI, callback_data="ai_config_models_gemini"),
                InlineKeyboardButton(copy_text.BTN_AI_MODELS_OPENAI, callback_data="ai_config_models_openai")
            ],
            [InlineKeyboardButton(copy_text.BTN_BACK_PANEL, callback_data="panel_back")]
        ]
        await query.edit_message_text(message, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')

    elif data.startswith("ai_config_models_"):
        if not await db.is_admin(user_id): return
        
        provider_type = data.split("_")[3]
        
        message = f"主人，请挑选要整理的 {provider_type.upper()} 功能模型:"
        
        keyboard = [
            [InlineKeyboardButton(copy_text.BTN_PANEL_AI_FILTER_MODEL, callback_data=f"ai_select_model_{provider_type}_filter")],
            [InlineKeyboardButton(copy_text.BTN_PANEL_AI_VERIFY_MODEL, callback_data=f"ai_select_model_{provider_type}_verification")],
            [InlineKeyboardButton(copy_text.BTN_PANEL_AI_AUTOREPLY_MODEL, callback_data=f"ai_select_model_{provider_type}_autoreply")],
            [InlineKeyboardButton(copy_text.BTN_BACK_AI, callback_data="panel_ai_settings")]
        ]
        
        await query.edit_message_text(message, reply_markup=InlineKeyboardMarkup(keyboard))

    elif data.startswith("ai_select_model_"):
        if not await db.is_admin(user_id): return
        
        parts = data.split("_")
        provider_type = parts[3]
        feature_type = parts[4]
        page = 1
        if len(parts) > 5:
            try:
                page = int(parts[5])
            except ValueError:
                page = 1
        
        from services.ai_service import ai_service
        
        await query.answer(copy_text.with_deco("女仆正在帮主人翻模型衣柜...", 'WAIT'), show_alert=False)
        
        try:
            models = await ai_service.get_available_models(provider_type)
        except Exception:
            # 异常详情只进日志，给主人的是简短说明
            logging.exception("翻模型衣柜失败")
            await query.answer(copy_text.with_deco("模型衣柜的门卡住啦，主人稍后再试一次嘛。", 'ERROR'), show_alert=True)
            return

        if not models:
             await query.answer(copy_text.with_deco("女仆没能翻到模型列表呢，主人检查一下 API Key 配置嘛。", 'ERROR'), show_alert=True)
             return
        
        message, keyboard = _build_ai_model_selection_view(
            context.application,
            provider_type,
            feature_type,
            models,
            page,
        )
        await query.edit_message_text(message, reply_markup=keyboard)
        return

    elif data.startswith("setmref:"):
        if not await db.is_admin(user_id): return

        token = data.split(":", 1)[1]
        payload = _resolve_rss_reference(context.application, token, "ai_model")
        if not payload:
            await query.answer(copy_text.with_deco("模型选择已经过期啦，请主人重新打开列表。", 'ERROR'), show_alert=True)
            return

        provider_type = payload["provider_type"]
        feature_type = payload["feature_type"]
        model_name = payload["model_name"]
        setting_key = f"{provider_type}_model_{feature_type}"

        async with db.db_manager.get_connection() as conn:
            await conn.execute("UPDATE settings SET value = ? WHERE key = ?", (model_name, setting_key))
            await conn.commit()

        await query.answer(copy_text.ai_model_set(provider_type, feature_type, model_name))

        message = f"主人，请挑选要整理的 {provider_type.upper()} 功能模型:"
        keyboard = [
            [InlineKeyboardButton(copy_text.BTN_PANEL_AI_FILTER_MODEL, callback_data=f"ai_select_model_{provider_type}_filter")],
            [InlineKeyboardButton(copy_text.BTN_PANEL_AI_VERIFY_MODEL, callback_data=f"ai_select_model_{provider_type}_verification")],
            [InlineKeyboardButton(copy_text.BTN_PANEL_AI_AUTOREPLY_MODEL, callback_data=f"ai_select_model_{provider_type}_autoreply")],
            [InlineKeyboardButton(copy_text.BTN_BACK_AI, callback_data="panel_ai_settings")]
        ]
        await query.edit_message_text(message, reply_markup=InlineKeyboardMarkup(keyboard))

    elif data.startswith("setm:"):
        if not await db.is_admin(user_id): return
        
        try:
            _, p_code, f_code, model_name = data.split(":", 3)
        except ValueError:
            await query.answer(copy_text.with_deco("这份请求数据不对劲哦，主人。", 'ERROR'), show_alert=True)
            return
            
        p_map = {'g': 'gemini', 'o': 'openai'}
        f_map = {'f': 'filter', 'v': 'verification', 'a': 'autoreply'}
        
        provider_type = p_map.get(p_code, 'gemini')
        feature_type = f_map.get(f_code, 'filter')
        
        setting_key = f"{provider_type}_model_{feature_type}"
        
        async with db.db_manager.get_connection() as conn:
            await conn.execute("UPDATE settings SET value = ? WHERE key = ?", (model_name, setting_key))
            await conn.commit()
            
        await query.answer(copy_text.ai_model_set(provider_type, feature_type, model_name))
        
        message = f"主人，请挑选要整理的 {provider_type.upper()} 功能模型:"
        keyboard = [
            [InlineKeyboardButton(copy_text.BTN_PANEL_AI_FILTER_MODEL, callback_data=f"ai_select_model_{provider_type}_filter")],
            [InlineKeyboardButton(copy_text.BTN_PANEL_AI_VERIFY_MODEL, callback_data=f"ai_select_model_{provider_type}_verification")],
            [InlineKeyboardButton(copy_text.BTN_PANEL_AI_AUTOREPLY_MODEL, callback_data=f"ai_select_model_{provider_type}_autoreply")],
            [InlineKeyboardButton(copy_text.BTN_BACK_AI, callback_data="panel_ai_settings")]
        ]
        await query.edit_message_text(message, reply_markup=InlineKeyboardMarkup(keyboard))

    
    elif data == "panel_rss_toggle":
        if not await db.is_admin(user_id):
            await query.answer(copy_text.perm_denied(), show_alert=True)
            return

        app = context.application
        if rss_settings.is_enabled():
            changed = rss_disable_feature(app)
            if changed:
                await query.answer(copy_text.with_deco("RSS 女仆已去休息", 'OK'), show_alert=True)
        else:
            changed = rss_enable_feature(app)
            if changed:
                await query.answer(copy_text.with_deco("RSS 女仆已开始值班", 'OK'), show_alert=True)

        message, keyboard = _build_rss_panel_view()
        await query.edit_message_text(message, reply_markup=keyboard)
    
    elif data.startswith("panel_rss_list_page_"):
        if not await db.is_admin(user_id):
            await query.answer(copy_text.perm_denied(), show_alert=True)
            return

        try:
            page = int(data.split("_")[-1])
        except (ValueError, IndexError):
            await query.answer(copy_text.page_invalid(), show_alert=True)
            return

        message, keyboard = _build_rss_list_view(context.application, page)
        await query.edit_message_text(message, reply_markup=keyboard)
    
    elif data.startswith("panel_rss_feed_"):
        if not await db.is_admin(user_id):
            await query.answer(copy_text.perm_denied(), show_alert=True)
            return

        token = data.split("_")[-1]
        ref = _resolve_rss_reference(context.application, token, "feed")
        if not ref:
            await query.answer(copy_text.with_deco("这份订阅引用过期啦，主人重新打开茶点单看一眼嘛。", 'ERROR'), show_alert=True)
            return

        chat_id = str(ref["chat_id"])
        feed_url = ref["feed_url"]
        message, keyboard = _build_rss_feed_detail(context.application, chat_id, feed_url)
        if not message:
            await query.answer(copy_text.with_deco("这份茶点不存在，或者已经被主人撤下啦。", 'ERROR'), show_alert=True)
            message, keyboard = _build_rss_list_view(context.application, 1)
        await query.edit_message_text(message, reply_markup=keyboard)
    
    elif data.startswith("panel_rss_remove_"):
        if not await db.is_admin(user_id):
            await query.answer(copy_text.perm_denied(), show_alert=True)
            return

        token = data.split("_")[-1]
        ref = _resolve_rss_reference(context.application, token, "feed")
        if not ref:
            await query.answer(copy_text.with_deco("这份订阅引用过期啦，主人重新打开茶点单看一眼嘛。", 'ERROR'), show_alert=True)
            return

        chat_id = str(ref["chat_id"])
        feed_url = ref["feed_url"]
        data_file = context.application.bot_data.get("rss_data_file", config.RSS_DATA_FILE)
        success = rss_data_manager.remove_feed(chat_id, feed_url, data_file)
        if success:
            await query.answer(copy_text.with_deco("哼，说撤就撤吗？这份茶点已经替主人收下来啦，可找不回来哦。", 'TSUNDERE'), show_alert=True)
        else:
            await query.answer(copy_text.with_deco("小本本里没有这份茶点呢。", 'ERROR'), show_alert=True)

        message, keyboard = _build_rss_list_view(context.application, 1)
        await query.edit_message_text(message, reply_markup=keyboard)
    
    elif data.startswith("panel_rss_kwrm_"):
        if not await db.is_admin(user_id):
            await query.answer(copy_text.perm_denied(), show_alert=True)
            return

        token = data.split("_")[-1]
        ref = _resolve_rss_reference(context.application, token, "keyword")
        if not ref:
            await query.answer(copy_text.with_deco("这个口味词的引用过期啦，主人重新打开茶点单看一眼嘛。", 'ERROR'), show_alert=True)
            return

        chat_id = str(ref["chat_id"])
        feed_url = ref["feed_url"]
        keyword = ref["keyword"]
        data_file = context.application.bot_data.get("rss_data_file", config.RSS_DATA_FILE)
        success = rss_data_manager.remove_keyword(chat_id, feed_url, keyword, data_file)
        if success:
            await query.answer(copy_text.with_deco(f"口味词已经替主人取下来啦：{keyword}", 'OK'), show_alert=True)
        else:
            await query.answer(copy_text.with_deco("小本本里没有这个口味词呢。", 'ERROR'), show_alert=True)

        message, keyboard = _build_rss_feed_detail(context.application, chat_id, feed_url)
        if not message:
            message, keyboard = _build_rss_list_view(context.application, 1)
        await query.edit_message_text(message, reply_markup=keyboard)
    
    elif data == "panel_autoreply_toggle":
        if not await db.is_admin(user_id):
            await query.answer(copy_text.perm_denied(), show_alert=True)
            return
        
        is_enabled = await db.get_autoreply_enabled()
        await db.set_autoreply_enabled(not is_enabled)
        new_status = "正在值班" if not is_enabled else "正在休息"
        await query.answer(copy_text.autoreply_status_changed(new_status), show_alert=True)
        
        is_enabled = await db.get_autoreply_enabled()
        status_text = "正在值班" if is_enabled else "正在休息"
        
        message = (
            f"自动回复女仆管理\n\n"
            f"当前状态: {status_text}\n\n"
            f"主人，请选择要安排的工作："
        )
        
        keyboard = [
            [
                InlineKeyboardButton(
                    copy_text.BTN_AUTOREPLY_REST if is_enabled else copy_text.BTN_AUTOREPLY_ON_DUTY,
                    callback_data="panel_autoreply_toggle"
                )
            ],
            [InlineKeyboardButton(copy_text.BTN_KB_LIST, callback_data="panel_autoreply_kb_list_page_1")],
            [InlineKeyboardButton(copy_text.BTN_KB_ADD, callback_data="panel_autoreply_kb_add")],
            [InlineKeyboardButton(copy_text.BTN_BACK_PANEL, callback_data="panel_back")],
        ]
        
        await query.edit_message_text(
            message,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='Markdown'
        )
    
    elif data.startswith("panel_autoreply_kb_list_page_"):
        if not await db.is_admin(user_id):
            await query.answer(copy_text.perm_denied(), show_alert=True)
            return
        
        try:
            page = int(data.split("_")[5])
        except (ValueError, IndexError):
            page = 1
        
        entries = await db.get_all_knowledge_entries()
        if not entries:
            back_keyboard = InlineKeyboardMarkup([[InlineKeyboardButton(copy_text.BTN_BACK_PANEL, callback_data="panel_back")]])
            await query.edit_message_text(copy_text.kb_empty(), reply_markup=back_keyboard)
            return
        
        MESSAGES_PER_PAGE = 5
        total_pages = (len(entries) + MESSAGES_PER_PAGE - 1) // MESSAGES_PER_PAGE
        if page < 1:
            page = 1
        elif page > total_pages:
            page = total_pages
        
        start_idx = (page - 1) * MESSAGES_PER_PAGE
        end_idx = start_idx + MESSAGES_PER_PAGE
        page_entries = entries[start_idx:end_idx]
        
        message = f"知识小本本条目 (第 {page}/{total_pages} 页)\n\n"
        keyboard = []
        
        for entry in page_entries:
            title = entry['title'][:30] + "..." if len(entry['title']) > 30 else entry['title']
            keyboard.append([
                InlineKeyboardButton(
                    f"{title}",
                    callback_data=f"panel_autoreply_kb_view_{entry['id']}"
                )
            ])
            keyboard.append([
                InlineKeyboardButton(
                    copy_text.BTN_KB_EDIT,
                    callback_data=f"panel_autoreply_kb_edit_{entry['id']}"
                ),
                InlineKeyboardButton(
                    copy_text.BTN_KB_DELETE,
                    callback_data=f"panel_autoreply_kb_delete_{entry['id']}"
                )
            ])
        
        nav_buttons = []
        if page > 1:
            nav_buttons.append(InlineKeyboardButton(copy_text.BTN_PREV_PAGE, callback_data=f"panel_autoreply_kb_list_page_{page-1}"))
        if page < total_pages:
            nav_buttons.append(InlineKeyboardButton(copy_text.BTN_NEXT_PAGE, callback_data=f"panel_autoreply_kb_list_page_{page+1}"))
        if nav_buttons:
            keyboard.append(nav_buttons)
        
        keyboard.append([InlineKeyboardButton(copy_text.BTN_BACK_PANEL, callback_data="panel_back")])
        
        await query.edit_message_text(
            message,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='Markdown'
        )
    
    elif data.startswith("panel_autoreply_kb_view_"):
        if not await db.is_admin(user_id):
            await query.answer(copy_text.perm_denied(), show_alert=True)
            return
        
        try:
            entry_id = int(data.split("_")[4])
        except (ValueError, IndexError):
            await query.answer(copy_text.kb_entry_id_invalid(), show_alert=True)
            return
        
        entry = await db.get_knowledge_entry(entry_id)
        if not entry:
            await query.answer(copy_text.kb_entry_missing(), show_alert=True)
            return
        
        message = (
            f"知识便签详情\n\n"
            f"ID: {entry['id']}\n"
            f"标题: {entry['title']}\n"
            f"内容: {entry['content']}\n\n"
            f"创建时间: {entry['created_at']}\n"
            f"更新时间: {entry['updated_at']}"
        )
        
        keyboard = [
            [
                InlineKeyboardButton(copy_text.BTN_KB_EDIT, callback_data=f"panel_autoreply_kb_edit_{entry_id}"),
                InlineKeyboardButton(copy_text.BTN_KB_DELETE, callback_data=f"panel_autoreply_kb_delete_{entry_id}")
            ],
            [InlineKeyboardButton(copy_text.BTN_BACK_KB_LIST, callback_data="panel_autoreply_kb_list_page_1")],
            [InlineKeyboardButton(copy_text.BTN_BACK_PANEL, callback_data="panel_back")],
        ]
        
        await query.edit_message_text(
            message,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='Markdown'
        )
    
    elif data.startswith("panel_autoreply_kb_edit_"):
        if not await db.is_admin(user_id):
            await query.answer(copy_text.perm_denied(), show_alert=True)
            return
        
        try:
            entry_id = int(data.split("_")[4])
        except (ValueError, IndexError):
            await query.answer(copy_text.kb_entry_id_invalid(), show_alert=True)
            return
        
        entry = await db.get_knowledge_entry(entry_id)
        if not entry:
            await query.answer(copy_text.kb_entry_missing(), show_alert=True)
            return
        
        back_keyboard = InlineKeyboardMarkup([[InlineKeyboardButton(copy_text.BTN_BACK_PANEL, callback_data="panel_back")]])
        await query.edit_message_text(
            copy_text.kb_edit_hint(entry['id'], entry['title'], entry['content'], entry_id),
            parse_mode='Markdown',
            reply_markup=back_keyboard
        )
    
    elif data.startswith("panel_autoreply_kb_delete_"):
        if not await db.is_admin(user_id):
            await query.answer(copy_text.perm_denied(), show_alert=True)
            return
        
        try:
            entry_id = int(data.split("_")[4])
        except (ValueError, IndexError):
            await query.answer(copy_text.kb_entry_id_invalid(), show_alert=True)
            return
        
        entry = await db.get_knowledge_entry(entry_id)
        if not entry:
            await query.answer(copy_text.kb_entry_missing(), show_alert=True)
            return
        
        await db.delete_knowledge_entry(entry_id)
        await query.answer(copy_text.kb_deleted(entry['title']), show_alert=True)
        
        entries = await db.get_all_knowledge_entries()
        if not entries:
            back_keyboard = InlineKeyboardMarkup([[InlineKeyboardButton(copy_text.BTN_BACK_PANEL, callback_data="panel_back")]])
            await query.edit_message_text(copy_text.kb_empty(), reply_markup=back_keyboard)
            return
        
        page = 1
        MESSAGES_PER_PAGE = 5
        total_pages = (len(entries) + MESSAGES_PER_PAGE - 1) // MESSAGES_PER_PAGE
        
        start_idx = (page - 1) * MESSAGES_PER_PAGE
        end_idx = start_idx + MESSAGES_PER_PAGE
        page_entries = entries[start_idx:end_idx]
        
        message = f"知识小本本条目 (第 {page}/{total_pages} 页)\n\n"
        keyboard = []
        
        for entry in page_entries:
            title = entry['title'][:30] + "..." if len(entry['title']) > 30 else entry['title']
            keyboard.append([
                InlineKeyboardButton(
                    f"{title}",
                    callback_data=f"panel_autoreply_kb_view_{entry['id']}"
                )
            ])
            keyboard.append([
                InlineKeyboardButton(
                    copy_text.BTN_KB_EDIT,
                    callback_data=f"panel_autoreply_kb_edit_{entry['id']}"
                ),
                InlineKeyboardButton(
                    copy_text.BTN_KB_DELETE,
                    callback_data=f"panel_autoreply_kb_delete_{entry['id']}"
                )
            ])
        
        nav_buttons = []
        if page < total_pages:
            nav_buttons.append(InlineKeyboardButton(copy_text.BTN_NEXT_PAGE, callback_data=f"panel_autoreply_kb_list_page_{page+1}"))
        if nav_buttons:
            keyboard.append(nav_buttons)
        
        keyboard.append([InlineKeyboardButton(copy_text.BTN_BACK_PANEL, callback_data="panel_back")])
        
        await query.edit_message_text(
            message,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='Markdown'
        )
    
    elif data == "panel_autoreply_kb_add":
        if not await db.is_admin(user_id):
            await query.answer(copy_text.perm_denied(), show_alert=True)
            return
        
        back_keyboard = InlineKeyboardMarkup([[InlineKeyboardButton(copy_text.BTN_BACK_PANEL, callback_data="panel_back")]])
        await query.edit_message_text(
            copy_text.kb_add_hint(),
            parse_mode='Markdown',
            reply_markup=back_keyboard
        )
    
    elif data == "panel_network_test":
        if not await db.is_admin(user_id):
            await query.answer(copy_text.perm_denied(), show_alert=True)
            return
        
        from network_test.state import user_data
        if user_id in user_data:
            operation = user_data[user_id].get("operation")
            if operation in ["addserver", "rmserver", "installnexttrace"]:
                prompt_msg_id = user_data[user_id].get("prompt_message_id")
                current_msg_id = query.message.message_id
                if prompt_msg_id and prompt_msg_id != current_msg_id:
                    try:
                        await context.bot.delete_message(
                            chat_id=user_data[user_id].get("chat_id", query.message.chat.id),
                            message_id=prompt_msg_id
                        )
                    except Exception:
                        pass
                del user_data[user_id]
        
        from network_test.config import SERVERS, AUTHORIZED_USERS, ADMIN_USERS
        from network_test.utils import check_is_admin
        
        is_admin = check_is_admin(user_id, ADMIN_USERS)
        server_count = len(SERVERS) if SERVERS else 0
        user_count = len(AUTHORIZED_USERS) if AUTHORIZED_USERS else 0
        
        message = (
            f"网络测试茶具管理\n\n"
            f"宅邸统计:\n"
            f"服务器茶具数: {server_count}\n"
            f"授权主人数: {user_count}\n\n"
            f"主人，请挑选要执行的工作："
        )
        
        keyboard = [
            [InlineKeyboardButton("Ping 测试", callback_data="panel_nt_ping"), InlineKeyboardButton("路由追踪", callback_data="panel_nt_nexttrace")],
        ]
        
        if is_admin:
            keyboard.extend([
                [InlineKeyboardButton("登记授权主人", callback_data="panel_nt_adduser"), InlineKeyboardButton("移除授权主人", callback_data="panel_nt_rmuser")],
                [InlineKeyboardButton("登记服务器", callback_data="panel_nt_addserver"), InlineKeyboardButton("撤下服务器", callback_data="panel_nt_rmserver")],
                [InlineKeyboardButton("安装 NextTrace", callback_data="panel_nt_install")],
            ])
        
        keyboard.append([InlineKeyboardButton(copy_text.BTN_BACK_PANEL, callback_data="panel_back")])
        
        try:
            await query.edit_message_text(
                message,
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode='Markdown'
            )
        except BadRequest as e:
            if "Message to edit not found" in str(e) or "message is not modified" in str(e).lower():
                await query.message.reply_text(
                    message,
                    reply_markup=InlineKeyboardMarkup(keyboard),
                    parse_mode='Markdown'
                )
            else:
                raise
    
    elif data == "panel_nt_ping":
        if not await db.is_admin(user_id):
            await query.answer(copy_text.perm_denied(), show_alert=True)
            return
        
        back_keyboard = InlineKeyboardMarkup([[InlineKeyboardButton(copy_text.BTN_BACK_NETWORK_TEST, callback_data="panel_network_test")]])
        await query.edit_message_text(
            copy_text.with_deco_head(
                "Ping 测试\n\n"
                "女仆小抄：\n"
                "`/ping` - 交互式选择服务器\n"
                "`/ping <目标> [次数]` - 直接指定目标和次数\n\n"
                "示例：\n"
                "`/ping 8.8.8.8`\n"
                "`/ping google.com 10`",
                'ASK',
            ),
            parse_mode='Markdown',
            reply_markup=back_keyboard
        )
    
    elif data == "panel_nt_nexttrace":
        if not await db.is_admin(user_id):
            await query.answer(copy_text.perm_denied(), show_alert=True)
            return
        
        back_keyboard = InlineKeyboardMarkup([[InlineKeyboardButton(copy_text.BTN_BACK_NETWORK_TEST, callback_data="panel_network_test")]])
        await query.edit_message_text(
            copy_text.with_deco_head(
                "路由追踪\n\n"
                "女仆小抄：\n"
                "`/nexttrace` - 交互式选择服务器和模式\n"
                "`/nexttrace <目标>` - 直接指定目标\n\n"
                "示例：\n"
                "`/nexttrace 8.8.8.8`\n"
                "`/nexttrace google.com`",
                'ASK',
            ),
            parse_mode='Markdown',
            reply_markup=back_keyboard
        )
    
    elif data == "panel_nt_adduser":
        if not await db.is_admin(user_id):
            await query.answer(copy_text.perm_denied(), show_alert=True)
            return
        
        from network_test.utils import check_is_admin
        from network_test.config import ADMIN_USERS
        
        if not check_is_admin(user_id, ADMIN_USERS):
            await query.answer(copy_text.nt_panel_not_admin(), show_alert=True)
            return
        
        back_keyboard = InlineKeyboardMarkup([[InlineKeyboardButton(copy_text.BTN_BACK_NETWORK_TEST, callback_data="panel_network_test")]])
        await query.edit_message_text(
            copy_text.with_deco_head(
                "登记授权主人\n\n"
                "请这样吩咐女仆：\n"
                "`/adduser <user_id>`\n\n"
                "示例：\n"
                "`/adduser 123456789`",
                'ASK',
            ),
            parse_mode='Markdown',
            reply_markup=back_keyboard
        )
    
    elif data == "panel_nt_rmuser":
        if not await db.is_admin(user_id):
            await query.answer(copy_text.perm_denied(), show_alert=True)
            return
        
        from network_test.utils import check_is_admin
        from network_test.config import ADMIN_USERS
        
        if not check_is_admin(user_id, ADMIN_USERS):
            await query.answer(copy_text.nt_panel_not_admin(), show_alert=True)
            return
        
        back_keyboard = InlineKeyboardMarkup([[InlineKeyboardButton(copy_text.BTN_BACK_NETWORK_TEST, callback_data="panel_network_test")]])
        await query.edit_message_text(
            copy_text.with_deco_head(
                "移除授权主人\n\n"
                "请这样吩咐女仆：\n"
                "`/rmuser <user_id>`\n\n"
                "示例：\n"
                "`/rmuser 123456789`",
                'ASK',
            ),
            parse_mode='Markdown',
            reply_markup=back_keyboard
        )
    
    elif data == "panel_nt_addserver":
        if not await db.is_admin(user_id):
            await query.answer(copy_text.perm_denied(), show_alert=True)
            return
        
        from network_test.utils import check_is_admin
        from network_test.config import ADMIN_USERS, SERVERS
        from network_test.state import user_data
        from network_test.utils import schedule_delete_message
        
        if not check_is_admin(user_id, ADMIN_USERS):
            await query.answer(copy_text.nt_panel_not_admin(), show_alert=True)
            return
        
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton(copy_text.BTN_BACK_NETWORK_TEST, callback_data="panel_network_test")]
        ])
        msg = await query.message.reply_text(
            copy_text.with_deco_head(
                "服务器登记女仆向导开始值班啦！\n\n"
                "请主人按提示一步一步交代服务器信息。\n"
                "步骤 1/5: 请告诉女仆服务器名称（如：日本 - Acck）：\n\n"
                "主人可以随时输入 /cancel 取消登记流程",
                'ASK',
            ),
            reply_markup=keyboard
        )
        
        user_data[user_id] = {
            "operation": "addserver",
            "step": 1,
            "server_data": {},
            "chat_id": msg.chat_id,
            "message_id": msg.message_id,
            "prompt_message_id": msg.message_id,
            "from_panel": True
        }
        
        try:
            await query.message.delete()
        except:
            pass
    
    elif data == "panel_nt_rmserver":
        if not await db.is_admin(user_id):
            await query.answer(copy_text.perm_denied(), show_alert=True)
            return
        
        from network_test.utils import check_is_admin
        from network_test.config import ADMIN_USERS, SERVERS
        from network_test.state import user_data
        
        if not check_is_admin(user_id, ADMIN_USERS):
            await query.answer(copy_text.nt_panel_not_admin(), show_alert=True)
            return
        
        if not SERVERS:
            back_keyboard = InlineKeyboardMarkup([[InlineKeyboardButton(copy_text.BTN_BACK_NETWORK_TEST, callback_data="panel_network_test")]])
            await query.edit_message_text(copy_text.nt_no_servers_registered(), reply_markup=back_keyboard)
            return
            
        keyboard = []
        for idx, server_info in enumerate(SERVERS):
            btn = InlineKeyboardButton(
                copy_text.nt_server_label(server_info['name'], server_info['host'], server_info['port']),
                callback_data=f"nt_rmserver_{idx}"
            )
            keyboard.append([btn])
        
        keyboard.append([InlineKeyboardButton(copy_text.BTN_BACK_NETWORK_TEST, callback_data="panel_network_test")])
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        msg = await query.message.reply_text(
            copy_text.nt_pick_remove_target(),
            reply_markup=reply_markup
        )
        
        user_data[user_id] = {
            "operation": "rmserver",
            "chat_id": msg.chat_id,
            "message_id": msg.message_id,
            "prompt_message_id": msg.message_id,
            "from_panel": True
        }
        
        try:
            await query.message.delete()
        except:
            pass
    
    elif data == "panel_nt_install":
        if not await db.is_admin(user_id):
            await query.answer(copy_text.perm_denied(), show_alert=True)
            return
        
        from network_test.utils import check_is_admin
        from network_test.config import ADMIN_USERS, SERVERS
        from network_test.state import user_data
        
        if not check_is_admin(user_id, ADMIN_USERS):
            await query.answer(copy_text.nt_panel_not_admin(), show_alert=True)
            return
        
        if not SERVERS:
            back_keyboard = InlineKeyboardMarkup([[InlineKeyboardButton(copy_text.BTN_BACK_NETWORK_TEST, callback_data="panel_network_test")]])
            await query.edit_message_text(copy_text.nt_no_servers_registered_hint(), reply_markup=back_keyboard)
            return
            
        keyboard = []
        for idx, server_info in enumerate(SERVERS):
            btn = InlineKeyboardButton(
                copy_text.nt_server_label(server_info['name'], server_info['host'], server_info['port']),
                callback_data=f"nt_installnexttrace_{idx}"
            )
            keyboard.append([btn])
        
        keyboard.append([InlineKeyboardButton(copy_text.BTN_BACK_NETWORK_TEST, callback_data="panel_network_test")])
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        msg = await query.message.reply_text(
            copy_text.nt_pick_install_target(),
            reply_markup=reply_markup
        )
        
        user_data[user_id] = {
            "operation": "installnexttrace",
            "chat_id": msg.chat_id,
            "message_id": msg.message_id,
            "prompt_message_id": msg.message_id,
            "from_panel": True
        }
        
        try:
            await query.message.delete()
        except:
            pass
    
    elif data.startswith("usercard_block_"):
        from services.blacklist import block_user, unblock_user

        if not await db.is_admin(user_id):
            return

        try:
            user_id_to_block = int(data.split("_")[2])
        except (ValueError, IndexError):
            return

        is_blocked, _ = await db.is_blacklisted(user_id_to_block)
        if is_blocked:
            await unblock_user(user_id_to_block)
        else:
            await block_user(
                user_id_to_block,
                "来访主人档案快捷锁门",
                user_id,
                permanent=True
            )

        await _refresh_usercard_keyboard(query, user_id_to_block)

    elif data.startswith("usercard_exempt_"):
        if not await db.is_admin(user_id):
            return

        try:
            user_id_to_exempt = int(data.split("_")[2])
        except (ValueError, IndexError):
            return

        is_exempted = await db.is_exempted(user_id_to_exempt)
        if is_exempted:
            await db.remove_exemption(user_id_to_exempt)
        else:
            await db.add_exemption(
                user_id_to_exempt,
                is_permanent=True,
                exempted_by=user_id,
                reason="来访主人档案快捷发通行证"
            )

        await _refresh_usercard_keyboard(query, user_id_to_exempt)

    elif data.startswith("unblock_"):
        from services.blacklist import verify_unblock_answer
        answer = data.split("_", 1)[1]
        message, success = await verify_unblock_answer(user_id, answer)
        
        await query.edit_message_text(text=message, reply_markup=None)
        
    elif data.startswith("admin_unblock_"):
        from services import blacklist
        
        user_id_to_unblock = int(data.split("_")[2])
        
        if not await db.is_admin(user_id):
            await query.answer(copy_text.perm_denied(), show_alert=True)
            return
            
        response = await blacklist.unblock_user(user_id_to_unblock)
        await query.answer(response, show_alert=True)

        current_page = 1
        message_text = query.message.text or ""
        reply_markup_str = str(query.message.reply_markup) if query.message.reply_markup else ""
        
        is_panel = "panel_blacklist" in reply_markup_str or "panel_stats_blacklist" in reply_markup_str
        is_stats_page = "黑名单小本本" in message_text or "stats_list_blacklist" in reply_markup_str
        
        if "第" in message_text and "/" in message_text:
            try:
                match = re.search(r'第\s*(\d+)/', message_text)
                if match:
                    current_page = int(match.group(1))
            except:
                pass
        
        if is_panel:
            message, keyboard = await blacklist.get_blacklist_keyboard(page=current_page)
            if keyboard:
                keyboard_buttons = [list(row) for row in keyboard.inline_keyboard]
                keyboard_buttons.append([InlineKeyboardButton(copy_text.BTN_BACK_PANEL, callback_data="panel_back")])
                keyboard = InlineKeyboardMarkup(keyboard_buttons)
            else:
                keyboard = InlineKeyboardMarkup([[InlineKeyboardButton(copy_text.BTN_BACK_PANEL, callback_data="panel_back")]])
            await query.edit_message_text(
                text=message,
                reply_markup=keyboard,
                parse_mode='Markdown'
            )
        elif is_stats_page:
            message, keyboard = await blacklist.get_blacklist_keyboard_detailed(page=current_page)
            if keyboard:
                keyboard_buttons = [list(row) for row in keyboard.inline_keyboard]
                for i, row in enumerate(keyboard_buttons):
                    for j, button in enumerate(row):
                        if button.callback_data == "stats_back_to_menu":
                            keyboard_buttons[i][j] = InlineKeyboardButton(copy_text.BTN_BACK_PANEL, callback_data="panel_back")
                            break
                keyboard = InlineKeyboardMarkup(keyboard_buttons)
            else:
                keyboard = InlineKeyboardMarkup([[InlineKeyboardButton(copy_text.BTN_BACK_PANEL, callback_data="panel_back")]])
            await query.edit_message_text(
                text=message,
                reply_markup=keyboard,
                parse_mode='Markdown'
            )
        else:
            message, keyboard = await blacklist.get_blacklist_keyboard(page=current_page)
            if keyboard:
                await query.edit_message_text(
                    text=message,
                    reply_markup=keyboard,
                    parse_mode='Markdown'
                )
            else:
                await query.edit_message_text(text=message)
    
    elif data.startswith("blacklist_page_"):
        from services import blacklist
        
        if not await db.is_admin(user_id):
            await query.answer(copy_text.perm_denied(), show_alert=True)
            return
        
        try:
            page = int(data.split("_")[2])
        except (ValueError, IndexError):
            await query.answer(copy_text.page_invalid(), show_alert=True)
            return
        
        message, keyboard = await blacklist.get_blacklist_keyboard(page=page)
        if keyboard:
            await query.edit_message_text(
                text=message,
                reply_markup=keyboard,
                parse_mode='Markdown'
            )
        else:
            await query.edit_message_text(text=message)
    
    elif data.startswith("filtered_page_"):
        from .admin_handler import _format_filtered_messages, _get_filtered_messages_keyboard
        
        if not await db.is_admin(user_id):
            await query.answer(copy_text.perm_denied(), show_alert=True)
            return
        
        try:
            page = int(data.split("_")[2])
        except (ValueError, IndexError):
            await query.answer(copy_text.page_invalid(), show_alert=True)
            return
        
        MESSAGES_PER_PAGE = 5

        total_count = await db.get_filtered_messages_count()
        
        if total_count == 0:
            await query.edit_message_text(copy_text.filtered_empty())
            return
        
        total_pages = (total_count + MESSAGES_PER_PAGE - 1) // MESSAGES_PER_PAGE

        if page < 1:
            page = 1
        elif page > total_pages:
            page = total_pages

        offset = (page - 1) * MESSAGES_PER_PAGE

        messages = await db.get_filtered_messages(MESSAGES_PER_PAGE, offset)
        
        if not messages:
            await query.edit_message_text(copy_text.filtered_empty())
            return

        response = await _format_filtered_messages(messages, page, total_pages)

        keyboard = await _get_filtered_messages_keyboard(page, total_pages)

        if keyboard:
            await query.edit_message_text(response, reply_markup=keyboard)
        else:
            await query.edit_message_text(response)
    
    elif data.startswith("panel_exemptions_page_"):
        from services import blacklist
        
        if not await db.is_admin(user_id):
            await query.answer(copy_text.perm_denied(), show_alert=True)
            return
        
        try:
            page = int(data.split("_")[3])
        except (ValueError, IndexError):
            await query.answer(copy_text.page_invalid(), show_alert=True)
            return
        
        message, keyboard = await blacklist.get_exemptions_keyboard(page=page)
        
        if keyboard:
            keyboard_buttons = [list(row) for row in keyboard.inline_keyboard]
            keyboard_buttons.append([InlineKeyboardButton(copy_text.BTN_BACK_PANEL, callback_data="panel_back")])
            keyboard = InlineKeyboardMarkup(keyboard_buttons)
        else:
            keyboard = InlineKeyboardMarkup([[InlineKeyboardButton(copy_text.BTN_BACK_PANEL, callback_data="panel_back")]])
        
        if keyboard:
            await query.edit_message_text(
                text=message,
                reply_markup=keyboard,
                parse_mode='Markdown'
            )
        else:
            await query.edit_message_text(text=message)
    
    elif data.startswith("admin_remove_exemption_"):
        from services import blacklist
        
        if not await db.is_admin(user_id):
            await query.answer(copy_text.perm_denied(), show_alert=True)
            return
        
        try:
            user_id_to_remove = int(data.split("_")[3])
        except (ValueError, IndexError):
            await query.answer(copy_text.user_id_invalid(), show_alert=True)
            return
        
        await db.remove_exemption(user_id_to_remove)
        await query.answer(copy_text.with_deco(f"已经替主人把 {user_id_to_remove} 的通行证收回来啦", 'OK'), show_alert=True)
        
        current_page = 1
        message_text = query.message.text or ""
        if "第" in message_text and "/" in message_text:
            try:
                match = re.search(r'第\s*(\d+)/', message_text)
                if match:
                    current_page = int(match.group(1))
            except:
                pass
        
        message, keyboard = await blacklist.get_exemptions_keyboard(page=current_page)
        
        if keyboard:
            keyboard_buttons = [list(row) for row in keyboard.inline_keyboard]
            keyboard_buttons.append([InlineKeyboardButton(copy_text.BTN_BACK_PANEL, callback_data="panel_back")])
            keyboard = InlineKeyboardMarkup(keyboard_buttons)
        else:
            keyboard = InlineKeyboardMarkup([[InlineKeyboardButton(copy_text.BTN_BACK_PANEL, callback_data="panel_back")]])
        
        if keyboard:
            await query.edit_message_text(
                text=message,
                reply_markup=keyboard,
                parse_mode='Markdown'
            )
        else:
            await query.edit_message_text(text=message)
    
    elif data.startswith("stats_list_all_users_page_"):
        from services.blacklist import get_all_users_keyboard
        
        if not await db.is_admin(user_id):
            await query.answer(copy_text.perm_denied(), show_alert=True)
            return
        
        try:
            page = int(data.split("_")[5])
        except (ValueError, IndexError):
            await query.answer(copy_text.page_invalid(), show_alert=True)
            return
        
        message, keyboard = await get_all_users_keyboard(page=page)
        if keyboard:
            await query.edit_message_text(
                text=message,
                reply_markup=keyboard,
                parse_mode='Markdown'
            )
        else:
            await query.edit_message_text(text=message, parse_mode='Markdown')
    
    elif data.startswith("stats_list_blacklist_page_"):
        from services.blacklist import get_blacklist_keyboard_detailed
        
        if not await db.is_admin(user_id):
            await query.answer(copy_text.perm_denied(), show_alert=True)
            return
        
        try:
            page = int(data.split("_")[4])
        except (ValueError, IndexError):
            await query.answer(copy_text.page_invalid(), show_alert=True)
            return
        
        message, keyboard = await get_blacklist_keyboard_detailed(page=page)
        if keyboard:
            await query.edit_message_text(
                text=message,
                reply_markup=keyboard,
                parse_mode='Markdown'
            )
        else:
            await query.edit_message_text(text=message, parse_mode='Markdown')
    
    elif data == "stats_back_to_menu":
        from .command_handler import stats
        
        if not await db.is_admin(user_id):
            await query.answer(copy_text.perm_denied(), show_alert=True)
            return
        
        total_users = await db.get_total_users_count()
        blocked_users = await db.get_blocked_users_count()
        
        stats_message = (
            f"女仆统计小本本\n"
            f"---------------------\n"
            f"接待过的主人: {total_users}\n"
            f"黑名单里的捣乱者: {blocked_users}\n\n"
            f"主人想翻哪一本记录呢："
        )
        
        keyboard = [
            [InlineKeyboardButton(copy_text.BTN_STATS_ALL_USERS, callback_data="stats_list_all_users_page_1")],
            [InlineKeyboardButton(copy_text.BTN_PANEL_BLACKLIST, callback_data="stats_list_blacklist_page_1")]
        ]
        
        await query.edit_message_text(
            text=stats_message,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='Markdown'
        )
    
    elif data.startswith("autoreply_"):
        if not await db.is_admin(user_id):
            await query.answer(copy_text.perm_denied(), show_alert=True)
            return
        
        if data == "autoreply_toggle":
            is_enabled = await db.get_autoreply_enabled()
            await db.set_autoreply_enabled(not is_enabled)
            new_status = "正在值班" if not is_enabled else "正在休息"
            await query.answer(copy_text.autoreply_status_changed(new_status), show_alert=True)
            
            is_enabled = await db.get_autoreply_enabled()
            status_text = "正在值班" if is_enabled else "正在休息"
            
            message = (
                f"自动回复女仆管理\n\n"
                f"当前状态: {status_text}\n\n"
                f"主人，请选择要安排的工作："
            )
            
            keyboard = [
                [
                    InlineKeyboardButton(
                        copy_text.BTN_AUTOREPLY_REST if is_enabled else copy_text.BTN_AUTOREPLY_ON_DUTY,
                        callback_data="autoreply_toggle"
                    )
                ],
                [InlineKeyboardButton(copy_text.BTN_KB_LIST, callback_data="autoreply_kb_list_page_1")],
                [InlineKeyboardButton(copy_text.BTN_KB_ADD, callback_data="autoreply_kb_add")],
            ]
            
            await query.edit_message_text(
                message,
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode='Markdown'
            )
        
        elif data.startswith("autoreply_kb_list_page_"):
            try:
                page = int(data.split("_")[4])
            except (ValueError, IndexError):
                page = 1
            
            entries = await db.get_all_knowledge_entries()
            if not entries:
                await query.edit_message_text(copy_text.kb_empty())
                return
            
            MESSAGES_PER_PAGE = 5
            total_pages = (len(entries) + MESSAGES_PER_PAGE - 1) // MESSAGES_PER_PAGE
            if page < 1:
                page = 1
            elif page > total_pages:
                page = total_pages
            
            start_idx = (page - 1) * MESSAGES_PER_PAGE
            end_idx = start_idx + MESSAGES_PER_PAGE
            page_entries = entries[start_idx:end_idx]
            
            message = f"知识小本本条目 (第 {page}/{total_pages} 页)\n\n"
            keyboard = []
            
            for entry in page_entries:
                title = entry['title'][:30] + "..." if len(entry['title']) > 30 else entry['title']
                keyboard.append([
                    InlineKeyboardButton(
                        f"{title}",
                        callback_data=f"autoreply_kb_view_{entry['id']}"
                    )
                ])
                keyboard.append([
                    InlineKeyboardButton(
                        copy_text.BTN_KB_EDIT,
                        callback_data=f"autoreply_kb_edit_{entry['id']}"
                    ),
                    InlineKeyboardButton(
                        copy_text.BTN_KB_DELETE,
                        callback_data=f"autoreply_kb_delete_{entry['id']}"
                    )
                ])
            
            nav_buttons = []
            if page > 1:
                nav_buttons.append(InlineKeyboardButton(copy_text.BTN_PREV_PAGE, callback_data=f"autoreply_kb_list_page_{page-1}"))
            if page < total_pages:
                nav_buttons.append(InlineKeyboardButton(copy_text.BTN_NEXT_PAGE, callback_data=f"autoreply_kb_list_page_{page+1}"))
            if nav_buttons:
                keyboard.append(nav_buttons)
            
            keyboard.append([InlineKeyboardButton(copy_text.BTN_BACK, callback_data="autoreply_back")])
            
            await query.edit_message_text(
                message,
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode='Markdown'
            )
        
        elif data.startswith("autoreply_kb_view_"):
            try:
                entry_id = int(data.split("_")[3])
            except (ValueError, IndexError):
                await query.answer(copy_text.kb_entry_id_invalid(), show_alert=True)
                return
            
            entry = await db.get_knowledge_entry(entry_id)
            if not entry:
                await query.answer(copy_text.kb_entry_missing(), show_alert=True)
                return
            
            message = (
                f"知识便签详情\n\n"
                f"ID: {entry['id']}\n"
                f"标题: {entry['title']}\n"
                f"内容: {entry['content']}\n\n"
                f"创建时间: {entry['created_at']}\n"
                f"更新时间: {entry['updated_at']}"
            )
            
            keyboard = [
                [
                    InlineKeyboardButton(copy_text.BTN_KB_EDIT, callback_data=f"autoreply_kb_edit_{entry_id}"),
                    InlineKeyboardButton(copy_text.BTN_KB_DELETE, callback_data=f"autoreply_kb_delete_{entry_id}")
                ],
                [InlineKeyboardButton(copy_text.BTN_BACK_KB_LIST, callback_data="autoreply_kb_list_page_1")]
            ]
            
            await query.edit_message_text(
                message,
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode='Markdown'
            )
        
        elif data.startswith("autoreply_kb_edit_"):
            try:
                entry_id = int(data.split("_")[3])
            except (ValueError, IndexError):
                await query.answer(copy_text.kb_entry_id_invalid(), show_alert=True)
                return
            
            entry = await db.get_knowledge_entry(entry_id)
            if not entry:
                await query.answer(copy_text.kb_entry_missing(), show_alert=True)
                return
            
            await query.edit_message_text(
                copy_text.kb_edit_hint(entry['id'], entry['title'], entry['content'], entry_id),
                parse_mode='Markdown'
            )
        
        elif data.startswith("autoreply_kb_delete_"):
            try:
                entry_id = int(data.split("_")[3])
            except (ValueError, IndexError):
                await query.answer(copy_text.kb_entry_id_invalid(), show_alert=True)
                return
            
            entry = await db.get_knowledge_entry(entry_id)
            if not entry:
                await query.answer(copy_text.kb_entry_missing(), show_alert=True)
                return
            
            await db.delete_knowledge_entry(entry_id)
            await query.answer(copy_text.kb_deleted(entry['title']), show_alert=True)
            
            entries = await db.get_all_knowledge_entries()
            if not entries:
                await query.edit_message_text(copy_text.kb_empty())
                return
            
            page = 1
            MESSAGES_PER_PAGE = 5
            total_pages = (len(entries) + MESSAGES_PER_PAGE - 1) // MESSAGES_PER_PAGE
            
            start_idx = (page - 1) * MESSAGES_PER_PAGE
            end_idx = start_idx + MESSAGES_PER_PAGE
            page_entries = entries[start_idx:end_idx]
            
            message = f"知识小本本条目 (第 {page}/{total_pages} 页)\n\n"
            keyboard = []
            
            for entry in page_entries:
                title = entry['title'][:30] + "..." if len(entry['title']) > 30 else entry['title']
                keyboard.append([
                    InlineKeyboardButton(
                        f"{title}",
                        callback_data=f"autoreply_kb_view_{entry['id']}"
                    )
                ])
                keyboard.append([
                    InlineKeyboardButton(
                        copy_text.BTN_KB_EDIT,
                        callback_data=f"autoreply_kb_edit_{entry['id']}"
                    ),
                    InlineKeyboardButton(
                        copy_text.BTN_KB_DELETE,
                        callback_data=f"autoreply_kb_delete_{entry['id']}"
                    )
                ])
            
            nav_buttons = []
            if page < total_pages:
                nav_buttons.append(InlineKeyboardButton(copy_text.BTN_NEXT_PAGE, callback_data=f"autoreply_kb_list_page_{page+1}"))
            if nav_buttons:
                keyboard.append(nav_buttons)
            
            keyboard.append([InlineKeyboardButton(copy_text.BTN_BACK, callback_data="autoreply_back")])
            
            await query.edit_message_text(
                message,
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode='Markdown'
            )
        
        elif data == "autoreply_back":
            is_enabled = await db.get_autoreply_enabled()
            status_text = "正在值班" if is_enabled else "正在休息"
            
            message = (
                f"自动回复女仆管理\n\n"
                f"当前状态: {status_text}\n\n"
                f"主人，请选择要安排的工作："
            )
            
            keyboard = [
                [
                    InlineKeyboardButton(
                        copy_text.BTN_AUTOREPLY_REST if is_enabled else copy_text.BTN_AUTOREPLY_ON_DUTY,
                        callback_data="autoreply_toggle"
                    )
                ],
                [InlineKeyboardButton(copy_text.BTN_KB_LIST, callback_data="autoreply_kb_list_page_1")],
                [InlineKeyboardButton(copy_text.BTN_KB_ADD, callback_data="autoreply_kb_add")],
            ]
            
            await query.edit_message_text(
                message,
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode='Markdown'
            )
        
        elif data == "autoreply_kb_add":
            await query.edit_message_text(
                copy_text.kb_add_hint(),
                parse_mode='Markdown'
            )
