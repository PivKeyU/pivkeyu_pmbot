import re
import secrets
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import BadRequest
from telegram.ext import ContextTypes
from services.verification import verify_answer, create_verification
from services.gemini_service import gemini_service
from database import models as db
from utils.media_converter import sticker_to_image
from services.thread_manager import get_or_create_thread, build_user_info_card_keyboard
from services import broadcast as broadcast_service
from .user_handler import _resend_message
from config import config
from rss import data_manager as rss_data_manager, settings as rss_settings
from rss import enable_feature as rss_enable_feature, disable_feature as rss_disable_feature

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
        [InlineKeyboardButton("黑名单小本本", callback_data="panel_blacklist_page_1"), InlineKeyboardButton("主人名册", callback_data="panel_stats")],
        [InlineKeyboardButton("拦截消息篮", callback_data="panel_filtered_page_1"), InlineKeyboardButton("自动回复女仆管理", callback_data="panel_autoreply")],
        [InlineKeyboardButton("通行证名单管理", callback_data="panel_exemptions_page_1"), InlineKeyboardButton("网络测试茶具管理", callback_data="panel_network_test")],
        [InlineKeyboardButton("广播与分组", callback_data="panel_broadcast"), InlineKeyboardButton("RSS 订阅茶点管理", callback_data="panel_rss")],
        [InlineKeyboardButton("AI 模型衣柜", callback_data="panel_ai_settings")],
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
        [InlineKeyboardButton("回女仆长面板", callback_data="panel_back")],
    ]

    return "\n".join(lines), InlineKeyboardMarkup(keyboard)


