"""网盘接口：面板与 Mini App 共用的管理接口，以及对外公开的直链。

管理接口在 /api/drive/ 下，受面板登录或 Mini App 会话保护；
直链 /drive/f/<token>/<文件名> 与缩略图 /drive/t/<token> 不需要登录，知道链接就能访问。
"""

import logging
import os
from functools import wraps
from urllib.parse import quote

from aiohttp import web

from config import config
from services import drive
from . import http_utils, keys

logger = logging.getLogger(__name__)

routes = web.RouteTableDef()
public_routes = web.RouteTableDef()

UPLOAD_CHUNK_BYTES = 256 * 1024
FILE_CONTENT_SECURITY_POLICY = (
    "default-src 'none'; img-src 'self'; media-src 'self'; style-src 'unsafe-inline'; sandbox"
)


def _uploader_id(request):
    user_id = request.get(keys.MINIAPP_USER_KEY)
    if user_id:
        return int(user_id)
    return int(config.ADMIN_IDS[0]) if config.ADMIN_IDS else None


def _parse_folder_id(value):
    if value in (None, '', 0, '0'):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        raise ValueError('文件夹参数需要是整数')


def _item_payload(row, base):
    item = drive.serialize(row, base)
    if not item['is_folder']:
        item['preview_url'] = drive.file_url(row)
        item['thumb_url'] = drive.thumb_url(row)
    return item


def _storage_mode():
    if config.DRIVE_STORAGE_CHAT_ID:
        return 'channel'
    return 'admin_chat' if config.ADMIN_IDS else 'none'


def _writable(handler):
    @wraps(handler)
    async def checked(request):
        if not drive.enabled():
            return http_utils.fail('网盘已关闭，当前为只读模式', status=403)
        return await handler(request)
    return checked


@routes.get('/api/drive/status')
async def drive_status(request):
    return http_utils.ok({
        'enabled': drive.enabled(),
        'storage': _storage_mode(),
        'auto_upload': bool(config.DRIVE_AUTO_UPLOAD),
        'max_upload_mb': int(config.DRIVE_MAX_UPLOAD_MB or 20),
        'download_limit_mb': (drive.download_limit(request.app[keys.BOT_KEY]) or 0) // (1024 * 1024),
        'public_base': drive.public_base(request),
        'public_base_configured': bool(config.DRIVE_PUBLIC_URL),
        'stats': await drive.stats(),
        'cleanup': await drive.cleanup_counts(),
    })


@routes.get('/api/drive/files')
async def drive_files(request):
    parent_id = http_utils.get_int(request, 'parent_id', None, minimum=0) or None
    query = http_utils.search_query(request)
    sort = request.query.get('sort') or 'time'
    page, per_page, offset = http_utils.pagination(request, default_per=50, max_per=200)
    if parent_id is not None:
        folder = await drive.get_item(parent_id)
        if not folder or not folder['is_folder']:
            return http_utils.fail('文件夹不存在', status=404)
    rows, total = await drive.list_items(parent_id, query, sort, offset, per_page)
    base = drive.public_base(request)
    payload = http_utils.page_payload([_item_payload(row, base) for row in rows], total, page, per_page)
    payload['breadcrumbs'] = await drive.breadcrumbs(parent_id) if parent_id else []
    payload['parent_id'] = parent_id
    return http_utils.ok(payload)


@routes.get('/api/drive/folders')
async def drive_folders(request):
    return http_utils.ok({'items': await drive.list_folders()})


@routes.post('/api/drive/folders')
@_writable
async def drive_create_folder(request):
    data = await http_utils.read_json(request)
    try:
        folder = await drive.create_folder(data.get('name'), _parse_folder_id(data.get('parent_id')))
    except drive.DriveError as exc:
        return http_utils.fail(exc)
    http_utils.audit(request, 'drive.folder.create', f"id={folder['id']}")
    return http_utils.ok(_item_payload(folder, drive.public_base(request)))


