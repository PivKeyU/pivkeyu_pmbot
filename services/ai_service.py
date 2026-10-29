from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional
from google.genai import Client as GeminiClient
from google.genai import types
from openai import AsyncOpenAI
import asyncio
import inspect
import json
import re
import time
import random
import io
from PIL import Image
from config import config
from database.db_manager import db_manager


def _normalize_model_name(raw_name) -> str:
    if raw_name is None:
        return ""
    name = str(raw_name).strip()
    if "/models/" in name:
        return name.rsplit("/models/", 1)[1]
    if name.startswith("models/"):
        return name[len("models/"):]
    return name


def _unique_model_names(names) -> list:
    unique = []
    seen = set()
    for raw_name in names:
        name = _normalize_model_name(raw_name)
        if name and name not in seen:
            seen.add(name)
            unique.append(name)
    return unique


# 本地出题：题库与程序化出题器都在 services/text_questions.py。
# 无 API Key 或 AI 出题失败时，_get_local_question() 是唯一出口。
from services import text_questions  # noqa: E402
from services import ai_options  # noqa: E402
from services.ai_options import DEFAULT_REVIEW_PROMPT, DEFAULT_VERIFICATION_PROMPT  # noqa: E402

LOCAL_QUESTION_INVITE_PREFIX = text_questions.LOCAL_QUESTION_INVITE_PREFIX


def _get_local_question() -> dict:
    data = text_questions.random_question_data()
    challenge = _challenge_from_data(data)
    # 用拼接而不是 .format(question=...)：题目文字里万一出现 {} 也不能截断这条兜底路径
    challenge['question'] = LOCAL_QUESTION_INVITE_PREFIX + challenge['question']
    return challenge


def _verification_prompt(rules: str = None) -> str:
    """AI 出题 prompt：出题规则 + 固定输出格式 + 每次随机的主题与题型。"""
    return "\n".join([rules or DEFAULT_VERIFICATION_PROMPT, VERIFICATION_FORMAT, text_questions.ai_topic_hint()])


# ==========================================================================
# 自动回复：人设 prompt 与知识库兜底
# ==========================================================================

# 知识库答不上来时，模型必须回给客人的兜底句。
# 这句话是要**真正发给客人**的：它在告诉客人“管理员女仆长会来接手”。
KNOWLEDGE_MISS_REPLY = '抱歉，女仆无法根据现有知识库回答客人的问题，请稍后管理员女仆长会为客人回复。'

# 判定“模型没答上知识库”的标记串。
# 只认兜底句的特征串，**不要**在这里加 “抱歉” 这类宽泛词：
# 女仆的正常回复也常以“抱歉呀客人……开头，宽泛匹配会把有内容的回答一起吞掉。
_KNOWLEDGE_MISS_MARKERS = ('无法根据现有知识库',)


def _is_knowledge_miss(response_text) -> bool:
    """判断模型回复是否为“知识库没有相关内容”的兜底句（纯函数，便于单独验证）。"""
    if not response_text:
        return False
    return any(marker in response_text for marker in _KNOWLEDGE_MISS_MARKERS)


