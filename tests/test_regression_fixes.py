"""核心修复回归测试：数据库约束/迁移、并发锁、限速、监控/RSS/网络工具、Web 面板安全边界。"""

import asyncio
import json
import time
from types import SimpleNamespace

import aiohttp
from aiohttp.test_utils import TestClient, TestServer
import pytest
from telegram.error import TelegramError

from config import config
from database import models as db
from database.db_manager import DatabaseManager
import network_test.commands as nt_commands
import network_test.network as nt_network
import network_test.state as nt_state
import rss
import rss.feed_checker as rss_checker
import rss.settings as rss_settings
from services import blacklist, tg_monitor, thread_manager, verification, web_monitor
from services.rate_limiter import RateLimiter
from web_panel import auth, miniapp
from web_panel.server import create_app


class _FakeReplyMessage:
    def __init__(self, chat_id: int, user_id: int, text: str = '', chat_type: str = 'private'):
        self.chat_id = chat_id
        self.chat = SimpleNamespace(id=chat_id, type=chat_type, title='测试群', username='testgroup')
        self.from_user = SimpleNamespace(id=user_id, username='tester', first_name='Test', full_name='Test')
        self.text = text
        self.caption = ''
        self.message_id = 101
        self.date = None
        self.replies: list[tuple[str, dict]] = []

    async def reply_text(self, text: str, **kwargs):
        self.replies.append((text, kwargs))
        return SimpleNamespace(message_id=201)

    async def reply_photo(self, photo=None, caption: str = '', **kwargs):
        self.replies.append((caption, kwargs))
        return SimpleNamespace(message_id=202)


def test_returning_user_update_profile_and_crm_metadata(tmp_path):
    async def scenario():
        manager = DatabaseManager(str(tmp_path / 'profile.db'))
        await manager.initialize()
        try:
            await db.add_user(user_id=5001, username='old', first_name='旧名', last_name='A', language_code='en')
            await db.update_user_metadata(5001, 'VIP 客户', ['vip', 'cloud'])
            await db.update_user_profile(
                user_id=5001,
                username='newname',
                first_name='新名',
                last_name='B',
                language_code='zh',
            )
            user = await db.get_user(5001)
            assert user['username'] == 'newname'
            assert user['first_name'] == '新名'
            assert user['note'] == 'VIP 客户'
            assert json.loads(user['tags']) == ['vip', 'cloud']
        finally:
            await manager.close_all()

    asyncio.run(scenario())


def test_spam_keyword_env_seeding_respects_config(tmp_path, monkeypatch):
    monkeypatch.setattr(config, 'SPAM_KEYWORD_FILTER_ENABLED', True)
    monkeypatch.setattr(config, 'SPAM_KEYWORD_AUTO_BLOCK', False)

    async def scenario():
        manager = DatabaseManager(str(tmp_path / 'spam_seed.db'))
        await manager.initialize()
        try:
            settings = await db.get_spam_keyword_filter_settings()
            assert settings['enabled'] is True
            assert settings['auto_block'] is False
        finally:
            await manager.close_all()

    asyncio.run(scenario())


def test_permanent_ban_blocks_self_unblock_and_no_ghost_user(tmp_path):
    async def scenario():
        manager = DatabaseManager(str(tmp_path / 'blacklist.db'))
        await manager.initialize()
        blacklist.pending_unblocks.clear()
        try:
            await db.add_user(user_id=2001, username='u2001', first_name='U2001')
            await db.add_to_blacklist(2001, reason='admin perm', blocked_by=10001, permanent=True)

            msg1, kb1 = await blacklist.start_unblock_process(2001)
            assert kb1 is None
            assert '永久' in msg1

            msg2, ok2 = await blacklist.verify_unblock_answer(2001, 'anything')
            assert ok2 is False
            assert '永久' in msg2

            await blacklist.unblock_user(999999)
            assert await db.get_user(999999) is None
        finally:
            blacklist.pending_unblocks.clear()
            await manager.close_all()

    asyncio.run(scenario())


def test_rate_limiter_reads_config_limit_dynamically(monkeypatch):
    async def scenario():
        rl = RateLimiter()
        monkeypatch.setattr(config, 'MAX_MESSAGES_PER_MINUTE', 2)
        assert await rl.check_user_rate_limit(1234) == (False, False)
        assert await rl.check_user_rate_limit(1234) == (False, False)
        assert await rl.check_user_rate_limit(1234) == (True, False)

        monkeypatch.setattr(config, 'MAX_MESSAGES_PER_MINUTE', 5)
        assert await rl.check_user_rate_limit(1234) == (False, False)

    asyncio.run(scenario())


