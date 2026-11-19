"""网盘：文件本体存在 Telegram，数据库只记元数据与 Telegram 映射，面板对外提供直链。

存放位置：配置了 DRIVE_STORAGE_CHAT_ID 时，机器人把文件再发一份到这个私有频道，
之后永远用频道里那条消息的 file_id 取文件；没配置时，私聊里收到的文件直接引用原消息，
面板上传的文件发到第一位管理员的私聊。

直链：/drive/f/<token>/<文件名>。token 是 22 位随机串，文件名只是装饰。
直链第一次被访问时用 getFile 拉回本地缓存（data/drive_cache），之后直接从磁盘返回。
"""

import asyncio
import html
import logging
import mimetypes
import os
import secrets
import tempfile
import time
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import quote

from telegram.error import BadRequest, Forbidden, RetryAfter

from config import config
from database.db_manager import db_manager

logger = logging.getLogger(__name__)

MAX_NAME_LENGTH = 255
MAX_EXTENSION_LENGTH = 20
_UNCHANGED = object()
OFFICIAL_DOWNLOAD_LIMIT = 20 * 1024 * 1024
THUMB_FALLBACK_MAX_BYTES = 5 * 1024 * 1024
UPLOAD_TIMEOUT_SECONDS = 300
CLEANUP_BATCH = 20
FILE_PREFIX = '/drive/f/'
THUMB_PREFIX = '/drive/t/'

# 直链允许在浏览器里直接打开的类型，其余一律作为附件下载（防止上传 HTML/SVG 做成钓鱼页）
INLINE_TYPES = {
    'image/jpeg', 'image/png', 'image/gif', 'image/webp', 'image/avif', 'image/bmp', 'image/x-icon',
    'video/mp4', 'video/webm', 'video/quicktime',
    'audio/mpeg', 'audio/mp4', 'audio/ogg', 'audio/wav', 'audio/x-wav', 'audio/flac', 'audio/aac', 'audio/webm',
    'application/pdf', 'text/plain',
}

MEDIA_KINDS = ('photo', 'animation', 'video', 'document', 'audio', 'voice', 'video_note', 'sticker')


class DriveError(Exception):
    """面向用户的网盘错误，消息可以直接展示。"""


class ItemNotFound(DriveError):
    pass


# ---------- 名称与令牌 ----------

def sanitize_name(name) -> str:
    text = str(name or '').replace('\\', '/').split('/')[-1].strip()
    if not text or text in ('.', '..') or '\x00' in text:
        raise DriveError('名称不能为空，也不能是 . 或 ..')
    if any(ord(ch) < 32 for ch in text):
        raise DriveError('名称里不能有控制字符')
    if len(text) > MAX_NAME_LENGTH:
        raise DriveError(f'名称最长 {MAX_NAME_LENGTH} 个字符')
    return text


def split_extension(name: str):
    base, dot, ext = name.rpartition('.')
    if not dot or not base or not ext or len(ext) > MAX_EXTENSION_LENGTH:
        return None
    return ext.lower()


def new_token() -> str:
    return secrets.token_urlsafe(16)


def guess_mime(name: str, fallback: str = '') -> str:
    guessed = mimetypes.guess_type(name)[0]
    if fallback and fallback != 'application/octet-stream':
        return fallback
    return guessed or fallback or 'application/octet-stream'


def is_inline_type(mime_type: str) -> bool:
    return (mime_type or '').split(';')[0].strip().lower() in INLINE_TYPES


def is_image(row: dict) -> bool:
    mime = (row.get('mime_type') or '').lower()
    return mime.startswith('image/') and 'svg' not in mime


def human_size(size) -> str:
    value = float(size or 0)
    for unit in ('B', 'KB', 'MB', 'GB'):
        if value < 1024 or unit == 'GB':
            return f'{int(value)} {unit}' if unit == 'B' else f'{value:.1f} {unit}'
        value /= 1024
    return f'{value:.1f} GB'


# ---------- 状态 ----------

def enabled() -> bool:
    return bool(config.DRIVE_ENABLED)