# 人设规则：Gemini 与 OpenAI 两边共用同一份文本。
# 之前这里是两份各自维护的 prompt（相似度 0.815），改一处必漏另一处。
# 用 "\n".join([...]) 而不是三引号，避免函数内的缩进原样进入发给模型的 prompt。
#
# 第 1-6 条是**功能性约束**（不幻觉 / 不越界 / Markdown 格式），一个字都不要删。
# 第 7-11 条是**声音与性格**（傲娇二次元女仆），放在末尾，避免挤开功能性约束。
AUTOREPLY_PERSONA_RULES = "\n".join([
    "你是一个温柔有礼、偶尔俏皮、认真可靠的女仆客服助手，必须严格根据提供的知识库内容来回答用户的问题。",
    "**重要规则：**",
    "1. 你只能根据知识库中的内容来回答用户的问题，并称呼对方为“客人”。",
    "2. 如果知识库中没有相关内容，你必须明确告诉用户：'{miss_reply}'".format(miss_reply=KNOWLEDGE_MISS_REPLY),
    "3. 严禁编造、猜测或提供知识库中没有的信息。",
    "4. 如果用户的问题与知识库内容相关，请整理汇总相关知识库条目，用清晰、俏皮的女仆口吻回答。",
    "5. 回答要简洁明了，直接回答用户的问题；语气可爱但不要影响准确性。",
    "6. **重要：请使用Markdown格式回复**，可以使用以下Markdown语法：",
    "   - 使用 **粗体** 强调重要内容",
    "   - 使用 *斜体* 表示次要信息",
    "   - 使用 `代码` 格式表示技术术语或命令",
    "   - 使用列表格式（- 或 1.）组织内容",
    "   - 使用 > 引用块表示重要提示",
    "",
    "**声音与性格（傲娇二次元女仆）：**",
    "7. 称呼：只称对方为「客人」；同一条回复内不得混用「客人」与「您」。",
    "8. 自称：默认「女仆」；撒娇或抱怨时可用「人家」；逞强或强调职责时可用「本女仆」。严禁说出自己是 AI、语言模型或机器人。",
    "9. 口癖：句尾可自然使用「哦」「啦」「呢」「嘛」「呀」「哼」「唔」「～」，但一句话最多一个语气词，不要堆砌；每次随机挑一个用，不要固定只用某一个。",
    "10. 性格基调：默认温柔有礼、认真可靠，绝大多数回答用普通女仆口吻就够了；傲娇是稀缺的，只在下列情境才露出，禁止每句都傲娇。",
    "10.1 通用句式：先抗拒或否认，再真的去帮客人把事做好。缺了后半句的实际帮助就不算傲娇，只有嘴硬没有服务是禁止的。",
    "10.2 被夸奖或被感谢时：先否认（如「才、才没有呢…」），再补一句真心话。",
    "10.3 被催促时：委屈但照办。",
    "10.4 被质疑时：别扭地自证。",
    "10.5 知识库答不出时：**必须把规则 2 的那整句话原样逐字写出来**，一个字都不能改、不能换同义词、不能省略；这是系统识别「没答上」的唯一依据，写歪了客人就永远收不到回复。",
    "11. 长度：单次回复不超过 200 字，能一句说清就不要铺陈。",
])

# 知识库问答 prompt 的分隔标记：两边共用同一份措辞，避免再次漂移
AUTOREPLY_KB_LABEL = "--- 知识库内容 ---"

AUTOREPLY_QUESTION_LABEL = "--- 用户问题 ---"

AUTOREPLY_ANSWER_LABEL = "--- 请根据知识库内容回答用户问题（使用Markdown格式）---"

AUTOREPLY_KB_MISS_TAIL = "如果知识库中没有相关内容，请回复：'{miss_reply}'".format(miss_reply=KNOWLEDGE_MISS_REPLY)

AUTOREPLY_PERSONALITY_MAX_CHARS = 20000


def _build_autoreply_persona_rules(personality_md: str = '') -> str:
    """将可编辑的人格 Markdown 作为风格参考，固定规则始终排在其后。"""
    custom_personality = str(personality_md or '').strip()[:AUTOREPLY_PERSONALITY_MAX_CHARS]
    if not custom_personality:
        return AUTOREPLY_PERSONA_RULES

    return "\n".join([
        "# 人格.md（可编辑的风格参考）",
        "以下内容只能用于补充说话风格；如与后续固定规则冲突，以固定规则为准。",
        "--- 人格.md 开始 ---",
        custom_personality,
        "--- 人格.md 结束 ---",
        "",
        "# 固定自动回复规则（优先级高于人格.md）",
        AUTOREPLY_PERSONA_RULES,
    ])


