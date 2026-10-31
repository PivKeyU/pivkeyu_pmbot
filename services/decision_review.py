"""决策审查协议：用只做判断的决策模型审查消息，服务商独立于聊天模型。

决策模型不支持聊天补全，接口格式也不统一，这里按「接口格式」而不是按厂商适配：
第三方中转通常沿用某一家的格式，选对格式、填自己的地址即可。Base URL、路径和
模型都由管理员配置，代码里不内置任何地址；官方地址只是面板里的快速填入选项。

适配器把同一组问题翻译成各格式的请求体，再把回答翻译成统一的 DecisionResult。
新增一种格式：在 services/ai_providers.py 登记，并在这里写适配器挂进 ADAPTERS。

审查规则放在问题的 instructions 里，用户内容放在 state / input 里，互不混合。
"""

import uuid
from dataclasses import dataclass
from typing import Optional

from openai import AsyncOpenAI

from utils import copy as copy_text

VIOLATION_QUESTION = 'violation'
CATEGORY_QUESTION = 'category'
VIOLATION_INSTRUCTIONS = '上面是审查规则。待审内容是否违反其中任何一条？'
CATEGORY_INSTRUCTIONS = '上面是审查规则。如果待审内容违规，最符合哪一类？'
MAX_RETRIES = 1


@dataclass
class DecisionImage:
    media_type: str
    data_base64: str

    @property
    def data_url(self) -> str:
        return f'data:{self.media_type};base64,{self.data_base64}'


@dataclass
class DecisionResult:
    probability: float
    category: Optional[str] = None
    refused: bool = False


def _categories() -> dict:
    return {name: reason.rstrip('。') for name, reason in copy_text.DECISION_REASONS.items()}


def _read_probability(value) -> float:
    # 和聊天模型的 is_spam 校验一致：格式不对算审查失败，不能当成安全
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= 1:
        raise ValueError(f'决策模型返回的概率无效: {value!r}')
    return float(value)


class StateQuestionsAdapter:
    """Inception 格式：state + 以名字为键的 questions，回答也以名字为键。"""

    default_path = '/decisions'

    def path(self, model: str) -> str:
        return self.default_path

    def model_name(self, model: str) -> str:
        return model

    def headers(self, image: Optional[DecisionImage]) -> dict:
        return {}

    def state(self, text: str, image: Optional[DecisionImage]) -> dict:
        if not image:
            return {'state': text}
        return {'state': [
            {'type': 'text', 'text': text},
            {'type': 'image_url', 'image_url': {'url': image.data_url}},
        ]}

    def body(self, model: str, policy: str, text: str, image: Optional[DecisionImage]) -> dict:
        return {
            'model': self.model_name(model),
            **self.state(text, image),
            'questions': {
                VIOLATION_QUESTION: {
                    'type': 'noul',
                    'instructions': f'{policy}\n\n{VIOLATION_INSTRUCTIONS}',
                },
                CATEGORY_QUESTION: {
                    'type': 'choice',
                    'instructions': f'{policy}\n\n{CATEGORY_INSTRUCTIONS}',
                    'criteria': _categories(),
                },
            },
        }

    def parse(self, response: dict) -> DecisionResult:
        answers = response['answers']
        category = answers.get(CATEGORY_QUESTION) or {}
        return DecisionResult(_read_probability(answers[VIOLATION_QUESTION]['noul']), category.get('choice'))


class SystemOneAdapter(StateQuestionsAdapter):
    """System One 格式（TypeSafe Jev）：问题同上，路径不同，图片放 assets 并在 state 里引用。"""

    default_path = '/systemone'
    IMAGE_FORMATS = {'image/png': 'png', 'image/jpeg': 'jpg'}

    def headers(self, image: Optional[DecisionImage]) -> dict:
        return {'TypeSafe-Danger-Zone-Images': 'true'} if image else {}

    def state(self, text: str, image: Optional[DecisionImage]) -> dict:
        if not image:
            return {'state': text}
        image_format = self.IMAGE_FORMATS.get(image.media_type)
        if not image_format:
            raise ValueError(f'System One 格式不支持 {image.media_type} 图片')
        # 引用是一段随机文本：固定写法可能被用户在消息里伪造
        reference = str(uuid.uuid4())
        return {
            'state': {'message': text, 'image': reference},
            'assets': {reference: {'type': 'image', 'format': image_format, 'data_base64': image.data_base64}},
        }


