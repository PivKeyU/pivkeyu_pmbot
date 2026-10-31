"""自动更新：启动时和定时检测新版本，得到管理员同意后在设定时间更新。

同意前只检测和提醒，绝不更新。同意后每天的更新窗口内，发现新版本就自动更新：
Docker 部署交给 Watchtower 拉镜像重建容器，源码部署执行 ff-only 更新后重启。

设置和运行状态存在 app_meta，不走配置中心：同意是一次明确操作，要记录时间，
不能被 .env 或批量改配置顺手打开。
"""

import json
import logging
import os
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from telegram.error import TelegramError
from telegram.ext import Application, ContextTypes

from config import config
from database import models as db
from services import broadcast, image_update, restart, safe_update
from utils import copy as copy_text

logger = logging.getLogger(__name__)

REPO_DIR = Path(__file__).resolve().parent.parent
SETTINGS_META_KEY = 'auto_update_settings'
STATE_META_KEY = 'auto_update_state'
AUTO_UPDATE_JOB_KEY = 'auto_update_job'

TICK_SECONDS = 60
FIRST_TICK_SECONDS = 30
CHECK_INTERVAL_SECONDS = 6 * 3600
# 机器人错过设定时间太久就等明天，避免白天突然重建容器
UPDATE_WINDOW_MINUTES = 60

DEFAULT_TIME = '04:00'
DEFAULT_UTC_OFFSET_MINUTES = 8 * 60
TIME_PATTERN = re.compile(r'^([01]\d|2[0-3]):[0-5]\d$')
MIN_UTC_OFFSET_MINUTES = -12 * 60
MAX_UTC_OFFSET_MINUTES = 14 * 60

_startup_checked = False
_tick_running = False


async def _load_json(key: str) -> dict:
    raw = await db.get_app_meta(key)
    try:
        data = json.loads(raw) if raw else {}
    except ValueError:
        logger.warning('自动更新数据无法解析，已忽略: %s', key)
        return {}
    return data if isinstance(data, dict) else {}


async def _save_json(key: str, data: dict) -> None:
    await db.set_app_meta(key, json.dumps(data, ensure_ascii=False))


async def get_settings() -> dict:
    saved = await _load_json(SETTINGS_META_KEY)
    return {
        'consented': bool(saved.get('consented')),
        'consented_at': saved.get('consented_at') or None,
        'time': saved.get('time') or DEFAULT_TIME,
        'utc_offset_minutes': saved.get('utc_offset_minutes', DEFAULT_UTC_OFFSET_MINUTES),
    }


async def update_settings(consented: bool, update_time: str, utc_offset_minutes: int) -> dict:
    if not TIME_PATTERN.match(update_time or ''):
        raise ValueError('更新时间格式应为 HH:MM，例如 04:00')
    if isinstance(utc_offset_minutes, bool) or not isinstance(utc_offset_minutes, int):
        raise ValueError('时区偏移需要是整数分钟')
    if not MIN_UTC_OFFSET_MINUTES <= utc_offset_minutes <= MAX_UTC_OFFSET_MINUTES:
        raise ValueError('时区偏移超出范围')

    current = await get_settings()
    # 保留第一次同意的时间；撤回后再同意算一次新的同意
    consented_at = current['consented_at'] if current['consented'] else None
    settings = {
        'consented': consented,
        'consented_at': (consented_at or time.time()) if consented else None,
        'time': update_time,
        'utc_offset_minutes': utc_offset_minutes,
    }
    await _save_json(SETTINGS_META_KEY, settings)
    return settings


async def get_state() -> dict:
    return await _load_json(STATE_META_KEY)


def local_now(now: float, utc_offset_minutes: int) -> datetime:
    return datetime.fromtimestamp(now, timezone(timedelta(minutes=utc_offset_minutes)))


def is_due(now: float, settings: dict, last_attempt_date: str = '') -> bool:
    """判断现在是否在今天的更新窗口内，且今天还没有尝试过。"""
    local = local_now(now, settings['utc_offset_minutes'])
    hour, minute = map(int, settings['time'].split(':'))
    scheduled = local.replace(hour=hour, minute=minute, second=0, microsecond=0)
    # 窗口可能跨过午夜：00:30 时也要看昨天 23:50 开始的窗口
    for start in (scheduled, scheduled - timedelta(days=1)):
        if start <= local < start + timedelta(minutes=UPDATE_WINDOW_MINUTES):
            return start.date().isoformat() != last_attempt_date
    return False


def window_date(now: float, settings: dict) -> str:
    """当前窗口所属日期：跨午夜时算开始那天，保证一个窗口只尝试一次。"""
    local = local_now(now, settings['utc_offset_minutes'])
    hour, minute = map(int, settings['time'].split(':'))
    scheduled = local.replace(hour=hour, minute=minute, second=0, microsecond=0)
    return (scheduled if scheduled <= local else scheduled - timedelta(days=1)).date().isoformat()


def next_update_at(now: float, settings: dict) -> float:
    local = local_now(now, settings['utc_offset_minutes'])
    hour, minute = map(int, settings['time'].split(':'))
    scheduled = local.replace(hour=hour, minute=minute, second=0, microsecond=0)
    return (scheduled if scheduled > local else scheduled + timedelta(days=1)).timestamp()