def test_verification_concurrency_lock(tmp_path, monkeypatch):
    async def fake_new_challenge(mode):
        await asyncio.sleep(0.02)
        return {'question': '1+1=?', 'correct_answer': '2', 'options': ['1', '2', '3', '4']}

    monkeypatch.setattr(verification, '_new_challenge', fake_new_challenge)
    monkeypatch.setattr(config, 'VERIFICATION_MODE', 'text')
    monkeypatch.setattr(config, 'VERIFICATION_TIMEOUT', 300)
    monkeypatch.setattr(config, 'MAX_VERIFICATION_ATTEMPTS', 3)

    async def scenario():
        manager = DatabaseManager(str(tmp_path / 'verify_lock.db'))
        await manager.initialize()
        verification.pending_verifications.clear()
        try:
            await db.add_user(user_id=3001, username='vuser', first_name='V')
            await db.update_user_verification(3001, False)

            await verification.create_verification(3001)
            res_ok = await asyncio.gather(
                verification.verify_answer(3001, '2'),
                verification.verify_answer(3001, '2'),
            )
            assert sorted(item[0] for item in res_ok) == [False, True]

            await db.update_user_verification(3001, False)
            await verification.create_verification(3001)
            res_wrong = await asyncio.gather(
                verification.verify_answer(3001, 'WRONG'),
                verification.verify_answer(3001, 'WRONG'),
            )
            assert [item[0] for item in res_wrong] == [False, False]
            assert verification.pending_verifications[3001]['attempts'] == 2
        finally:
            verification.pending_verifications.clear()
            await manager.close_all()

    asyncio.run(scenario())


def test_thread_manager_serializes_concurrent_creation(tmp_path, monkeypatch):
    monkeypatch.setattr(config, 'FORUM_GROUP_ID', -10077777)

    class StubThreadBot:
        def __init__(self):
            self.create_calls = 0

        async def create_forum_topic(self, **kwargs):
            self.create_calls += 1
            await asyncio.sleep(0.02)
            return SimpleNamespace(message_thread_id=88001)

        async def send_message(self, **kwargs):
            return SimpleNamespace(message_id=900)

    async def scenario():
        manager = DatabaseManager(str(tmp_path / 'thread_lock.db'))
        await manager.initialize()
        try:
            await db.add_user(user_id=4001, username='tu', first_name='TU')
            bot = StubThreadBot()
            user = SimpleNamespace(id=4001, username='tu', first_name='TU', last_name=None, full_name='TU')
            update = SimpleNamespace(effective_user=user)
            context = SimpleNamespace(bot=bot)

            r1, r2 = await asyncio.gather(
                thread_manager.get_or_create_thread(update, context),
                thread_manager.get_or_create_thread(update, context),
            )
            assert bot.create_calls == 1
            assert r1[0] == 88001 and r2[0] == 88001
            assert {r1[1], r2[1]} == {True, False}
        finally:
            await manager.close_all()

    asyncio.run(scenario())


def test_web_monitor_items_stock_phrases_and_status_prefix(tmp_path, monkeypatch):
    items = web_monitor._extract_items(
        '<html><body><article><h2>Same</h2></article><article><h2>Same</h2></article></body></html>',
        {'item_selector': 'article', 'title_selector': 'h2', 'link_selector': 'a', 'url': 'https://example.com'},
    )
    assert len(items) == 2
    assert len({item.key for item in items}) == 2

    stock = web_monitor.detect_stock(
        '<div>当前规格没有货，请稍后再试</div>',
        {'product_keyword': '', 'in_stock_words': '', 'out_of_stock_words': ''},
    )
    assert stock.state == 'out'

    async def scenario():
        manager = DatabaseManager(str(tmp_path / 'web_mon.db'))
        await manager.initialize()
        try:
            mid = await db.create_web_monitor(
                name='特价机',
                url='https://example.com/item',
                keywords=[],
                created_by=10001,
                monitor_type='stock',
            )
            monitor = await db.get_web_monitor(mid)

            async def fake_fetch(url):
                return '<html><body><div>Welcome to shop</div></body></html>'

            monkeypatch.setattr(web_monitor, '_fetch_html', fake_fetch)
            app = SimpleNamespace(bot=SimpleNamespace())
            sent = await web_monitor.run_monitor(app, monitor)
            assert sent == 0
            statuses = await db.get_runtime_statuses()
            stock_row = next(row for row in statuses if row['name'] == 'stock:特价机')
            assert stock_row['consecutive_failures'] == 1
        finally:
            await manager.close_all()

    asyncio.run(scenario())