def storage_chat_id(uploader_id=None):
    """面板上传时文件要发去的地方：存储频道优先，其次上传者 / 第一位管理员的私聊。"""
    if config.DRIVE_STORAGE_CHAT_ID:
        return int(config.DRIVE_STORAGE_CHAT_ID)
    if uploader_id:
        return int(uploader_id)
    return int(config.ADMIN_IDS[0]) if config.ADMIN_IDS else None


def max_upload_bytes(bot) -> int:
    configured = max(1, int(config.DRIVE_MAX_UPLOAD_MB or 20)) * 1024 * 1024
    return min(configured, download_limit(bot) or configured)


def download_limit(bot):
    """官方 Bot API 只允许机器人取回 20MB 以内的文件；自建服务器不受限。"""
    base_url = str(getattr(bot, 'base_url', '') or 'https://api.telegram.org/bot')
    return OFFICIAL_DOWNLOAD_LIMIT if 'api.telegram.org' in base_url else None


def public_base(request=None) -> str:
    base = str(config.DRIVE_PUBLIC_URL or '').strip().rstrip('/')
    if base:
        return base
    miniapp = str(config.MINIAPP_PUBLIC_URL or '').strip().rstrip('/')
    if miniapp:
        return miniapp[:-len('/miniapp')] if miniapp.endswith('/miniapp') else miniapp
    if request is not None:
        scheme, host = request.scheme, request.host
        if config.WEB_PANEL_TRUST_PROXY:
            scheme = request.headers.get('X-Forwarded-Proto', scheme).split(',')[0].strip() or scheme
            host = request.headers.get('X-Forwarded-Host', host).split(',')[0].strip() or host
        return f'{scheme}://{host}'
    return ''


def file_url(row: dict, base: str = '', download: bool = False) -> str:
    url = f"{base}{FILE_PREFIX}{row['token']}/{quote(row['name'], safe='')}"
    return f'{url}?dl=1' if download else url


def thumb_url(row: dict, base: str = ''):
    if row.get('thumb_file_id') or is_image(row):
        return f"{base}{THUMB_PREFIX}{row['token']}"
    return None


def link_formats(row: dict, base: str) -> dict:
    url = file_url(row, base)
    name = row['name']
    safe_name = html.escape(name, quote=True)
    if is_image(row):
        return {
            'url': url,
            'markdown': f'![{name}]({url})',
            'html': f'<img src="{url}" alt="{safe_name}">',
            'bbcode': f'[img]{url}[/img]',
        }
    return {
        'url': url,
        'markdown': f'[{name}]({url})',
        'html': f'<a href="{url}">{safe_name}</a>',
        'bbcode': f'[url={url}]{name}[/url]',
    }


def serialize(row: dict, base: str = '') -> dict:
    item = {
        'id': row['id'],
        'parent_id': row['parent_id'],
        'name': row['name'],
        'is_folder': bool(row['is_folder']),
        'size': row['size'] or 0,
        'mime_type': row['mime_type'],
        'extension': row['extension'],
        'media_kind': row['media_kind'],
        'source': row['source'],
        'created_at': row['created_at'],
        'updated_at': row['updated_at'],
    }
    if not row['is_folder']:
        item['url'] = file_url(row, base)
        item['download_url'] = file_url(row, base, download=True)
        item['thumb_url'] = thumb_url(row, base)
        item['links'] = link_formats(row, base)
    return item


# ---------- 数据库 ----------

def _rows(cursor, rows) -> list:
    columns = [column[0] for column in cursor.description]
    return [dict(zip(columns, row)) for row in rows]


async def _get_item(db, item_id):
    cursor = await db.execute('SELECT * FROM drive_files WHERE id = ?', (int(item_id),))
    rows = _rows(cursor, await cursor.fetchall())
    return rows[0] if rows else None


async def get_item(item_id):
    async with db_manager.get_connection() as db:
        return await _get_item(db, item_id)


async def get_by_token(token: str):
    if not token or len(token) > 64:
        return None
    async with db_manager.get_connection() as db:
        cursor = await db.execute('SELECT * FROM drive_files WHERE token = ? AND is_folder = 0', (token,))
        rows = _rows(cursor, await cursor.fetchall())
    return rows[0] if rows else None


