from abc import ABC, abstractmethod
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

LOCAL_QUESTION_INVITE_PREFIX = text_questions.LOCAL_QUESTION_INVITE_PREFIX


def _get_local_question() -> dict:
    data = text_questions.random_question_data()
    challenge = _challenge_from_data(data)
    # 用拼接而不是 .format(question=...)：题目文字里万一出现 {} 也不能截断这条兜底路径
    challenge['question'] = LOCAL_QUESTION_INVITE_PREFIX + challenge['question']
    return challenge


def _verification_prompt() -> str:
    """AI 出题 prompt：固定规则 + 每次随机的主题与题型，避免模型反复出同一类题。"""
    return VERIFICATION_CAPTCHA_PROMPT + "\n" + text_questions.ai_topic_hint()


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
# 小验证（CAPTCHA）出题 prompt：Gemini 与 OpenAI 两边共用同一份文本
# ==========================================================================
# 原先这里是两份逐字重复的三引号字符串（sha1 相同），改一处必漏另一处；
# 三引号写法还会把函数缩进的 8 个空格原样发进 prompt，改用 "\n".join 一并修掉。
VERIFICATION_CAPTCHA_PROMPT = "\n".join([
    "# 角色",
    "你是一个女仆风格的人机验证（CAPTCHA）问题生成器。",
    "# 任务",
    "生成一个随机的、适合成年人的中文常识性问题，用于区分人类和机器人。问题要保持清楚易懂。",
    "# 语气要求",
    "- 题干可以带轻快女仆语气，句尾可自然使用「～」「呀」「哦」，但一句话最多一个语气词，不要堆砌。",
    "- 选项文本必须保持纯中性：correct_answer 与 incorrect_answers 都不得出现语气词、颜文字或情绪符号。",
    "- 题干与选项都不得使用「主人」「客人」「女仆」「人家」等称呼或自称：这是给用户做的题，不是对话。",
    "- 语气不得影响题意：提问的对象、类别与逻辑必须一眼看明白。",
    "# 要求",
    "1.  **问题格式多样性**: 你需要随机选择以下两种问题格式之一进行提问：",
    '    *   **a) 标准直接提问**: 例如，"中国的首都是哪里？"',
    '    *   **b) 反向排除提问**: 使用"以下哪个不属于...？"或类似的句式。例如，"以下哪个不属于行星？"',
    "2.  **主题**: 问题主题应为完全随机的日常通用常识，无需限定在特定领域。",
    '3.  **难度**: 问题和选项的难度应设定为"绝大多数18岁以上母语为中文的成年人都能立即回答正确"的水平，避免专业或冷门知识。',
    "4.  **明确性**: 问题必须只有一个明确无误的正确答案。",
    "5.  **答案逻辑**:",
    "    *   提供一个`correct_answer`（正确答案）。",
    "    *   提供一个包含三个字符串的列表`incorrect_answers`（干扰项）。",
    "    *   **对于标准问题**，所有选项应属于同一类别。",
    "    *   **对于反向排除问题**，三个`incorrect_answers`应属于同一类别，而`correct_answer`则是那个不属于该类别的 outlier（局外者）。",
    "6.  **语言**: 所有内容必须为简体中文。",
    "7.  **输出格式**: 严格按照以下JSON格式返回，不要包含任何额外的解释或文字。",
    "# JSON格式示例",
    "{",
    '  "question": "问题文本",',
    '  "correct_answer": "正确答案",',
    '  "incorrect_answers": ["干扰项1", "干扰项2", "干扰项3"]',
    "}",
])


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


# 内容审查 system prompt：OpenAI 与 Claude 共用（Gemini 走原有的拼接方式）
CONTENT_REVIEW_SYSTEM_PROMPT = "你是一个内容审查员。你的任务是分析提供给你的文本和/或图片内容，并判断其是否包含垃圾信息、恶意软件、钓鱼链接、不当言论、辱骂、攻击性词语或任何违反安全政策的内容。\n请严格按照要求，仅以JSON格式返回你的分析结果，不要包含任何额外的解释或标记。\n**输出格式**: 你必须且只能以严格的JSON格式返回你的分析结果，不得包含任何解释性文字或代码块标记。\n**JSON结构**:\n```json\n{\n  \"is_spam\": boolean,\n  \"reason\": \"string\"\n}\n```\n*   `is_spam`: 如果内容违反**任何一条**安全策略，则为 `true`；如果内容完全安全，则为 `false`。\n*   `reason`: 用一句话精准概括判断依据。如果违规，请明确指出违规的类型。如果安全，此字段固定为 `\"内容未发现违规。\"`"

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


