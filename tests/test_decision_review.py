"""决策审查协议：用真实 OpenAI SDK 打本地假服务，验证各家请求形状、回答解析和回退逻辑。"""

import asyncio
import base64
from types import SimpleNamespace

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

import config_meta
from config import config
from database.db_manager import DatabaseManager
from services import ai_service, decision_review
from services.ai_providers import DECISION_PROTOCOLS, is_decision_model
from utils import copy as copy_text
from web_panel import api_admin, miniapp

PNG_BYTES = b'\x89PNG\r\n\x1a\n' + b'\x00' * 16
WEBP_BYTES = b'RIFF\x00\x00\x00\x00WEBPVP8 '


class FakeDecisionServer:
    def __init__(self):
        self.requests = []
        self.reply = {}
        self.status = 200

    def make_app(self):
        app = web.Application()
        app.router.add_post('/v1/{path:.+}', self.handle)
        return app

    async def handle(self, request):
        self.requests.append({
            'path': request.path,
            'headers': request.headers,
            'body': await request.json(),
        })
        return web.json_response(self.reply, status=self.status)


def _run_server(scenario):
    async def main():
        fake = FakeDecisionServer()
        server = TestServer(fake.make_app())
        await server.start_server()
        try:
            await scenario(fake, str(server.make_url('/v1')))
        finally:
            await server.close()

    asyncio.run(main())


INCEPTION_REPLY = {
    'model': 'mercury-decide',
    'answers': {
        'violation': {'type': 'noul', 'noul': 0.97},
        'category': {'type': 'choice', 'choice': 'scam', 'probabilities': {'scam': 0.9}, 'confidence': 0.8},
    },
    'usage': {'input_tokens': 48, 'output_tokens': 1},
}

OPENAI_REPLY = {
    'model': 'gpt-6-luna',
    'answers': [
        {'type': 'predicate', 'name': 'violation', 'probability': 0.95},
        {'type': 'choice', 'name': 'category', 'choice': 'ad',
         'probabilities': [{'value': 'ad', 'probability': 0.9}], 'confidence': 0.8},
    ],
}


def test_every_protocol_has_an_adapter_and_config_option():
    assert set(DECISION_PROTOCOLS) == set(decision_review.ADAPTERS)
    options = config_meta.META_BY_KEY['DECISION_PROTOCOL']['options']
    assert set(options) == set(DECISION_PROTOCOLS) | {'off'}


def test_no_default_address_is_built_into_the_review_path():
    # 官方地址只能作为面板的快速填入选项，运行时必须来自配置
    source = open(decision_review.__file__, encoding='utf-8').read()
    assert 'https://' not in source
    assert config_meta.META_BY_KEY['DECISION_BASE_URL']['default'] == ''
    assert config_meta.META_BY_KEY['DECISION_MODEL']['default'] == ''


def test_inception_request_and_response():
    async def scenario(fake, base_url):
        fake.reply = INCEPTION_REPLY
        client = decision_review.DecisionClient('inception', 'key-1', base_url)
        image = decision_review.DecisionImage('image/png', 'QUJD')

        result = await client.ask('mercury-decide', '禁止广告', '加我微信', image)

        assert result == decision_review.DecisionResult(0.97, 'scam')
        sent = fake.requests[-1]
        assert sent['path'] == '/v1/decisions'
        assert sent['headers']['Authorization'] == 'Bearer key-1'
        body = sent['body']
        assert body['model'] == 'mercury-decide'
        assert body['state'] == [
            {'type': 'text', 'text': '加我微信'},
            {'type': 'image_url', 'image_url': {'url': 'data:image/png;base64,QUJD'}},
        ]
        assert body['questions']['violation']['type'] == 'noul'
        assert '禁止广告' in body['questions']['violation']['instructions']
        # 用户内容只能出现在 state 里，不能和审查规则混在一起
        assert '加我微信' not in body['questions']['violation']['instructions']
        assert body['questions']['category']['type'] == 'choice'
        assert set(body['questions']['category']['criteria']) == set(copy_text.DECISION_REASONS)
        await client.client.close()

    _run_server(scenario)