async def _require_folder(db, folder_id):
    if folder_id in (None, '', 0):
        return None
    folder = await _get_item(db, folder_id)
    if not folder or not folder['is_folder']:
        raise DriveError('目标文件夹不存在')
    return folder['id']


async def _folder_name_taken(db, parent_id, name, exclude_id=None) -> bool:
    cursor = await db.execute(
        'SELECT id FROM drive_files WHERE parent_id IS ? AND is_folder = 1 AND name = ? AND id IS NOT ?',
        (parent_id, name, exclude_id),
    )
    return await cursor.fetchone() is not None


async def list_items(parent_id=None, query=None, sort='time', offset=0, limit=50):
    if query:
        escaped = query.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
        where, params = "name LIKE ? ESCAPE '\\'", [f'%{escaped}%']
    else:
        where, params = 'parent_id IS ?', [parent_id]
    order = {
        'name': 'name COLLATE NOCASE ASC, id ASC',
        'size': 'size DESC, id DESC',
    }.get(sort, 'created_at DESC, id DESC')
    async with db_manager.get_connection() as db:
        cursor = await db.execute(f'SELECT COUNT(*) FROM drive_files WHERE {where}', params)
        total = (await cursor.fetchone())[0]
        cursor = await db.execute(
            f'SELECT * FROM drive_files WHERE {where} ORDER BY is_folder DESC, {order} LIMIT ? OFFSET ?',
            [*params, int(limit), int(offset)],
        )
        items = _rows(cursor, await cursor.fetchall())
    return items, total


async def _folder_trail(db, folder_id) -> list:
    trail = []
    seen = set()
    current = folder_id
    while current is not None:
        if current in seen:
            raise DriveError('目标目录存在循环，请先将文件夹移回根目录')
        seen.add(current)
        folder = await _get_item(db, current)
        if not folder or not folder['is_folder']:
            raise DriveError('目标文件夹不存在')
        trail.append({'id': folder['id'], 'name': folder['name']})
        current = folder['parent_id']
    return list(reversed(trail))


async def breadcrumbs(folder_id) -> list:
    async with db_manager.get_connection() as db:
        return await _folder_trail(db, folder_id)


async def list_folders() -> list:
    async with db_manager.get_connection() as db:
        cursor = await db.execute(
            'SELECT id, parent_id, name FROM drive_files WHERE is_folder = 1 ORDER BY name COLLATE NOCASE'
        )
        return _rows(cursor, await cursor.fetchall())


async def stats() -> dict:
    async with db_manager.get_connection() as db:
        cursor = await db.execute(
            'SELECT COALESCE(SUM(is_folder = 0), 0), COALESCE(SUM(is_folder = 1), 0), '
            'COALESCE(SUM(CASE WHEN is_folder = 0 THEN size ELSE 0 END), 0) FROM drive_files'
        )
        files, folders, total_bytes = await cursor.fetchone()
    return {'files': files, 'folders': folders, 'bytes': total_bytes}


async def create_folder(name, parent_id=None) -> dict:
    name = sanitize_name(name)
    async with db_manager.get_connection() as db:
        await db.execute('BEGIN IMMEDIATE')
        parent_id = await _require_folder(db, parent_id)
        if await _folder_name_taken(db, parent_id, name):
            raise DriveError('这里已经有同名文件夹了')
        cursor = await db.execute(
            'INSERT INTO drive_files (parent_id, name, is_folder, source) VALUES (?, ?, 1, ?)',
            (parent_id, name, 'web'),
        )
        result = await _get_item(db, cursor.lastrowid)
        await db.commit()
        return result


async def add_file(**fields) -> dict:
    name = sanitize_name(fields['name'])
    row = {
        'parent_id': fields.get('parent_id'),
        'name': name,
        'size': int(fields.get('size') or 0),
        'mime_type': fields.get('mime_type') or guess_mime(name),
        'extension': split_extension(name),
        'token': fields.get('token') or new_token(),
        'media_kind': fields.get('media_kind') or 'document',
        'tg_chat_id': fields.get('tg_chat_id'),
        'tg_message_id': fields.get('tg_message_id'),
        'tg_owned': 1 if fields.get('tg_owned') else 0,
        'tg_file_id': fields['tg_file_id'],
        'tg_file_unique_id': fields.get('tg_file_unique_id'),
        'thumb_file_id': fields.get('thumb_file_id'),
        'uploader_id': fields.get('uploader_id'),
        'source': fields.get('source') or 'bot',
    }
    columns = ', '.join(row)
    marks = ', '.join('?' for _ in row)
    async with db_manager.get_connection() as db:
        await db.execute('BEGIN IMMEDIATE')
        row['parent_id'] = await _require_folder(db, row['parent_id'])
        cursor = await db.execute(f'INSERT INTO drive_files ({columns}) VALUES ({marks})', list(row.values()))
        result = await _get_item(db, cursor.lastrowid)
        await db.commit()
        return result