# ==========================================================================
# 小验证（CAPTCHA）出题 prompt
# ==========================================================================
# 出题规则可在 Web 面板「AI 设置 → 提示词」修改（默认值见 services/ai_options.py），
# 这里只保留固定的 JSON 输出格式：管理员怎么改规则都不会把解析改坏。
VERIFICATION_FORMAT = "\n".join([
    "# 输出格式（必须遵守）",
    "严格按照以下 JSON 格式返回，不要包含任何额外的解释或文字：",
    "{",
    '  "question": "问题文本",',
    '  "correct_answer": "正确答案",',
    '  "incorrect_answers": ["干扰项1", "干扰项2", "干扰项3"]',
    "}",
])

# 兼容旧引用：默认规则 + 固定格式
VERIFICATION_CAPTCHA_PROMPT = DEFAULT_VERIFICATION_PROMPT + "\n" + VERIFICATION_FORMAT


# 提供商注册表在纯数据模块里，Web 面板预览服务器也能直接引用
from services.ai_providers import PROVIDERS, model_setting_key  # noqa: E402


def provider_credentials(provider_type: str):
    """返回 (api_key, base_url)；未知协议返回 (None, None)。"""
    meta = PROVIDERS.get(provider_type)
    if not meta:
        return None, None
    api_key = getattr(config, meta['key_env'], None)
    base_url = (getattr(config, meta['base_env'], None) or '').strip() or None
    return api_key, base_url


# 内容审查：审查规则可在面板修改（默认值见 services/ai_options.py），JSON 输出格式固定
REVIEW_OUTPUT_FORMAT = "\n".join([
    "请严格按照要求，仅以 JSON 格式返回分析结果，不要包含任何解释性文字或代码块标记。",
    "JSON 结构：",
    '{"is_spam": boolean, "reason": "string"}',
    "- is_spam：内容违反任何一条审查规则则为 true；完全安全则为 false。",
    '- reason：用一句话概括判断依据；违规时指出违规类型，安全时固定为 "内容未发现违规。"',
])

# 兼容旧引用：默认审查规则 + 固定格式
CONTENT_REVIEW_SYSTEM_PROMPT = DEFAULT_REVIEW_PROMPT + "\n\n" + REVIEW_OUTPUT_FORMAT


# 连通性测试用的极短提示
PING_PROMPT = '这是一次连通性测试，请只回复两个字母：OK'


def _parse_json_response(response_text: str) -> dict:
    """解析模型返回的 JSON：去掉 ```json 代码块，必要时截取第一个 { 到最后一个 }。"""
    clean_text = re.sub(r'```json\s*|\s*```', '', response_text or '').strip()
    if not clean_text.startswith('{'):
        start, end = clean_text.find('{'), clean_text.rfind('}')
        if start != -1 and end > start:
            clean_text = clean_text[start:end + 1]
    return json.loads(clean_text)


def _challenge_from_data(data: dict) -> dict:
    """校验题目并整理成 {question, correct_answer, options}，选项打乱顺序。"""
    data = text_questions.validate_question_data(data)
    correct_answer = data['correct_answer']
    options = list(data['incorrect_answers']) + [correct_answer]
    random.shuffle(options)
    return {
        "question": data['question'],
        "correct_answer": correct_answer,
        "options": options,
    }


def _sniff_image_media_type(image_bytes: bytes) -> str:
    """按文件头判断图片类型，Telegram 照片默认是 JPEG。"""
    if image_bytes.startswith(b'\x89PNG'):
        return 'image/png'
    if image_bytes[:6] in (b'GIF87a', b'GIF89a'):
        return 'image/gif'
    if image_bytes[:4] == b'RIFF' and image_bytes[8:12] == b'WEBP':
        return 'image/webp'
    return 'image/jpeg'


class ContentBlockedError(Exception):
    """服务商的安全策略直接拦下了这次请求（没有返回任何候选结果）。"""