@routes.post('/api/drive/upload')
@_writable
async def drive_upload(request):
    parent_id = _parse_folder_id(request.query.get('parent_id'))
    limit = drive.max_upload_bytes(request.app[keys.BOT_KEY])
    too_large = f'文件超过单文件上限 {drive.human_size(limit)}'
    if request.content_length and request.content_length > limit + 64 * 1024:
        return http_utils.fail(too_large, status=413)
    try:
        reader = await request.multipart()
    except (AssertionError, ValueError):
        return http_utils.fail('请用表单方式上传文件')

    temp_path = None
    try:
        field = await reader.next()
        while field is not None and getattr(field, 'name', None) != 'file':
            await field.release()
            field = await reader.next()
        if field is None or not field.filename:
            return http_utils.fail('没有收到文件')

        name = field.filename
        mime_type = field.headers.get('Content-Type') or ''
        temp_path = drive.new_temp_file()
        size = 0
        with open(temp_path, 'wb') as handle:
            while True:
                chunk = await field.read_chunk(UPLOAD_CHUNK_BYTES)
                if not chunk:
                    break
                size += len(chunk)
                if size > limit:
                    return http_utils.fail(too_large, status=413)
                handle.write(chunk)
        if size == 0:
            return http_utils.fail('不能上传空文件')

        row = await drive.store_upload(
            request.app[keys.BOT_KEY], temp_path, name, mime_type, size,
            uploader_id=_uploader_id(request), parent_id=parent_id,
        )
    except drive.DriveError as exc:
        return http_utils.fail(exc)
    finally:
        if temp_path is not None and temp_path.exists():
            try:
                os.remove(temp_path)
            except OSError:
                logger.warning('清理网盘上传临时文件失败 %s', temp_path)
    http_utils.audit(request, 'drive.upload', f"id={row['id']} size={row['size']}")
    return http_utils.ok(_item_payload(row, drive.public_base(request)))


@routes.put('/api/drive/files/{file_id}')
@_writable
async def drive_update(request):
    item_id = int(request.match_info['file_id'])
    data = await http_utils.read_json(request)
    changes = {key: data[key] for key in ('name', 'parent_id') if key in data}
    if 'parent_id' in changes:
        changes['parent_id'] = _parse_folder_id(changes['parent_id'])
    try:
        row = await drive.update_item(item_id, **changes)
    except drive.ItemNotFound as exc:
        return http_utils.fail(exc, status=404)
    except drive.DriveError as exc:
        return http_utils.fail(exc)
    http_utils.audit(request, 'drive.update', f'id={item_id}')
    return http_utils.ok(_item_payload(row, drive.public_base(request)))


@routes.delete('/api/drive/files/{file_id}')
@_writable
async def drive_delete(request):
    item_id = int(request.match_info['file_id'])
    try:
        result = await drive.delete_item(request.app[keys.BOT_KEY], item_id)
    except drive.ItemNotFound as exc:
        return http_utils.fail(exc, status=404)
    http_utils.audit(request, 'drive.delete', f'id={item_id} result={result}')
    return http_utils.ok(result)


# ---------- 公开直链 ----------

def _content_disposition(kind: str, name: str) -> str:
    fallback = ''.join(ch if 32 <= ord(ch) < 127 and ch not in '"\\;' else '_' for ch in name) or 'file'
    return f"{kind}; filename=\"{fallback}\"; filename*=UTF-8''{quote(name, safe='')}"


def _file_headers(content_type: str, disposition: str) -> dict:
    return {
        'Content-Type': content_type,
        'Content-Disposition': disposition,
        'Content-Security-Policy': FILE_CONTENT_SECURITY_POLICY,
        'Access-Control-Allow-Origin': '*',
        'Cache-Control': 'no-cache, must-revalidate',
        'X-Content-Type-Options': 'nosniff',
    }


def _text_error(text: str, status: int):
    return web.Response(text=text, status=status, content_type='text/plain', charset='utf-8')


async def _serve_file(request):
    row = await drive.get_by_token(request.match_info['token'])
    if not row:
        return _text_error('文件不存在或已被删除', 404)
    try:
        path = await drive.ensure_cached(request.app[keys.BOT_KEY], row)
    except drive.DriveError as exc:
        return _text_error(str(exc), 502)
    mime_type = row['mime_type'] or 'application/octet-stream'
    inline = drive.is_inline_type(mime_type) and request.query.get('dl') != '1'
    content_type = mime_type if inline else 'application/octet-stream'
    disposition = _content_disposition('inline' if inline else 'attachment', row['name'])
    return web.FileResponse(path, headers=_file_headers(content_type, disposition))


@public_routes.get('/drive/f/{token}')
async def drive_public_file(request):
    return await _serve_file(request)


@public_routes.get('/drive/f/{token}/{name}')
async def drive_public_file_named(request):
    return await _serve_file(request)


@public_routes.get('/drive/t/{token}')
async def drive_public_thumb(request):
    row = await drive.get_by_token(request.match_info['token'])
    if not row:
        return _text_error('文件不存在或已被删除', 404)
    bot = request.app[keys.BOT_KEY]
    try:
        if row['thumb_file_id']:
            path = await drive.ensure_cached(bot, row, thumb=True)
            content_type = 'image/webp' if row['media_kind'] == 'sticker' else 'image/jpeg'
        elif drive.is_image(row) and (row['size'] or 0) <= drive.THUMB_FALLBACK_MAX_BYTES:
            path = await drive.ensure_cached(bot, row)
            content_type = row['mime_type']
        else:
            return _text_error('这个文件没有缩略图', 404)
    except drive.DriveError as exc:
        return _text_error(str(exc), 502)
    disposition = _content_disposition('inline', f"thumb_{row['name']}")
    return web.FileResponse(path, headers=_file_headers(content_type, disposition))