async def update_item(item_id, *, name=_UNCHANGED, parent_id=_UNCHANGED) -> dict:
    async with db_manager.get_connection() as db:
        await db.execute('BEGIN IMMEDIATE')
        item = await _get_item(db, item_id)
        if not item:
            raise ItemNotFound('文件不存在')
        if name is _UNCHANGED and parent_id is _UNCHANGED:
            raise DriveError('没有需要修改的内容')
        name = item['name'] if name is _UNCHANGED else sanitize_name(name)
        if parent_id is _UNCHANGED:
            parent_id = item['parent_id']
        else:
            parent_id = await _require_folder(db, parent_id)
            if item['is_folder'] and parent_id is not None:
                trail = await _folder_trail(db, parent_id)
                if any(folder['id'] == item['id'] for folder in trail):
                    raise DriveError('不能把文件夹移到它自己里面')
        if item['is_folder'] and await _folder_name_taken(db, parent_id, name, item['id']):
            raise DriveError('目标位置已经有同名文件夹了')
        await db.execute(
            'UPDATE drive_files SET name = ?, extension = ?, parent_id = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?',
            (name, None if item['is_folder'] else split_extension(name), parent_id, item['id']),
        )
        result = await _get_item(db, item_id)
        await db.commit()
        return result


async def rename_item(item_id, name) -> dict:
    return await update_item(item_id, name=name)


async def move_item(item_id, parent_id) -> dict:
    return await update_item(item_id, parent_id=parent_id)


async def delete_records(item_id) -> list:
    """删掉这一项和它下面的全部内容，返回被删的行（调用方据此清理缓存和 Telegram 消息）。"""
    async with db_manager.get_connection() as db:
        await db.execute('BEGIN IMMEDIATE')
        cursor = await db.execute(
            'WITH RECURSIVE tree(id) AS ('
            ' SELECT id FROM drive_files WHERE id = ?'
            ' UNION SELECT f.id FROM drive_files f JOIN tree t ON f.parent_id = t.id'
            ') SELECT * FROM drive_files WHERE id IN (SELECT id FROM tree)',
            (int(item_id),),
        )
        rows = _rows(cursor, await cursor.fetchall())
        if rows:
            await db.executemany(
                'INSERT OR IGNORE INTO drive_copy_cleanup (chat_id, message_id) VALUES (?, ?)',
                [(row['tg_chat_id'], row['tg_message_id']) for row in rows if row['tg_owned']],
            )
            await db.executemany('DELETE FROM drive_files WHERE id = ?', [(row['id'],) for row in rows])
            await db.commit()
    return rows