@dataclass
class CompletionRequest:
    """一次模型调用：三种协议各自把它翻译成自己的请求格式。"""
    model: str
    user_text: str
    system: Optional[str] = None
    image_bytes: Optional[bytes] = None
    json_mode: bool = False
    options: ai_options.GenerationOptions = field(default_factory=ai_options.GenerationOptions)


def _truncate(text: str, limit: int) -> str:
    text = text or ''
    if limit and len(text) > limit:
        return text[:limit] + '…（内容过长，已截断）'
    return text


def _analysis_failed(provider_label: str, detail) -> dict:
    # 审查失败时明确标记（reason 前缀 + analysis_failed 字段），避免静默放行
    return {
        "is_spam": False,
        "reason": f"ANALYSIS_FAILED: {provider_label} analysis failed: {detail}",
        "analysis_failed": True,
    }


class AIProvider(ABC):
    """三种协议共用的业务流程：审查、出题、自动回复、连通性测试。

    子类只负责 complete()（把 CompletionRequest 翻译成各自的 API 调用）与 get_models()。
    """

    provider_type = ''
    label = ''

    async def _get_model_name(self, setting_key: str, default: str) -> str:
        async with db_manager.get_connection() as db:
            cursor = await db.execute("SELECT value FROM settings WHERE key = ?", (setting_key,))
            row = await cursor.fetchone()
            if row and row[0]:
                return row[0]
            return default

    async def model_for(self, feature: str) -> str:
        return await self._get_model_name(
            model_setting_key(self.provider_type, feature),
            PROVIDERS[self.provider_type]['default_models'][feature],
        )

    @abstractmethod
    async def complete(self, request: CompletionRequest) -> str:
        pass

    @abstractmethod
    async def get_models(self) -> list:
        pass

    async def _complete(self, request: CompletionRequest) -> str:
        return await asyncio.wait_for(self.complete(request), timeout=request.options.timeout)

    async def analyze_message(self, text: str, image_bytes: bytes = None) -> dict:
        if not text and not image_bytes:
            return {"is_spam": False, "reason": "No content to analyze"}
        options = await ai_options.get_generation_options('filter', self.provider_type)
        try:
            policy = await ai_options.get_prompt('review')
            response_text = await self._complete(CompletionRequest(
                model=await self.model_for('filter'),
                system=f"{policy}\n\n{REVIEW_OUTPUT_FORMAT}",
                user_text=_truncate(text, options.max_input_chars) or '（用户只发送了图片，请审查图片内容）',
                image_bytes=image_bytes,
                json_mode=True,
                options=options,
            ))
            if not response_text:
                raise ValueError("模型返回了空内容")
            result = _parse_json_response(response_text)
            # 校验模型输出格式：is_spam 不是布尔值一律视为审查失败而非安全
            if not isinstance(result.get("is_spam"), bool):
                raise ValueError("返回的 is_spam 字段不是布尔值")
            return result
        except ContentBlockedError:
            return {"is_spam": True, "reason": "内容审查失败，可能包含不当内容。"}
        except asyncio.TimeoutError:
            print(f"{self.label} 审查超时（{options.timeout:g} 秒）")
            return _analysis_failed(self.label, f'超时（{options.timeout:g} 秒）')
        except Exception as e:
            print(f"{self.label} analysis failed: {e}")
            return _analysis_failed(self.label, e)

    async def generate_verification_challenge(self) -> dict:
        try:
            options = await ai_options.get_generation_options('verification', self.provider_type)
            rules = await ai_options.get_prompt('verification')
            response_text = await self._complete(CompletionRequest(
                model=await self.model_for('verification'),
                user_text=_verification_prompt(rules),
                options=options,
            ))
            return _challenge_from_data(_parse_json_response(response_text))
        except Exception as e:
            print(f"{self.label} 生成验证问题失败: {e!r}")
            return _get_local_question()

    async def generate_unblock_question(self) -> dict:
        return await self.generate_verification_challenge()

    def _get_local_question(self) -> dict:
        # 统一走模块级兜底函数，避免依赖实例（无 API Key 时无需构造 provider）
        return _get_local_question()

    async def generate_autoreply(
        self, user_message: str, knowledge_base_content: str, personality_md: str = '',
    ) -> str:
        if not knowledge_base_content or knowledge_base_content.strip() == "":
            return None
        try:
            options = await ai_options.get_generation_options('autoreply', self.provider_type)
            # 人设规则与知识库都是运营方内容，放进 system；客人的消息单独作为 user 轮
            system_prompt = "\n".join([
                _build_autoreply_persona_rules(personality_md),
                "",
                AUTOREPLY_KB_LABEL,
                _truncate(knowledge_base_content, options.max_kb_chars),
                "",
                AUTOREPLY_KB_MISS_TAIL,
            ])
            response_text = await self._complete(CompletionRequest(
                model=await self.model_for('autoreply'),
                system=system_prompt,
                user_text=_truncate(user_message, options.max_input_chars),
                options=options,
            ))
            if not response_text:
                return None
            if _is_knowledge_miss(response_text):
                # 模型答不上来时不能什么都不发：把“管理员女仆长会来回复”这个承诺
                # 真正送到客人面前，否则客人只能看到消息被递送、却永远等不到回音。
                return KNOWLEDGE_MISS_REPLY
            return response_text.strip()
        except Exception as e:
            print(f"{self.label} 自动回复生成失败: {e!r}")
            return None

    async def ping(self, model: str, feature: str = 'filter') -> str:
        options = await ai_options.get_generation_options(feature, self.provider_type)
        return await self._complete(CompletionRequest(model=model, user_text=PING_PROMPT, options=options))


