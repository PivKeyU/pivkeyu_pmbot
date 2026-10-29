"""用户处置：撤销验证、AI 自动撤销、误杀申诉 / 暂停审查、会话已读、AI 生成参数与静态资源版本号。"""

import asyncio
import json
from types import SimpleNamespace

import aiohttp
import pytest
from aiohttp.test_utils import TestClient, TestServer

from config import config
from database import models as db
from database.db_manager import DatabaseManager
from services import ai_options, ai_service, moderation
from web_panel import auth
from web_panel.server import create_app

PANEL_HEADERS = {'X-Requested-With': 'PMBotPanel'}


class RecordingBot:
    """记录发出去的消息；ForceReply / 话题提醒都走 send_message。"""

    id = 424242
    username = 'stub_bot'
    token = '123456:STUB'
    base_url = 'https://api.telegram.org/bot'

    def __init__(self):
        self.sent = []
        self.edited = []
        self._next_id = 5000

    async def send_message(self, **kwargs):
        self._next_id += 1
        self.sent.append(kwargs)
        return SimpleNamespace(message_id=self._next_id, chat_id=kwargs.get('chat_id'))

    async def edit_message_text(self, **kwargs):
        self.edited.append(kwargs)
        return True

    async def create_forum_topic(self, **kwargs):
        return SimpleNamespace(message_thread_id=77)


@pytest.mark.parametrize('text,minutes', [
    ('90', 90), ('45m', 45), ('3h', 180), ('1h30m', 90), ('2d', 2880), ('2天', 2880),
    ('1.5小时', 90), ('1w', 10080), ('3 h', 180),
])
def test_parse_duration(text, minutes):
    assert moderation.parse_duration(text) == minutes


@pytest.mark.parametrize('text', ['', 'abc', '0', '3x', 'h3', '-5'])
def test_parse_duration_rejects(text):
    assert moderation.parse_duration(text) is None


def test_format_duration():
    assert moderation.format_duration(None) == '永久'
    assert moderation.format_duration(90) == '1 小时 30 分钟'
    assert moderation.format_duration(1440 * 2) == '2 天'


@pytest.mark.parametrize('base_url,effort,kwargs,extra', [
    ('https://api.deepseek.com/v1', 'off', {}, {'thinking': {'type': 'disabled'}}),
    ('https://dashscope.aliyuncs.com/compatible-mode/v1', 'off', {}, {'enable_thinking': False}),
    ('https://openrouter.ai/api/v1', 'low', {}, {'reasoning': {'effort': 'low'}}),
    (None, 'off', {'reasoning_effort': 'minimal'}, {}),
    ('https://generativelanguage.googleapis.com/v1beta/openai/', 'off', {'reasoning_effort': 'none'}, {}),
    ('https://api.deepseek.com/v1', 'high', {'reasoning_effort': 'high'}, {}),
    ('https://api.deepseek.com/v1', '', {}, {}),
])
def test_openai_effort_translation(base_url, effort, kwargs, extra):
    assert ai_service.openai_effort_params(base_url, effort) == (kwargs, extra)


def test_extra_body_must_be_json_object():
    assert ai_options.parse_extra_body('') is None
    assert ai_options.parse_extra_body('{"a": 1}') == {'a': 1}
    for bad in ('[1]', '{oops', '"text"'):
        with pytest.raises(ValueError):
            ai_options.parse_extra_body(bad)


class FakeCompletions:
    def __init__(self):
        self.calls = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        message = SimpleNamespace(content='{"is_spam": false, "reason": "内容未发现违规。"}')
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])


async def _openai_options_flow(tmp_path):
    manager = DatabaseManager(str(tmp_path / 'opts.db'))
    await manager.initialize()
    try:
        await ai_options.update_options({
            'features': {'filter': {'effort': 'off', 'max_tokens': '256', 'timeout': '8'}},
            'max_input_chars': '100',
            'extra_body': {'openai': '{"temperature": 0.1}'},
            'prompts': {'review': '只拦广告，其他都放行。'},
        })
        provider = ai_service.OpenAIProvider('sk-test', 'https://api.deepseek.com/v1')
        completions = FakeCompletions()
        provider.client = SimpleNamespace(chat=SimpleNamespace(completions=completions))

        result = await provider.analyze_message('长' * 500)
        assert result['is_spam'] is False
        call = completions.calls[-1]
        assert call['max_tokens'] == 256
        assert call['extra_body'] == {'thinking': {'type': 'disabled'}, 'temperature': 0.1}
        assert call['response_format'] == {'type': 'json_object'}
        system = call['messages'][0]['content']
        assert system.startswith('只拦广告，其他都放行。')
        assert ai_service.REVIEW_OUTPUT_FORMAT in system
        # 用户消息按上限截断
        assert len(call['messages'][1]['content']) < 130

        # 非法参数整体拒绝，不会写入一半
        with pytest.raises(ValueError):
            await ai_options.update_options({'features': {'filter': {'effort': 'turbo'}}})
        with pytest.raises(ValueError):
            await ai_options.update_options({'extra_body': {'openai': 'not json'}})
        assert (await ai_options.get_generation_options('filter', 'openai')).effort == 'off'

        # 超时：审查直接放行并标记失败，不会卡住用户
        async def slow_complete(request):
            await asyncio.sleep(5)
        provider.complete = slow_complete
        await ai_options.update_options({'features': {'filter': {'timeout': '3'}}})
        options = await ai_options.get_generation_options('filter', 'openai')
        options.timeout = 0.05
        original = ai_options.get_generation_options

        async def fast_timeout(feature, provider_type):
            return options
        ai_options.get_generation_options = fast_timeout
        try:
            result = await provider.analyze_message('hello')
        finally:
            ai_options.get_generation_options = original
        assert result['analysis_failed'] is True and '超时' in result['reason']
    finally:
        await manager.close_all()


