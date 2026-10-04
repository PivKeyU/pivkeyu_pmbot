"""自动更新服务的隔离测试：不访问 Docker Hub，也不触发真实容器更新。"""

import asyncio
import subprocess
from pathlib import Path

from services import image_update, safe_update


def test_image_update_establishes_baseline_and_detects_new_digest(monkeypatch):
    metadata = {}
    digest = {'value': 'sha256:first'}

    async def get_meta(key):
        return metadata.get(key)

    async def set_meta(key, value):
        metadata[key] = value

    async def fetch_digest(repo, tag):
        return digest['value']

    monkeypatch.setattr(image_update.db, 'get_app_meta', get_meta)
    monkeypatch.setattr(image_update.db, 'set_app_meta', set_meta)
    monkeypatch.setattr(image_update, 'detect_container', lambda: True)
    monkeypatch.setattr(image_update, 'resolve_image', lambda: ('example/bot', 'latest'))
    monkeypatch.setattr(image_update, '_local_sha', lambda: 'local-build')
    monkeypatch.setattr(image_update, 'fetch_remote_digest', fetch_digest)

    first = asyncio.run(image_update.check_for_update())
    assert first.in_container is True
    assert first.update_available is False
    assert metadata[image_update.IMAGE_DIGEST_META_KEY] == 'sha256:first'

    digest['value'] = 'sha256:second'
    second = asyncio.run(image_update.check_for_update())
    assert second.known_digest == 'sha256:first'
    assert second.remote_digest == 'sha256:second'
    assert second.update_available is True


def test_watchtower_trigger_sends_token_and_requires_configuration(monkeypatch):
    calls = []

    def post_update(url, token):
        calls.append((url, token))
        return 202, 'accepted'

    monkeypatch.setenv('WATCHTOWER_HTTP_API_URL', 'http://watchtower:8080/')
    monkeypatch.setenv('WATCHTOWER_HTTP_API_TOKEN', 'test-token')
    monkeypatch.setattr(image_update, '_post_watchtower_update', post_update)

    ok, _ = asyncio.run(image_update.trigger_watchtower_update())
    assert ok is True
    assert calls == [('http://watchtower:8080/v1/update', 'test-token')]

    monkeypatch.delenv('WATCHTOWER_HTTP_API_TOKEN')
    ok, _ = asyncio.run(image_update.trigger_watchtower_update())
    assert ok is False
    assert len(calls) == 1


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ['git', '-C', str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def test_git_update_fast_forwards_and_rolls_back(tmp_path, monkeypatch):
    remote = tmp_path / 'remote.git'
    local = tmp_path / 'local'
    peer = tmp_path / 'peer'
    metadata = {}

    async def get_meta(key):
        return metadata.get(key)

    async def set_meta(key, value):
        metadata[key] = value

    monkeypatch.setattr(safe_update.db, 'get_app_meta', get_meta)
    monkeypatch.setattr(safe_update.db, 'set_app_meta', set_meta)

    _git(tmp_path, 'init', '--bare', '--initial-branch=main', str(remote))
    subprocess.run(
        ['git', 'init', '--initial-branch=main', str(local)],
        check=True,
        capture_output=True,
        text=True,
    )
    _git(local, 'config', 'user.name', 'Update Test')
    _git(local, 'config', 'user.email', 'update-test@example.invalid')
    (local / 'README.md').write_text('base\n', encoding='utf-8')
    _git(local, 'add', 'README.md')
    _git(local, 'commit', '-m', 'base')
    original = _git(local, 'rev-parse', 'HEAD')
    _git(local, 'remote', 'add', 'origin', str(remote))
    _git(local, 'push', '--set-upstream', 'origin', 'main')

    subprocess.run(
        ['git', 'clone', str(remote), str(peer)],
        check=True,
        capture_output=True,
        text=True,
    )
    _git(peer, 'config', 'user.name', 'Update Test')
    _git(peer, 'config', 'user.email', 'update-test@example.invalid')
    (peer / 'release.txt').write_text('new release\n', encoding='utf-8')
    _git(peer, 'add', 'release.txt')
    _git(peer, 'commit', '-m', 'release')
    _git(peer, 'push', 'origin', 'main')

    updated = asyncio.run(safe_update.apply_update(local))
    assert updated.behind == 0
    assert updated.ahead == 0
    assert updated.dirty is False
    assert (local / 'release.txt').read_text(encoding='utf-8') == 'new release\n'
    assert metadata['last_update_rollback'] == original

    rolled_back = asyncio.run(safe_update.rollback_last_update(local))
    assert rolled_back == original
    assert not (local / 'release.txt').exists()