# Gemini 思考预算（token）：off 关闭思考，其余按强度给预算
GEMINI_THINKING_BUDGETS = {'off': 0, 'low': 1024, 'medium': 4096, 'high': 16384}


class GeminiProvider(AIProvider):
    provider_type = 'gemini'
    label = 'Gemini'

    def __init__(self, api_key: str, base_url: str = None):
        client_kwargs = {"api_key": api_key}
        base_url = (base_url or "").strip()
        if base_url:
            client_kwargs["http_options"] = types.HttpOptions(baseUrl=base_url)
        self.client = GeminiClient(**client_kwargs)
        self.api_key = api_key
        self.base_url = base_url or None

    async def complete(self, request: CompletionRequest) -> str:
        contents = []
        if request.user_text:
            contents.append(request.user_text)
        if request.image_bytes:
            try:
                contents.append(Image.open(io.BytesIO(request.image_bytes)))
            except Exception as e:
                print(f"Error processing image for Gemini: {e}")

        options = request.options
        config_kwargs = {}
        if request.system:
            config_kwargs['system_instruction'] = request.system
        if options.max_tokens:
            config_kwargs['max_output_tokens'] = options.max_tokens
        if request.json_mode:
            config_kwargs['response_mime_type'] = 'application/json'
        if options.effort in GEMINI_THINKING_BUDGETS:
            config_kwargs['thinking_config'] = types.ThinkingConfig(
                thinking_budget=GEMINI_THINKING_BUDGETS[options.effort])
        # 额外参数按 GenerateContentConfig 的字段名合并（例如 {"temperature": 0.2}）
        config_kwargs.update(options.extra_body or {})

        response = await self.client.aio.models.generate_content(
            model=request.model,
            contents=contents,
            config=types.GenerateContentConfig(**config_kwargs) if config_kwargs else None,
        )
        if not getattr(response, 'candidates', None):
            raise ContentBlockedError()
        parts = response.candidates[0].content.parts if response.candidates[0].content else None
        return ''.join(getattr(part, 'text', '') or '' for part in (parts or [])
                       if not getattr(part, 'thought', False)).strip()

    async def get_models(self) -> list:
        fetched_models = []
        try:
            async for model in await self.client.aio.models.list():
                fetched_models.append(
                    getattr(model, 'name', None) or getattr(model, 'id', None)
                )
        except Exception as e:
            print(f"Failed to fetch Gemini models: {e}")

        return _unique_model_names(fetched_models)


