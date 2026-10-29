"""AI 调用的可调参数：思考等级、输出 / 输入长度、超时、额外请求参数、可编辑提示词与功能开关。

全部存在 settings 表里，Web 面板「AI 设置」读写，调用模型时实时读取（改完立即生效）。
留空表示「用默认值 / 不向服务商发送该参数」，让各家模型按自己的默认行为工作。
"""

import json
from dataclasses import dataclass
from typing import Optional

from database import models as db
from services.ai_providers import AI_FEATURES, PROVIDER_IDS

# 思考等级：'' = 不发送（服务商默认），off = 尽量关闭思考，其余为强度
EFFORT_LEVELS = ('', 'off', 'low', 'medium', 'high')

# 各功能的默认超时（秒）：审查要快，自动回复可以慢一点
DEFAULT_TIMEOUTS = {'filter': 15, 'verification': 20, 'autoreply': 60}

DEFAULT_MAX_INPUT_CHARS = 2000
DEFAULT_MAX_KB_CHARS = 20000

PROMPT_MAX_CHARS = 20000

# ---- 可编辑提示词的默认值（固定的 JSON 输出格式不在这里，管理员改不坏解析） ----
DEFAULT_REVIEW_PROMPT = "\n".join([
    "你是一个内容审查员。你的任务是分析提供给你的文本和/或图片内容，判断其是否包含垃圾信息、"
    "恶意软件、钓鱼链接、不当言论、辱骂、攻击性词语或任何违反安全政策的内容。",
    "正常的聊天、提问、吐槽、表情包都应视为安全，不要因为语气随意就判定违规。",
])

DEFAULT_VERIFICATION_PROMPT = "\n".join([
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
    '1. 难度：绝大多数 18 岁以上、母语为中文的成年人都能立即答对，避免专业或冷门知识。',
    "2. 明确性：问题必须只有一个明确无误的正确答案。",
    "3. 对于标准问题，所有选项属于同一类别；对于反向排除问题（以下哪个不属于……），"
    "三个干扰项属于同一类别，正确答案是那个局外者。",
    "4. 所有内容必须为简体中文。",
])


@dataclass
class GenerationOptions:
    effort: str = ''
    max_tokens: Optional[int] = None
    timeout: float = 30.0
    max_input_chars: int = DEFAULT_MAX_INPUT_CHARS
    max_kb_chars: int = DEFAULT_MAX_KB_CHARS
    extra_body: Optional[dict] = None


def _key(name: str) -> str:
    return f'ai_{name}'


def _to_int(value, default=None, low=None, high=None):
    try:
        number = int(str(value).strip())
    except (TypeError, ValueError):
        return default
    if low is not None and number < low:
        return default
    if high is not None and number > high:
        number = high
    return number


def parse_extra_body(raw: str) -> Optional[dict]:
    """解析「额外请求参数」JSON；空字符串返回 None，非对象 JSON 抛 ValueError。"""
    raw = (raw or '').strip()
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f'额外请求参数不是合法 JSON：{exc.msg}（第 {exc.lineno} 行）') from exc
    if not isinstance(data, dict):
        raise ValueError('额外请求参数必须是 JSON 对象，例如 {"thinking": {"type": "disabled"}}')
    return data


async def _settings() -> dict:
    return await db.get_all_settings()


async def get_generation_options(feature: str, provider_type: str) -> GenerationOptions:
    settings = await _settings()
    effort = settings.get(_key(f'effort_{feature}'), '') or ''
    if effort not in EFFORT_LEVELS:
        effort = ''
    try:
        extra = parse_extra_body(settings.get(_key(f'extra_body_{provider_type}'), ''))
    except ValueError:
        extra = None
    return GenerationOptions(
        effort=effort,
        max_tokens=_to_int(settings.get(_key(f'max_tokens_{feature}')), None, low=1, high=200000),
        timeout=float(_to_int(settings.get(_key(f'timeout_{feature}')), DEFAULT_TIMEOUTS.get(feature, 30), low=3, high=600)),
        max_input_chars=_to_int(settings.get(_key('max_input_chars')), DEFAULT_MAX_INPUT_CHARS, low=100, high=200000),
        max_kb_chars=_to_int(settings.get(_key('max_kb_chars')), DEFAULT_MAX_KB_CHARS, low=500, high=500000),
        extra_body=extra,
    )


async def get_prompt(name: str) -> str:
    """name: review / verification；未自定义时返回默认提示词。"""
    default = DEFAULT_REVIEW_PROMPT if name == 'review' else DEFAULT_VERIFICATION_PROMPT
    custom = (await db.get_setting(_key(f'prompt_{name}'), '') or '').strip()
    return custom or default


async def get_switch(name: str, default: bool) -> bool:
    value = await db.get_setting(_key(name), None)
    if value is None or value == '':
        return default
    return str(value).strip().lower() in ('1', 'true', 'yes', 'on')


async def get_int(name: str, default: int, low: int = None, high: int = None) -> int:
    return _to_int(await db.get_setting(_key(name), None), default, low=low, high=high)