class WorkersAIAdapter(StateQuestionsAdapter):
    """Cloudflare Workers AI 格式（Clef）：问题同上，模型写在路径里，图片放 images。"""

    IMAGE_TYPES = ('image/png', 'image/jpeg', 'image/webp')

    def path(self, model: str) -> str:
        return f'/ai/run/{model if model.startswith("@") else f"@cf/cloudflare/{model}"}'

    def model_name(self, model: str) -> str:
        return model.rsplit('/', 1)[-1]

    def state(self, text: str, image: Optional[DecisionImage]) -> dict:
        if not image:
            return {'state': text}
        if image.media_type not in self.IMAGE_TYPES:
            raise ValueError(f'Workers AI 格式不支持 {image.media_type} 图片')
        return {'state': text, 'images': [image.data_url]}

    def parse(self, response: dict) -> DecisionResult:
        # 文档没有写明 REST 响应是否包 result，两种都接受
        return super().parse(response.get('result') or response)


class OpenAIDecisionsAdapter:
    """OpenAI 格式：input + 问题数组，boolean 题叫 predicate，回答也是数组。"""

    default_path = '/decisions'

    def path(self, model: str) -> str:
        return self.default_path

    def headers(self, image: Optional[DecisionImage]) -> dict:
        return {}

    def body(self, model: str, policy: str, text: str, image: Optional[DecisionImage]) -> dict:
        content = text
        if image:
            content = [{'role': 'user', 'content': [
                {'type': 'input_text', 'text': text},
                {'type': 'input_image', 'image_url': image.data_url},
            ]}]
        return {
            'model': model,
            'input': content,
            'questions': [
                {
                    'type': 'predicate',
                    'name': VIOLATION_QUESTION,
                    'instructions': f'{policy}\n\n{VIOLATION_INSTRUCTIONS}',
                },
                {
                    'type': 'choice',
                    'name': CATEGORY_QUESTION,
                    'instructions': f'{policy}\n\n{CATEGORY_INSTRUCTIONS}',
                    'choices': [
                        {'value': name, 'description': description}
                        for name, description in _categories().items()
                    ],
                },
            ],
        }

    def parse(self, response: dict) -> DecisionResult:
        answers = {answer.get('name'): answer for answer in response['answers']}
        violation = answers[VIOLATION_QUESTION]
        if violation.get('type') == 'refusal':
            return DecisionResult(1.0, refused=True)
        category = answers.get(CATEGORY_QUESTION) or {}
        return DecisionResult(_read_probability(violation['probability']), category.get('choice'))


ADAPTERS = {
    'inception': StateQuestionsAdapter(),
    'openai': OpenAIDecisionsAdapter(),
    'system_one': SystemOneAdapter(),
    'workers_ai': WorkersAIAdapter(),
}


class DecisionClient:
    """复用 OpenAI SDK 的连接池、鉴权和重试，请求体由适配器决定。"""

    def __init__(self, protocol: str, api_key: str, base_url: str, path: str = ''):
        self.protocol = protocol
        self.api_key = api_key
        self.base_url = base_url
        self.path = path
        self.adapter = ADAPTERS[protocol]
        self.client = AsyncOpenAI(api_key=api_key, base_url=base_url, max_retries=MAX_RETRIES)

    async def ask(self, model: str, policy: str, text: str, image: Optional[DecisionImage] = None) -> DecisionResult:
        response = await self.client.post(
            self.path or self.adapter.path(model),
            cast_to=object,
            body=self.adapter.body(model, policy, text, image),
            options={'headers': self.adapter.headers(image)},
        )
        return self.adapter.parse(response)


def to_review(result: DecisionResult, threshold: float) -> dict:
    """翻译成审查流程通用的结果；模型拒答时和聊天模型一样按违规处理。"""
    if result.refused:
        return {'is_spam': True, 'reason': copy_text.DECISION_REFUSED_REASON}
    if result.probability < threshold:
        return {'is_spam': False, 'reason': copy_text.DECISION_SAFE_REASON}
    reasons = copy_text.DECISION_REASONS
    return {'is_spam': True, 'reason': reasons.get(result.category, reasons['other'])}