def test_openai_request_and_response():
    async def scenario(fake, base_url):
        fake.reply = OPENAI_REPLY
        client = decision_review.DecisionClient('openai', 'key-2', base_url)
        image = decision_review.DecisionImage('image/webp', 'QUJD')

        result = await client.ask('gpt-6-luna', '禁止广告', '加我微信', image)

        assert result == decision_review.DecisionResult(0.95, 'ad')
        sent = fake.requests[-1]
        assert sent['path'] == '/v1/decisions'
        body = sent['body']
        assert 'state' not in body
        assert body['input'] == [{'role': 'user', 'content': [
            {'type': 'input_text', 'text': '加我微信'},
            {'type': 'input_image', 'image_url': 'data:image/webp;base64,QUJD'},
        ]}]
        violation, category = body['questions']
        assert (violation['type'], violation['name']) == ('predicate', 'violation')
        assert (category['type'], category['name']) == ('choice', 'category')
        assert {choice['value'] for choice in category['choices']} == set(copy_text.DECISION_REASONS)
        await client.client.close()

    _run_server(scenario)


def test_openai_refusal_counts_as_blocked():
    adapter = decision_review.OpenAIDecisionsAdapter()
    result = adapter.parse({'answers': [{'type': 'refusal', 'name': 'violation'}]})
    assert result.refused
    assert decision_review.to_review(result, 0.5) == {
        'is_spam': True, 'reason': copy_text.DECISION_REFUSED_REASON,
    }


def test_third_party_gateway_uses_configured_address_path_and_model():
    async def scenario(fake, base_url):
        fake.reply = INCEPTION_REPLY
        client = decision_review.DecisionClient('system_one', 'key-9', base_url, '/custom/decide')

        await client.ask('vendor/jev-1.13.0', '禁止广告', '你好')

        sent = fake.requests[-1]
        assert sent['path'] == '/v1/custom/decide'
        assert sent['headers']['Authorization'] == 'Bearer key-9'
        assert sent['body']['model'] == 'vendor/jev-1.13.0'
        await client.client.close()

    _run_server(scenario)


@pytest.mark.parametrize('wrapped', [False, True])
def test_workers_ai_request_and_response(wrapped):
    async def scenario(fake, base_url):
        fake.reply = {'result': INCEPTION_REPLY, 'success': True} if wrapped else INCEPTION_REPLY
        client = decision_review.DecisionClient('workers_ai', 'cf-token', base_url)
        image = decision_review.DecisionImage('image/webp', 'QUJD')

        result = await client.ask('clef-flash', '禁止广告', '加我微信', image)

        assert result.probability == 0.97
        sent = fake.requests[-1]
        assert sent['path'] == '/v1/ai/run/@cf/cloudflare/clef-flash'
        assert sent['headers']['Authorization'] == 'Bearer cf-token'
        body = sent['body']
        assert body['model'] == 'clef-flash'
        assert body['state'] == '加我微信'
        assert body['images'] == ['data:image/webp;base64,QUJD']

        await client.ask('@cf/cloudflare/clef', '禁止广告', '你好')
        sent = fake.requests[-1]
        assert sent['path'] == '/v1/ai/run/@cf/cloudflare/clef'
        assert sent['body']['model'] == 'clef'
        assert 'images' not in sent['body']
        await client.client.close()

    _run_server(scenario)


def test_workers_ai_rejects_unsupported_image_format():
    adapter = decision_review.WorkersAIAdapter()
    with pytest.raises(ValueError):
        adapter.body('clef', '规则', '文字', decision_review.DecisionImage('image/gif', 'QUJD'))