async def appeal_enabled() -> bool:
    return await get_switch('appeal_enabled', True)


async def auto_revoke_settings() -> tuple:
    """(是否开启, 触发次数, 统计窗口小时数)"""
    return (
        await get_switch('auto_revoke_enabled', False),
        await get_int('auto_revoke_threshold', 2, low=1, high=50),
        await get_int('auto_revoke_window_hours', 24, low=1, high=24 * 30),
    )


# --------------------------------------------------------------------------- #
# Web 面板读写
# --------------------------------------------------------------------------- #

async def dump_options() -> dict:
    settings = await _settings()
    return {
        'features': {
            feature: {
                'effort': settings.get(_key(f'effort_{feature}'), '') or '',
                'max_tokens': settings.get(_key(f'max_tokens_{feature}'), '') or '',
                'timeout': settings.get(_key(f'timeout_{feature}'), '') or '',
                'default_timeout': DEFAULT_TIMEOUTS[feature],
            }
            for feature in AI_FEATURES
        },
        'max_input_chars': settings.get(_key('max_input_chars'), '') or '',
        'max_kb_chars': settings.get(_key('max_kb_chars'), '') or '',
        'default_max_input_chars': DEFAULT_MAX_INPUT_CHARS,
        'default_max_kb_chars': DEFAULT_MAX_KB_CHARS,
        'extra_body': {p: settings.get(_key(f'extra_body_{p}'), '') or '' for p in PROVIDER_IDS},
        'prompts': {
            'review': settings.get(_key('prompt_review'), '') or '',
            'verification': settings.get(_key('prompt_verification'), '') or '',
        },
        'default_prompts': {
            'review': DEFAULT_REVIEW_PROMPT,
            'verification': DEFAULT_VERIFICATION_PROMPT,
        },
        'switches': {
            'appeal_enabled': await get_switch('appeal_enabled', True),
            'auto_revoke_enabled': await get_switch('auto_revoke_enabled', False),
            'auto_revoke_threshold': await get_int('auto_revoke_threshold', 2, low=1, high=50),
            'auto_revoke_window_hours': await get_int('auto_revoke_window_hours', 24, low=1, high=24 * 30),
        },
    }


def _check_int_text(value, label: str, low: int, high: int) -> str:
    text = str(value if value is not None else '').strip()
    if text == '':
        return ''
    number = _to_int(text)
    if number is None or number < low or number > high:
        raise ValueError(f'{label} 需要是 {low}~{high} 之间的整数，留空表示默认')
    return str(number)


async def update_options(data: dict) -> None:
    """校验并保存面板提交的参数；任何一项非法都整体拒绝。"""
    writes = {}
    for feature, values in (data.get('features') or {}).items():
        if feature not in AI_FEATURES or not isinstance(values, dict):
            raise ValueError(f'未知功能: {feature}')
        if 'effort' in values:
            effort = str(values.get('effort') or '')
            if effort not in EFFORT_LEVELS:
                raise ValueError(f'思考等级只能是 {", ".join(e or "默认" for e in EFFORT_LEVELS)}')
            writes[_key(f'effort_{feature}')] = effort
        if 'max_tokens' in values:
            writes[_key(f'max_tokens_{feature}')] = _check_int_text(values['max_tokens'], '最大输出 token', 16, 200000)
        if 'timeout' in values:
            writes[_key(f'timeout_{feature}')] = _check_int_text(values['timeout'], '超时秒数', 3, 600)
    if 'max_input_chars' in data:
        writes[_key('max_input_chars')] = _check_int_text(data['max_input_chars'], '输入字符上限', 100, 200000)
    if 'max_kb_chars' in data:
        writes[_key('max_kb_chars')] = _check_int_text(data['max_kb_chars'], '知识库字符上限', 500, 500000)
    for provider, raw in (data.get('extra_body') or {}).items():
        if provider not in PROVIDER_IDS:
            raise ValueError(f'未知提供商: {provider}')
        parse_extra_body(raw)
        writes[_key(f'extra_body_{provider}')] = (raw or '').strip()
    for name, text in (data.get('prompts') or {}).items():
        if name not in ('review', 'verification'):
            raise ValueError(f'未知提示词: {name}')
        text = str(text or '')
        if len(text) > PROMPT_MAX_CHARS:
            raise ValueError(f'提示词最多 {PROMPT_MAX_CHARS} 个字符')
        writes[_key(f'prompt_{name}')] = text.strip()
    switches = data.get('switches') or {}
    for name in ('appeal_enabled', 'auto_revoke_enabled'):
        if name in switches:
            writes[_key(name)] = '1' if switches[name] else '0'
    if 'auto_revoke_threshold' in switches:
        writes[_key('auto_revoke_threshold')] = _check_int_text(switches['auto_revoke_threshold'], '触发次数', 1, 50)
    if 'auto_revoke_window_hours' in switches:
        writes[_key('auto_revoke_window_hours')] = _check_int_text(switches['auto_revoke_window_hours'], '统计窗口小时数', 1, 720)

    for key, value in writes.items():
        await db.set_setting(key, value, 'Web 面板 AI 设置')