def test_generation_options_reach_the_request(tmp_path):
    asyncio.run(_openai_options_flow(tmp_path))


async def _moderation_flow(tmp_path):
    manager = DatabaseManager(str(tmp_path / 'mod.db'))
    await manager.initialize()
    bot = RecordingBot()
    application = SimpleNamespace(bot_data={})
    try:
        await db.add_user(900, 'spammer', 'Spammer', None)
        await db.update_user_verification(900, True)
        await db.update_user_thread_id(900, 31)

        # AI 自动撤销：默认关闭
        await db.save_filtered_message(900, 1, '广告', 'AI: 广告')
        await db.save_filtered_message(900, 2, '广告', 'AI: 广告')
        assert await moderation.maybe_auto_revoke(bot, 900) is False
        await ai_options.update_options({'switches': {'auto_revoke_enabled': True, 'auto_revoke_threshold': '3'}})
        assert await moderation.maybe_auto_revoke(bot, 900) is False  # 只有 2 次
        # 关键词拦截不计入
        await db.save_filtered_message(900, 3, 'x', '关键词广告拦截命中: 加群')
        assert await moderation.maybe_auto_revoke(bot, 900) is False
        await db.save_filtered_message(900, 4, '广告', 'AI: 广告')
        assert await moderation.maybe_auto_revoke(bot, 900) is True
        assert not (await db.get_user(900))['is_verified']
        assert bot.sent[-1]['message_thread_id'] == 31

        # 申诉：提交 → 话题收到带按钮的申请 → 冷却期内不能重复提交
        await moderation.restore_verification(900)
        ok, _ = await moderation.create_appeal(bot, 900, lambda user: None)
        assert ok
        appeal = await db.get_pending_review_appeal(900)
        topic_message = bot.sent[-1]
        assert topic_message['message_thread_id'] == 31
        buttons = [b.callback_data for row in topic_message['reply_markup'].inline_keyboard for b in row]
        assert f"appeal_ok:{appeal['id']}:30" in buttons and f"appeal_no:{appeal['id']}" in buttons
        ok, notice = await moderation.create_appeal(bot, 900, lambda user: None)
        assert not ok and '已经' in notice

        # 管理员选「自定义时长」并回复 3h
        config_admins = config.ADMIN_IDS
        config.ADMIN_IDS = [10001]
        try:
            await moderation.ask_custom_duration(bot, application, appeal)
            prompt_id = next(iter(application.bot_data[moderation.CUSTOM_PROMPTS_KEY]))
            replies = []

            async def reply_text(text):
                replies.append(text)
            admin_reply = SimpleNamespace(
                text='3h', reply_to_message=SimpleNamespace(message_id=prompt_id),
                from_user=SimpleNamespace(id=10001), reply_text=reply_text,
            )
            assert await moderation.handle_custom_duration_reply(admin_reply, bot, application) is True
        finally:
            config.ADMIN_IDS = config_admins
        assert await db.is_exempted(900)
        assert (await db.get_review_appeal(appeal['id']))['status'] == 'approved'
        assert any('3 小时' in m['text'] for m in bot.sent if m['chat_id'] == 900)
        # 话题里那条申请消息被改成处理结果、按钮收起
        assert bot.edited and bot.edited[-1]['reply_markup'] is None

        # 普通话题回复不受影响
        normal = SimpleNamespace(reply_to_message=SimpleNamespace(message_id=1), from_user=SimpleNamespace(id=10001))
        assert await moderation.handle_custom_duration_reply(normal, bot, application) is False
    finally:
        await manager.close_all()


