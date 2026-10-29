"""AI 提供商注册表（纯数据、零依赖）。

Web 面板、Telegram 面板、配置中心与本地预览服务器都从这里取协议列表；
运行时逻辑（建客户端、调用模型）在 services/ai_service.py。
"""

# 新增一种协议只需要：在这里登记一项 + 在 services/ai_service.py 写一个 AIProvider 子类
# 并挂进 PROVIDER_CLASSES。
AI_FEATURES = ('filter', 'verification', 'autoreply')

PROVIDERS = {
    'gemini': {
        'label': 'Google Gemini',
        'short': 'Gemini',
        'protocol': 'Google GenAI 原生协议',
        'key_env': 'GEMINI_API_KEY',
        'base_env': 'GEMINI_BASE_URL',
        'default_models': {
            'filter': 'gemini-2.5-flash',
            'verification': 'gemini-2.5-flash-lite',
            'autoreply': 'gemini-2.5-flash',
        },
        'presets': [
            {'label': 'Google 官方', 'url': ''},
        ],
    },
    'openai': {
        'label': 'OpenAI 兼容',
        'short': 'OpenAI',
        'protocol': 'OpenAI Chat Completions 协议（绝大多数国内外模型服务都兼容）',
        'key_env': 'OPENAI_API_KEY',
        'base_env': 'OPENAI_BASE_URL',
        'default_models': {
            'filter': 'gpt-4.1',
            'verification': 'gpt-4.1-mini',
            'autoreply': 'gpt-4.1',
        },
        'presets': [
            {'label': 'OpenAI 官方', 'url': 'https://api.openai.com/v1'},
            {'label': 'DeepSeek 深度求索', 'url': 'https://api.deepseek.com/v1'},
            {'label': '阿里云百炼 · 通义千问', 'url': 'https://dashscope.aliyuncs.com/compatible-mode/v1'},
            {'label': 'Moonshot · Kimi', 'url': 'https://api.moonshot.cn/v1'},
            {'label': '智谱 · GLM', 'url': 'https://open.bigmodel.cn/api/paas/v4'},
            {'label': '火山方舟 · 豆包', 'url': 'https://ark.cn-beijing.volces.com/api/v3'},
            {'label': '硅基流动 SiliconFlow', 'url': 'https://api.siliconflow.cn/v1'},
            {'label': 'OpenRouter', 'url': 'https://openrouter.ai/api/v1'},
            {'label': 'Google Gemini（OpenAI 兼容端点）', 'url': 'https://generativelanguage.googleapis.com/v1beta/openai/'},
            {'label': 'xAI · Grok', 'url': 'https://api.x.ai/v1'},
            {'label': 'Groq', 'url': 'https://api.groq.com/openai/v1'},
            {'label': 'Mistral', 'url': 'https://api.mistral.ai/v1'},
            {'label': 'Ollama（本机）', 'url': 'http://localhost:11434/v1'},
            {'label': 'Ollama（Docker 访问宿主机）', 'url': 'http://host.docker.internal:11434/v1'},
            {'label': 'LM Studio（本机）', 'url': 'http://localhost:1234/v1'},
        ],
    },
    'anthropic': {
        'label': 'Anthropic Claude',
        'short': 'Claude',
        'protocol': 'Anthropic Messages 协议',
        'key_env': 'ANTHROPIC_API_KEY',
        'base_env': 'ANTHROPIC_BASE_URL',
        'default_models': {
            'filter': 'claude-opus-5-5',
            'verification': 'claude-opus-5-5',
            'autoreply': 'claude-opus-5-5',
        },
        'presets': [
            {'label': 'Anthropic 官方', 'url': ''},
            {'label': 'DeepSeek（Anthropic 兼容端点）', 'url': 'https://api.deepseek.com/anthropic'},
            {'label': 'Moonshot · Kimi（Anthropic 兼容端点）', 'url': 'https://api.moonshot.cn/anthropic'},
            {'label': '智谱 · GLM（Anthropic 兼容端点）', 'url': 'https://open.bigmodel.cn/api/anthropic'},
        ],
    },
}

PROVIDER_IDS = tuple(PROVIDERS)


def model_setting_key(provider_type: str, feature: str) -> str:
    """settings 表里某个协议某项功能的模型键，例如 anthropic_model_filter。"""
    return f'{provider_type}_model_{feature}'


def all_model_setting_keys() -> tuple:
    return tuple(model_setting_key(p, f) for p in PROVIDER_IDS for f in AI_FEATURES)
