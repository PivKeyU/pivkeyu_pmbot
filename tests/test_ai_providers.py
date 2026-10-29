"""AI 提供商：注册表一致性、Anthropic 协议真实请求形状、Web 面板 AI 接口。

Anthropic 部分用真实的 anthropic SDK 打到本地假服务器，验证 base_url、
图片内容块、system 字段、JSON 解析、拒答处理与模型列表都按协议走。
"""

import asyncio
import json

import aiohttp
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

import config_meta
from config import config
from database.db_manager import DatabaseManager
from services import ai_service
from services.ai_providers import AI_FEATURES, PROVIDERS, all_model_setting_keys
from web_panel import api_config, auth
from web_panel.server import create_app

PANEL_HEADERS = {'X-Requested-With': 'PMBotPanel'}
PNG_BYTES = b'\x89PNG\r\n\x1a\n' + b'\x00' * 16


def test_registry_is_consistent():
    assert set(PROVIDERS) == set(ai_service.PROVIDER_CLASSES)
    for provider_type, meta in PROVIDERS.items():
        # 密钥 / 地址必须能在配置中心编辑，且修改后会重建客户端
        assert meta['key_env'] in config_meta.META_BY_KEY, provider_type
        assert meta['base_env'] in config_meta.META_BY_KEY, provider_type
        assert config_meta.META_BY_KEY[meta['key_env']].get('secret') is True
        assert {meta['key_env'], meta['base_env']} <= api_config.AI_CLIENT_KEYS
        assert hasattr(config, meta['key_env']) and hasattr(config, meta['base_env'])
        assert set(meta['default_models']) == set(AI_FEATURES)
        assert meta['short'] and meta['presets']
    assert len(all_model_setting_keys()) == len(PROVIDERS) * len(AI_FEATURES)


def test_parse_json_response_tolerates_wrapping():
    assert ai_service._parse_json_response('```json\n{"a": 1}\n```') == {'a': 1}
    assert ai_service._parse_json_response('结果如下：{"is_spam": false, "reason": "ok"} 以上') == {
        'is_spam': False, 'reason': 'ok',
    }


def test_sniff_image_media_type():
    assert ai_service._sniff_image_media_type(PNG_BYTES) == 'image/png'
    assert ai_service._sniff_image_media_type(b'GIF89a....') == 'image/gif'
    assert ai_service._sniff_image_media_type(b'RIFF\x00\x00\x00\x00WEBPVP8 ') == 'image/webp'
    assert ai_service._sniff_image_media_type(b'\xff\xd8\xff\xe0') == 'image/jpeg'


class FakeAnthropicServer:
    """最小化的 Anthropic Messages API 假服务，按队列返回预设文本。"""

    def __init__(self):
        self.requests = []
        self.replies = []

    def make_app(self):
        app = web.Application()
        app.router.add_post('/proxy/v1/messages', self.messages)
        app.router.add_get('/proxy/v1/models', self.models)
        return app

    async def messages(self, request):
        body = await request.json()
        self.requests.append({'headers': dict(request.headers), 'body': body})
        text, stop_reason = self.replies.pop(0)
        return web.json_response({
            'id': 'msg_test', 'type': 'message', 'role': 'assistant', 'model': body['model'],
            'content': [{'type': 'text', 'text': text}],
            'stop_reason': stop_reason, 'stop_sequence': None,
            'usage': {'input_tokens': 1, 'output_tokens': 1},
        })

    async def models(self, request):
        return web.json_response({
            'data': [
                {'type': 'model', 'id': 'claude-opus-5-5', 'display_name': 'Claude Opus 5.5', 'created_at': '2026-01-01T00:00:00Z'},
                {'type': 'model', 'id': 'claude-sonnet-5-5', 'display_name': 'Claude Sonnet 5.5', 'created_at': '2026-01-01T00:00:00Z'},
            ],
            'has_more': False, 'first_id': 'claude-opus-5-5', 'last_id': 'claude-sonnet-5-5',
        })