class AIProvider(ABC):
    async def _get_model_name(self, setting_key: str, default: str) -> str:
        async with db_manager.get_connection() as db:
            cursor = await db.execute("SELECT value FROM settings WHERE key = ?", (setting_key,))
            row = await cursor.fetchone()
            if row and row[0]:
                return row[0]
            return default

    @abstractmethod
    async def ping(self, model: str) -> str:
        """发一条极短的请求验证 Key / 地址 / 模型是否可用，返回模型回复。"""
        pass

    @abstractmethod
    async def analyze_message(self, text: str, image_bytes: bytes = None) -> dict:
        pass

    @abstractmethod
    async def generate_verification_challenge(self) -> dict:
        pass

    @abstractmethod
    async def generate_unblock_question(self) -> dict:
        pass

    @abstractmethod
    async def generate_autoreply(
        self, user_message: str, knowledge_base_content: str, personality_md: str = '',
    ) -> str:
        pass
        
    @abstractmethod
    async def get_models(self) -> list:
        pass

class GeminiProvider(AIProvider):
    def __init__(self, api_key: str, base_url: str = None):
        client_kwargs = {"api_key": api_key}
        base_url = (base_url or "").strip()
        if base_url:
            client_kwargs["http_options"] = types.HttpOptions(baseUrl=base_url)
        self.client = GeminiClient(**client_kwargs)
        self.api_key = api_key
        self.base_url = base_url or None
        
    async def analyze_message(self, text: str, image_bytes: bytes = None) -> dict:
        model_name = await self._get_model_name('gemini_model_filter', 'gemini-2.5-flash')
        content = []
        prompt_parts = [
            "你是一个内容审查员。你的任务是分析提供给你的文本和/或图片内容，并判断其是否包含垃圾信息、恶意软件、钓鱼链接、不当言论、辱骂、攻击性词语或任何违反安全政策的内容。",
            "请严格按照要求，仅以JSON格式返回你的分析结果，不要包含任何额外的解释或标记。",
            "**输出格式**: 你必须且只能以严格的JSON格式返回你的分析结果，不得包含任何解释性文字或代码块标记。",
            "**JSON结构**:\n```json\n{\n  \"is_spam\": boolean,\n  \"reason\": \"string\"\n}\n```\n*   `is_spam`: 如果内容违反**任何一条**安全策略，则为 `true`；如果内容完全安全，则为 `false`。\n*   `reason`: 用一句话精准概括判断依据。如果违规，请明确指出违规的类型。如果安全，此字段固定为 `\"内容未发现违规。\"`",
            "\n--- 以下是需要分析的内容 ---",
        ]

        if text:
            content.append(text)
        
        if image_bytes:
            try:
                image = Image.open(io.BytesIO(image_bytes))
                content.append(image)
            except Exception as e:
                print(f"Error processing image for Gemini: {e}")

        if not content:
            return {"is_spam": False, "reason": "No content to analyze"}

        content.append("\n".join(prompt_parts))

        try:
            response = await self.client.aio.models.generate_content(
                model=model_name,
                contents=content
            )
            
            if not hasattr(response, 'candidates') or not response.candidates:
                return {"is_spam": True, "reason": "内容审查失败，可能包含不当内容。"}

            if response.candidates and response.candidates[0].content.parts:
                response_text = response.candidates[0].content.parts[0].text
            else:
                response_text = None
            
            if not response_text:
                raise ValueError("Gemini API returned an empty response.")
            
            clean_text = re.sub(r'```json\s*|\s*```', '', response_text).strip()
            result = json.loads(clean_text)
            # 校验模型输出格式：is_spam 不是布尔值一律视为审查失败而非安全
            if not isinstance(result.get("is_spam"), bool):
                raise ValueError("Gemini 返回的 is_spam 字段不是布尔值")
            return result
        except Exception as e:
            print(f"Gemini analysis failed: {e}")
            # 审查失败时明确标记（reason 前缀 + analysis_failed 字段），避免静默放行
            return {"is_spam": False, "reason": f"ANALYSIS_FAILED: Gemini analysis failed: {e}", "analysis_failed": True}

    async def generate_verification_challenge(self) -> dict:
        model_name = await self._get_model_name('gemini_model_verification', 'gemini-2.5-flash-lite')
        # 出题 prompt 与 OpenAI 版共用同一常量（原先是两份逐字重复的三引号字符串）
        prompt = _verification_prompt()
        try:
            response = await self.client.aio.models.generate_content(
                model=model_name,
                contents=prompt
            )
            
            if response.candidates and response.candidates[0].content.parts:
                response_text = response.candidates[0].content.parts[0].text
            else:
                response_text = None
            
            if not response_text:
                raise ValueError("Gemini API返回空响应")
            
            return _challenge_from_data(_parse_json_response(response_text))
        except Exception as e:
            print(f"生成验证问题失败: {e}")
            return self._get_local_question()

    async def generate_unblock_question(self) -> dict:
        return await self.generate_verification_challenge()

    def _get_local_question(self) -> dict:
        # 统一走模块级兜底函数，避免依赖实例（无 API Key 时无需构造 provider）
        return _get_local_question()

    async def generate_autoreply(
        self, user_message: str, knowledge_base_content: str, personality_md: str = '',
    ) -> str:
        model_name = await self._get_model_name('gemini_model_autoreply', 'gemini-2.5-flash')
        if not knowledge_base_content or knowledge_base_content.strip() == "":
            return None

        # 空字符串元素 = 章节之间的空行；人设规则与分隔标记都取共用常量
        prompt_parts = [
            _build_autoreply_persona_rules(personality_md),
            "",
            AUTOREPLY_KB_LABEL,
            knowledge_base_content,
            "",
            AUTOREPLY_QUESTION_LABEL,
            user_message,
            "",
            AUTOREPLY_ANSWER_LABEL,
            AUTOREPLY_KB_MISS_TAIL
        ]

        try:
            response = await self.client.aio.models.generate_content(
                model=model_name,
                contents="\n".join(prompt_parts)
            )
            
            if not hasattr(response, 'candidates') or not response.candidates:
                return None

            if response.candidates and response.candidates[0].content.parts:
                response_text = response.candidates[0].content.parts[0].text
            else:
                response_text = None
            
            if not response_text:
                return None
            
            if _is_knowledge_miss(response_text):
                # 模型答不上来时不能什么都不发：把“管理员女仆长会来回复”这个承诺
                # 真正送到客人面前，否则客人只能看到消息被递送、却永远等不到回音。
                return KNOWLEDGE_MISS_REPLY
            
            return response_text.strip()
        except Exception as e:
            print(f"Gemini自动回复生成失败: {e}")
            return None
    
    async def ping(self, model: str) -> str:
        response = await self.client.aio.models.generate_content(model=model, contents=PING_PROMPT)
        return (getattr(response, 'text', None) or '').strip()

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


