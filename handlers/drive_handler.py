"""网盘：管理员私聊发文件自动存进网盘并回直链，/drive 查看状态或把回复的文件存进去。"""

import html
import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update, WebAppInfo
from telegram.constants import ChatType
from telegram.ext import ContextTypes

from config import config
from services import drive
from services.verification import miniapp_url
from utils import copy as copy_text
from utils.decorators import admin_only

logger = logging.getLogger(__name__)


def has_media(message) -> bool:
    return drive.extract_media(message) is not None


def _link_text(row: dict, base: str) -> str:
    links = drive.link_formats(row, base)
    lines = [
        '女仆把文件收进网盘啦',
        '',
        f"文件：{html.escape(row['name'])}（{drive.human_size(row['size'])}）",
        '',
        '直链：',
        f"<code>{html.escape(links['url'])}</code>",
        '',
        'Markdown：',
        f"<code>{html.escape(links['markdown'])}</code>",
    ]
    if not base.startswith('https://'):
        lines += ['', '还没有配置网盘公网地址（DRIVE_PUBLIC_URL），外部可能打不开这个链接。']
    return '\n'.join(lines)


async def reply_with_link(message, bot, uploader_id, parent_id=None) -> None:
    try:
        row = await drive.store_message(bot, message, uploader_id=uploader_id, parent_id=parent_id)
    except drive.DriveError as exc:
        await message.reply_text(copy_text.with_deco(str(exc), 'ERROR'))
        return
    except Exception:
        logger.exception('网盘保存文件失败')
        await message.reply_text(copy_text.with_deco('保存到网盘时出错了，请查看日志', 'ERROR'))
        return

    base = drive.public_base()
    keyboard = None
    if base.startswith('https://'):
        keyboard = InlineKeyboardMarkup([[InlineKeyboardButton('打开直链', url=drive.file_url(row, base))]])
    await message.reply_text(
        copy_text.with_deco_head(_link_text(row, base), 'OK'),
        parse_mode='HTML',
        reply_markup=keyboard,
        disable_web_page_preview=True,
    )


@admin_only
async def drive_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message
    if not drive.enabled():
        await message.reply_text(copy_text.with_deco('网盘功能已关闭，可以在配置中心打开 DRIVE_ENABLED', 'ERROR'))
        return

    target = message.reply_to_message
    if target is not None and has_media(target):
        await reply_with_link(target, context.bot, update.effective_user.id)
        return

    info = await drive.stats()
    if config.DRIVE_STORAGE_CHAT_ID:
        storage = f'存储频道 {config.DRIVE_STORAGE_CHAT_ID}'
    else:
        storage = '管理员私聊（建议配置 DRIVE_STORAGE_CHAT_ID 使用私有频道）'
    base = drive.public_base()
    lines = [
        '女仆的小网盘',
        '',
        f"文件：{info['files']} 个，文件夹：{info['folders']} 个",
        f"总大小：{drive.human_size(info['bytes'])}",
        f'存放位置：{storage}',
        f"私聊自动存图：{'开启' if config.DRIVE_AUTO_UPLOAD else '关闭'}",
        f'公网地址：{base or "未配置"}',
        '',
        '用法：管理员私聊直接发图片或文件就会自动收进网盘；回复一条带文件的消息再发 /drive 也可以存进去。',
    ]
    buttons = []
    app_url = miniapp_url()
    if app_url and update.effective_chat.type == ChatType.PRIVATE:
        buttons.append(InlineKeyboardButton('在 Mini App 打开', web_app=WebAppInfo(url=f'{app_url}?page=drive')))
    if base.startswith('https://'):
        buttons.append(InlineKeyboardButton('在面板打开', url=f'{base}/#/drive'))
    await message.reply_text(
        copy_text.with_deco_head('\n'.join(lines), 'OK'),
        reply_markup=InlineKeyboardMarkup([buttons]) if buttons else None,
        disable_web_page_preview=True,
    )