async def _detect() -> dict:
    """查一次有没有新版本；两种部署方式返回同样的结构。"""
    if image_update.detect_container():
        status = await image_update.check_for_update()
        return {
            'mode': 'image',
            'update_available': status.update_available,
            'version': status.remote_digest,
            'error': status.error,
        }

    try:
        status = await safe_update.get_status(REPO_DIR, fetch_remote=True)
    except safe_update.SafeUpdateError as exc:
        return {'mode': 'git', 'update_available': False, 'version': '', 'error': str(exc)}
    return {
        'mode': 'git',
        'update_available': status.behind > 0,
        'version': status.remote_head,
        'error': '',
    }


async def _notify_admins(application: Application, text: str) -> None:
    for admin_id in config.ADMIN_IDS:
        try:
            await application.bot.send_message(chat_id=admin_id, text=text)
        except TelegramError:
            logger.warning('发送自动更新通知失败 admin_id=%s', admin_id)


async def check_now(application: Application, state: dict, settings: dict, now: float) -> dict:
    """检测一次并记录结果；同一个版本只提醒一次。"""
    result = await _detect()
    state = {**state, **result, 'last_checked_at': now}
    version = result['version']
    if result['update_available'] and version and state.get('notified_version') != version:
        state['notified_version'] = version
        if settings['consented']:
            text = copy_text.auto_update_found_scheduled(settings['time'])
        else:
            text = copy_text.AUTO_UPDATE_FOUND_NEEDS_CONSENT
        await _notify_admins(application, copy_text.with_deco(text, 'ASK'))
    await _save_json(STATE_META_KEY, state)
    return state


async def _apply_image_update(application: Application, version: str) -> str:
    # 顺序不能换：Watchtower 重建容器会杀掉本进程，要先记下基线和通知对象；
    # 失败时还原，否则这个版本再也不会被判定为新版本
    previous = await db.get_app_meta(image_update.IMAGE_DIGEST_META_KEY)
    await image_update.remember_current_digest(version)
    await db.set_app_meta(restart.NOTIFY_META_KEY, json.dumps(list(config.ADMIN_IDS)))
    triggered, message = await image_update.trigger_watchtower_update()
    if triggered:
        return ''
    await db.set_app_meta(image_update.IMAGE_DIGEST_META_KEY, previous)
    await db.set_app_meta(restart.NOTIFY_META_KEY, '')
    return message


async def _apply_git_update(application: Application) -> str:
    try:
        await safe_update.apply_update(REPO_DIR)
        await restart.request_restart(application, config.ADMIN_IDS)
    except (safe_update.SafeUpdateError, restart.RestartRefused) as exc:
        return str(exc)
    return ''


async def run_update(application: Application, state: dict, now: float) -> dict:
    """执行更新前重新确认一次版本，失败时通知管理员，当天不再重试。"""
    result = await _detect()
    state = {**state, **result, 'last_checked_at': now, 'last_attempt_at': now}
    await _save_json(STATE_META_KEY, state)
    if not result['update_available']:
        return state

    await _notify_admins(application, copy_text.with_deco(copy_text.AUTO_UPDATE_STARTING, 'WAIT'))
    if result['mode'] == 'image':
        error = await _apply_image_update(application, result['version'])
    else:
        error = await _apply_git_update(application)

    state = {**state, 'last_result': error or 'ok'}
    await _save_json(STATE_META_KEY, state)
    if error:
        logger.warning('自动更新失败: %s', error)
        await _notify_admins(application, copy_text.with_deco(copy_text.auto_update_failed(error), 'ERROR'))
    return state


async def tick(application: Application, now: float = None) -> None:
    global _startup_checked, _tick_running
    if _tick_running:
        return
    _tick_running = True
    try:
        now = time.time() if now is None else now
        settings = await get_settings()
        state = await get_state()

        if not _startup_checked or now - state.get('last_checked_at', 0) >= CHECK_INTERVAL_SECONDS:
            _startup_checked = True
            state = await check_now(application, state, settings, now)

        if not settings['consented'] or not state.get('update_available'):
            return
        if not is_due(now, settings, state.get('last_attempt_date', '')):
            return
        # 广播发送中先不动，窗口内下一分钟再试
        if broadcast.is_running():
            return

        state['last_attempt_date'] = window_date(now, settings)
        await run_update(application, state, now)
    finally:
        _tick_running = False


async def _tick_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        await tick(context.application)
    except Exception:
        logger.exception('自动更新检查失败')


def setup(app: Application) -> None:
    job = app.bot_data.get(AUTO_UPDATE_JOB_KEY)
    if job:
        job.schedule_removal()
    app.bot_data[AUTO_UPDATE_JOB_KEY] = app.job_queue.run_repeating(
        _tick_job,
        interval=TICK_SECONDS,
        first=FIRST_TICK_SECONDS,
        name='auto_update_checker',
    )
    logger.info('自动更新检查任务已调度。')


async def describe() -> dict:
    """给面板展示的完整状态。"""
    settings = await get_settings()
    state = await get_state()
    in_container = image_update.detect_container()
    watchtower_ready = bool(os.environ.get('WATCHTOWER_HTTP_API_TOKEN', '').strip())
    return {
        'settings': settings,
        'state': state,
        'mode': 'image' if in_container else 'git',
        'watchtower_ready': watchtower_ready,
        'next_update_at': next_update_at(time.time(), settings) if settings['consented'] else None,
    }