def test_typesafe_request_uses_systemone_and_assets():
    async def scenario(fake, base_url):
        fake.reply = {**INCEPTION_REPLY, 'model': 'jev-1.13.0'}
        client = decision_review.DecisionClient('system_one', 'key-3', base_url)
        image = decision_review.DecisionImage('image/jpeg', 'QUJD')

        result = await client.ask('jev-latest', '禁止广告', '加我微信', image)

        assert result.probability == 0.97
        sent = fake.requests[-1]
        assert sent['path'] == '/v1/systemone'
        assert sent['headers']['TypeSafe-Danger-Zone-Images'] == 'true'
        body = sent['body']
        reference = body['state']['image']
        assert body['state']['message'] == '加我微信'
        assert body['assets'][reference] == {'type': 'image', 'format': 'jpg', 'data_base64': 'QUJD'}

        await client.ask('jev-latest', '禁止广告', '纯文字')
        text_request = fake.requests[-1]
        assert text_request['body']['state'] == '纯文字'
        assert 'assets' not in text_request['body']
        assert 'TypeSafe-Danger-Zone-Images' not in text_request['headers']
        await client.client.close()

    _run_server(scenario)


def test_typesafe_rejects_unsupported_image_format():
    adapter = decision_review.SystemOneAdapter()
    with pytest.raises(ValueError):
        adapter.body('jev-latest', '规则', '文字', decision_review.DecisionImage('image/webp', 'QUJD'))


@pytest.mark.parametrize('value', [True, None, '0.9', 1.5, -0.1])
def test_invalid_probability_is_an_error_not_safe(value):
    adapter = decision_review.StateQuestionsAdapter()
    with pytest.raises(ValueError):
        adapter.parse({'answers': {'violation': {'type': 'noul', 'noul': value}}})


def test_threshold_and_reason_mapping():
    result = decision_review.DecisionResult(0.6, 'scam')
    assert decision_review.to_review(result, 0.5)['is_spam'] is True
    assert decision_review.to_review(result, 0.5)['reason'] == copy_text.DECISION_REASONS['scam']
    assert decision_review.to_review(result, 0.7) == {'is_spam': False, 'reason': copy_text.DECISION_SAFE_REASON}
    unknown = decision_review.DecisionResult(0.9, 'something-else')
    assert decision_review.to_review(unknown, 0.5)['reason'] == copy_text.DECISION_REASONS['other']


@pytest.fixture
def decision_env(tmp_path, monkeypatch):
    monkeypatch.setattr(config, 'ENABLE_AI_FILTER', True)
    monkeypatch.setattr(config, 'DECISION_PROTOCOL', 'inception')
    monkeypatch.setattr(config, 'DECISION_API_KEY', 'key-1')
    monkeypatch.setattr(config, 'DECISION_MODEL', 'mercury-decide')
    monkeypatch.setattr(config, 'DECISION_PATH', '')
    monkeypatch.setattr(config, 'DECISION_THRESHOLD', 50)
    ai_service._provider_cache.pop('decision', None)
    yield str(tmp_path / 'decision_test.db')
    ai_service._provider_cache.pop('decision', None)


class ChatProvider:
    def __init__(self):
        self.calls = 0

    async def analyze_message(self, text, image_bytes=None):
        self.calls += 1
        return {'is_spam': False, 'reason': 'chat'}


def _run_review(db_path, monkeypatch, scenario):
    async def main(fake, base_url):
        await DatabaseManager(db_path).initialize()
        monkeypatch.setattr(config, 'DECISION_BASE_URL', base_url)
        service = ai_service.AIService()
        chat = ChatProvider()

        async def get_provider():
            return chat

        monkeypatch.setattr(service, 'get_provider', get_provider)
        await scenario(fake, service, chat)

    _run_server(main)