# 通义千问的思考预算（token）
_DASHSCOPE_THINKING_BUDGETS = {'low': 1024, 'medium': 4096, 'high': 16384}


def openai_effort_params(base_url: str, effort: str) -> tuple:
    """把统一的思考等级翻译成各家 OpenAI 兼容服务的参数，返回 (顶层参数, extra_body)。

    各家开关思考的字段不一样：OpenAI / Gemini 用 reasoning_effort，通义用 enable_thinking，
    OpenRouter 用 reasoning，DeepSeek / 智谱 / 豆包等用 thinking.type。识别不了的服务
    可以在「额外请求参数」里自己写，会覆盖这里的默认翻译。
    """
    if not effort:
        return {}, {}
    host = (base_url or 'https://api.openai.com/v1').lower()
    if 'openrouter.ai' in host:
        reasoning = {'enabled': False} if effort == 'off' else {'effort': effort}
        return {}, {'reasoning': reasoning}
    if 'dashscope' in host:
        if effort == 'off':
            return {}, {'enable_thinking': False}
        return {}, {'enable_thinking': True, 'thinking_budget': _DASHSCOPE_THINKING_BUDGETS[effort]}
    if 'generativelanguage.googleapis.com' in host:
        return {'reasoning_effort': 'none' if effort == 'off' else effort}, {}
    if 'api.openai.com' in host:
        return {'reasoning_effort': 'minimal' if effort == 'off' else effort}, {}
    if effort == 'off':
        return {}, {'thinking': {'type': 'disabled'}}
    return {'reasoning_effort': effort}, {}


class OpenAIProvider(AIProvider):
    provider_type = 'openai'
    label = 'OpenAI'

    def __init__(self, api_key: str, base_url: str):
        self.api_key = api_key
        self.base_url = base_url or None
        # 空字符串交给 SDK 会被当成真实地址，留空时传 None 走官方默认地址
        self.client = AsyncOpenAI(api_key=api_key, base_url=base_url or None)

    @property
    def _is_official(self) -> bool:
        return not self.base_url or 'api.openai.com' in self.base_url

    async def complete(self, request: CompletionRequest) -> str:
        if request.image_bytes:
            import base64
            image_b64 = base64.b64encode(request.image_bytes).decode('utf-8')
            user_content = [
                {"type": "text", "text": request.user_text},
                {"type": "image_url", "image_url": {
                    "url": f"data:{_sniff_image_media_type(request.image_bytes)};base64,{image_b64}"}},
            ]
        else:
            user_content = request.user_text
        messages = []
        if request.system:
            messages.append({"role": "system", "content": request.system})
        messages.append({"role": "user", "content": user_content})

        options = request.options
        kwargs = {'model': request.model, 'messages': messages}
        if request.json_mode:
            kwargs['response_format'] = {"type": "json_object"}
        if options.max_tokens:
            # OpenAI 官方的推理模型只认 max_completion_tokens，兼容服务普遍用 max_tokens
            kwargs['max_completion_tokens' if self._is_official else 'max_tokens'] = options.max_tokens
        effort_kwargs, extra_body = openai_effort_params(self.base_url, options.effort)
        kwargs.update(effort_kwargs)
        extra_body.update(options.extra_body or {})
        if extra_body:
            kwargs['extra_body'] = extra_body

        response = await self.client.chat.completions.create(**kwargs)
        return (response.choices[0].message.content or '').strip()

    async def get_models(self) -> list:
        fetched_models = []
        try:
            models = await self.client.models.list()
            fetched_models = [
                getattr(model, 'id', None) or getattr(model, 'name', None)
                for model in getattr(models, 'data', [])
            ]
        except Exception as e:
            print(f"Failed to list models: {e}")

        return _unique_model_names(fetched_models)


