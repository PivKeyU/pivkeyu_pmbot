import logging
import os
from telegram.ext import Application, CommandHandler, filters
from utils.commands import names_for
from telegram.ext import Job
from typing import Optional
from . import data_manager, feed_checker, handlers as rss_handlers, settings

logger = logging.getLogger(__name__)

RSS_JOB_KEY = "rss_feed_job"

# 最近一次 setup 传入的 Application 实例，供间隔变化回调重建定时任务使用
_current_app: Optional[Application] = None


def _schedule_feed_job(app: Application) -> Optional[Job]:
    # 幂等调度：先取消可能已存在的旧任务，避免 setup/enable_feature
    # 重复调用时注册多个 run_repeating 定时器（任务泄漏、检查翻倍）
    _cancel_feed_job(app)

    interval = settings.get_check_interval()
    if not isinstance(interval, int) or interval <= 0:
        logger.warning("RSS 检查间隔无效 (%s)，回退为 300 秒。", interval)
        interval = 300

    job = app.job_queue.run_repeating(
        feed_checker.check_feeds_job,
        interval=interval,
        first=10,
        name="rss_feed_checker",
    )
    app.bot_data[RSS_JOB_KEY] = job
    logger.info("RSS 订阅检查任务已调度，间隔 %s 秒", interval)
    return job


def _cancel_feed_job(app: Application) -> None:
    job = app.bot_data.pop(RSS_JOB_KEY, None)
    if job:
        job.schedule_removal()
        logger.info("RSS 订阅检查任务已停止。")


def _on_check_interval_changed(_seconds: int) -> None:
    """检查间隔被修改后，按新间隔重建定时任务。"""
    if _current_app is None:
        return
    if settings.is_enabled():
        _schedule_feed_job(_current_app)
        logger.info("RSS 检查间隔已更新，任务已按新间隔重新调度。")


def _on_enabled_changed(enabled: bool) -> None:
    """开关状态被修改后，自动调度或停止 RSS 检查任务。"""
    if _current_app is None:
        return
    if enabled:
        _schedule_feed_job(_current_app)
    else:
        _cancel_feed_job(_current_app)


def setup(app: Application) -> None:
    global _current_app
    _current_app = app

    data_file = settings.get_data_file() or "./data/rss_subscriptions.json"
    data_dir = os.path.dirname(data_file)
    if data_dir:
        os.makedirs(data_dir, exist_ok=True)

    data_manager.load_subscriptions(data_file, skip_title_fetch=True)
    app.bot_data["rss_data_file"] = data_file

    for command, handler in rss_handlers.COMMAND_MAP.items():
        # 与 handlers/__init__.py 同样的理由：CommandHandler 默认 filters 是 UpdateType.MESSAGES，
        # 会把「编辑成命令」的 Update 也放进来，而那种 Update 的 update.message 是 None。
        app.add_handler(CommandHandler(
            names_for(command),
            handler,
            filters=filters.ChatType.PRIVATE & filters.UpdateType.MESSAGE,
        ))

    # 注册开关与间隔变化回调
    settings.set_check_interval_callback(_on_check_interval_changed)
    settings.set_enabled_callback(_on_enabled_changed)

    if settings.is_enabled():
        _schedule_feed_job(app)
        logger.info("RSS 订阅功能已启用，数据文件: %s", data_file)
    else:
        logger.info("RSS 订阅功能当前为关闭状态，可在面板中开启。")


def enable_feature(app: Optional[Application] = None) -> bool:
    if settings.is_enabled():
        return False
    target_app = app or _current_app
    settings.set_enabled(True)
    if target_app is not None and RSS_JOB_KEY not in target_app.bot_data:
        _schedule_feed_job(target_app)
    logger.info("RSS 订阅功能已在运行时开启。")
    return True


def disable_feature(app: Optional[Application] = None) -> bool:
    if not settings.is_enabled():
        return False
    target_app = app or _current_app
    settings.set_enabled(False)
    if target_app is not None:
        _cancel_feed_job(target_app)
    logger.info("RSS 订阅功能已在运行时关闭。")
    return True
