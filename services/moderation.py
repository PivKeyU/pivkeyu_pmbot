"""管理员对单个用户的处置：撤销 / 恢复验证、AI 自动撤销验证、误杀申诉与暂停审查。

Telegram 话题里的按钮 / 命令和 Web 面板调的是同一组函数，两边结果一致：
- 暂停审查 = 发放审查通行证（exemptions 表），到期自动恢复审查；
- 申诉记录在 review_appeals 表，处理后同步改掉话题里那条申请消息的按钮。
"""

import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Optional

from telegram import ForceReply, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import TelegramError

from config import config
from database import models as db
from services import ai_options
from utils import copy as copy_text
from web_panel import events as panel_events

logger = logging.getLogger(__name__)

# 话题里的快捷时长按钮（分钟）
APPEAL_PRESETS = ((30, '30 分钟'), (120, '2 小时'), (1440, '1 天'))
# 同一个用户两次申请之间至少间隔多久（分钟）
APPEAL_COOLDOWN_MINUTES = 10
MAX_PAUSE_MINUTES = 60 * 24 * 365

# 关键词拦截的理由前缀（不是 AI 判的，不提供申诉按钮）
KEYWORD_REASON_PREFIX = '关键词广告拦截命中'

# 「自定义时长」：ForceReply 提示消息 id -> 申请 id。进程内存即可，重启后让管理员重新点按钮
CUSTOM_PROMPTS_KEY = 'appeal_custom_prompts'

_DURATION_UNITS = {
    'm': 1, 'min': 1, '分': 1, '分钟': 1,
    'h': 60, 'hr': 60, '小时': 60, '时': 60,
    'd': 1440, 'day': 1440, '天': 1440, '日': 1440,
    'w': 10080, '周': 10080, '星期': 10080,
}


def parse_duration(text: str) -> Optional[int]:
    """把「90」「45m」「3h」「1h30m」「2天」「1.5小时」解析成分钟；纯数字按分钟算。无法解析返回 None。"""
    text = (text or '').strip().lower().replace(' ', '')
    if not text:
        return None
    if re.fullmatch(r'\d+(\.\d+)?', text):
        minutes = float(text)
    else:
        parts = re.findall(r'(\d+(?:\.\d+)?)([a-z一-鿿]+)', text)
        if not parts or ''.join(n + u for n, u in parts) != text:
            return None
        minutes = 0.0
        for number, unit in parts:
            factor = _DURATION_UNITS.get(unit)
            if factor is None:
                return None
            minutes += float(number) * factor
    minutes = int(round(minutes))
    if minutes <= 0:
        return None
    return min(minutes, MAX_PAUSE_MINUTES)


def format_duration(minutes: Optional[int]) -> str:
    if minutes is None:
        return '永久'
    days, rest = divmod(int(minutes), 1440)
    hours, mins = divmod(rest, 60)
    parts = []
    if days:
        parts.append(f'{days} 天')
    if hours:
        parts.append(f'{hours} 小时')
    if mins:
        parts.append(f'{mins} 分钟')
    return ' '.join(parts) or '0 分钟'


def _user_label(user: dict) -> str:
    name = user.get('first_name') or str(user['user_id'])
    return f"{name}（{user['user_id']}）"


async def _notify_topic(bot, user: dict, text: str, reply_markup=None):
    thread_id = user.get('thread_id')
    if not thread_id or not config.FORUM_GROUP_ID:
        return None
    try:
        return await bot.send_message(
            chat_id=config.FORUM_GROUP_ID, message_thread_id=thread_id,
            text=text, reply_markup=reply_markup,
        )
    except TelegramError as exc:
        logger.warning('向用户 %s 的话题发送提醒失败: %s', user['user_id'], exc)
        return None


async def _notify_user(bot, user_id: int, text: str) -> None:
    try:
        await bot.send_message(chat_id=user_id, text=text)
    except TelegramError as exc:
        logger.warning('通知用户 %s 失败: %s', user_id, exc)


# --------------------------------------------------------------------------- #
# 验证状态
# --------------------------------------------------------------------------- #

async def revoke_verification(user_id: int) -> bool:
    """收回验证结果：下次发消息时要重新完成人机验证。用户不存在返回 False。"""
    from services import verification

    user = await db.get_user(user_id)
    if not user:
        return False
    verification.pending_verifications.pop(user_id, None)
    await db.update_user_verification(user_id, False)
    panel_events.publish('user_status', user_id=user_id, change='unverified')
    return True


async def restore_verification(user_id: int) -> bool:
    from services import verification

    user = await db.get_user(user_id)
    if not user:
        return False
    verification.pending_verifications.pop(user_id, None)
    await db.update_user_verification(user_id, True)
    panel_events.publish('user_status', user_id=user_id, change='verified')
    return True