async def _clean_copy(bot, chat_id, message_id):
    key = (chat_id, message_id)
    now = int(time.time())
    async with db_manager.get_connection() as db:
        claim = await db.execute(
            'UPDATE drive_copy_cleanup SET next_attempt = ?, attempts = attempts + 1 '
            'WHERE chat_id = ? AND message_id = ? AND next_attempt <= ?', (now + 300, *key, now),
        )
        cursor = await db.execute('SELECT next_attempt, attempts FROM drive_copy_cleanup WHERE chat_id = ? AND message_id = ?', key)
        row = await cursor.fetchone()
        await db.commit()
    if not row:
        return 'cleaned'
    if not claim.rowcount:
        return 'manual' if row[0] is None else 'pending'

    status = 'cleaned'
    retry_at = None
    try:
        await bot.delete_message(chat_id=chat_id, message_id=message_id)
    except (BadRequest, Forbidden) as exc:
        if not (isinstance(exc, BadRequest) and 'message to delete not found' in str(exc).lower()):
            status = 'manual'
            logger.warning('Telegram 存储副本需手动删除 chat=%s msg=%s: %s', *key, exc)
    except RetryAfter as exc:
        delay = exc.retry_after
        seconds = delay.total_seconds() if isinstance(delay, timedelta) else delay
        retry_at = int(time.time()) + max(60, int(seconds) + 1)
        status = 'pending'
    except Exception:
        retry_at = int(time.time()) + min(3600, 60 * 2 ** min(6, row[1] - 1))
        status = 'pending'
        logger.warning('Telegram 存储副本清理失败，将重试 chat=%s msg=%s', *key, exc_info=True)

    async with db_manager.get_connection() as db:
        if status == 'cleaned':
            await db.execute('DELETE FROM drive_copy_cleanup WHERE chat_id = ? AND message_id = ?', key)
        else:
            await db.execute('UPDATE drive_copy_cleanup SET next_attempt = ? WHERE chat_id = ? AND message_id = ?',
                             (retry_at, *key))
        await db.commit()
    return status


async def retry_cleanup(context):
    async with db_manager.get_connection() as db:
        cursor = await db.execute('SELECT chat_id, message_id FROM drive_copy_cleanup '
                                  'WHERE next_attempt <= ? ORDER BY next_attempt LIMIT ?',
                                  (int(time.time()), CLEANUP_BATCH))
        due = await cursor.fetchall()
    for chat_id, message_id in due:
        await _clean_copy(context.bot, chat_id, message_id)


async def cleanup_counts():
    async with db_manager.get_connection() as db:
        cursor = await db.execute('SELECT COUNT(next_attempt), COUNT(*) - COUNT(next_attempt) FROM drive_copy_cleanup')
        pending, manual = await cursor.fetchone()
    return {'pending': pending, 'manual': manual}


async def delete_item(bot, item_id) -> dict:
    rows = await delete_records(item_id)
    if not rows:
        raise ItemNotFound('文件不存在')
    result = {'deleted': len(rows), 'pending': 0, 'manual': 0}
    attempted = 0
    for row in rows:
        if row['is_folder']:
            continue
        for path in (cache_path(row['token']), cache_path(row['token'], thumb=True)):
            try:
                path.unlink(missing_ok=True)
            except OSError:
                logger.warning('删除网盘缓存失败 %s', path, exc_info=True)
        if row['tg_owned']:
            status = 'pending'
            if bot is not None and attempted < CLEANUP_BATCH:
                status = await _clean_copy(bot, row['tg_chat_id'], row['tg_message_id'])
                attempted += 1
            if status != 'cleaned':
                result[status] += 1
    return result


# ---------- 从 Telegram 消息入库 ----------

def _stamp() -> str:
    return datetime.now().strftime('%Y%m%d_%H%M%S')


def extract_media(message):
    """返回消息里的媒体信息字典；没有媒体时返回 None。"""
    if message is None:
        return None
    if message.photo:
        photos = list(message.photo)
        best = photos[-1]
        thumb = next((p for p in photos if max(p.width or 0, p.height or 0) >= 320), best)
        return {
            'kind': 'photo', 'file': best, 'name': f'photo_{_stamp()}.jpg', 'mime_type': 'image/jpeg',
            'thumb_file_id': thumb.file_id if thumb is not best else None,
        }
    for kind, default_ext, default_mime in (
        ('animation', 'mp4', 'video/mp4'),
        ('video', 'mp4', 'video/mp4'),
        ('document', '', 'application/octet-stream'),
        ('audio', 'mp3', 'audio/mpeg'),
        ('voice', 'ogg', 'audio/ogg'),
        ('video_note', 'mp4', 'video/mp4'),
        ('sticker', 'webp', 'image/webp'),
    ):
        obj = getattr(message, kind, None)
        if not obj:
            continue
        mime = getattr(obj, 'mime_type', None) or ''
        ext, mime_default = default_ext, default_mime
        if kind == 'sticker':
            if getattr(obj, 'is_animated', False):
                ext, mime_default = 'tgs', 'application/x-tgsticker'
            elif getattr(obj, 'is_video', False):
                ext, mime_default = 'webm', 'video/webm'
        if not ext:
            ext = (mimetypes.guess_extension(mime) or '.bin').lstrip('.') if mime else 'bin'
        name = getattr(obj, 'file_name', None) or f'{kind}_{_stamp()}.{ext}'
        thumb = getattr(obj, 'thumbnail', None)
        return {
            'kind': kind, 'file': obj, 'name': name, 'mime_type': guess_mime(name, mime) if mime else mime_default,
            'thumb_file_id': thumb.file_id if thumb else None,
        }
    return None