def test_review_uses_decision_provider_instead_of_chat_model(decision_env, monkeypatch):
    async def scenario(fake, service, chat):
        fake.reply = INCEPTION_REPLY
        message = SimpleNamespace(text='加我微信', caption=None)

        result = await service.analyze_message(message, PNG_BYTES)

        assert result == {'is_spam': True, 'reason': copy_text.DECISION_REASONS['scam']}
        assert chat.calls == 0
        body = fake.requests[-1]['body']
        assert body['model'] == 'mercury-decide'
        assert body['state'][1]['image_url']['url'].endswith(base64.b64encode(PNG_BYTES).decode())
        assert body['state'][1]['image_url']['url'].startswith('data:image/png;base64,')
        assert body['questions']['violation']['instructions'].startswith(await _review_policy())

    _run_review(decision_env, monkeypatch, scenario)


async def _review_policy():
    from services import ai_options

    return await ai_options.get_prompt('review')


def test_review_falls_back_to_chat_model_when_decision_fails(decision_env, monkeypatch):
    async def scenario(fake, service, chat):
        fake.status = 400
        fake.reply = {'error': {'message': 'bad request'}}

        result = await service.analyze_message(SimpleNamespace(text='你好', caption=None))

        assert result == {'is_spam': False, 'reason': 'chat'}
        assert chat.calls == 1

    _run_review(decision_env, monkeypatch, scenario)


def test_failure_is_marked_when_no_chat_model_available(decision_env, monkeypatch):
    async def scenario(fake, service, chat):
        fake.status = 400
        fake.reply = {'error': {'message': 'bad request'}}

        async def no_provider():
            return None

        monkeypatch.setattr(service, 'get_provider', no_provider)
        result = await service.analyze_message(SimpleNamespace(text='你好', caption=None))

        assert result['is_spam'] is False
        assert result['analysis_failed'] is True

    _run_review(decision_env, monkeypatch, scenario)


@pytest.mark.parametrize('field', ['DECISION_BASE_URL', 'DECISION_MODEL', 'DECISION_API_KEY'])
def test_incomplete_config_uses_chat_model(decision_env, monkeypatch, field):
    async def scenario(fake, service, chat):
        monkeypatch.setattr(config, field, '')
        await service.analyze_message(SimpleNamespace(text='你好', caption=None))
        assert chat.calls == 1
        assert fake.requests == []

    _run_review(decision_env, monkeypatch, scenario)


def test_decision_off_uses_chat_model(decision_env, monkeypatch):
    monkeypatch.setattr(config, 'DECISION_PROTOCOL', 'off')

    async def scenario(fake, service, chat):
        await service.analyze_message(SimpleNamespace(text='你好', caption=None))
        assert chat.calls == 1
        assert fake.requests == []

    _run_review(decision_env, monkeypatch, scenario)


def test_test_decision_reports_probability(decision_env, monkeypatch):
    async def scenario(fake, service, chat):
        fake.reply = INCEPTION_REPLY
        result = await service.test_decision()
        assert (result['protocol'], result['model'], result['probability']) == ('inception', 'mercury-decide', 0.97)

    _run_review(decision_env, monkeypatch, scenario)


def test_decision_models_are_recognized():
    assert is_decision_model('mercury-decide')
    assert is_decision_model('inception/mercury-decide')
    assert is_decision_model('jev-latest')
    assert is_decision_model('@cf/cloudflare/clef-flash')
    assert is_decision_model('cloudflare/clef')
    assert not is_decision_model('mercury-2')
    assert not is_decision_model('gpt-6-luna')


def test_decision_test_route_is_available_in_miniapp():
    assert ('POST', '/api/ai-decision-test') in miniapp.ALLOWED_ROUTES
    assert any(route.path == '/api/ai-decision-test' for route in api_admin.routes)


def test_review_config_is_cleared_on_key_change():
    from web_panel import api_config

    assert {'DECISION_PROTOCOL', 'DECISION_API_KEY', 'DECISION_BASE_URL', 'DECISION_PATH'} <= api_config.AI_CLIENT_KEYS
