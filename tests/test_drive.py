"""网盘测试：名称校验、文件夹树、面板上传接口与公开直链（假 Bot 顶替 Telegram）。"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import aiohttp
import pytest
from aiohttp.test_utils import TestClient, TestServer
from telegram.error import BadRequest, RetryAfter, TimedOut

from config import config
from database.db_manager import DatabaseManager
from services import drive
from web_panel import auth
from web_panel.server import create_app

PANEL_HEADERS = {'X-Requested-With': 'PMBotPanel'}
DRIVE_SETTINGS = (
    'DRIVE_ENABLED', 'DRIVE_STORAGE_CHAT_ID', 'DRIVE_PUBLIC_URL',
    'DRIVE_MAX_UPLOAD_MB', 'DRIVE_CACHE_MAX_MB', 'MINIAPP_PUBLIC_URL',
)


class FakeTelegramFile:
    def __init__(self, content):
        self.content = content

    async def download_to_drive(self, custom_path=None):
        with open(custom_path, 'wb') as handle:
            handle.write(self.content)


class StubBot:
    """把发出去的文件记在内存里，get_file 再原样还回来。"""

    id = 424242
    username = 'stub_bot'
    token = '123456:STUB'
    base_url = 'https://api.telegram.org/bot'

    def __init__(self):
        self.files = {}
        self.sent = []
        self.deleted = []
        self.downloads = 0

    async def send_document(self, chat_id, document, filename=None, **kwargs):
        content = document.read()
        file_id = f'file-{len(self.files) + 1}'
        self.files[file_id] = content
        self.sent.append(chat_id)
        return SimpleNamespace(
            chat_id=chat_id, message_id=100 + len(self.sent), photo=None,
            document=SimpleNamespace(
                file_id=file_id, file_unique_id=f'u-{file_id}', file_name=filename,
                file_size=len(content), mime_type='application/octet-stream', thumbnail=None,
            ),
        )

    async def get_file(self, file_id):
        self.downloads += 1
        return FakeTelegramFile(self.files[file_id])

    async def delete_message(self, chat_id, message_id):
        self.deleted.append((chat_id, message_id))

    async def send_message(self, **kwargs):
        raise AssertionError('测试环境不应真的发送 Telegram 消息')


def test_sanitize_name_and_token():
    assert drive.sanitize_name('  ../a/b\\c.png ') == 'c.png'
    for bad in ('', '..', '.', 'a\x01b', 'x' * 256):
        with pytest.raises(drive.DriveError):
            drive.sanitize_name(bad)
    assert len(drive.new_token()) == 22
    assert drive.split_extension('Photo.JPG') == 'jpg'
    assert drive.split_extension('.env') is None
    assert drive.is_inline_type('image/png; charset=binary')
    assert not drive.is_inline_type('text/html')
    assert not drive.is_inline_type('image/svg+xml')


def test_link_formats():
    row = {'token': 'tok', 'name': 'a b.png', 'mime_type': 'image/png', 'thumb_file_id': None}
    links = drive.link_formats(row, 'https://pm.example')
    assert links['url'] == 'https://pm.example/drive/f/tok/a%20b.png'
    assert links['markdown'] == '![a b.png](https://pm.example/drive/f/tok/a%20b.png)'
    assert links['bbcode'].startswith('[img]')
    assert drive.file_url(row, download=True) == '/drive/f/tok/a%20b.png?dl=1'
    doc = dict(row, name='a.zip', mime_type='application/zip')
    assert drive.link_formats(doc, '')['markdown'] == '[a.zip](/drive/f/tok/a.zip)'
    assert drive.thumb_url(doc) is None


async def _folder_flow(tmp_path):
    db_manager = DatabaseManager(str(tmp_path / 'drive_tree.db'))
    await db_manager.initialize()
    bot = StubBot()
    try:
        root = await drive.create_folder('相册')
        child = await drive.create_folder('2026', root['id'])
        grandchild = await drive.create_folder('11', child['id'])
        with pytest.raises(drive.DriveError):
            await drive.create_folder('相册')
        with pytest.raises(drive.DriveError):
            await drive.create_folder('x', 99999)

        await drive.add_file(name='a.jpg', tg_file_id='f1', size=10, parent_id=grandchild['id'],
                             tg_chat_id=1, tg_message_id=2, tg_owned=True)
        await drive.add_file(name='b.txt', tg_file_id='f2', size=5, parent_id=child['id'])

        items, total = await drive.list_items(child['id'])
        assert total == 2
        assert items[0]['is_folder'] == 1
        _, total = await drive.list_items(query='a.j')
        assert total == 1
        _, total = await drive.list_items(query='%')
        assert total == 0

        trail = await drive.breadcrumbs(grandchild['id'])
        assert [node['name'] for node in trail] == ['相册', '2026', '11']

        with pytest.raises(drive.DriveError):
            await drive.move_item(root['id'], grandchild['id'])
        with pytest.raises(drive.DriveError):
            await drive.move_item(root['id'], root['id'])
        moved = await drive.move_item(grandchild['id'], None)
        assert moved['parent_id'] is None
        renamed = await drive.rename_item(moved['id'], '十一月')
        assert renamed['name'] == '十一月'
        with pytest.raises(drive.DriveError):
            await drive.rename_item(moved['id'], '相册')

        assert (await drive.stats()) == {'files': 2, 'folders': 3, 'bytes': 15}
        assert await drive.delete_item(bot, root['id']) == {'deleted': 3, 'manual': 0, 'pending': 0}
        assert await drive.delete_item(bot, moved['id']) == {'deleted': 2, 'manual': 0, 'pending': 0}
        assert bot.deleted == [(1, 2)]
        assert (await drive.stats()) == {'files': 0, 'folders': 0, 'bytes': 0}
        with pytest.raises(drive.DriveError):
            await drive.delete_item(bot, root['id'])
    finally:
        await db_manager.close_all()


def test_drive_folder_tree(tmp_path):
    asyncio.run(_folder_flow(tmp_path))


@pytest.mark.asyncio
async def test_atomic_folder_moves(tmp_path):
    manager = DatabaseManager(str(tmp_path / 'moves.db'))
    await manager.initialize()
    try:
        root = await drive.create_folder('root')
        deepest = root
        for depth in range(66):
            deepest = await drive.create_folder(str(depth), deepest['id'])
        with pytest.raises(drive.DriveError):
            await drive.update_item(root['id'], name='changed', parent_id=deepest['id'])
        assert (await drive.get_item(root['id']))['name'] == 'root'
        assert len(await drive.breadcrumbs(deepest['id'])) == 67

        other = await drive.create_folder('other')
        concurrent = await asyncio.gather(drive.move_item(root['id'], other['id']),
                                          drive.move_item(other['id'], root['id']), return_exceptions=True)
        assert sum(isinstance(result, drive.DriveError) for result in concurrent) == 1
        roots, total = await drive.list_items()
        assert total == len(roots) == 1

        source = await drive.create_folder('source')
        destination = await drive.create_folder('destination')
        moving = await drive.create_folder('before', source['id'])
        await drive.create_folder('taken', destination['id'])
        with pytest.raises(drive.DriveError):
            await drive.update_item(moving['id'], name='taken', parent_id=destination['id'])
        assert await drive.get_item(moving['id']) == moving
        await drive.create_folder('after', source['id'])
        changed = await drive.update_item(moving['id'], name='after', parent_id=destination['id'])
        assert (changed['name'], changed['parent_id']) == ('after', destination['id'])
    finally:
        await manager.close_all()


@pytest.mark.asyncio
async def test_copy_cleanup_retries_after_restart(tmp_path, monkeypatch):
    manager = DatabaseManager(str(tmp_path / 'cleanup.db'))
    await manager.initialize()
    clock = 1000
    monkeypatch.setenv('PTB_TIMEDELTA', '1')
    monkeypatch.setattr(drive, 'time', SimpleNamespace(time=lambda: clock))
    bot = StubBot()
    failures = {1: TimedOut(), 2: BadRequest("Message can't be deleted"),
                3: BadRequest('Message to delete not found'), 4: RetryAfter(120)}

    async def delete_copy(chat_id, message_id):
        bot.deleted.append((chat_id, message_id))
        if message_id in failures:
            raise failures.pop(message_id)

    bot.delete_message = delete_copy
    try:
        folder = await drive.create_folder('cleanup')
        for message_id in range(1, 6):
            item = await drive.add_file(name=str(message_id), parent_id=folder['id'], tg_file_id='file',
                                        tg_chat_id=55, tg_message_id=message_id, tg_owned=message_id != 5)
        result = await drive.delete_item(bot, folder['id'])
        assert result == {'deleted': 6, 'pending': 2, 'manual': 1}
        assert await drive.get_by_token(item['token']) is None
        await manager.close_all()
        await manager.initialize()
        assert await drive.cleanup_counts() == {'pending': 2, 'manual': 1}
        clock += 61
        await drive.retry_cleanup(SimpleNamespace(bot=bot))
        assert await drive.cleanup_counts() == {'pending': 1, 'manual': 1}
        clock += 61
        await drive.retry_cleanup(SimpleNamespace(bot=bot))
        assert await drive.cleanup_counts() == {'pending': 0, 'manual': 1}
        assert sorted(message for _, message in bot.deleted) == [1, 1, 2, 3, 4, 4]
    finally:
        await manager.close_all()


async def _store_message_flow(tmp_path):
    db_manager = DatabaseManager(str(tmp_path / 'drive_message.db'))
    await db_manager.initialize()
    originals = {name: getattr(config, name) for name in DRIVE_SETTINGS}
    config.DRIVE_ENABLED = True
    config.DRIVE_STORAGE_CHAT_ID = ''
    config.DRIVE_MAX_UPLOAD_MB = 20
    try:
        small = SimpleNamespace(file_id='p-small', file_unique_id='s', width=90, height=90, file_size=100)
        middle = SimpleNamespace(file_id='p-mid', file_unique_id='m', width=320, height=240, file_size=500)
        large = SimpleNamespace(file_id='p-big', file_unique_id='b', width=1280, height=960, file_size=9000)
        message = SimpleNamespace(chat_id=7, message_id=8, photo=(small, middle, large))
        row = await drive.store_message(StubBot(), message, uploader_id=7)
        assert row['tg_file_id'] == 'p-big'
        assert row['thumb_file_id'] == 'p-mid'
        assert row['mime_type'] == 'image/jpeg'
        assert row['tg_owned'] == 0
        assert row['name'].endswith('.jpg')

        too_big = SimpleNamespace(
            chat_id=7, message_id=9, photo=None,
            document=SimpleNamespace(file_id='d', file_size=30 * 1024 * 1024, file_name='big.zip',
                                     mime_type='application/zip', thumbnail=None),
        )
        with pytest.raises(drive.DriveError):
            await drive.store_message(StubBot(), too_big)
        assert drive.extract_media(SimpleNamespace(photo=None)) is None

        config.DRIVE_ENABLED = False
        with pytest.raises(drive.DriveError):
            await drive.store_message(StubBot(), message)
    finally:
        for name, value in originals.items():
            setattr(config, name, value)
        await db_manager.close_all()


def test_drive_store_message(tmp_path):
    asyncio.run(_store_message_flow(tmp_path))


async def _http_flow(tmp_path):
    db_manager = DatabaseManager(str(tmp_path / 'drive_http.db'))
    await db_manager.initialize()

    originals = {name: getattr(config, name) for name in DRIVE_SETTINGS}
    original_password = config.WEB_PANEL_PASSWORD
    original_sha = config.WEB_PANEL_PASSWORD_SHA256
    original_admins = config.ADMIN_IDS
    config.WEB_PANEL_PASSWORD = 'test-pass'
    config.WEB_PANEL_PASSWORD_SHA256 = ''
    config.ADMIN_IDS = [555]
    config.DRIVE_ENABLED = True
    config.DRIVE_STORAGE_CHAT_ID = ''
    config.DRIVE_PUBLIC_URL = 'https://pm.example'
    config.DRIVE_MAX_UPLOAD_MB = 1
    config.DRIVE_CACHE_MAX_MB = 1024
    auth._sessions.clear()
    auth._login_failures.clear()

    bot = StubBot()
    http_session = aiohttp.ClientSession()
    client = TestClient(TestServer(create_app(bot, http_session)))
    await client.start_server()
    try:
        response = await client.get('/api/drive/status')
        assert response.status == 401

        response = await client.post('/api/login', json={'password': 'test-pass'})
        assert response.status == 200

        response = await client.get('/api/drive/status')
        status = (await response.json())['data']
        assert status['enabled'] is True
        assert status['storage'] == 'admin_chat'
        assert status['public_base'] == 'https://pm.example'
        assert status['download_limit_mb'] == 20

        response = await client.post('/api/drive/folders', json={'name': '图片'})
        assert response.status == 403
        response = await client.post('/api/drive/folders', json={'name': '图片'}, headers=PANEL_HEADERS)
        assert response.status == 200
        folder = (await response.json())['data']

        form = aiohttp.FormData()
        form.add_field('file', b'\x89PNG fake image', filename='猫猫.png', content_type='image/png')
        response = await client.post(f"/api/drive/upload?parent_id={folder['id']}", data=form, headers=PANEL_HEADERS)
        assert response.status == 200, await response.text()
        item = (await response.json())['data']
        assert bot.sent == [555]
        assert item['parent_id'] == folder['id']
        assert item['mime_type'] == 'image/png'
        assert item['url'].startswith('https://pm.example/drive/f/')
        assert item['preview_url'].startswith('/drive/f/')
        assert item['thumb_url'].startswith('/drive/t/')

        form = aiohttp.FormData()
        form.add_field('file', b'x' * (1024 * 1024 + 10), filename='big.bin')
        response = await client.post('/api/drive/upload', data=form, headers=PANEL_HEADERS)
        assert response.status == 413

        config.DRIVE_MAX_UPLOAD_MB = 30
        assert drive.max_upload_bytes(bot) == 20 * 1024 * 1024
        assert drive.max_upload_bytes(SimpleNamespace(base_url='http://localhost/bot')) == 30 * 1024 * 1024
        with patch.object(drive, 'OFFICIAL_DOWNLOAD_LIMIT', 1024):
            for size in (1025, 100_000):
                form = aiohttp.FormData()
                form.add_field('file', bytes(size), filename='above-limit.bin')
                response = await client.post('/api/drive/upload', data=form, headers=PANEL_HEADERS)
                assert response.status == 413
            with pytest.raises(drive.DriveError):
                await drive.store_upload(bot, tmp_path / 'not-opened', 'above-limit.bin', '', 1025)
        assert bot.sent == [555]

        response = await client.get(f"/api/drive/files?parent_id={folder['id']}")
        listing = (await response.json())['data']
        assert listing['total'] == 1
        assert listing['breadcrumbs'] == [{'id': folder['id'], 'name': '图片'}]
        response = await client.get('/api/drive/files?parent_id=99999')
        assert response.status == 404

        # 公开直链不需要登录：上传时已写入缓存，不应回源下载
        anonymous = TestClient(TestServer(create_app(bot, http_session)))
        await anonymous.start_server()
        try:
            response = await anonymous.get(item['preview_url'])
            assert response.status == 200
            assert await response.read() == b'\x89PNG fake image'
            assert response.headers['Content-Type'] == 'image/png'
            assert response.headers['Content-Disposition'].startswith('inline;')
            assert 'sandbox' in response.headers['Content-Security-Policy']
            assert response.headers['Access-Control-Allow-Origin'] == '*'
            assert 'no-cache' in response.headers['Cache-Control']
            validator = response.headers['ETag']
            assert bot.downloads == 0

            response = await anonymous.get(item['preview_url'], headers={'If-None-Match': validator})
            assert response.status == 304
            assert 'no-cache' in response.headers['Cache-Control']

            response = await anonymous.get(item['preview_url'] + '?dl=1')
            assert response.headers['Content-Type'] == 'application/octet-stream'
            assert response.headers['Content-Disposition'].startswith('attachment;')

            response = await anonymous.get(item['thumb_url'])
            assert response.status == 200
            assert 'no-cache' in response.headers['Cache-Control']
            assert await response.read() == b'\x89PNG fake image'

            # 删除缓存后会通过 getFile 回源
            drive.cache_path(item['url'].split('/')[-2]).unlink()
            response = await anonymous.get(item['preview_url'])
            assert response.status == 200
            assert await response.read() == b'\x89PNG fake image'
            assert bot.downloads == 1

            response = await anonymous.get('/drive/f/not-a-real-token/x.png')
            assert response.status == 404
            response = await anonymous.get('/api/drive/files')
            assert response.status == 401
        finally:
            await anonymous.close()

        config.DRIVE_ENABLED = False
        for method, path, changes in (
            ('POST', '/api/drive/folders', {'name': 'disabled'}),
            ('POST', '/api/drive/upload', {}),
            ('PUT', f"/api/drive/files/{item['id']}", {'name': 'disabled', 'parent_id': None}),
            ('DELETE', f"/api/drive/files/{folder['id']}", {}),
        ):
            response = await client.request(method, path, headers=PANEL_HEADERS, json=changes)
            assert response.status == 403
        for path in ('/api/drive/files', '/api/drive/folders', item['preview_url'], item['thumb_url']):
            response = await client.get(path)
            assert response.status == 200
            await response.read()
        config.DRIVE_ENABLED = True
        before = await drive.get_item(item['id'])
        response = await client.put(f"/api/drive/files/{item['id']}", json={'name': 'changed', 'parent_id': -1},
                                    headers=PANEL_HEADERS)
        assert response.status == 400
        assert await drive.get_item(item['id']) == before
        response = await client.put(f"/api/drive/files/{item['id']}", json={'name': '狗狗.png', 'parent_id': None},
                                    headers=PANEL_HEADERS)
        assert response.status == 200
        updated = (await response.json())['data']
        assert updated['name'] == '狗狗.png'
        assert updated['parent_id'] is None

        html_form = aiohttp.FormData()
        html_form.add_field('file', b'<script>alert(1)</script>', filename='page.html', content_type='text/html')
        response = await client.post('/api/drive/upload', data=html_form, headers=PANEL_HEADERS)
        page = (await response.json())['data']
        response = await client.get(page['preview_url'])
        assert response.headers['Content-Type'] == 'application/octet-stream'
        assert response.headers['Content-Disposition'].startswith('attachment;')

        response = await client.delete(f"/api/drive/files/{folder['id']}", headers=PANEL_HEADERS)
        assert (await response.json())['data']['deleted'] == 1
        with patch.object(bot, 'delete_message', AsyncMock(side_effect=TimedOut())):
            response = await client.delete(f"/api/drive/files/{item['id']}", headers=PANEL_HEADERS)
        assert response.status == 200
        assert (await response.json())['data'] == {'deleted': 1, 'pending': 1, 'manual': 0}
        assert not drive.cache_path(item['url'].split('/')[-2]).exists()
        for path in (item['preview_url'], item['thumb_url']):
            response = await client.get(path, headers={'If-None-Match': validator})
            assert response.status == 404
    finally:
        await client.close()
        await http_session.close()
        for name, value in originals.items():
            setattr(config, name, value)
        config.WEB_PANEL_PASSWORD = original_password
        config.WEB_PANEL_PASSWORD_SHA256 = original_sha
        config.ADMIN_IDS = original_admins
        auth._sessions.clear()
        auth._login_failures.clear()
        await db_manager.close_all()


def test_drive_http_flow(tmp_path):
    asyncio.run(_http_flow(tmp_path))