def test_tg_monitor_multi_rule_and_allow_send_lock(tmp_path, monkeypatch):
    monkeypatch.setattr(config, 'ADMIN_IDS', [10001])
    sent_messages = []

    class StubPushBot:
        async def send_message(self, chat_id, text, **kwargs):
            sent_messages.append((chat_id, text))
            return SimpleNamespace(message_id=1)

    async def scenario():
        manager = DatabaseManager(str(tmp_path / 'tg_mon.db'))
        await manager.initialize()
        try:
            m1 = await db.create_tg_group_monitor(
                name='规则1', chat_id=-100999, keywords=['vps'], created_by=10001, listen_source='bot',
            )
            m2 = await db.create_tg_group_monitor(
                name='规则2', chat_id=-100999, keywords=[], created_by=10001, listen_source='bot',
            )

            msg = _FakeReplyMessage(-100999, 7001, text='cheap vps promo', chat_type='supergroup')
            update = SimpleNamespace(effective_message=msg, message=msg, channel_post=None)
            bot = StubPushBot()
            ctx = SimpleNamespace(bot=bot, application=SimpleNamespace(bot=bot))
            hit = await tg_monitor.handle_bot_group_message(update, ctx)
            assert hit is True
            assert len(sent_messages) == 2

            future_ts = time.time() + 120.0
            r_a1, r_a2 = await asyncio.gather(
                db.tg_monitor_allow_send(m1, 'fp1', min_interval_seconds=60, dedupe_window_seconds=300, now_ts=future_ts),
                db.tg_monitor_allow_send(m1, 'fp2', min_interval_seconds=60, dedupe_window_seconds=300, now_ts=future_ts + 1.0),
            )
            assert sorted([r_a1[0], r_a2[0]]) == [False, True]
            ok_b, _ = await db.tg_monitor_allow_send(
                m2, 'fp3', min_interval_seconds=60, dedupe_window_seconds=300, now_ts=future_ts + 1.0,
            )
            assert ok_b is True
        finally:
            await manager.close_all()

    asyncio.run(scenario())


def test_rss_ssrf_error_status_and_enabled_toggle(tmp_path, monkeypatch):
    old_cb = rss_settings._enabled_changed_callback
    old_enabled = rss_settings.is_enabled()

    class FakeJobQueue:
        def __init__(self):
            self.jobs = []

        def get_jobs_by_name(self, name):
            return [j for j in self.jobs if not j.removed]

        def run_repeating(self, cb, interval, first, name):
            job = SimpleNamespace(name=name, removed=False)
            job.schedule_removal = lambda: setattr(job, 'removed', True)
            self.jobs.append(job)
            return job

    fake_app = SimpleNamespace(
        job_queue=FakeJobQueue(),
        bot_data={},
        add_handler=lambda *a, **kw: None,
    )
    monkeypatch.setattr(rss.data_manager, 'load_subscriptions', lambda *a, **kw: {})
    rss_settings.set_enabled(False)
    rss.setup(fake_app)
    try:
        rss_settings.set_enabled(True)
        assert len(fake_app.job_queue.get_jobs_by_name('rss_feed_checker')) == 1
        rss_settings.set_enabled(False)
        assert len(fake_app.job_queue.get_jobs_by_name('rss_feed_checker')) == 0
    finally:
        rss_settings.set_enabled(old_enabled)
        rss_settings.set_enabled_callback(old_cb)

    async def scenario():
        manager = DatabaseManager(str(tmp_path / 'rss_ssrf.db'))
        await manager.initialize()
        try:
            data_file = str(tmp_path / 'rss.json')
            cfg = {'title': 'Local', 'keywords': [], 'last_entry_id': 'prev-1'}
            rss.data_manager.subscriptions_data.clear()
            rss.data_manager.subscriptions_data['10001'] = {'https://127.0.0.1/feed.xml': cfg}
            rss.data_manager.save_subscriptions(data_file)
            ctx = SimpleNamespace(bot=SimpleNamespace(), job_queue=fake_app.job_queue)

            count = await rss_checker.check_single_feed(ctx, '10001', 'https://127.0.0.1/feed.xml', cfg, data_file)
            assert count == 0
            assert cfg['last_entry_id'] == 'prev-1'
            statuses = await db.get_runtime_statuses()
            row = next(item for item in statuses if item['name'].startswith('rss:'))
            assert row['consecutive_failures'] >= 1
        finally:
            rss.data_manager.subscriptions_data.clear()
            await manager.close_all()

    asyncio.run(scenario())


