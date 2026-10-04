import os
from dotenv import load_dotenv

import config_meta

load_dotenv()


def _parse_int_list(raw_value: str):
    values = []
    for item in (raw_value or '').split(','):
        item = item.strip()
        if not item:
            continue
        values.append(int(item))
    return values


def _parse_bool(raw_value) -> bool:
    return str(raw_value).strip().lower() in ('1', 'true', 'yes', 'on')


def parse_config_value(raw_value, type_name: str):
    """按元数据类型把字符串转成运行期值（Web 面板覆盖层共用）。"""
    if type_name == 'bool':
        return _parse_bool(raw_value)
    if type_name == 'int':
        return int(str(raw_value).strip() or 0)
    if type_name == 'list':
        return _parse_int_list(str(raw_value))
    return str(raw_value).strip()


class Config:
    BOT_TOKEN = os.getenv('BOT_TOKEN')
    BOT_ID = None
    BOT_USERNAME = None
    FORUM_GROUP_ID = int(os.getenv('FORUM_GROUP_ID') or 0)
    ADMIN_IDS = _parse_int_list(os.getenv('ADMIN_IDS', ''))
    
    GEMINI_API_KEY = os.getenv('GEMINI_API_KEY')
    GEMINI_BASE_URL = (os.getenv('GEMINI_BASE_URL') or '').strip() or None
    
    OPENAI_API_KEY = os.getenv('OPENAI_API_KEY')
    OPENAI_BASE_URL = os.getenv('OPENAI_BASE_URL', 'https://api.openai.com/v1')
    
    ENABLE_AI_FILTER = os.getenv('ENABLE_AI_FILTER', 'true').lower() == 'true'
    AI_CONFIDENCE_THRESHOLD = int(os.getenv('AI_CONFIDENCE_THRESHOLD', '70'))
    
    VERIFICATION_ENABLED = os.getenv('VERIFICATION_ENABLED', 'true').lower() == 'true'
    AUTO_UNBLOCK_ENABLED = os.getenv('AUTO_UNBLOCK_ENABLED', 'true').lower() == 'true'
    
    DATABASE_PATH = os.getenv('DATABASE_PATH', './data/bot.db')
    
    MAX_WORKERS = int(os.getenv('MAX_WORKERS', '5'))
    QUEUE_TIMEOUT = int(os.getenv('QUEUE_TIMEOUT', '30'))
    
    VERIFICATION_TIMEOUT = int(os.getenv('VERIFICATION_TIMEOUT', '300'))
    MAX_VERIFICATION_ATTEMPTS = int(os.getenv('MAX_VERIFICATION_ATTEMPTS', '3'))
    
    MAX_MESSAGES_PER_MINUTE = int(os.getenv('MAX_MESSAGES_PER_MINUTE', '30'))

    RSS_ENABLED = os.getenv('RSS_ENABLED', 'false').lower() == 'true'
    RSS_DATA_FILE = os.getenv('RSS_DATA_FILE', './data/rss_subscriptions.json')
    RSS_CHECK_INTERVAL = int(os.getenv('RSS_CHECK_INTERVAL', '300'))
    RSS_AUTHORIZED_USER_IDS = _parse_int_list(os.getenv('RSS_AUTHORIZED_USER_IDS', ''))

    SPAM_KEYWORD_FILTER_ENABLED = os.getenv('SPAM_KEYWORD_FILTER_ENABLED', 'false').lower() == 'true'
    SPAM_KEYWORD_AUTO_BLOCK = os.getenv('SPAM_KEYWORD_AUTO_BLOCK', 'true').lower() == 'true'

    TG_API_ID = os.getenv('TG_API_ID', '').strip()
    TG_API_HASH = os.getenv('TG_API_HASH', '').strip()
    TG_API_SESSION = os.getenv('TG_API_SESSION', '').strip()
    TG_PROXY = os.getenv('TG_PROXY', '').strip()
    TG_MONITOR_ENABLED = os.getenv('TG_MONITOR_ENABLED', 'true').lower() == 'true'
    TG_MONITOR_DEFAULT_SOURCE = os.getenv('TG_MONITOR_DEFAULT_SOURCE', 'user_session').strip().lower()
    TG_MONITOR_NOTIFY_CHAT_IDS = _parse_int_list(os.getenv('TG_MONITOR_NOTIFY_CHAT_IDS', ''))

    WEB_PANEL_ENABLED = os.getenv('WEB_PANEL_ENABLED', 'true').lower() == 'true'
    WEB_PANEL_HOST = os.getenv('WEB_PANEL_HOST', '127.0.0.1').strip()
    WEB_PANEL_PORT = int(os.getenv('WEB_PANEL_PORT', '18080'))
    WEB_PANEL_PASSWORD = os.getenv('WEB_PANEL_PASSWORD', '').strip()
    WEB_PANEL_PASSWORD_SHA256 = os.getenv('WEB_PANEL_PASSWORD_SHA256', '').strip().lower()
    WEB_PANEL_TRUST_PROXY = os.getenv('WEB_PANEL_TRUST_PROXY', 'false').lower() == 'true'

    # 镜像更新：services/image_update.py 直接读 os.environ，
    # 这里登记一份让面板能统一管理（覆盖时会同步写入 os.environ）
    WATCHTOWER_HTTP_API_URL = os.getenv('WATCHTOWER_HTTP_API_URL', 'http://watchtower:8080').strip()
    WATCHTOWER_HTTP_API_TOKEN = os.getenv('WATCHTOWER_HTTP_API_TOKEN', '').strip()
    UPDATE_IMAGE_REPO = os.getenv('UPDATE_IMAGE_REPO', '').strip()
    UPDATE_IMAGE_TAG = os.getenv('UPDATE_IMAGE_TAG', '').strip()
    RUNNING_IN_DOCKER = os.getenv('RUNNING_IN_DOCKER', '').strip()

    # Web 面板覆盖层：key -> 原始字符串值；_override_originals 记录被覆盖前的值以便还原
    _env_overrides = {}
    _override_originals = {}
    _override_original_env = {}

    def apply_override(self, key: str, raw_value):
        """把 Web 面板提交的值热应用到运行中的配置。

        返回转换后的值；未知 key 抛 KeyError，值非法抛 ValueError。
        注意必须是实例方法：config 是单例实例，setattr 到类上会被
        实例属性遮蔽（setattr(self, ...) 才是调用方真正读到的值）。
        """
        meta = config_meta.META_BY_KEY.get(key)
        if not meta:
            raise KeyError(key)

        parsed = parse_config_value(raw_value, meta['type'])
        if meta['type'] == 'enum' and meta.get('options') and parsed not in meta['options']:
            raise ValueError(f"{key} 只允许: {', '.join(meta['options'])}")
        if meta['type'] == 'int':
            if 'min' in meta and parsed < meta['min']:
                raise ValueError(f"{key} 不能小于 {meta['min']}")
            if 'max' in meta and parsed > meta['max']:
                raise ValueError(f"{key} 不能大于 {meta['max']}")

        if key not in self._override_originals:
            self._override_originals[key] = getattr(self, key, None)
            self._override_original_env[key] = os.environ.get(key)

        setattr(self, key, parsed)
        self._env_overrides[key] = str(raw_value)
        # 部分模块（如镜像更新）绕过 config 直接读 os.environ，同步写入保持一致
        os.environ[key] = str(raw_value)
        return parsed

    def clear_override(self, key: str) -> bool:
        """撤销覆盖，还原到 .env / 默认值。返回是否确实存在覆盖。"""
        existed = key in self._env_overrides
        self._env_overrides.pop(key, None)
        if key in self._override_originals:
            setattr(self, key, self._override_originals.pop(key))
            original_env = self._override_original_env.pop(key, None)
            if original_env is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = original_env
        return existed

    def has_override(self, key: str) -> bool:
        return key in self._env_overrides

    def get_env_value(self, key: str):
        """读取覆盖前的 .env / 默认值，供配置页展示「原始值」。"""
        if key in self._override_originals:
            return self._override_originals[key]
        return getattr(self, key, None)

    async def load_overrides(self):
        """启动时从 settings 表加载覆盖层（在数据库初始化之后调用）。"""
        from database import models as db

        rows = await db.get_all_settings()
        applied = []
        for key, value in rows.items():
            if key not in config_meta.META_BY_KEY:
                continue
            try:
                self.apply_override(key, value)
                applied.append(key)
            except (ValueError, TypeError) as exc:
                print(f"忽略非法配置覆盖 {key}={value!r}: {exc}")
        if applied:
            print(f"已从数据库加载 {len(applied)} 项配置覆盖: {', '.join(sorted(applied))}")
        return applied

    @classmethod
    def validate(cls):
        if not cls.BOT_TOKEN:
            raise ValueError("BOT_TOKEN未设置")
        if not cls.FORUM_GROUP_ID or not cls.ADMIN_IDS:
            print("警告: FORUM_GROUP_ID 或 ADMIN_IDS 未设置。只有 /getid 功能可用。")

config = Config()