def test_moderation_flow(tmp_path):
    asyncio.run(_moderation_flow(tmp_path))


async def _panel_flow(tmp_path):
    manager = DatabaseManager(str(tmp_path / 'panel_mod.db'))
    await manager.initialize()
    original_password = config.WEB_PANEL_PASSWORD
    original_sha = config.WEB_PANEL_PASSWORD_SHA256
    config.WEB_PANEL_PASSWORD = 'test-pass'
    config.WEB_PANEL_PASSWORD_SHA256 = ''
    auth._sessions.clear()
    auth._login_failures.clear()

    bot = RecordingBot()
    http_session = aiohttp.ClientSession()
    client = TestClient(TestServer(create_app(bot, http_session)))
    await client.start_server()
    try:
        # 静态资源带版本号，带版本号的地址长期缓存
        response = await client.get('/')
        html = await response.text()
        assert '/static/v-' in html and '"/static/css' not in html
        css_path = next(part.split('"')[0] for part in html.split('"') if part.startswith('/static/v-') and part.endswith('.css'))
        response = await client.get(css_path)
        assert response.status == 200
        assert 'immutable' in response.headers['Cache-Control']

        assert (await client.post('/api/login', json={'password': 'test-pass'})).status == 200

        await db.add_user(901, 'guest', 'Guest', None)
        await db.update_user_verification(901, True)
        await db.save_message(user_id=901, message_id=1, content='在吗', direction='user_to_admin')

        response = await client.get('/api/conversations')
        item = (await response.json())['data']['items'][0]
        assert item['is_unread'] == 1

        # 标为已读
        assert (await client.post('/api/conversations/901/read', headers=PANEL_HEADERS)).status == 200
        item = (await (await client.get('/api/conversations')).json())['data']['items'][0]
        assert item['is_unread'] == 0
        # 新消息进来又变成未读，全部已读再清掉
        await asyncio.sleep(1.1)
        await db.save_message(user_id=901, message_id=2, content='还在吗', direction='user_to_admin')
        item = (await (await client.get('/api/conversations')).json())['data']['items'][0]
        assert item['is_unread'] == 1
        assert (await client.post('/api/conversations/read-all', headers=PANEL_HEADERS)).status == 200
        item = (await (await client.get('/api/conversations')).json())['data']['items'][0]
        assert item['is_unread'] == 0

        # 撤销验证后会话仍然可见，可以恢复
        assert (await client.post('/api/conversations/901/unverify', headers=PANEL_HEADERS)).status == 200
        assert not (await db.get_user(901))['is_verified']
        detail = (await (await client.get('/api/conversations/901')).json())['data']
        assert detail['user']['is_verified'] is False
        assert (await client.post('/api/conversations/901/verify', headers=PANEL_HEADERS)).status == 200
        assert (await db.get_user(901))['is_verified']

        # 网页暂停审查
        response = await client.post('/api/conversations/901/review-pause', json={'minutes': '2h'}, headers=PANEL_HEADERS)
        body = await response.json()
        assert response.status == 200, body
        assert body['data']['duration_text'] == '2 小时'
        assert await db.is_exempted(901)
        response = await client.post('/api/conversations/901/review-pause', json={'minutes': 'abc'}, headers=PANEL_HEADERS)
        assert response.status == 400
        assert (await client.delete('/api/conversations/901/review-pause', headers=PANEL_HEADERS)).status == 200
        assert not await db.is_exempted(901)

        # 没有申请时拒绝会报错；有申请时拒绝成功
        assert (await client.post('/api/conversations/901/appeal/reject', headers=PANEL_HEADERS)).status == 400
        await db.create_review_appeal(901, 'AI: 广告')
        item = (await (await client.get('/api/conversations')).json())['data']['items'][0]
        assert item['pending_appeal'] == 1
        assert (await client.post('/api/conversations/901/appeal/reject', headers=PANEL_HEADERS)).status == 200

        # AI 参数接口
        response = await client.put('/api/ai-options', json={'features': {'filter': {'effort': 'off'}}}, headers=PANEL_HEADERS)
        data = (await response.json())['data']
        assert data['features']['filter']['effort'] == 'off'
        assert 'is_spam' in data['fixed_suffix']['review']
        response = await client.put('/api/ai-options', json={'extra_body': {'openai': '[1]'}}, headers=PANEL_HEADERS)
        assert response.status == 400
    finally:
        await client.close()
        await http_session.close()
        config.WEB_PANEL_PASSWORD = original_password
        config.WEB_PANEL_PASSWORD_SHA256 = original_sha
        auth._sessions.clear()
        await manager.close_all()


def test_panel_moderation_endpoints(tmp_path):
    asyncio.run(_panel_flow(tmp_path))