_SENDERS = {
    'photo': ('send_photo', 'photo'),
    'animation': ('send_animation', 'animation'),
    'video': ('send_video', 'video'),
    'document': ('send_document', 'document'),
    'audio': ('send_audio', 'audio'),
    'voice': ('send_voice', 'voice'),
    'video_note': ('send_video_note', 'video_note'),
    'sticker': ('send_sticker', 'sticker'),
}


async def store_message(bot, message, uploader_id=None, parent_id=None) -> dict:
    """把一条带媒体的消息存进网盘。配置了存储频道时先在频道里存一份副本。"""
    if not enabled():
        raise DriveError('网盘功能已关闭')
    media = extract_media(message)
    if not media:
        raise DriveError('这条消息里没有可以保存的文件')
    source_file = media['file']
    size = getattr(source_file, 'file_size', None) or 0
    limit = download_limit(bot)
    if limit and size > limit:
        raise DriveError(f'文件有 {human_size(size)}，Telegram 只允许机器人取回 20MB 以内的文件，没法生成直链')
    if size > max_upload_bytes(bot):
        raise DriveError(f'文件超过网盘单文件上限 {config.DRIVE_MAX_UPLOAD_MB}MB')

    fields = {
        'name': media['name'], 'size': size, 'mime_type': media['mime_type'], 'media_kind': media['kind'],
        'tg_chat_id': message.chat_id, 'tg_message_id': message.message_id, 'tg_owned': False,
        'tg_file_id': source_file.file_id, 'tg_file_unique_id': getattr(source_file, 'file_unique_id', None),
        'thumb_file_id': media['thumb_file_id'], 'uploader_id': uploader_id, 'parent_id': parent_id,
        'source': 'bot',
    }
    if config.DRIVE_STORAGE_CHAT_ID:
        method, argument = _SENDERS[media['kind']]
        try:
            sent = await getattr(bot, method)(
                chat_id=int(config.DRIVE_STORAGE_CHAT_ID), disable_notification=True,
                **{argument: source_file.file_id},
            )
        except Exception as exc:
            logger.warning('网盘存储频道转存失败', exc_info=True)
            raise DriveError(f'存进存储频道失败，请确认机器人是频道管理员：{exc}') from exc
        stored = extract_media(sent)
        fields.update(tg_chat_id=sent.chat_id, tg_message_id=sent.message_id, tg_owned=True)
        if stored:
            fields['tg_file_id'] = stored['file'].file_id
            fields['tg_file_unique_id'] = getattr(stored['file'], 'file_unique_id', None)
            fields['thumb_file_id'] = stored['thumb_file_id'] or fields['thumb_file_id']
    return await add_file(**fields)