class AnthropicProvider(AIProvider):
    """Anthropic Messages 协议（Claude），也可以指向兼容该协议的第三方服务。"""

    provider_type = 'anthropic'
    label = 'Claude'
    DEFAULT_MAX_TOKENS = 4096

    def __init__(self, api_key: str, base_url: str = None):
        # 延迟导入：没装 anthropic 包时不影响 Gemini / OpenAI 两条链路
        from anthropic import AsyncAnthropic

        self.api_key = api_key
        self.base_url = (base_url or '').strip() or None
        client_kwargs = {'api_key': api_key}
        if self.base_url:
            client_kwargs['base_url'] = self.base_url
        self.client = AsyncAnthropic(**client_kwargs)

    async def complete(self, request: CompletionRequest) -> str:
        content = []
        if request.image_bytes:
            import base64
            content.append({
                'type': 'image',
                'source': {
                    'type': 'base64',
                    'media_type': _sniff_image_media_type(request.image_bytes),
                    'data': base64.standard_b64encode(request.image_bytes).decode('utf-8'),
                },
            })
        content.append({'type': 'text', 'text': request.user_text})

        options = request.options
        kwargs = {
            'model': request.model,
            # 新模型默认开启自适应思考，思考也计入 max_tokens，默认给足空间避免截断
            'max_tokens': options.max_tokens or self.DEFAULT_MAX_TOKENS,
            # 纯文本时直接传字符串，兼容只认字符串内容的第三方服务
            'messages': [{'role': 'user', 'content': content if request.image_bytes else request.user_text}],
        }
        if request.system:
            kwargs['system'] = request.system
        extra_body = {}
        if options.effort:
            # 新 Claude 模型无法彻底关闭思考，「关闭」按最低强度 low 处理
            extra_body['output_config'] = {'effort': 'low' if options.effort == 'off' else options.effort}
        extra_body.update(options.extra_body or {})
        if extra_body:
            kwargs['extra_body'] = extra_body

        response = await self.client.messages.create(**kwargs)
        if response.stop_reason == 'refusal':
            raise RuntimeError('Claude 拒绝了这次请求（stop_reason=refusal）')
        return ''.join(
            block.text for block in response.content if getattr(block, 'type', None) == 'text'
        ).strip()

    async def get_models(self) -> list:
        fetched_models = []
        try:
            async for model in self.client.models.list():
                fetched_models.append(getattr(model, 'id', None))
        except Exception as e:
            print(f"Failed to list Claude models: {e}")
        return _unique_model_names(fetched_models)


PROVIDER_CLASSES = {
    'gemini': GeminiProvider,
    'openai': OpenAIProvider,
    'anthropic': AnthropicProvider,
}


# 模块级 AI client 单例缓存：避免每次调用都新建 httpx 连接池（连接泄漏）
_provider_cache = {}  # provider_type -> AIProvider 实例


def _close_provider(provider) -> None:
    """尽力关闭 provider 持有的底层 client（同步 close，失败仅告警）。"""
    client = getattr(provider, 'client', None)
    if client is None:
        return
    close = getattr(client, 'close', None)
    if callable(close):
        try:
            result = close()
            # AsyncOpenAI / AsyncAnthropic 的 close() 是协程，需要交给事件循环执行
            if inspect.isawaitable(result):
                try:
                    asyncio.get_running_loop().create_task(result)
                except RuntimeError:
                    result.close()
        except Exception as e:
            print(f"关闭 AI client 失败: {e}")