async def maybe_auto_revoke(bot, user_id: int) -> bool:
    """AI 判定违规后调用：开启了「AI 自动撤销验证」且窗口内违规次数达标时，收回验证结果。"""
    enabled, threshold, window_hours = await ai_options.auto_revoke_settings()
    if not enabled:
        return False
    user = await db.get_user(user_id)
    if not user or not user.get('is_verified'):
        return False
    count = await db.count_recent_ai_filtered(user_id, window_hours)
    if count < threshold:
        return False
    await revoke_verification(user_id)
    await _notify_topic(bot, user, copy_text.with_deco(
        f'{_user_label(user)} 在 {window_hours} 小时内被 AI 拦下 {count} 次，'
        '女仆已经自动收回他的验证结果，下次发消息要重新答题。', 'BLOCK'))
    return True


# --------------------------------------------------------------------------- #
# 误杀申诉 → 暂停审查
# --------------------------------------------------------------------------- #

def appeal_request_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton('🙋 申请暂停审查', callback_data='appeal_req')]])


async def blocked_reply_markup(reason: str):
    """AI 拦截提示下面挂的按钮：只有 AI 判定且开启了申诉功能时才给。"""
    if (reason or '').startswith(KEYWORD_REASON_PREFIX):
        return None
    if not await ai_options.appeal_enabled():
        return None
    return appeal_request_keyboard()


def _appeal_admin_keyboard(appeal_id: int) -> InlineKeyboardMarkup:
    presets = [
        InlineKeyboardButton(f'暂停 {label}', callback_data=f'appeal_ok:{appeal_id}:{minutes}')
        for minutes, label in APPEAL_PRESETS
    ]
    return InlineKeyboardMarkup([
        presets,
        [
            InlineKeyboardButton('自定义时长', callback_data=f'appeal_custom:{appeal_id}'),
            InlineKeyboardButton('拒绝', callback_data=f'appeal_no:{appeal_id}'),
        ],
    ])


def _parse_db_time(value) -> Optional[datetime]:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


async def create_appeal(bot, user_id: int, ensure_thread) -> tuple:
    """用户点了「申请暂停审查」。返回 (是否已提交, 给用户看的提示)。

    ensure_thread(user) -> thread_id：用户还没有话题时（第一条消息就被拦）负责建一个。
    """
    if not await ai_options.appeal_enabled():
        return False, copy_text.with_deco('申请通道暂时关闭啦，客人稍后再试嘛。', 'ERROR')
    user = await db.get_user(user_id)
    if not user:
        return False, copy_text.with_deco('女仆找不到客人的登记信息，请重新发一条消息。', 'ERROR')
    if await db.get_pending_review_appeal(user_id):
        return False, copy_text.with_deco('客人的申请已经送到管理员手上啦，耐心等一下嘛。', 'WAIT')
    latest = await db.get_latest_review_appeal(user_id)
    created = _parse_db_time(latest.get('created_at')) if latest else None
    if created and datetime.now(timezone.utc) - created < timedelta(minutes=APPEAL_COOLDOWN_MINUTES):
        return False, copy_text.with_deco(f'申请太频繁啦，{APPEAL_COOLDOWN_MINUTES} 分钟后再试吧。', 'WAIT')

    filtered = await db.get_latest_filtered_message(user_id)
    reason = (filtered or {}).get('reason') or '（未记录）'
    appeal_id = await db.create_review_appeal(user_id, reason)

    if not user.get('thread_id'):
        user['thread_id'] = await ensure_thread(user)
    preview = ((filtered or {}).get('content') or '').strip()
    lines = [
        f'🙋 {_user_label(user)} 认为自己的消息被 AI 误拦，申请暂停审查。',
        f'拦截理由：{reason}',
    ]
    if preview:
        lines.append(f'被拦消息：{preview[:300]}')
    lines.append('请选择暂停时长（期间该用户的消息不经过 AI 审查，到期自动恢复）：')
    sent = await _notify_topic(bot, user, '\n'.join(lines), _appeal_admin_keyboard(appeal_id))
    if sent:
        await db.set_review_appeal_topic_message(appeal_id, sent.message_id)
    panel_events.publish('appeal', user_id=user_id, appeal_id=appeal_id)
    return True, copy_text.with_deco('申请已经交给管理员啦，处理好之后女仆会第一时间告诉客人。', 'OK')


async def _close_topic_appeal_message(bot, appeal: Optional[dict], text: str) -> None:
    if not appeal or not appeal.get('topic_message_id') or not config.FORUM_GROUP_ID:
        return
    try:
        await bot.edit_message_text(
            chat_id=config.FORUM_GROUP_ID, message_id=appeal['topic_message_id'],
            text=text, reply_markup=None,
        )
    except TelegramError as exc:
        logger.info('更新申请消息失败（可能已被删除或内容未变）: %s', exc)