def test_network_test_cancel_and_nexttrace_flags_and_escape(monkeypatch):
    monkeypatch.setattr(config, 'ADMIN_IDS', [10001])

    async def scenario():
        nt_state.user_data[10001] = {'step': 2, 'servers': {}}
        try:
            msg = _FakeReplyMessage(10001, 10001, text='/cancel', chat_type='private')
            update = SimpleNamespace(effective_user=msg.from_user, effective_chat=msg.chat, message=msg)
            ctx = SimpleNamespace(application=SimpleNamespace(create_task=lambda coro: coro.close()))
            await nt_commands.cancel_command(update, ctx)
            assert 10001 not in nt_state.user_data
            assert len(msg.replies) == 1
        finally:
            nt_state.user_data.pop(10001, None)

    asyncio.run(scenario())

    captured_cmds = []

    def fake_ssh(server_info, cmd, cmd_timeout=20):
        captured_cmds.append(cmd)
        return (
            'traceroute to 8.8.8.8, UDP mode\n'
            '1   192.168.1.1    1.2 ms  AS100  RFC1918\n'
            '2   8.8.8.8        9.5 ms  AS15169 Google <script>alert(1)</script>\n',
            '',
            0,
        )

    monkeypatch.setattr(nt_network, '_exec_ssh_command', fake_ssh)
    raw = nt_network.nexttrace_on_server({'name': 'HK-1'}, '8.8.8.8', ip_type='IPv4', trace_mode='UDP')
    assert captured_cmds and '-4' in captured_cmds[0] and '--udp' in captured_cmds[0]
    html = nt_network.format_nexttrace_result(raw, 'HK-1', '8.8.8.8', ip_type='IPv4', trace_mode='UDP')
    assert '192.168.1.1' not in html
    assert 'x.x.x.x' in html
    assert '&lt;script&gt;alert(1)&lt;/script&gt;' in html
    assert '<script>' not in html
    html_v6 = nt_network.format_nexttrace_result(
        'traceroute to 2001:4860:4860::8888, ICMP mode\n1   2001:db8::1   1.0 ms  AS100\n',
        'HK-1', '2001:4860:4860::8888', ip_type='ipv6', trace_mode='ICMP',
    )
    assert '2001:db8::1' not in html_v6
    assert 'x:x:x:x' in html_v6


def test_web_panel_xff_miniapp_replay_and_media_proxy(tmp_path, monkeypatch):
    monkeypatch.setattr(config, 'WEB_PANEL_TRUST_PROXY', False)
    req_direct = SimpleNamespace(remote='127.0.0.1', headers={'X-Forwarded-For': '203.0.113.5'})
    assert auth.client_ip(req_direct) == '127.0.0.1'

    monkeypatch.setattr(config, 'WEB_PANEL_TRUST_PROXY', True)
    assert auth.client_ip(req_direct) == '203.0.113.5'
    req_leftmost_spoof = SimpleNamespace(
        remote='127.0.0.1',
        headers={'X-Forwarded-For': '198.51.100.9, 203.0.113.5'},
    )
    assert auth.client_ip(req_leftmost_spoof) == '203.0.113.5'

    miniapp._used_admin_init_hashes.clear()
    init_sample = 'auth_date=1700000000&hash=deadbeefcafebabe'
    assert miniapp._claim_admin_init_hash(init_sample) is True
    assert miniapp._claim_admin_init_hash(init_sample) is False
    miniapp._used_admin_init_hashes.clear()

    class StubUpstream:
        status = 200
        headers = {'Content-Type': 'image/jpeg', 'Content-Length': '8'}

        class _Content:
            async def iter_chunked(self, size):
                yield b'test-img'

        content = _Content()
        released = False

        def release(self):
            self.released = True

    class StubMediaBot:
        token = '123:ABC'

        async def get_file(self, file_id):
            if file_id == 'bad_missing_id':
                raise TelegramError('wrong file_id')
            return SimpleNamespace(file_path='photos/test.jpg')

    class StubHttpSession:
        def __init__(self):
            self.upstream = StubUpstream()

        async def get(self, url, **kwargs):
            return self.upstream

    async def scenario():
        manager = DatabaseManager(str(tmp_path / 'media_proxy.db'))
        await manager.initialize()
        monkeypatch.setattr(config, 'BOT_TOKEN', '123:ABC')
        monkeypatch.setattr(config, 'WEB_PANEL_PASSWORD', 'secret-pass-1')
        monkeypatch.setattr(config, 'WEB_PANEL_PASSWORD_SHA256', '')
        auth._sessions.clear()
        auth._login_failures.clear()

        http_session = StubHttpSession()
        panel_app = create_app(StubMediaBot(), http_session)
        client = TestClient(TestServer(panel_app))
        await client.start_server()
        try:
            r_unauth = await client.get('/api/media/valid_file_123')
            assert r_unauth.status == 401

            login_resp = await client.post('/api/login', json={'password': 'secret-pass-1'})
            assert login_resp.status == 200

            r_bad = await client.get('/api/media/bad_missing_id')
            assert r_bad.status == 404

            r_ok = await client.get('/api/media/valid_file_123')
            assert r_ok.status == 200
            assert r_ok.headers['Content-Type'].startswith('image/jpeg')
            assert await r_ok.read() == b'test-img'
            assert http_session.upstream.released is True
        finally:
            await client.close()
            auth._sessions.clear()
            auth._login_failures.clear()
            await manager.close_all()

    asyncio.run(scenario())