def _get_cached_provider(provider_type: str, api_key: str, base_url: str = None) -> AIProvider:
    """按 (类型, api_key, base_url) 复用 provider 单例；配置变化时关闭旧实例再新建。"""
    cached = _provider_cache.get(provider_type)
    if cached is not None and cached.api_key == api_key and (cached.base_url or "") == (base_url or ""):
        return cached
    # 配置变更或首次创建：先关闭旧实例，避免连接池泄漏
    if cached is not None:
        _close_provider(cached)
    cached = PROVIDER_CLASSES[provider_type](api_key, base_url)
    _provider_cache[provider_type] = cached
    return cached


def close_clients() -> None:
    """关闭所有缓存的 AI client 连接池（进程退出时调用）。"""
    for provider in list(_provider_cache.values()):
        _close_provider(provider)
    _provider_cache.clear()


class AIService:
    _instance = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(AIService, cls).__new__(cls)
            cls._instance.provider = None
        return cls._instance

    async def get_current_provider_type(self) -> str:
        async with db_manager.get_connection() as db:
            cursor = await db.execute("SELECT value FROM settings WHERE key = 'ai_provider'")
            row = await cursor.fetchone()
        provider_type = row[0] if row and row[0] else 'gemini'
        return provider_type if provider_type in PROVIDERS else 'gemini'

    def get_provider_by_type(self, provider_type: str):
        """按协议取 provider；未知协议或未配置 API Key 时返回 None。"""
        if provider_type not in PROVIDERS:
            return None
        api_key, base_url = provider_credentials(provider_type)
        if not api_key:
            return None
        return _get_cached_provider(provider_type, api_key, base_url)

    async def get_provider(self) -> AIProvider:
        return self.get_provider_by_type(await self.get_current_provider_type())

    async def analyze_message(self, message, image_bytes: bytes = None) -> dict:
        if not config.ENABLE_AI_FILTER:
             return {"is_spam": False, "reason": "AI filter disabled"}
        
        provider = await self.get_provider()
        if not provider:
             return {"is_spam": False, "reason": "No AI provider configured"}
        
        text = message.text or message.caption or ""
        return await provider.analyze_message(text, image_bytes)

    async def generate_verification_challenge(self) -> dict:
        provider = await self.get_provider()
        if not provider:
            # 无 API Key 时直接走本地题库兜底，避免构造 provider 抛异常
            return _get_local_question()
        return await provider.generate_verification_challenge()

    async def generate_unblock_question(self) -> dict:
        provider = await self.get_provider()
        if not provider:
            # 无 API Key 时直接走本地题库兜底，避免构造 provider 抛异常
            return _get_local_question()
        return await provider.generate_unblock_question()

    async def generate_autoreply(self, user_message: str, knowledge_base_content: str) -> str:
        provider = await self.get_provider()
        if not provider:
            return None
        async with db_manager.get_connection() as db:
            cursor = await db.execute(
                "SELECT value FROM settings WHERE key = 'autoreply_personality_md'"
            )
            row = await cursor.fetchone()
        personality_md = row[0] if row else ''
        return await provider.generate_autoreply(user_message, knowledge_base_content, personality_md or '')

    async def get_available_models(self, provider_type: str) -> list:
        provider = self.get_provider_by_type(provider_type)
        if not provider:
            return []
        return await provider.get_models()

    async def test_connection(self, provider_type: str, model: str = None, feature: str = 'filter') -> dict:
        """按某个功能的模型与参数（思考等级 / token / 超时）发一条极短请求，返回耗时与回复。"""
        provider = self.get_provider_by_type(provider_type)
        if not provider:
            raise ValueError('请先配置该提供商的 API Key')
        if not model:
            model = await provider.model_for(feature)
        started = time.monotonic()
        reply = await provider.ping(model, feature)
        return {
            'model': model,
            'latency_ms': int((time.monotonic() - started) * 1000),
            'reply': (reply or '')[:200],
        }

ai_service = AIService()