async def store_upload(bot, temp_path: Path, name: str, mime_type: str, size: int,
                       uploader_id=None, parent_id=None) -> dict:
    """把面板上传的临时文件发到 Telegram 保存，并直接放进本地缓存（省一次回源下载）。"""
    if not enabled():
        raise DriveError('网盘功能已关闭')
    limit = max_upload_bytes(bot)
    if size > limit:
        raise DriveError(f'文件超过单文件上限 {human_size(limit)}')
    name = sanitize_name(name)
    async with db_manager.get_connection() as db:
        parent_id = await _require_folder(db, parent_id)
    chat_id = storage_chat_id(uploader_id)
    if not chat_id:
        raise DriveError('还没有可以存放文件的地方：请配置 DRIVE_STORAGE_CHAT_ID 或 ADMIN_IDS')
    try:
        with open(temp_path, 'rb') as handle:
            sent = await bot.send_document(
                chat_id=chat_id, document=handle, filename=name,
                disable_content_type_detection=True, disable_notification=True,
                read_timeout=UPLOAD_TIMEOUT_SECONDS, write_timeout=UPLOAD_TIMEOUT_SECONDS,
                connect_timeout=30,
            )
    except Exception as exc:
        logger.warning('网盘上传到 Telegram 失败 name=%s', name, exc_info=True)
        raise DriveError(f'上传到 Telegram 失败：{exc}') from exc

    stored = extract_media(sent)
    if not stored:
        raise DriveError('Telegram 没有返回文件信息')
    row = await add_file(
        name=name, size=size, mime_type=guess_mime(name, mime_type), media_kind=stored['kind'],
        tg_chat_id=sent.chat_id, tg_message_id=sent.message_id, tg_owned=True,
        tg_file_id=stored['file'].file_id, tg_file_unique_id=getattr(stored['file'], 'file_unique_id', None),
        thumb_file_id=stored['thumb_file_id'], uploader_id=uploader_id, parent_id=parent_id, source='web',
    )
    try:
        target = cache_path(row['token'])
        target.parent.mkdir(parents=True, exist_ok=True)
        os.replace(temp_path, target)
        evict_cache(keep=target)
    except OSError:
        logger.warning('网盘上传文件写入缓存失败', exc_info=True)
    return row


# ---------- 本地缓存 ----------

_locks: dict[str, asyncio.Lock] = {}


def cache_dir() -> Path:
    return Path(os.path.dirname(os.path.abspath(db_manager.db_path))) / 'drive_cache'


def cache_path(token: str, thumb: bool = False) -> Path:
    return cache_dir() / (f'{token}.thumb' if thumb else token)


def new_temp_file() -> Path:
    folder = cache_dir() / 'tmp'
    folder.mkdir(parents=True, exist_ok=True)
    fd, path = tempfile.mkstemp(dir=folder, prefix='upload_')
    os.close(fd)
    return Path(path)


def _lock_for(key: str) -> asyncio.Lock:
    lock = _locks.get(key)
    if lock is None:
        if len(_locks) >= 512:
            for stale in [k for k, v in _locks.items() if not v.locked()]:
                _locks.pop(stale, None)
        lock = asyncio.Lock()
        _locks[key] = lock
    return lock


def evict_cache(keep: Path = None) -> None:
    limit = max(50, int(config.DRIVE_CACHE_MAX_MB or 1024)) * 1024 * 1024
    folder = cache_dir()
    if not folder.exists():
        return
    entries = []
    for path in folder.iterdir():
        if path.is_file():
            stat = path.stat()
            entries.append((stat.st_atime, stat.st_size, path))
    total = sum(size for _, size, _ in entries)
    for _, size, path in sorted(entries):
        if total <= limit:
            break
        if keep is not None and path == keep:
            continue
        try:
            path.unlink()
            total -= size
        except OSError:
            pass


async def ensure_cached(bot, row: dict, thumb: bool = False) -> Path:
    """返回文件在本地缓存里的路径，没有就先从 Telegram 拉回来。"""
    file_id = row['thumb_file_id'] if thumb else row['tg_file_id']
    if not file_id:
        raise DriveError('没有可用的文件')
    target = cache_path(row['token'], thumb=thumb)
    async with _lock_for(target.name):
        if target.exists():
            try:
                os.utime(target, ns=(time.time_ns(), target.stat().st_mtime_ns))
            except OSError:
                pass
            return target
        limit = download_limit(bot)
        if not thumb and limit and (row['size'] or 0) > limit:
            raise DriveError('文件超过 20MB，Telegram 不允许机器人取回')
        target.parent.mkdir(parents=True, exist_ok=True)
        partial = target.with_name(f'{target.name}.part')
        try:
            tg_file = await bot.get_file(file_id)
            await tg_file.download_to_drive(custom_path=partial)
            os.replace(partial, target)
        except DriveError:
            raise
        except Exception as exc:
            partial.unlink(missing_ok=True)
            logger.warning('网盘回源下载失败 token=%s', row['token'], exc_info=True)
            raise DriveError('暂时取不回这个文件，请稍后再试') from exc
    evict_cache(keep=target)
    return target