async def grant_review_pause(bot, user_id: int, minutes: Optional[int], granted_by: int, source: str) -> dict:
    """暂停审查 minutes 分钟（None = 永久）；同时批准该用户所有待处理的申请并通知用户。"""
    user = await db.get_user(user_id)
    if not user:
        raise ValueError('用户不存在')
    if minutes is not None and not (1 <= minutes <= MAX_PAUSE_MINUTES):
        raise ValueError('暂停时长需要在 1 分钟到 365 天之间')
    expires_at = None
    if minutes is not None:
        expires_at = (datetime.now(timezone.utc) + timedelta(minutes=minutes)).isoformat()
    await db.add_exemption(
        user_id, is_permanent=minutes is None, exempted_by=granted_by,
        reason=f'暂停 AI 审查（{source}）', expires_at=expires_at,
    )
    pending = await db.get_pending_review_appeal(user_id)
    await db.resolve_review_appeals(user_id, 'approved', granted_by, minutes)
    duration = format_duration(minutes)
    await _close_topic_appeal_message(bot, pending, copy_text.with_deco(
        f'已为 {_user_label(user)} 暂停审查 {duration}（{source}处理）。', 'OK'))
    await _notify_user(bot, user_id, copy_text.with_deco(
        f'管理员已经为客人暂停审查 {duration}，现在可以直接把消息交给女仆啦。', 'OK'))
    panel_events.publish('user_status', user_id=user_id, change='review_paused')
    return {'user_id': user_id, 'minutes': minutes, 'expires_at': expires_at, 'duration_text': duration}


async def resume_review(user_id: int) -> None:
    await db.remove_exemption(user_id)
    panel_events.publish('user_status', user_id=user_id, change='review_resumed')


async def reject_appeal(bot, user_id: int, handled_by: int, source: str) -> bool:
    user = await db.get_user(user_id)
    pending = await db.get_pending_review_appeal(user_id)
    if not user or not pending:
        return False
    await db.resolve_review_appeals(user_id, 'rejected', handled_by)
    await _close_topic_appeal_message(bot, pending, copy_text.with_deco(
        f'已拒绝 {_user_label(user)} 的暂停审查申请（{source}处理）。', 'BLOCK'))
    await _notify_user(bot, user_id, copy_text.with_deco(
        '管理员暂时没有同意暂停审查，客人换个说法再试试嘛。', 'TSUNDERE'))
    panel_events.publish('user_status', user_id=user_id, change='appeal_rejected')
    return True


async def ask_custom_duration(bot, application, appeal: dict) -> None:
    """话题里点了「自定义时长」：发一条 ForceReply，管理员回复它即可输入时长。"""
    user = await db.get_user(appeal['user_id'])
    if not user or not user.get('thread_id'):
        return
    sent = await bot.send_message(
        chat_id=config.FORUM_GROUP_ID, message_thread_id=user['thread_id'],
        text=copy_text.with_deco(
            '请直接回复这条消息告诉女仆要暂停多久，例如：45m、3h、1h30m、2d、7天（纯数字按分钟算），'
            '回复「永久」则一直不审查。这条回复不会转发给用户。', 'ASK'),
        reply_markup=ForceReply(selective=True, input_field_placeholder='例如 3h'),
    )
    application.bot_data.setdefault(CUSTOM_PROMPTS_KEY, {})[sent.message_id] = appeal['id']


async def handle_custom_duration_reply(message, bot, application) -> bool:
    """管理员在话题里回复了「自定义时长」提示：处理并返回 True（这条消息不再转发给用户）。"""
    reply_to = message.reply_to_message
    prompts = application.bot_data.get(CUSTOM_PROMPTS_KEY) or {}
    if not reply_to or reply_to.message_id not in prompts:
        return False
    if not message.from_user or not await db.is_admin(message.from_user.id):
        return False
    appeal = await db.get_review_appeal(prompts[reply_to.message_id])
    text = (message.text or '').strip()
    if text in ('永久', 'permanent', 'forever'):
        minutes = None
    else:
        minutes = parse_duration(text)
        if minutes is None:
            await message.reply_text(copy_text.with_deco(
                '这个时长女仆看不懂呢，换成 45m / 3h / 2d 这样的写法再回复一次嘛。', 'ERROR'))
            return True
    prompts.pop(reply_to.message_id, None)
    if not appeal:
        await message.reply_text(copy_text.with_deco('这条申请找不到啦。', 'ERROR'))
        return True
    result = await grant_review_pause(bot, appeal['user_id'], minutes, message.from_user.id, 'TG 话题')
    await message.reply_text(copy_text.with_deco(f"好的，已暂停审查 {result['duration_text']}。", 'OK'))
    return True