async def _anthropic_flow(tmp_path):
    db_manager = DatabaseManager(str(tmp_path / 'ai_test.db'))
    await db_manager.initialize()

    fake = FakeAnthropicServer()
    server = TestServer(fake.make_app())
    await server.start_server()
    base_url = str(server.make_url('/proxy'))
    provider = ai_service.AnthropicProvider('sk-ant-test', base_url)
    try:
        # 内容审查：图片 + 文本，system 里是审查规则，JSON 外面包了一层也能解析
        fake.replies.append(('```json\n{"is_spam": true, "reason": "广告"}\n```', 'end_turn'))
        result = await provider.analyze_message('加我微信领红包', PNG_BYTES)
        assert result == {'is_spam': True, 'reason': '广告'}
        sent = fake.requests[-1]
        # 新版 SDK 用 Authorization: Bearer，旧版用 x-api-key，两种都算带上了密钥
        headers = {k.lower(): v for k, v in sent['headers'].items()}
        assert 'sk-ant-test' in (headers.get('x-api-key', ''), headers.get('authorization', '').removeprefix('Bearer '))
        body = sent['body']
        assert body['model'] == 'claude-opus-5-5'
        assert body['system'] == ai_service.CONTENT_REVIEW_SYSTEM_PROMPT
        content = body['messages'][0]['content']
        assert content[0]['type'] == 'image'
        assert content[0]['source']['media_type'] == 'image/png'
        assert content[1] == {'type': 'text', 'text': '加我微信领红包'}

        # 拒答：标记为审查失败，而不是当成安全放行的正常结果
        fake.replies.append(('', 'refusal'))
        result = await provider.analyze_message('test')
        assert result['analysis_failed'] is True

        # 出题：JSON 解析成带选项的验证题
        fake.replies.append((json.dumps({
            'question': '一周有几天？', 'correct_answer': '7', 'incorrect_answers': ['5', '6', '8'],
        }, ensure_ascii=False), 'end_turn'))
        challenge = await provider.generate_verification_challenge()
        assert challenge['correct_answer'] == '7'
        assert sorted(challenge['options']) == ['5', '6', '7', '8']

        # 出题失败时回落到本地题库
        fake.replies.append(('不是 JSON', 'end_turn'))
        challenge = await provider.generate_verification_challenge()
        assert challenge['correct_answer'] in challenge['options']

        # 自动回复：知识库进 system，客人消息是 user 轮
        fake.replies.append(('营业时间是早九晚六哦～', 'end_turn'))
        reply = await provider.generate_autoreply('几点营业？', '营业时间：9:00-18:00')
        assert reply == '营业时间是早九晚六哦～'
        body = fake.requests[-1]['body']
        assert '营业时间：9:00-18:00' in body['system']
        assert body['messages'] == [{'role': 'user', 'content': '几点营业？'}]

        # 知识库答不上来：原样返回兜底句
        fake.replies.append((f'唔……{ai_service.KNOWLEDGE_MISS_REPLY}', 'end_turn'))
        assert await provider.generate_autoreply('你是谁', '营业时间') == ai_service.KNOWLEDGE_MISS_REPLY

        # 模型列表与连通性测试
        assert await provider.get_models() == ['claude-opus-5-5', 'claude-sonnet-5-5']
        fake.replies.append(('OK', 'end_turn'))
        assert await provider.ping('claude-opus-5-5') == 'OK'
    finally:
        await provider.client.close()
        await server.close()
        await db_manager.close_all()


def test_anthropic_provider_speaks_messages_protocol(tmp_path):
    asyncio.run(_anthropic_flow(tmp_path))


class StubBot:
    id = 424242
    username = 'stub_bot'
    token = '123456:STUB'
    base_url = 'https://api.telegram.org/bot'


async def _panel_flow(tmp_path):
    db_manager = DatabaseManager(str(tmp_path / 'ai_panel.db'))
    await db_manager.initialize()

    saved = {key: getattr(config, key) for key in (
        'WEB_PANEL_PASSWORD', 'WEB_PANEL_PASSWORD_SHA256', 'ANTHROPIC_API_KEY', 'ANTHROPIC_BASE_URL',
    )}
    config.WEB_PANEL_PASSWORD = 'test-pass'
    config.WEB_PANEL_PASSWORD_SHA256 = ''
    config.ANTHROPIC_API_KEY = None
    auth._sessions.clear()
    auth._login_failures.clear()

    http_session = aiohttp.ClientSession()
    client = TestClient(TestServer(create_app(StubBot(), http_session)))
    await client.start_server()
    try:
        response = await client.post('/api/login', json={'password': 'test-pass'})
        assert response.status == 200

        response = await client.get('/api/ai-settings')
        data = (await response.json())['data']
        assert [p['id'] for p in data['providers']] == list(PROVIDERS)
        claude = next(p for p in data['providers'] if p['id'] == 'anthropic')
        assert claude['configured'] is False
        assert data['models']['anthropic_model_autoreply'] == 'claude-opus-5-5'

        # 未配置 Key 时测试连接直接给出提示
        response = await client.post('/api/ai-test', json={'provider': 'anthropic'}, headers=PANEL_HEADERS)
        assert response.status == 400
        assert 'API Key' in (await response.json())['error']

        # 未知协议被拒绝
        response = await client.put('/api/ai-settings', json={'provider': 'nope'}, headers=PANEL_HEADERS)
        assert response.status == 400

        # 通过配置中心写入 Key 后：AI 设置里只看得到掩码，并可切换为当前提供商
        response = await client.put(
            '/api/config/ANTHROPIC_API_KEY', json={'value': 'sk-ant-secret-123456'}, headers=PANEL_HEADERS,
        )
        assert response.status == 200
        response = await client.put('/api/ai-settings', json={'provider': 'anthropic'}, headers=PANEL_HEADERS)
        data = (await response.json())['data']
        assert data['provider'] == 'anthropic'
        claude = next(p for p in data['providers'] if p['id'] == 'anthropic')
        assert claude['configured'] is True
        assert 'sk-ant-secret-123456' not in json.dumps(data)
    finally:
        await client.close()
        await http_session.close()
        for key, value in saved.items():
            setattr(config, key, value)
        ai_service.close_clients()
        await db_manager.close_all()


def test_panel_ai_endpoints(tmp_path):
    asyncio.run(_panel_flow(tmp_path))
