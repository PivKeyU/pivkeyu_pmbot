"""环境变量类配置的元数据注册表。

两处使用：
1. config.apply_override() 按类型把 Web 面板提交的字符串转成正确的 Python 类型；
2. Web 面板配置页渲染分组、控件、密钥掩码与「需重启」标记。

本模块必须保持纯数据、零依赖（不要 import config），否则会与 config.py 形成循环导入。

字段说明：
- key       环境变量名，同时也是 Config 类属性名
- type      bool / int / str / list(逗号分隔整数) / enum(需配 options)
- category  分组键，见 CATEGORIES
- label     中文名
- hint      说明文案
- default   与 .env.example 一致的默认值（字符串形式）
- restart   修改后是否需要重启进程才生效
- secret    是否敏感值（接口只回掩码，永不回显明文）
- env_direct 是否有代码绕过 config 直接读 os.environ（覆盖时同步写入 os.environ）
"""

CATEGORIES = [
    ('bot', '机器人'),
    ('ai', 'AI 能力'),
    ('security', '安全与验证'),
    ('runtime', '运行参数'),
    ('tg', 'Telegram 监听'),
    ('update', '更新部署'),
    ('web', 'Web 面板'),
]

META = [
    # --- 机器人 ---
    {
        'key': 'BOT_TOKEN', 'type': 'str', 'category': 'bot',
        'label': 'Bot Token', 'hint': 'BotFather 发放的机器人令牌，修改后需重启进程',
        'default': '', 'restart': True, 'secret': True,
    },
    {
        'key': 'FORUM_GROUP_ID', 'type': 'int', 'category': 'bot',
        'label': '论坛群组 ID', 'hint': '接收用户工单话题的超级群 ID（-100 开头）',
        'default': '0', 'restart': True,
    },
    {
        'key': 'ADMIN_IDS', 'type': 'list', 'category': 'bot',
        'label': '管理员用户 ID', 'hint': '逗号分隔，例如 123456789,987654321',
        'default': '', 'restart': False,
    },

    # --- AI 能力 ---
    {
        'key': 'GEMINI_API_KEY', 'type': 'str', 'category': 'ai',
        'label': 'Gemini API Key', 'hint': '修改后立即重建 AI 客户端',
        'default': '', 'restart': False, 'secret': True,
    },
    {
        'key': 'GEMINI_BASE_URL', 'type': 'str', 'category': 'ai',
        'label': 'Gemini 反代地址', 'hint': '留空使用官方地址；修改后立即重建 AI 客户端',
        'default': '', 'restart': False,
    },
    {
        'key': 'OPENAI_API_KEY', 'type': 'str', 'category': 'ai',
        'label': 'OpenAI API Key', 'hint': '修改后立即重建 AI 客户端',
        'default': '', 'restart': False, 'secret': True,
    },
    {
        'key': 'OPENAI_BASE_URL', 'type': 'str', 'category': 'ai',
        'label': 'OpenAI Base URL', 'hint': '兼容 OpenAI 协议的服务地址',
        'default': 'https://api.openai.com/v1', 'restart': False,
    },
    {
        'key': 'ENABLE_AI_FILTER', 'type': 'bool', 'category': 'ai',
        'label': '启用 AI 内容审查', 'hint': '关闭后不再对私聊消息做 AI 垃圾审查',
        'default': 'true', 'restart': False,
    },
    {
        'key': 'AI_CONFIDENCE_THRESHOLD', 'type': 'int', 'category': 'ai',
        'label': 'AI 判定阈值', 'hint': '置信度达到该值才判为垃圾消息（0-100）',
        'default': '70', 'restart': False, 'min': 0, 'max': 100,
    },

    # --- 安全与验证 ---
    {
        'key': 'VERIFICATION_ENABLED', 'type': 'bool', 'category': 'security',
        'label': '新用户人机验证', 'hint': '关闭后新用户无需答题即可发消息',
        'default': 'true', 'restart': False,
    },
    {
        'key': 'AUTO_UNBLOCK_ENABLED', 'type': 'bool', 'category': 'security',
        'label': '黑名单自助解封', 'hint': '被拉黑用户可答题自助解封',
        'default': 'true', 'restart': False,
    },
    {
        'key': 'VERIFICATION_TIMEOUT', 'type': 'int', 'category': 'security',
        'label': '验证超时（秒）', 'hint': '超时后验证会话失效',
        'default': '300', 'restart': False, 'min': 30,
    },
    {
        'key': 'MAX_VERIFICATION_ATTEMPTS', 'type': 'int', 'category': 'security',
        'label': '验证最大尝试次数', 'hint': '答错超过该次数将被拉黑',
        'default': '3', 'restart': False, 'min': 1, 'max': 10,
    },
    {
        'key': 'MAX_MESSAGES_PER_MINUTE', 'type': 'int', 'category': 'security',
        'label': '每分钟消息上限', 'hint': '触发限速后超速将进入黑名单流程',
        'default': '30', 'restart': True, 'min': 1,
    },

    # --- 运行参数 ---
    {
        'key': 'DATABASE_PATH', 'type': 'str', 'category': 'runtime',
        'label': '数据库路径', 'hint': 'SQLite 文件位置，修改后需重启进程',
        'default': './data/bot.db', 'restart': True,
    },
    {
        'key': 'MAX_WORKERS', 'type': 'int', 'category': 'runtime',
        'label': '工作协程数', 'hint': '预留参数，修改后需重启进程',
        'default': '5', 'restart': True, 'min': 1,
    },
    {
        'key': 'QUEUE_TIMEOUT', 'type': 'int', 'category': 'runtime',
        'label': '队列超时（秒）', 'hint': '预留参数，修改后需重启进程',
        'default': '30', 'restart': True, 'min': 1,
    },

    # --- Telegram 监听（Telethon 用户会话）---
    {
        'key': 'TG_API_ID', 'type': 'str', 'category': 'tg',
        'label': 'TG API ID', 'hint': 'my.telegram.org 申请，修改后需重启进程',
        'default': '', 'restart': True,
    },
    {
        'key': 'TG_API_HASH', 'type': 'str', 'category': 'tg',
        'label': 'TG API Hash', 'hint': '修改后需重启进程',
        'default': '', 'restart': True, 'secret': True,
    },
    {
        'key': 'TG_API_SESSION', 'type': 'str', 'category': 'tg',
        'label': 'TG 用户会话字符串', 'hint': 'Telethon StringSession，修改后需重启进程',
        'default': '', 'restart': True, 'secret': True,
    },
    {
        'key': 'TG_PROXY', 'type': 'str', 'category': 'tg',
        'label': 'TG 代理', 'hint': '如 socks5://127.0.0.1:1080，修改后需重启进程',
        'default': '', 'restart': True,
    },
    {
        'key': 'TG_MONITOR_ENABLED', 'type': 'bool', 'category': 'tg',
        'label': '启用 TG 监听', 'hint': '监听器在启动时创建，修改后需重启进程',
        'default': 'true', 'restart': True,
    },
    {
        'key': 'TG_MONITOR_DEFAULT_SOURCE', 'type': 'enum', 'category': 'tg',
        'label': '默认监听模式', 'hint': 'user_session 需要用户会话，bot 模式仅能看机器人可见的消息',
        'default': 'user_session', 'restart': False,
        'options': ['user_session', 'bot'],
    },
    {
        'key': 'TG_MONITOR_NOTIFY_CHAT_IDS', 'type': 'list', 'category': 'tg',
        'label': '监听推送目标', 'hint': '逗号分隔的 chat id，命中关键词后推送到这些会话',
        'default': '', 'restart': False,
    },

    # --- 更新部署 ---
    {
        'key': 'WATCHTOWER_HTTP_API_URL', 'type': 'str', 'category': 'update',
        'label': 'Watchtower API 地址', 'hint': '容器内默认 http://watchtower:8080',
        'default': 'http://watchtower:8080', 'restart': False, 'env_direct': True,
    },
    {
        'key': 'WATCHTOWER_HTTP_API_TOKEN', 'type': 'str', 'category': 'update',
        'label': 'Watchtower API 口令', 'hint': '能触发容器重建，请使用强随机值',
        'default': '', 'restart': False, 'secret': True, 'env_direct': True,
    },
    {
        'key': 'UPDATE_IMAGE_REPO', 'type': 'str', 'category': 'update',
        'label': '镜像仓库', 'hint': '留空则自动识别，如 pivkeyu/pivkeyu_pmbot',
        'default': '', 'restart': False, 'env_direct': True,
    },
    {
        'key': 'UPDATE_IMAGE_TAG', 'type': 'str', 'category': 'update',
        'label': '镜像标签', 'hint': '留空默认 latest',
        'default': '', 'restart': False, 'env_direct': True,
    },
    {
        'key': 'RUNNING_IN_DOCKER', 'type': 'str', 'category': 'update',
        'label': '容器部署标记', 'hint': '设为 1 时面板显示「镜像更新」而非「Git 更新」',
        'default': '', 'restart': False, 'env_direct': True,
    },

    # --- Web 面板 ---
    {
        'key': 'WEB_PANEL_ENABLED', 'type': 'bool', 'category': 'web',
        'label': '启用 Web 面板', 'hint': '关闭后需重启进程，面板不再监听端口',
        'default': 'true', 'restart': True,
    },
    {
        'key': 'WEB_PANEL_HOST', 'type': 'str', 'category': 'web',
        'label': '监听地址', 'hint': '127.0.0.1 仅本机；对外提供服务需改为 0.0.0.0 并设置密码',
        'default': '127.0.0.1', 'restart': True,
    },
    {
        'key': 'WEB_PANEL_PORT', 'type': 'int', 'category': 'web',
        'label': '监听端口', 'hint': '默认 18080，修改后需重启进程',
        'default': '18080', 'restart': True, 'min': 1, 'max': 65535,
    },
    {
        'key': 'WEB_PANEL_PASSWORD', 'type': 'str', 'category': 'web',
        'label': '面板密码', 'hint': '首次设置受 15 分钟窗口限制；登录后请在配置中心使用「修改面板密码」表单随时更改',
        'default': '', 'restart': False, 'secret': True,
    },
    {
        'key': 'WEB_PANEL_PASSWORD_SHA256', 'type': 'str', 'category': 'web',
        'label': '面板密码（SHA256）', 'hint': '由面板密码更改功能自动维护，不要手动编辑',
        'default': '', 'restart': False, 'secret': True,
    },
    {
        'key': 'WEB_PANEL_TRUST_PROXY', 'type': 'bool', 'category': 'web',
        'label': '信任反向代理', 'hint': '经 Nginx/Caddy 反代时开启，登录限速按 X-Forwarded-For 识别来源',
        'default': 'false', 'restart': False,
    },
]

META_BY_KEY = {item['key']: item for item in META}

CATEGORY_LABELS = dict(CATEGORIES)
