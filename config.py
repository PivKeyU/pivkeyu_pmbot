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


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    return int(raw.strip())


def _parse_bool(raw_value) -> bool:
    return str(raw_value).strip().lower() in ('1', 'true', 'yes', 'on')


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    return _parse_bool(raw)


def parse_config_value(raw_value, type_name: str, key: str = ''):
    """按元数据类型把字符串转成运行期值（Web 面板覆盖层共用）。"""
    if type_name == 'bool':
        return _parse_bool(raw_value)
    if type_name == 'int':
        return int(str(raw_value).strip() or 0)
    if type_name == 'list':
        return _parse_int_list(str(raw_value))
    text = str(raw_value).strip()
    if type_name == 'enum' or key in ('DECISION_PROTOCOL', 'VERIFICATION_MODE', 'TG_MONITOR_DEFAULT_SOURCE'):
        text = text.lower()
    if key in ('GEMINI_BASE_URL', 'ANTHROPIC_BASE_URL'):
        return text or None
    if key in ('DECISION_BASE_URL', 'MINIAPP_PUBLIC_URL', 'DRIVE_PUBLIC_URL'):
        return text.rstrip('/')
    if key == 'WEB_PANEL_PASSWORD_SHA256':
        return text.lower()
    return text


class Config:
    BOT_TOKEN = os.getenv('BOT_TOKEN')
    BOT_ID = None
    BOT_USERNAME = None
    FORUM_GROUP_ID = _env_int('FORUM_GROUP_ID', 0)
    ADMIN_IDS = _parse_int_list(os.getenv('ADMIN_IDS', ''))
    
    GEMINI_API_KEY = os.getenv('GEMINI_API_KEY')
    GEMINI_BASE_URL = (os.getenv('GEMINI_BASE_URL') or '').strip() or None
    
    OPENAI_API_KEY = os.getenv('OPENAI_API_KEY')
    OPENAI_BASE_URL = os.getenv('OPENAI_BASE_URL', 'https://api.openai.com/v1')

    ANTHROPIC_API_KEY = os.getenv('ANTHROPIC_API_KEY')
    ANTHROPIC_BASE_URL = (os.getenv('ANTHROPIC_BASE_URL') or '').strip() or None

    # 决策审查：独立于聊天模型的服务商，只用于内容审查；off 表示不启用。
    # 地址和模型都要自己填，官方或第三方中转都可以
    DECISION_PROTOCOL = os.getenv('DECISION_PROTOCOL', 'off').strip().lower()
    DECISION_API_KEY = os.getenv('DECISION_API_KEY', '').strip()
    DECISION_BASE_URL = os.getenv('DECISION_BASE_URL', '').strip().rstrip('/')
    DECISION_PATH = os.getenv('DECISION_PATH', '').strip()
    DECISION_MODEL = os.getenv('DECISION_MODEL', '').strip()
    DECISION_THRESHOLD = _env_int('DECISION_THRESHOLD', 50)
    
    ENABLE_AI_FILTER = _env_bool('ENABLE_AI_FILTER', True)
    
    VERIFICATION_ENABLED = _env_bool('VERIFICATION_ENABLED', True)
    # 首次验证方式：random（图片 / 文字随机）、text（常识文字题）、image（图片验证码）、
    # turnstile（Mini App 里的 Cloudflare Turnstile，未配置完整时退回 random）
    VERIFICATION_MODE = os.getenv('VERIFICATION_MODE', 'random').strip().lower()
    TURNSTILE_SITE_KEY = os.getenv('TURNSTILE_SITE_KEY', '').strip()
    TURNSTILE_SECRET_KEY = os.getenv('TURNSTILE_SECRET_KEY', '').strip()
    AUTO_UNBLOCK_ENABLED = _env_bool('AUTO_UNBLOCK_ENABLED', True)
    
    DATABASE_PATH = os.getenv('DATABASE_PATH', './data/bot.db')
    
    MAX_WORKERS = _env_int('MAX_WORKERS', 5)
    QUEUE_TIMEOUT = _env_int('QUEUE_TIMEOUT', 30)
    
    VERIFICATION_TIMEOUT = _env_int('VERIFICATION_TIMEOUT', 300)
    MAX_VERIFICATION_ATTEMPTS = _env_int('MAX_VERIFICATION_ATTEMPTS', 3)
    
    MAX_MESSAGES_PER_MINUTE = _env_int('MAX_MESSAGES_PER_MINUTE', 30)

    RSS_ENABLED = _env_bool('RSS_ENABLED', False)
    RSS_DATA_FILE = os.getenv('RSS_DATA_FILE', './data/rss_subscriptions.json')
    RSS_CHECK_INTERVAL = _env_int('RSS_CHECK_INTERVAL', 300)
    RSS_AUTHORIZED_USER_IDS = _parse_int_list(os.getenv('RSS_AUTHORIZED_USER_IDS', ''))

    SPAM_KEYWORD_FILTER_ENABLED = _env_bool('SPAM_KEYWORD_FILTER_ENABLED', False)
    SPAM_KEYWORD_AUTO_BLOCK = _env_bool('SPAM_KEYWORD_AUTO_BLOCK', True)

    TG_API_ID = os.getenv('TG_API_ID', '').strip()
    TG_API_HASH = os.getenv('TG_API_HASH', '').strip()
    TG_API_SESSION = os.getenv('TG_API_SESSION', '').strip()
    TG_PROXY = os.getenv('TG_PROXY', '').strip()
    TG_MONITOR_ENABLED = _env_bool('TG_MONITOR_ENABLED', True)
    TG_MONITOR_DEFAULT_SOURCE = os.getenv('TG_MONITOR_DEFAULT_SOURCE', 'user_session').strip().lower()
    TG_MONITOR_NOTIFY_CHAT_IDS = _parse_int_list(os.getenv('TG_MONITOR_NOTIFY_CHAT_IDS', ''))

    WEB_PANEL_ENABLED = _env_bool('WEB_PANEL_ENABLED', True)
    WEB_PANEL_HOST = os.getenv('WEB_PANEL_HOST', '127.0.0.1').strip()
    WEB_PANEL_PORT = _env_int('WEB_PANEL_PORT', 18080)
    WEB_PANEL_PASSWORD = os.getenv('WEB_PANEL_PASSWORD', '').strip()
    WEB_PANEL_PASSWORD_SHA256 = os.getenv('WEB_PANEL_PASSWORD_SHA256', '').strip().lower()
    WEB_PANEL_TRUST_PROXY = _env_bool('WEB_PANEL_TRUST_PROXY', False)

    # Mini App 挂在面板的 /miniapp/ 路径下，只带白名单接口；留空公网地址则不启动
    MINIAPP_PUBLIC_URL = os.getenv('MINIAPP_PUBLIC_URL', '').strip().rstrip('/')

    # 网盘：文件本体存在 Telegram（存储频道或管理员私聊），数据库只记元数据，面板对外提供直链
    DRIVE_ENABLED = _env_bool('DRIVE_ENABLED', True)
    DRIVE_STORAGE_CHAT_ID = _env_int('DRIVE_STORAGE_CHAT_ID', 0)
    DRIVE_PUBLIC_URL = os.getenv('DRIVE_PUBLIC_URL', '').strip().rstrip('/')
    DRIVE_AUTO_UPLOAD = _env_bool('DRIVE_AUTO_UPLOAD', True)
    DRIVE_MAX_UPLOAD_MB = _env_int('DRIVE_MAX_UPLOAD_MB', 20)
    DRIVE_CACHE_MAX_MB = _env_int('DRIVE_CACHE_MAX_MB', 1024)

    # 镜像更新：services/image_update.py 直接读 os.environ，
    # 这里登记一份让面板能统一管理（覆盖时会同步写入 os.environ）
    WATCHTOWER_HTTP_API_URL = os.getenv('WATCHTOWER_HTTP_API_URL', 'http://watchtower:8080').strip()
    WATCHTOWER_HTTP_API_TOKEN = os.getenv('WATCHTOWER_HTTP_API_TOKEN', '').strip()
    UPDATE_IMAGE_REPO = os.getenv('UPDATE_IMAGE_REPO', '').strip()
    UPDATE_IMAGE_TAG = os.getenv('UPDATE_IMAGE_TAG', '').strip()
    RUNNING_IN_DOCKER = os.getenv('RUNNING_IN_DOCKER', '').strip()

    def __init__(self):
        # Web 面板覆盖层：key -> 原始字符串值；_override_originals 记录被覆盖前的值以便还原
        self._env_overrides = {}
        self._override_originals = {}
        self._override_original_env = {}

    def apply_override(self, key: str, raw_value):
        """把 Web 面板提交的值热应用到运行中的配置。

        返回转换后的值；未知 key 抛 KeyError，值非法抛 ValueError。
        注意必须是实例方法：config 是单例实例，setattr 到类上会被
        实例属性遮蔽（setattr(self, ...) 才是调用方真正读到的值）。
        """
        meta = config_meta.META_BY_KEY.get(key)
        if not meta:
            raise KeyError(key)

        parsed = parse_config_value(raw_value, meta['type'], key=key)
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
        # 仅对非敏感项或标记 env_direct 的项同步 os.environ，避免将 DB 密钥暴露给子进程环境
        if not meta.get('secret') or meta.get('env_direct'):
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

    def validate(self):
        if not self.BOT_TOKEN:
            raise ValueError("BOT_TOKEN未设置")
        if not self.FORUM_GROUP_ID or not self.ADMIN_IDS:
            print("警告: FORUM_GROUP_ID 或 ADMIN_IDS 未设置。只有 /myid 与基础命令可用。")

config = Config()