def _build_rss_list_view(application, page: int):
    feeds = _collect_rss_feeds()
    total = len(feeds)

    if total == 0:
        keyboard = [
            [InlineKeyboardButton("回 RSS 控制台", callback_data="panel_rss")],
            [InlineKeyboardButton("回女仆长面板", callback_data="panel_back")],
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
            InlineKeyboardButton("上一页", callback_data=f"panel_rss_list_page_{page-1}")
        )
    if page < total_pages:
        nav_buttons.append(
            InlineKeyboardButton("下一页", callback_data=f"panel_rss_list_page_{page+1}")
        )
    if nav_buttons:
        keyboard_rows.append(nav_buttons)

    keyboard_rows.append([InlineKeyboardButton("回 RSS 控制台", callback_data="panel_rss")])
    keyboard_rows.append([InlineKeyboardButton("回女仆长面板", callback_data="panel_back")])

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
    keyboard_rows.append([InlineKeyboardButton("回 RSS 控制台", callback_data="panel_rss")])

    return "\n".join(lines), InlineKeyboardMarkup(keyboard_rows)

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
        nav_buttons.append(InlineKeyboardButton("上一页", callback_data=f"{callback_prefix}_{page - 1}"))
    if page < total_pages:
        nav_buttons.append(InlineKeyboardButton("下一页", callback_data=f"{callback_prefix}_{page + 1}"))
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
    await query.answer()
    
    data = query.data
    user_id = query.from_user.id
    
    if data.startswith("nt_"):
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
                        text="女仆正在用 AI 小扫帚检查消息，请稍等...",
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
                        await analyzing_message.edit_text(f"这条消息被女仆拦进了小篮子，所以没有继续递送\n\n拦截理由：{reason}")
                    else:
                        await analyzing_message.delete()

                if should_forward:
                    thread_id, is_new = await get_or_create_thread(pending_update, context)
                    if not thread_id:
                        await pending_update.message.reply_text("女仆没能找到或创建专属会客厅，请联系管理员女仆长。")
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
                            
                            full_message = (
                                "主人，之前的会客厅已经关门啦。请重新完成小验证，女仆再为您递送消息。\n\n"
                                f"{question}"
                            )
                            
                            await pending_update.message.reply_text(
                                text=full_message,
                                reply_markup=keyboard
                            )
                        else:
                            print(f"发送消息时发生未知错误: {e}")
                            await pending_update.message.reply_text("递送消息时出了点小状况，请主人稍后再试。")
            else:
                await query.message.reply_text("门已经打开啦，主人现在可以发送消息。")
    
    elif data == "panel_back":
        if not await db.is_admin(user_id):
            await query.answer("主人没有权限吩咐这项工作哦。", show_alert=True)
            return
        
        message, keyboard = await _build_panel_back_view()
        
        await query.edit_message_text(
            message,
            reply_markup=keyboard,
            parse_mode='Markdown'
        )
    
    elif data == "panel_broadcast":
        if not await db.is_admin(user_id):
            await query.answer("主人没有权限吩咐这项工作哦。", show_alert=True)
            return

        groups = await db.get_all_user_groups()
        message = broadcast_service.build_broadcast_panel_text(groups)
        keyboard = broadcast_service.build_broadcast_panel_keyboard()
        await query.edit_message_text(message, reply_markup=keyboard)

    elif data == "broadcast_groups":
        if not await db.is_admin(user_id):
            await query.answer("主人没有权限吩咐这项工作哦。", show_alert=True)
            return

        groups = await db.get_all_user_groups()
        if not groups:
            await query.edit_message_text(
                "当前还没有分组。\n\n请使用 /group create <分组名> 创建。",
                reply_markup=broadcast_service.build_broadcast_panel_keyboard(),
            )
            return
        await query.edit_message_text("请选择要查看的分组：", reply_markup=broadcast_service.build_groups_keyboard(groups))

    elif data.startswith("broadcast_group_view_"):
        if not await db.is_admin(user_id):
            await query.answer("主人没有权限吩咐这项工作哦。", show_alert=True)
            return

        try:
            group_id = int(data.split("_")[-1])
        except (ValueError, IndexError):
            await query.answer("这个分组编号不对劲，主人。", show_alert=True)
            return

        group = await db.get_user_group_by_id(group_id)
        if not group:
            await query.answer("没有找到这个分组。", show_alert=True)
            return

        message = await broadcast_service.format_group_members(group['name'])
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("回分组列表", callback_data="broadcast_groups")],
            [InlineKeyboardButton("回广播与分组", callback_data="panel_broadcast")],
        ])
        await query.edit_message_text(message, reply_markup=keyboard)

    elif data.startswith("usercard_groups_"):
        if not await db.is_admin(user_id):
            await query.answer("主人没有权限吩咐这项工作哦。", show_alert=True)
            return

        try:
            target_user_id = int(data.split("_")[-1])
        except (ValueError, IndexError):
            await query.answer("这个用户 ID 不对劲，主人。", show_alert=True)
            return

        message, keyboard = await broadcast_service.build_user_group_keyboard(target_user_id)
        await query.message.reply_text(message, reply_markup=keyboard)

    elif data.startswith("usergroup_toggle_"):
        if not await db.is_admin(user_id):
            await query.answer("主人没有权限吩咐这项工作哦。", show_alert=True)
            return

        try:
            _, _, target_user_id_text, group_id_text = data.split("_", 3)
            target_user_id = int(target_user_id_text)
            group_id = int(group_id_text)
        except (ValueError, IndexError):
            await query.answer("这份分组请求不对劲，主人。", show_alert=True)
            return

        group = await db.get_user_group_by_id(group_id)
        if not group:
            await query.answer("分组不存在。", show_alert=True)
            return

        user_groups = await db.get_groups_for_user(target_user_id)
        user_group_ids = {item['id'] for item in user_groups}
        if group_id in user_group_ids:
            await db.remove_user_from_group(group['name'], target_user_id)
            await query.answer(f"已移出 {group['name']}")
        else:
            await db.add_user_to_group(group['name'], target_user_id, user_id)
            await query.answer(f"已加入 {group['name']}")

        message, keyboard = await broadcast_service.build_user_group_keyboard(target_user_id)
        try:
            await query.edit_message_text(message, reply_markup=keyboard)
        except BadRequest as exc:
            if "message is not modified" not in exc.message.lower():
                raise

    elif data.startswith("panel_blacklist_page_"):
        from services import blacklist
        
        if not await db.is_admin(user_id):
            await query.answer("主人没有权限吩咐这项工作哦。", show_alert=True)
            return
        
        try:
            page = int(data.split("_")[3])
        except (ValueError, IndexError):
            await query.answer("这个页码不对劲，主人。", show_alert=True)
            return
        
        message, keyboard = await blacklist.get_blacklist_keyboard(page=page)
        
        if keyboard:
            keyboard_buttons = list(keyboard.inline_keyboard)
            keyboard_buttons.append([InlineKeyboardButton("回女仆长面板", callback_data="panel_back")])
            keyboard = InlineKeyboardMarkup(keyboard_buttons)
        
        if keyboard:
            await query.edit_message_text(
                text=message,
                reply_markup=keyboard,
                parse_mode='Markdown'
            )
        else:
            back_keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("回女仆长面板", callback_data="panel_back")]])
            await query.edit_message_text(text=message, reply_markup=back_keyboard)
    
    elif data == "panel_stats":
        if not await db.is_admin(user_id):
            await query.answer("主人没有权限吩咐这项工作哦。", show_alert=True)
            return
        
        from services.blacklist import get_all_users_keyboard
        
        page = 1
        message, keyboard = await get_all_users_keyboard(
            page=page,
            callback_prefix="panel_stats_all_users_page_",
            back_callback="panel_back",
            back_text="回女仆长面板"
        )
        
        if keyboard:
            await query.edit_message_text(
                text=message,
                reply_markup=keyboard,
                parse_mode='Markdown'
            )
        else:
            back_keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("回女仆长面板", callback_data="panel_back")]])
            await query.edit_message_text(text=message, reply_markup=back_keyboard, parse_mode='Markdown')
    
    elif data.startswith("panel_stats_all_users_page_"):
        from services.blacklist import get_all_users_keyboard
        
        if not await db.is_admin(user_id):
            await query.answer("主人没有权限吩咐这项工作哦。", show_alert=True)
            return
        
        try:
            page = int(data.split("_")[5])
        except (ValueError, IndexError):
            await query.answer("这个页码不对劲，主人。", show_alert=True)
            return
        
        message, keyboard = await get_all_users_keyboard(
            page=page,
            callback_prefix="panel_stats_all_users_page_",
            back_callback="panel_back",
            back_text="回女仆长面板"
        )
        
        if keyboard:
            await query.edit_message_text(
                text=message,
                reply_markup=keyboard,
                parse_mode='Markdown'
            )
        else:
            keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("回女仆长面板", callback_data="panel_back")]])
            await query.edit_message_text(
                text=message,
                reply_markup=keyboard,
                parse_mode='Markdown'
            )
    
    elif data.startswith("panel_stats_blacklist_page_"):
        from services.blacklist import get_blacklist_keyboard_detailed
        
        if not await db.is_admin(user_id):
            await query.answer("主人没有权限吩咐这项工作哦。", show_alert=True)
            return
        
        try:
            page = int(data.split("_")[4])
        except (ValueError, IndexError):
            await query.answer("这个页码不对劲，主人。", show_alert=True)
            return
        
        message, keyboard = await get_blacklist_keyboard_detailed(page=page)
        
        if keyboard:
            keyboard_buttons = [list(row) for row in keyboard.inline_keyboard]
            for i, row in enumerate(keyboard_buttons):
                for j, button in enumerate(row):
                    if button.callback_data == "stats_back_to_menu":
                        keyboard_buttons[i][j] = InlineKeyboardButton("回女仆长面板", callback_data="panel_back")
                        break
            keyboard = InlineKeyboardMarkup(keyboard_buttons)
        
        if keyboard:
            await query.edit_message_text(
                text=message,
                reply_markup=keyboard,
                parse_mode='Markdown'
            )
        else:
            back_keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("回女仆长面板", callback_data="panel_back")]])
            await query.edit_message_text(text=message, reply_markup=back_keyboard, parse_mode='Markdown')
    
    elif data.startswith("panel_filtered_page_"):
        from .admin_handler import _format_filtered_messages, _get_filtered_messages_keyboard
        
        if not await db.is_admin(user_id):
            await query.answer("主人没有权限吩咐这项工作哦。", show_alert=True)
            return
        
        try:
            page = int(data.split("_")[3])
        except (ValueError, IndexError):
            await query.answer("这个页码不对劲，主人。", show_alert=True)
            return
        
        MESSAGES_PER_PAGE = 5

        total_count = await db.get_filtered_messages_count()
        
        if total_count == 0:
            back_keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("回女仆长面板", callback_data="panel_back")]])
            await query.edit_message_text("拦截篮里暂时没有消息。", reply_markup=back_keyboard)
            return
        
        total_pages = (total_count + MESSAGES_PER_PAGE - 1) // MESSAGES_PER_PAGE

        if page < 1:
            page = 1
        elif page > total_pages:
            page = total_pages

        offset = (page - 1) * MESSAGES_PER_PAGE

        messages = await db.get_filtered_messages(MESSAGES_PER_PAGE, offset)
        
        if not messages:
            back_keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("回女仆长面板", callback_data="panel_back")]])
            await query.edit_message_text("拦截篮里暂时没有消息。", reply_markup=back_keyboard)
            return

        response = await _format_filtered_messages(messages, page, total_pages)

        keyboard = await _get_filtered_messages_keyboard(page, total_pages, callback_prefix="panel_filtered_page_")
        
        if keyboard:
            keyboard_buttons = [list(row) for row in keyboard.inline_keyboard]
            keyboard_buttons.append([InlineKeyboardButton("回女仆长面板", callback_data="panel_back")])
            keyboard = InlineKeyboardMarkup(keyboard_buttons)
        else:
            keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("回女仆长面板", callback_data="panel_back")]])

        await query.edit_message_text(response, reply_markup=keyboard)
    
    elif data == "panel_autoreply":
        if not await db.is_admin(user_id):
            await query.answer("主人没有权限吩咐这项工作哦。", show_alert=True)
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
                    "让自动回复女仆休息" if is_enabled else "让自动回复女仆值班",
                    callback_data="panel_autoreply_toggle"
                )
            ],
            [InlineKeyboardButton("整理知识小本本", callback_data="panel_autoreply_kb_list_page_1")],
            [InlineKeyboardButton("新增知识便签", callback_data="panel_autoreply_kb_add")],
            [InlineKeyboardButton("回女仆长面板", callback_data="panel_back")],
        ]
        
        await query.edit_message_text(
            message,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='Markdown'
        )
    
    elif data == "panel_rss":
        if not await db.is_admin(user_id):
            await query.answer("主人没有权限吩咐这项工作哦。", show_alert=True)
            return

        message, keyboard = _build_rss_panel_view()
        await query.edit_message_text(message, reply_markup=keyboard)

    elif data == "panel_ai_settings":
        if not await db.is_admin(user_id):
            await query.answer("主人没有权限吩咐这项工作哦。", show_alert=True)
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
                InlineKeyboardButton(f"{'✅ ' if current_provider == 'gemini' else ''}启用 Gemini", callback_data="ai_set_provider_gemini"),
                InlineKeyboardButton(f"{'✅ ' if current_provider == 'openai' else ''}启用 OpenAI", callback_data="ai_set_provider_openai")
            ],
            [
                InlineKeyboardButton("整理 Gemini 模型", callback_data="ai_config_models_gemini"),
                InlineKeyboardButton("整理 OpenAI 模型", callback_data="ai_config_models_openai")
            ],
            [InlineKeyboardButton("回女仆长面板", callback_data="panel_back")]
        ]
        
        await query.edit_message_text(message, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')

    elif data.startswith("ai_set_provider_"):
        if not await db.is_admin(user_id): return
        
        new_provider = data.split("_")[3]
        async with db.db_manager.get_connection() as conn:
            await conn.execute("UPDATE settings SET value = ? WHERE key = 'ai_provider'", (new_provider,))
            await conn.commit()
            
        await query.answer(f"AI 提供商已换成 {new_provider.upper()}")
        
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
                InlineKeyboardButton(f"{'✅ ' if current_provider == 'gemini' else ''}启用 Gemini", callback_data="ai_set_provider_gemini"),
                InlineKeyboardButton(f"{'✅ ' if current_provider == 'openai' else ''}启用 OpenAI", callback_data="ai_set_provider_openai")
            ],
            [
                InlineKeyboardButton("整理 Gemini 模型", callback_data="ai_config_models_gemini"),
                InlineKeyboardButton("整理 OpenAI 模型", callback_data="ai_config_models_openai")
            ],
            [InlineKeyboardButton("回女仆长面板", callback_data="panel_back")]
        ]
        await query.edit_message_text(message, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')

    elif data.startswith("ai_config_models_"):
        if not await db.is_admin(user_id): return
        
        provider_type = data.split("_")[3]
        
        message = f"主人，请挑选要整理的 {provider_type.upper()} 功能模型:"
        
        keyboard = [
            [InlineKeyboardButton("内容审查模型", callback_data=f"ai_select_model_{provider_type}_filter")],
            [InlineKeyboardButton("小验证生成模型", callback_data=f"ai_select_model_{provider_type}_verification")],
            [InlineKeyboardButton("自动回复模型", callback_data=f"ai_select_model_{provider_type}_autoreply")],
            [InlineKeyboardButton("回模型衣柜", callback_data="panel_ai_settings")]
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
        
        await query.answer("女仆正在翻模型衣柜...", show_alert=False)
        
        try:
            models = await ai_service.get_available_models(provider_type)
        except Exception as e:
            await query.answer(f"翻模型衣柜失败: {e}", show_alert=True)
            return

        if not models:
             await query.answer("没能翻到模型列表，请主人检查 API Key 配置。", show_alert=True)
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
            await query.answer("模型选择已经过期啦，请主人重新打开列表。", show_alert=True)
            return

        provider_type = payload["provider_type"]
        feature_type = payload["feature_type"]
        model_name = payload["model_name"]
        setting_key = f"{provider_type}_model_{feature_type}"

        async with db.db_manager.get_connection() as conn:
            await conn.execute("UPDATE settings SET value = ? WHERE key = ?", (model_name, setting_key))
            await conn.commit()

        await query.answer(f"已替主人设置 {provider_type.upper()} {feature_type} 模型为 {model_name}")

        message = f"主人，请挑选要整理的 {provider_type.upper()} 功能模型:"
        keyboard = [
            [InlineKeyboardButton("内容审查模型", callback_data=f"ai_select_model_{provider_type}_filter")],
            [InlineKeyboardButton("小验证生成模型", callback_data=f"ai_select_model_{provider_type}_verification")],
            [InlineKeyboardButton("自动回复模型", callback_data=f"ai_select_model_{provider_type}_autoreply")],
            [InlineKeyboardButton("回模型衣柜", callback_data="panel_ai_settings")]
        ]
        await query.edit_message_text(message, reply_markup=InlineKeyboardMarkup(keyboard))

    elif data.startswith("setm:"):
        if not await db.is_admin(user_id): return
        
        try:
            _, p_code, f_code, model_name = data.split(":", 3)
        except ValueError:
            await query.answer("这份请求数据不对劲，主人。", show_alert=True)
            return
            
        p_map = {'g': 'gemini', 'o': 'openai'}
        f_map = {'f': 'filter', 'v': 'verification', 'a': 'autoreply'}
        
        provider_type = p_map.get(p_code, 'gemini')
        feature_type = f_map.get(f_code, 'filter')
        
        setting_key = f"{provider_type}_model_{feature_type}"
        
        async with db.db_manager.get_connection() as conn:
            await conn.execute("UPDATE settings SET value = ? WHERE key = ?", (model_name, setting_key))
            await conn.commit()
            
        await query.answer(f"已替主人设置 {provider_type.upper()} {feature_type} 模型为 {model_name}")
        
        message = f"主人，请挑选要整理的 {provider_type.upper()} 功能模型:"
        keyboard = [
            [InlineKeyboardButton("内容审查模型", callback_data=f"ai_select_model_{provider_type}_filter")],
            [InlineKeyboardButton("小验证生成模型", callback_data=f"ai_select_model_{provider_type}_verification")],
            [InlineKeyboardButton("自动回复模型", callback_data=f"ai_select_model_{provider_type}_autoreply")],
            [InlineKeyboardButton("回模型衣柜", callback_data="panel_ai_settings")]
        ]
        await query.edit_message_text(message, reply_markup=InlineKeyboardMarkup(keyboard))

    
    elif data == "panel_rss_toggle":
        if not await db.is_admin(user_id):
            await query.answer("主人没有权限吩咐这项工作哦。", show_alert=True)
            return

        app = context.application
        if rss_settings.is_enabled():
            changed = rss_disable_feature(app)
            if changed:
                await query.answer("RSS 女仆已去休息", show_alert=True)
        else:
            changed = rss_enable_feature(app)
            if changed:
                await query.answer("RSS 女仆已开始值班", show_alert=True)

        message, keyboard = _build_rss_panel_view()
        await query.edit_message_text(message, reply_markup=keyboard)
    
    elif data.startswith("panel_rss_list_page_"):
        if not await db.is_admin(user_id):
            await query.answer("主人没有权限吩咐这项工作哦。", show_alert=True)
            return

        try:
            page = int(data.split("_")[-1])
        except (ValueError, IndexError):
            await query.answer("这个页码不对劲，主人。", show_alert=True)
            return

        message, keyboard = _build_rss_list_view(context.application, page)
        await query.edit_message_text(message, reply_markup=keyboard)
    
    elif data.startswith("panel_rss_feed_"):
        if not await db.is_admin(user_id):
            await query.answer("主人没有权限吩咐这项工作哦。", show_alert=True)
            return

        token = data.split("_")[-1]
        ref = _resolve_rss_reference(context.application, token, "feed")
        if not ref:
            await query.answer("没找到这份订阅引用，请主人重新打开茶点单。", show_alert=True)
            return

        chat_id = str(ref["chat_id"])
        feed_url = ref["feed_url"]
        message, keyboard = _build_rss_feed_detail(context.application, chat_id, feed_url)
        if not message:
            await query.answer("这份茶点不存在，或已经被撤下啦。", show_alert=True)
            message, keyboard = _build_rss_list_view(context.application, 1)
        await query.edit_message_text(message, reply_markup=keyboard)
    
    elif data.startswith("panel_rss_remove_"):
        if not await db.is_admin(user_id):
            await query.answer("主人没有权限吩咐这项工作哦。", show_alert=True)
            return

        token = data.split("_")[-1]
        ref = _resolve_rss_reference(context.application, token, "feed")
        if not ref:
            await query.answer("没找到这份订阅引用。", show_alert=True)
            return

        chat_id = str(ref["chat_id"])
        feed_url = ref["feed_url"]
        data_file = context.application.bot_data.get("rss_data_file", config.RSS_DATA_FILE)
        success = rss_data_manager.remove_feed(chat_id, feed_url, data_file)
        if success:
            await query.answer("这份茶点已撤下。", show_alert=True)
        else:
            await query.answer("这份茶点不存在。", show_alert=True)

        message, keyboard = _build_rss_list_view(context.application, 1)
        await query.edit_message_text(message, reply_markup=keyboard)
    
    elif data.startswith("panel_rss_kwrm_"):
        if not await db.is_admin(user_id):
            await query.answer("主人没有权限吩咐这项工作哦。", show_alert=True)
            return

        token = data.split("_")[-1]
        ref = _resolve_rss_reference(context.application, token, "keyword")
        if not ref:
            await query.answer("没找到这个口味词引用。", show_alert=True)
            return

        chat_id = str(ref["chat_id"])
        feed_url = ref["feed_url"]
        keyword = ref["keyword"]
        data_file = context.application.bot_data.get("rss_data_file", config.RSS_DATA_FILE)
        success = rss_data_manager.remove_keyword(chat_id, feed_url, keyword, data_file)
        if success:
            await query.answer(f"已删除口味词: {keyword}", show_alert=True)
        else:
            await query.answer("这个口味词不存在。", show_alert=True)

        message, keyboard = _build_rss_feed_detail(context.application, chat_id, feed_url)
        if not message:
            message, keyboard = _build_rss_list_view(context.application, 1)
        await query.edit_message_text(message, reply_markup=keyboard)
    
    elif data == "panel_autoreply_toggle":
        if not await db.is_admin(user_id):
            await query.answer("主人没有权限吩咐这项工作哦。", show_alert=True)
            return
        
        is_enabled = await db.get_autoreply_enabled()
        await db.set_autoreply_enabled(not is_enabled)
        new_status = "正在值班" if not is_enabled else "正在休息"
        await query.answer(f"自动回复女仆{new_status}", show_alert=True)
        
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
                    "让自动回复女仆休息" if is_enabled else "让自动回复女仆值班",
                    callback_data="panel_autoreply_toggle"
                )
            ],
            [InlineKeyboardButton("整理知识小本本", callback_data="panel_autoreply_kb_list_page_1")],
            [InlineKeyboardButton("新增知识便签", callback_data="panel_autoreply_kb_add")],
            [InlineKeyboardButton("回女仆长面板", callback_data="panel_back")],
        ]
        
        await query.edit_message_text(
            message,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='Markdown'
        )
    
    elif data.startswith("panel_autoreply_kb_list_page_"):
        if not await db.is_admin(user_id):
            await query.answer("主人没有权限吩咐这项工作哦。", show_alert=True)
            return
        
        try:
            page = int(data.split("_")[5])
        except (ValueError, IndexError):
            page = 1
        
        entries = await db.get_all_knowledge_entries()
        if not entries:
            back_keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("回女仆长面板", callback_data="panel_back")]])
            await query.edit_message_text("知识小本本还是空的，主人。", reply_markup=back_keyboard)
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
                    "修改",
                    callback_data=f"panel_autoreply_kb_edit_{entry['id']}"
                ),
                InlineKeyboardButton(
                    "删除",
                    callback_data=f"panel_autoreply_kb_delete_{entry['id']}"
                )
            ])
        
        nav_buttons = []
        if page > 1:
            nav_buttons.append(InlineKeyboardButton("上一页", callback_data=f"panel_autoreply_kb_list_page_{page-1}"))
        if page < total_pages:
            nav_buttons.append(InlineKeyboardButton("下一页", callback_data=f"panel_autoreply_kb_list_page_{page+1}"))
        if nav_buttons:
            keyboard.append(nav_buttons)
        
        keyboard.append([InlineKeyboardButton("回女仆长面板", callback_data="panel_back")])
        
        await query.edit_message_text(
            message,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='Markdown'
        )
    
    elif data.startswith("panel_autoreply_kb_view_"):
        if not await db.is_admin(user_id):
            await query.answer("主人没有权限吩咐这项工作哦。", show_alert=True)
            return
        
        try:
            entry_id = int(data.split("_")[4])
        except (ValueError, IndexError):
            await query.answer("这个条目 ID 不对劲，主人再看一眼吧。", show_alert=True)
            return
        
        entry = await db.get_knowledge_entry(entry_id)
        if not entry:
            await query.answer("女仆翻遍小本本，也没找到这个条目。", show_alert=True)
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
                InlineKeyboardButton("修改", callback_data=f"panel_autoreply_kb_edit_{entry_id}"),
                InlineKeyboardButton("删除", callback_data=f"panel_autoreply_kb_delete_{entry_id}")
            ],
            [InlineKeyboardButton("回小本本列表", callback_data="panel_autoreply_kb_list_page_1")],
            [InlineKeyboardButton("回女仆长面板", callback_data="panel_back")],
        ]
        
        await query.edit_message_text(
            message,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='Markdown'
        )
    
    elif data.startswith("panel_autoreply_kb_edit_"):
        if not await db.is_admin(user_id):
            await query.answer("主人没有权限吩咐这项工作哦。", show_alert=True)
            return
        
        try:
            entry_id = int(data.split("_")[4])
        except (ValueError, IndexError):
            await query.answer("这个条目 ID 不对劲，主人再看一眼吧。", show_alert=True)
            return
        
        entry = await db.get_knowledge_entry(entry_id)
        if not entry:
            await query.answer("女仆翻遍小本本，也没找到这个条目。", show_alert=True)
            return
        
        back_keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("回女仆长面板", callback_data="panel_back")]])
        await query.edit_message_text(
            f"修改知识便签\n\n"
            f"ID: {entry['id']}\n"
            f"标题: {entry['title']}\n"
            f"内容: {entry['content']}\n\n"
            f"主人，请这样让女仆修改：\n"
            f"`/autoreply edit {entry_id} <新标题> <新内容>`\n\n"
            f"示例：\n"
            f"`/autoreply edit {entry_id} 新标题 新内容`",
            parse_mode='Markdown',
            reply_markup=back_keyboard
        )
    
    elif data.startswith("panel_autoreply_kb_delete_"):
        if not await db.is_admin(user_id):
            await query.answer("主人没有权限吩咐这项工作哦。", show_alert=True)
            return
        
        try:
            entry_id = int(data.split("_")[4])
        except (ValueError, IndexError):
            await query.answer("这个条目 ID 不对劲，主人再看一眼吧。", show_alert=True)
            return
        
        entry = await db.get_knowledge_entry(entry_id)
        if not entry:
            await query.answer("女仆翻遍小本本，也没找到这个条目。", show_alert=True)
            return
        
        await db.delete_knowledge_entry(entry_id)
        await query.answer(f"已删除便签: {entry['title']}", show_alert=True)
        
        entries = await db.get_all_knowledge_entries()
        if not entries:
            back_keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("回女仆长面板", callback_data="panel_back")]])
            await query.edit_message_text("知识小本本还是空的，主人。", reply_markup=back_keyboard)
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
                    "修改",
                    callback_data=f"panel_autoreply_kb_edit_{entry['id']}"
                ),
                InlineKeyboardButton(
                    "删除",
                    callback_data=f"panel_autoreply_kb_delete_{entry['id']}"
                )
            ])
        
        nav_buttons = []
        if page < total_pages:
            nav_buttons.append(InlineKeyboardButton("下一页", callback_data=f"panel_autoreply_kb_list_page_{page+1}"))
        if nav_buttons:
            keyboard.append(nav_buttons)
        
        keyboard.append([InlineKeyboardButton("回女仆长面板", callback_data="panel_back")])
        
        await query.edit_message_text(
            message,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='Markdown'
        )
    
    elif data == "panel_autoreply_kb_add":
        if not await db.is_admin(user_id):
            await query.answer("主人没有权限吩咐这项工作哦。", show_alert=True)
            return
        
        back_keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("回女仆长面板", callback_data="panel_back")]])
        await query.edit_message_text(
            "新增知识便签\n\n"
            "主人，请这样交给女仆新便签：\n"
            "`/autoreply add <标题> <内容>`\n\n"
            "示例：\n"
            "`/autoreply add 常见问题 这是问题的答案`",
            parse_mode='Markdown',
            reply_markup=back_keyboard
        )
    
    elif data == "panel_network_test":
        if not await db.is_admin(user_id):
            await query.answer("主人没有权限吩咐这项工作哦。", show_alert=True)
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
        
        keyboard.append([InlineKeyboardButton("回女仆长面板", callback_data="panel_back")])
        
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
            await query.answer("主人没有权限吩咐这项工作哦。", show_alert=True)
            return
        
        back_keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("回网络测试茶具", callback_data="panel_network_test")]])
        await query.edit_message_text(
            "Ping 测试\n\n"
            "女仆小抄：\n"
            "`/ping` - 交互式选择服务器\n"
            "`/ping <目标> [次数]` - 直接指定目标和次数\n\n"
            "示例：\n"
            "`/ping 8.8.8.8`\n"
            "`/ping google.com 10`",
            parse_mode='Markdown',
            reply_markup=back_keyboard
        )
    
    elif data == "panel_nt_nexttrace":
        if not await db.is_admin(user_id):
            await query.answer("主人没有权限吩咐这项工作哦。", show_alert=True)
            return
        
        back_keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("回网络测试茶具", callback_data="panel_network_test")]])
        await query.edit_message_text(
            "路由追踪\n\n"
            "女仆小抄：\n"
            "`/nexttrace` - 交互式选择服务器和模式\n"
            "`/nexttrace <目标>` - 直接指定目标\n\n"
            "示例：\n"
            "`/nexttrace 8.8.8.8`\n"
            "`/nexttrace google.com`",
            parse_mode='Markdown',
            reply_markup=back_keyboard
        )
    
    elif data == "panel_nt_adduser":
        if not await db.is_admin(user_id):
            await query.answer("主人没有权限吩咐这项工作哦。", show_alert=True)
            return
        
        from network_test.utils import check_is_admin
        from network_test.config import ADMIN_USERS
        
        if not check_is_admin(user_id, ADMIN_USERS):
            await query.answer("主人还不是网络测试茶具间的管理员哦。", show_alert=True)
            return
        
        back_keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("回网络测试茶具", callback_data="panel_network_test")]])
        await query.edit_message_text(
            "登记授权主人\n\n"
            "请这样吩咐女仆：\n"
            "`/adduser <user_id>`\n\n"
            "示例：\n"
            "`/adduser 123456789`",
            parse_mode='Markdown',
            reply_markup=back_keyboard
        )
    
    elif data == "panel_nt_rmuser":
        if not await db.is_admin(user_id):
            await query.answer("主人没有权限吩咐这项工作哦。", show_alert=True)
            return
        
        from network_test.utils import check_is_admin
        from network_test.config import ADMIN_USERS
        
        if not check_is_admin(user_id, ADMIN_USERS):
            await query.answer("主人还不是网络测试茶具间的管理员哦。", show_alert=True)
            return
        
        back_keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("回网络测试茶具", callback_data="panel_network_test")]])
        await query.edit_message_text(
            "移除授权主人\n\n"
            "请这样吩咐女仆：\n"
            "`/rmuser <user_id>`\n\n"
            "示例：\n"
            "`/rmuser 123456789`",
            parse_mode='Markdown',
            reply_markup=back_keyboard
        )
    
    elif data == "panel_nt_addserver":
        if not await db.is_admin(user_id):
            await query.answer("主人没有权限吩咐这项工作哦。", show_alert=True)
            return
        
        from network_test.utils import check_is_admin
        from network_test.config import ADMIN_USERS, SERVERS
        from network_test.state import user_data
        from network_test.utils import schedule_delete_message
        
        if not check_is_admin(user_id, ADMIN_USERS):
            await query.answer("主人还不是网络测试茶具间的管理员哦。", show_alert=True)
            return
        
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("回网络测试茶具", callback_data="panel_network_test")]
        ])
        msg = await query.message.reply_text(
            "服务器登记女仆向导开始值班啦！\n\n"
            "请主人按提示一步一步交代服务器信息。\n"
            "步骤 1/5: 请告诉女仆服务器名称（如：日本 - Acck）：\n\n"
            "主人可以随时输入 /cancel 取消登记流程",
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
            await query.answer("主人没有权限吩咐这项工作哦。", show_alert=True)
            return
        
        from network_test.utils import check_is_admin
        from network_test.config import ADMIN_USERS, SERVERS
        from network_test.state import user_data
        
        if not check_is_admin(user_id, ADMIN_USERS):
            await query.answer("主人还不是网络测试茶具间的管理员哦。", show_alert=True)
            return
        
        if not SERVERS:
            back_keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("回网络测试茶具", callback_data="panel_network_test")]])
            await query.edit_message_text("当前还没有登记任何服务器。", reply_markup=back_keyboard)
            return
            
        keyboard = []
        for idx, server_info in enumerate(SERVERS):
            btn = InlineKeyboardButton(
                f"{server_info['name']} ({server_info['host']}:{server_info['port']})", 
                callback_data=f"nt_rmserver_{idx}"
            )
            keyboard.append([btn])
        
        keyboard.append([InlineKeyboardButton("回网络测试茶具", callback_data="panel_network_test")])
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        msg = await query.message.reply_text(
            "主人，请选择要撤下的服务器：",
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
            await query.answer("主人没有权限吩咐这项工作哦。", show_alert=True)
            return
        
        from network_test.utils import check_is_admin
        from network_test.config import ADMIN_USERS, SERVERS
        from network_test.state import user_data
        
        if not check_is_admin(user_id, ADMIN_USERS):
            await query.answer("主人还不是网络测试茶具间的管理员哦。", show_alert=True)
            return
        
        if not SERVERS:
            back_keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("回网络测试茶具", callback_data="panel_network_test")]])
            await query.edit_message_text("当前还没有登记任何服务器。\n请先使用 /addserver 登记服务器。", reply_markup=back_keyboard)
            return
            
        keyboard = []
        for idx, server_info in enumerate(SERVERS):
            btn = InlineKeyboardButton(
                f"{server_info['name']} ({server_info['host']}:{server_info['port']})", 
                callback_data=f"nt_installnexttrace_{idx}"
            )
            keyboard.append([btn])
        
        keyboard.append([InlineKeyboardButton("回网络测试茶具", callback_data="panel_network_test")])
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        msg = await query.message.reply_text(
            "主人，请选择要安装 NextTrace 的服务器：",
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
            await query.answer("主人没有权限吩咐这项工作哦。", show_alert=True)
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
                keyboard_buttons.append([InlineKeyboardButton("回女仆长面板", callback_data="panel_back")])
                keyboard = InlineKeyboardMarkup(keyboard_buttons)
            else:
                keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("回女仆长面板", callback_data="panel_back")]])
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
                            keyboard_buttons[i][j] = InlineKeyboardButton("回女仆长面板", callback_data="panel_back")
                            break
                keyboard = InlineKeyboardMarkup(keyboard_buttons)
            else:
                keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("回女仆长面板", callback_data="panel_back")]])
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
            await query.answer("主人没有权限吩咐这项工作哦。", show_alert=True)
            return
        
        try:
            page = int(data.split("_")[2])
        except (ValueError, IndexError):
            await query.answer("这个页码不对劲，主人。", show_alert=True)
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
            await query.answer("主人没有权限吩咐这项工作哦。", show_alert=True)
            return
        
        try:
            page = int(data.split("_")[2])
        except (ValueError, IndexError):
            await query.answer("这个页码不对劲，主人。", show_alert=True)
            return
        
        MESSAGES_PER_PAGE = 5

        total_count = await db.get_filtered_messages_count()
        
        if total_count == 0:
            await query.edit_message_text("拦截篮里暂时没有消息。")
            return
        
        total_pages = (total_count + MESSAGES_PER_PAGE - 1) // MESSAGES_PER_PAGE

        if page < 1:
            page = 1
        elif page > total_pages:
            page = total_pages

        offset = (page - 1) * MESSAGES_PER_PAGE

        messages = await db.get_filtered_messages(MESSAGES_PER_PAGE, offset)
        
        if not messages:
            await query.edit_message_text("拦截篮里暂时没有消息。")
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
            await query.answer("主人没有权限吩咐这项工作哦。", show_alert=True)
            return
        
        try:
            page = int(data.split("_")[3])
        except (ValueError, IndexError):
            await query.answer("这个页码不对劲，主人。", show_alert=True)
            return
        
        message, keyboard = await blacklist.get_exemptions_keyboard(page=page)
        
        if keyboard:
            keyboard_buttons = [list(row) for row in keyboard.inline_keyboard]
            keyboard_buttons.append([InlineKeyboardButton("回女仆长面板", callback_data="panel_back")])
            keyboard = InlineKeyboardMarkup(keyboard_buttons)
        else:
            keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("回女仆长面板", callback_data="panel_back")]])
        
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
            await query.answer("主人没有权限吩咐这项工作哦。", show_alert=True)
            return
        
        try:
            user_id_to_remove = int(data.split("_")[3])
        except (ValueError, IndexError):
            await query.answer("无效的用户ID。", show_alert=True)
            return
        
        await db.remove_exemption(user_id_to_remove)
        await query.answer(f"已收回用户 {user_id_to_remove} 的通行证", show_alert=True)
        
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
            keyboard_buttons.append([InlineKeyboardButton("回女仆长面板", callback_data="panel_back")])
            keyboard = InlineKeyboardMarkup(keyboard_buttons)
        else:
            keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("回女仆长面板", callback_data="panel_back")]])
        
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
            await query.answer("主人没有权限吩咐这项工作哦。", show_alert=True)
            return
        
        try:
            page = int(data.split("_")[5])
        except (ValueError, IndexError):
            await query.answer("这个页码不对劲，主人。", show_alert=True)
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
            await query.answer("主人没有权限吩咐这项工作哦。", show_alert=True)
            return
        
        try:
            page = int(data.split("_")[4])
        except (ValueError, IndexError):
            await query.answer("这个页码不对劲，主人。", show_alert=True)
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
            await query.answer("主人没有权限吩咐这项工作哦。", show_alert=True)
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
            [InlineKeyboardButton("所有主人名册", callback_data="stats_list_all_users_page_1")],
            [InlineKeyboardButton("黑名单小本本", callback_data="stats_list_blacklist_page_1")]
        ]
        
        await query.edit_message_text(
            text=stats_message,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='Markdown'
        )
    
    elif data.startswith("autoreply_"):
        if not await db.is_admin(user_id):
            await query.answer("主人没有权限吩咐这项工作哦。", show_alert=True)
            return
        
        if data == "autoreply_toggle":
            is_enabled = await db.get_autoreply_enabled()
            await db.set_autoreply_enabled(not is_enabled)
            new_status = "正在值班" if not is_enabled else "正在休息"
            await query.answer(f"自动回复女仆{new_status}", show_alert=True)
            
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
                        "让自动回复女仆休息" if is_enabled else "让自动回复女仆值班",
                        callback_data="autoreply_toggle"
                    )
                ],
                [InlineKeyboardButton("整理知识小本本", callback_data="autoreply_kb_list_page_1")],
                [InlineKeyboardButton("新增知识便签", callback_data="autoreply_kb_add")],
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
                await query.edit_message_text("知识小本本还是空的，主人。")
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
                        "修改",
                        callback_data=f"autoreply_kb_edit_{entry['id']}"
                    ),
                    InlineKeyboardButton(
                        "删除",
                        callback_data=f"autoreply_kb_delete_{entry['id']}"
                    )
                ])
            
            nav_buttons = []
            if page > 1:
                nav_buttons.append(InlineKeyboardButton("上一页", callback_data=f"autoreply_kb_list_page_{page-1}"))
            if page < total_pages:
                nav_buttons.append(InlineKeyboardButton("下一页", callback_data=f"autoreply_kb_list_page_{page+1}"))
            if nav_buttons:
                keyboard.append(nav_buttons)
            
            keyboard.append([InlineKeyboardButton("返回", callback_data="autoreply_back")])
            
            await query.edit_message_text(
                message,
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode='Markdown'
            )
        
        elif data.startswith("autoreply_kb_view_"):
            try:
                entry_id = int(data.split("_")[3])
            except (ValueError, IndexError):
                await query.answer("这个条目 ID 不对劲，主人再看一眼吧。", show_alert=True)
                return
            
            entry = await db.get_knowledge_entry(entry_id)
            if not entry:
                await query.answer("女仆翻遍小本本，也没找到这个条目。", show_alert=True)
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
                    InlineKeyboardButton("修改", callback_data=f"autoreply_kb_edit_{entry_id}"),
                    InlineKeyboardButton("删除", callback_data=f"autoreply_kb_delete_{entry_id}")
                ],
                [InlineKeyboardButton("回小本本列表", callback_data="autoreply_kb_list_page_1")]
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
                await query.answer("这个条目 ID 不对劲，主人再看一眼吧。", show_alert=True)
                return
            
            entry = await db.get_knowledge_entry(entry_id)
            if not entry:
                await query.answer("女仆翻遍小本本，也没找到这个条目。", show_alert=True)
                return
            
            await query.edit_message_text(
                f"修改知识便签\n\n"
                f"ID: {entry['id']}\n"
                f"标题: {entry['title']}\n"
                f"内容: {entry['content']}\n\n"
                f"主人，请这样让女仆修改：\n"
                f"`/autoreply edit {entry_id} <新标题> <新内容>`\n\n"
                f"示例：\n"
                f"`/autoreply edit {entry_id} 新标题 新内容`",
                parse_mode='Markdown'
            )
        
        elif data.startswith("autoreply_kb_delete_"):
            try:
                entry_id = int(data.split("_")[3])
            except (ValueError, IndexError):
                await query.answer("这个条目 ID 不对劲，主人再看一眼吧。", show_alert=True)
                return
            
            entry = await db.get_knowledge_entry(entry_id)
            if not entry:
                await query.answer("女仆翻遍小本本，也没找到这个条目。", show_alert=True)
                return
            
            await db.delete_knowledge_entry(entry_id)
            await query.answer(f"已删除便签: {entry['title']}", show_alert=True)
            
            entries = await db.get_all_knowledge_entries()
            if not entries:
                await query.edit_message_text("知识小本本还是空的，主人。")
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
                        "修改",
                        callback_data=f"autoreply_kb_edit_{entry['id']}"
                    ),
                    InlineKeyboardButton(
                        "删除",
                        callback_data=f"autoreply_kb_delete_{entry['id']}"
                    )
                ])
            
            nav_buttons = []
            if page < total_pages:
                nav_buttons.append(InlineKeyboardButton("下一页", callback_data=f"autoreply_kb_list_page_{page+1}"))
            if nav_buttons:
                keyboard.append(nav_buttons)
            
            keyboard.append([InlineKeyboardButton("返回", callback_data="autoreply_back")])
            
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
                        "让自动回复女仆休息" if is_enabled else "让自动回复女仆值班",
                        callback_data="autoreply_toggle"
                    )
                ],
                [InlineKeyboardButton("整理知识小本本", callback_data="autoreply_kb_list_page_1")],
                [InlineKeyboardButton("新增知识便签", callback_data="autoreply_kb_add")],
            ]
            
            await query.edit_message_text(
                message,
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode='Markdown'
            )
        
        elif data == "autoreply_kb_add":
            await query.edit_message_text(
                "新增知识便签\n\n"
                "主人，请这样交给女仆新便签：\n"
                "`/autoreply add <标题> <内容>`\n\n"
                "示例：\n"
                "`/autoreply add 常见问题 这是问题的答案`",
                parse_mode='Markdown'
            )