class OpenAIProvider(AIProvider):
    def __init__(self, api_key: str, base_url: str):
        self.api_key = api_key
        self.base_url = base_url or None
        # 空字符串交给 SDK 会被当成真实地址，留空时传 None 走官方默认地址
        self.client = AsyncOpenAI(api_key=api_key, base_url=base_url or None)

    async def analyze_message(self, text: str, image_bytes: bytes = None) -> dict:
        model_name = await self._get_model_name('openai_model_filter', 'gpt-4.1')
        messages = [
            {"role": "system", "content": CONTENT_REVIEW_SYSTEM_PROMPT},
            {"role": "user", "content": []}
        ]

        if text:
             messages[1]["content"].append({"type": "text", "text": text})
        
        if image_bytes:
             import base64
             base64_image = base64.b64encode(image_bytes).decode('utf-8')
             messages[1]["content"].append({
                "type": "image_url",
                "image_url": {
                    "url": f"data:image/jpeg;base64,{base64_image}"
                }
            })

        if not messages[1]["content"]:
             return {"is_spam": False, "reason": "No content to analyze"}

        try:
            response = await self.client.chat.completions.create(
                model=model_name,
                messages=messages,
                response_format={ "type": "json_object" }
            )
            
            response_text = response.choices[0].message.content
            if not response_text:
                 raise ValueError("OpenAI API returned an empty response.")

            result = json.loads(response_text)
            # 校验模型输出格式：is_spam 不是布尔值一律视为审查失败而非安全
            if not isinstance(result.get("is_spam"), bool):
                raise ValueError("OpenAI 返回的 is_spam 字段不是布尔值")
            return result
        except Exception as e:
            print(f"OpenAI analysis failed: {e}")
            # 审查失败时明确标记（reason 前缀 + analysis_failed 字段），避免静默放行
            return {"is_spam": False, "reason": f"ANALYSIS_FAILED: OpenAI analysis failed: {e}", "analysis_failed": True}

    async def generate_verification_challenge(self) -> dict:
        model_name = await self._get_model_name('openai_model_verification', 'gpt-4.1-mini')
        # 出题 prompt 与 Gemini 版共用同一常量（原先是两份逐字重复的三引号字符串）
        prompt = _verification_prompt()
        try:
            response = await self.client.chat.completions.create(
                model=model_name,
                messages=[{"role": "user", "content": prompt}]
            )
            response_text = response.choices[0].message.content
            
            return _challenge_from_data(_parse_json_response(response_text))
        except Exception as e:
            print(f"OpenAI Generate verification failed: {e}")
            return self._get_local_question()

    async def generate_unblock_question(self) -> dict:
        return await self.generate_verification_challenge()

    def _get_local_question(self) -> dict:
        # 统一走模块级兜底函数，避免依赖实例（无 API Key 时无需构造 provider）
        return _get_local_question()

    async def generate_autoreply(
        self, user_message: str, knowledge_base_content: str, personality_md: str = '',
    ) -> str:
        model_name = await self._get_model_name('openai_model_autoreply', 'gpt-4.1')
        if not knowledge_base_content or knowledge_base_content.strip() == "":
            return None

        # 人设规则与 Gemini 版共用同一常量（原先是两份独立的 prompt，且这里的
        # 三引号字符串把函数缩进 12/15 个空格原样带进了发给模型的 system message）
        system_prompt = _build_autoreply_persona_rules(personality_md)

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "system", "content": f"{AUTOREPLY_KB_LABEL}\n{knowledge_base_content}"},
            {"role": "user", "content": user_message}
        ]

        try:
            response = await self.client.chat.completions.create(
                model=model_name,
                messages=messages
            )
            response_text = response.choices[0].message.content
            
            if not response_text:
                return None
            
            if _is_knowledge_miss(response_text):
                # 模型答不上来时不能什么都不发：把“管理员女仆长会来回复”这个承诺
                # 真正送到客人面前，否则客人只能看到消息被递送、却永远等不到回音。
                return KNOWLEDGE_MISS_REPLY
            
            return response_text.strip()
        except Exception as e:
             print(f"OpenAI autoreply failed: {e}")
             return None
    
    async def ping(self, model: str) -> str:
        response = await self.client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": PING_PROMPT}],
        )
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

    def __init__(self, api_key: str, base_url: str = None):
        # 延迟导入：没装 anthropic 包时不影响 Gemini / OpenAI 两条链路
        from anthropic import AsyncAnthropic

        self.api_key = api_key
        self.base_url = (base_url or '').strip() or None
        client_kwargs = {'api_key': api_key}
        if self.base_url:
            client_kwargs['base_url'] = self.base_url
        self.client = AsyncAnthropic(**client_kwargs)

    async def _create_text(self, model: str, content, system: str = None, max_tokens: int = 4096) -> str:
        """发一次 Messages 请求，拼出全部 text 块；被安全策略拒绝时抛异常。"""
        kwargs = {
            'model': model,
            # 新模型默认开启自适应思考，思考也计入 max_tokens，给足空间避免截断
            'max_tokens': max_tokens,
            'messages': [{'role': 'user', 'content': content}],
        }
        if system:
            kwargs['system'] = system
        response = await self.client.messages.create(**kwargs)
        if response.stop_reason == 'refusal':
            raise RuntimeError('Claude 拒绝了这次请求（stop_reason=refusal）')
        return ''.join(
            block.text for block in response.content if getattr(block, 'type', None) == 'text'
        ).strip()

    async def analyze_message(self, text: str, image_bytes: bytes = None) -> dict:
        model_name = await self._get_model_name(
            'anthropic_model_filter', PROVIDERS['anthropic']['default_models']['filter'])
        content = []
        if image_bytes:
            import base64
            content.append({
                'type': 'image',
                'source': {
                    'type': 'base64',
                    'media_type': _sniff_image_media_type(image_bytes),
                    'data': base64.standard_b64encode(image_bytes).decode('utf-8'),
                },
            })
        if text:
            content.append({'type': 'text', 'text': text})
        if not content:
            return {"is_spam": False, "reason": "No content to analyze"}

        try:
            response_text = await self._create_text(model_name, content, system=CONTENT_REVIEW_SYSTEM_PROMPT)
            if not response_text:
                raise ValueError("Claude API returned an empty response.")
            result = _parse_json_response(response_text)
            # 校验模型输出格式：is_spam 不是布尔值一律视为审查失败而非安全
            if not isinstance(result.get("is_spam"), bool):
                raise ValueError("Claude 返回的 is_spam 字段不是布尔值")
            return result
        except Exception as e:
            print(f"Claude analysis failed: {e}")
            # 审查失败时明确标记（reason 前缀 + analysis_failed 字段），避免静默放行
            return {"is_spam": False, "reason": f"ANALYSIS_FAILED: Claude analysis failed: {e}", "analysis_failed": True}

    async def generate_verification_challenge(self) -> dict:
        model_name = await self._get_model_name(
            'anthropic_model_verification', PROVIDERS['anthropic']['default_models']['verification'])
        try:
            response_text = await self._create_text(model_name, _verification_prompt())
            return _challenge_from_data(_parse_json_response(response_text))
        except Exception as e:
            print(f"Claude 生成验证问题失败: {e}")
            return _get_local_question()

    async def generate_unblock_question(self) -> dict:
        return await self.generate_verification_challenge()

    async def generate_autoreply(
        self, user_message: str, knowledge_base_content: str, personality_md: str = '',
    ) -> str:
        model_name = await self._get_model_name(
            'anthropic_model_autoreply', PROVIDERS['anthropic']['default_models']['autoreply'])
        if not knowledge_base_content or knowledge_base_content.strip() == "":
            return None

        # 人设规则与知识库都是运营方内容，放进 system；客人的消息单独作为 user 轮
        system_prompt = "\n".join([
            _build_autoreply_persona_rules(personality_md),
            "",
            AUTOREPLY_KB_LABEL,
            knowledge_base_content,
        ])
        try:
            response_text = await self._create_text(model_name, user_message, system=system_prompt)
            if not response_text:
                return None
            if _is_knowledge_miss(response_text):
                # 答不上来时把「管理员女仆长会来回复」的承诺送到客人面前
                return KNOWLEDGE_MISS_REPLY
            return response_text
        except Exception as e:
            print(f"Claude 自动回复生成失败: {e}")
            return None

    async def ping(self, model: str) -> str:
        return await self._create_text(model, PING_PROMPT, max_tokens=1024)

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

    async def test_connection(self, provider_type: str, model: str = None) -> dict:
        """用自动回复模型（或指定模型）发一条极短请求，返回耗时与回复；失败直接抛异常。"""
        provider = self.get_provider_by_type(provider_type)
        if not provider:
            raise ValueError('请先配置该提供商的 API Key')
        if not model:
            model = await provider._get_model_name(
                model_setting_key(provider_type, 'autoreply'),
                PROVIDERS[provider_type]['default_models']['autoreply'],
            )
        started = time.monotonic()
        reply = await provider.ping(model)
        return {
            'model': model,
            'latency_ms': int((time.monotonic() - started) * 1000),
            'reply': (reply or '')[:200],
        }

ai_service = AIService()
