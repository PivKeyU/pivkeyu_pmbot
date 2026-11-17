# 配置参考

[← 返回 README](../README.md) · [文档目录](../README.md#-文档)

所有配置通过 `.env` 文件加载（项目使用 `python-dotenv`）。以下变量与 `config.py` / `.env.example` 逐项核对列出，请按需填写。完整示例见 [.env.example](../.env.example)。

## 🎯 决策审查

内容审查可以改用只做判断的决策模型，服务商、API Key 和 Base URL 都独立于聊天模型。比如聊天用 Gemini，审查用 Inception 或第三方中转。

代码按**接口格式**适配，不内置任何地址。官方地址只是面板里的快速填入选项，第三方中转选它兼容的格式，再填自己的地址和模型名就行。

| 接口格式 | `DECISION_PROTOCOL` | 默认路径 | 官方示例 |
| :--- | :--- | :--- | :--- |
| Inception | `inception` | `/decisions` | `https://api.inceptionlabs.ai/v1`，`mercury-decide` |
| OpenAI | `openai` | `/decisions` | `https://api.openai.com/v1`，`gpt-6-luna` |
| System One（Jev） | `system_one` | `/systemone` | `https://api.typesafe.ai/v1`，`jev-latest` |
| Cloudflare Workers AI | `workers_ai` | `/ai/run/@cf/cloudflare/模型` | `https://api.cloudflare.com/client/v4/accounts/<账号ID>`，`clef` 或 `clef-flash` |

| 变量 | 必填 | 默认值 | 说明 |
| :--- | :---: | :--- | :--- |
| `DECISION_PROTOCOL` | ❌ | `off` | `off` 继续用聊天模型审查，其余值见上表 |
| `DECISION_API_KEY` | 启用时必填 | 空 | 决策服务的 API Key |
| `DECISION_BASE_URL` | 启用时必填 | 空 | 官方地址或第三方中转地址 |
| `DECISION_MODEL` | 启用时必填 | 空 | 模型名以服务商为准，中转的名字可能带前缀 |
| `DECISION_PATH` | ❌ | 空 | 中转服务路径不同时才需要填 |
| `DECISION_THRESHOLD` | ❌ | `50` | 违规概率达到这个百分比就拦截 |

也可以在「AI 设置 → 决策审查」里配置和测试，改完立即生效。

- 审查规则继续使用「提示词」页的内容
- 决策模型不写解释，拦截理由来自广告、诈骗、辱骂、色情、违法、其他六个固定类别
- 每条消息只问一道选择题，违规概率是「安全」以外所有选项的概率之和
- 「保存并测试」分别显示首次连接和复用连接的耗时，日常审查更接近后者；连接空闲 5 分钟或被服务商断开后，下一条消息要重新握手
- 决策接口出错或 10 秒内没有结果时，退回聊天模型再审一次，所以聊天协议的内容审查模型要填真实的聊天模型。两边都失败时消息会放行
- `mercury-decide`、`clef`、`clef-flash`、`jev-*` 不支持聊天接口，不能填在聊天模型里
- System One 格式只接受 PNG 和 JPEG，Workers AI 格式只接受 PNG、JPEG 和 WebP，其他图片会退回聊天模型审查

## 🤖 Bot / 管理员（必填）

| 变量 | 必填 | 默认值 | 说明 |
| :--- | :---: | :--- | :--- |
| `BOT_TOKEN` | ✅ | 无 | Telegram Bot Token，从 [@BotFather](https://t.me/BotFather) 获取 |
| `FORUM_GROUP_ID` | ✅ | 无 | 论坛话题群组 ID（超级群组需开启 Topics），机器人须为该群管理员；**未设置时私聊收发与核心管理功能禁用，仅 `/start`、`/myid`、网络测试等基础入口可用** |
| `ADMIN_IDS` | ✅ | 无 | 管理员 Telegram 用户 ID，多个用逗号分隔（如 `123456789,987654321`） |

## 🧠 AI

| 变量 | 必填 | 默认值 | 说明 |
| :--- | :---: | :--- | :--- |
| `GEMINI_API_KEY` | ❌ | 空 | Gemini API 密钥，从 [Google AI Studio](https://aistudio.google.com/api-keys) 获取；配置后启用 AI 审查/自动回复 |
| `GEMINI_BASE_URL` | ❌ | 空 | Gemini 自定义 Base URL（兼容网关/代理），留空使用官方接口 |
| `OPENAI_API_KEY` | ❌ | 空 | OpenAI 兼容协议密钥（OpenAI / DeepSeek / 通义 / Kimi / 智谱 / Ollama 等，可在面板中切换） |
| `OPENAI_BASE_URL` | ❌ | `https://api.openai.com/v1` | OpenAI 兼容服务地址，「AI 设置」里有常用服务预设 |
| `ANTHROPIC_API_KEY` | ❌ | 空 | Anthropic Claude 密钥（Messages 协议） |
| `ANTHROPIC_BASE_URL` | ❌ | 空 | Claude 自定义地址，留空使用官方接口；也可指向兼容 Anthropic 协议的服务 |
| `ENABLE_AI_FILTER` | ❌ | `true` | 是否启用 AI 内容审查 |

## 🔀 功能开关 / 数据库 / 队列

| 变量 | 必填 | 默认值 | 说明 |
| :--- | :---: | :--- | :--- |
| `VERIFICATION_ENABLED` | ❌ | `true` | 是否启用新用户人机验证 |
| `VERIFICATION_MODE` | ❌ | `random` | 验证方式：`random` 每次随机出图片验证码或文字题，`text` 只出文字题（AI 出题，失败时用本地题库与程序化题目），`image` 只出本地生成的图片验证码，`turnstile` 使用 Mini App 里的 Cloudflare Turnstile。配好 Turnstile 后 `random` 和 `turnstile` 都先走人机验证，没通过才出普通题；配置不完整时退回随机普通题 |
| `AUTO_UNBLOCK_ENABLED` | ❌ | `true` | 是否启用黑名单用户 AI 挑战自助解封 |
| `DATABASE_PATH` | ❌ | `./data/bot.db` | SQLite 数据库路径（容器内路径通常无需修改） |
| `MAX_WORKERS` | ❌ | `5` | 消息队列 Worker 数量。**预留配置，当前代码未使用** |
| `QUEUE_TIMEOUT` | ❌ | `30` | 队列消息超时时间（秒）。**预留配置，当前代码未使用** |

## ✅ 验证 / 限速

| 变量 | 必填 | 默认值 | 说明 |
| :--- | :---: | :--- | :--- |
| `VERIFICATION_TIMEOUT` | ❌ | `300` | 人机验证会话超时时间（秒） |
| `MAX_VERIFICATION_ATTEMPTS` | ❌ | `3` | 用户最大尝试验证次数，超限自动拉黑 |
| `MAX_MESSAGES_PER_MINUTE` | ❌ | `30` | 每用户每分钟最大消息数，收到警告后继续刷屏会永久拉黑 |

## 📡 TG 群/频道监听

以下为扩展配置，按需填写。

| 变量 | 必填 | 默认值 | 说明 |
| :--- | :---: | :--- | :--- |
| `TG_API_ID` | ❌ | 空 | Telegram API ID，从 https://my.telegram.org 获取，`user_session` 监听需要 |
| `TG_API_HASH` | ❌ | 空 | Telegram API Hash，同上 |
| `TG_API_SESSION` | ❌ | 空 | Telethon StringSession，**未配置时用户会话监听不会启动** |
| `TG_PROXY` | ❌ | 空 | 可选代理，如 `socks5://127.0.0.1:1080` 或 `http://127.0.0.1:7890` |
| `TG_MONITOR_ENABLED` | ❌ | `true` | 是否允许启动 TG 监听服务 |
| `TG_MONITOR_DEFAULT_SOURCE` | ❌ | `user_session` | 新监听默认来源：`user_session` 或 `bot` |
| `TG_MONITOR_NOTIFY_CHAT_IDS` | ❌ | 空 | TG 监听推送目标（逗号分隔），留空则使用 `ADMIN_IDS` |

## 🚫 关键词广告拦截

以下为扩展配置，按需填写。

| 变量 | 必填 | 默认值 | 说明 |
| :--- | :---: | :--- | :--- |
| `SPAM_KEYWORD_FILTER_ENABLED` | ❌ | `false` | 首次建库时决定是否默认启用关键词广告拦截；之后以 `/keywords` 或面板保存的运行时开关为准 |
| `SPAM_KEYWORD_AUTO_BLOCK` | ❌ | `true` | 命中后是否自动拉黑 |

运行时的关键词管理（命中消息会进入拦截篮，可用 `/intercepts` 查看）：

```bash
/keywords on
/keywords add 广告词1,广告词2
/keywords autoblock on    # 命中后自动拉黑
/keywords del 广告词1
/keywords                  # 查看当前设置
/keywords clear            # 清空全部关键词
```

## 📰 RSS

以下变量均已收录于 [.env.example](../.env.example) 末尾注释区，按需取消注释即可。

| 变量 | 必填 | 默认值 | 说明 |
| :--- | :---: | :--- | :--- |
| `RSS_ENABLED` | ❌ | `false` | 是否启用 RSS 轮询推送（也可运行时在 `/manage` → RSS 管理中开启） |
| `RSS_DATA_FILE` | ❌ | `./data/rss_subscriptions.json` | RSS 订阅数据文件 |
| `RSS_CHECK_INTERVAL` | ❌ | `300` | RSS 轮询间隔（秒），建议 ≥ 120 |
| `RSS_AUTHORIZED_USER_IDS` | ❌ | 空 | RSS 命令授权用户（逗号分隔），不填则仅 `ADMIN_IDS` 可用 |

## 🔔 Watchtower 通知钩子

可选。

| 变量 | 必填 | 默认值 | 说明 |
| :--- | :---: | :--- | :--- |
| `WATCHTOWER_NOTIFICATIONS` | ❌ | 空 | 使用 shoutrrr 作为统一通知系统，启用需去除 `#` 注释并设为 `shoutrrr` |
| `WATCHTOWER_NOTIFICATION_URL` | ❌ | 空 | 通知渠道钩子，如 `telegram://token@telegram?chats=channel-1[,chat-id-1,...]` |

## 🐳 镜像更新（HTTP API 远程更新）

仅在 Docker 部署 + [watchtower/docker-compose.yml](../watchtower/docker-compose.yml) 下使用，让机器人面板能远程触发拉镜像与重建容器。

| 变量 | 必填 | 默认值 | 说明 |
| :--- | :---: | :--- | :--- |
| `WATCHTOWER_HTTP_API_URL` | ❌ | `http://watchtower:8080` | Watchtower HTTP API 地址；用仓库的 compose 时按服务名访问，不用改 |
| `WATCHTOWER_HTTP_API_TOKEN` | ❌（使用 watchtower compose 时**必填**） | 空 | 与 compose 里 `WATCHTOWER_HTTP_API_TOKEN` 同值；用 `openssl rand -hex 32` 生成。**它等于容器重建权限，不要泄露、不要用弱口令** |
| `UPDATE_IMAGE` | ❌ | 空 | 组合覆盖要检查的镜像（格式 `repo:tag`，优先级高于下方仓库/标签两项） |
| `UPDATE_IMAGE_REPO` | ❌ | 空（回退 `pivkeyu/pivkeyu_pmbot`） | 覆盖要检查的镜像仓库名 |
| `UPDATE_IMAGE_TAG` | ❌ | 空（回退 `latest`） | 覆盖要检查的镜像 tag |
| `RUNNING_IN_DOCKER` | ❌ | 自动检测 | 非容器部署时设为 `1`，让面板显示「镜像更新」而非「Git 更新」 |

> `.env.example` 中 `UPDATE_IMAGE`、`UPDATE_IMAGE_REPO`、`UPDATE_IMAGE_TAG`、`RUNNING_IN_DOCKER` 默认是注释状态；不配 token 时面板的「检查镜像更新」仍可查看状态，但「确认更新」会提示没配口令。

## 🌐 Web 管理面板

面板默认开启且只监听本机，详见 [Web 管理面板](web-panel.md) 一节。这些变量也已收录于 `.env.example`。

| 变量 | 必填 | 默认值 | 说明 |
| :--- | :---: | :--- | :--- |
| `WEB_PANEL_ENABLED` | ❌ | `true` | 是否启动 Web 管理面板 |
| `WEB_PANEL_HOST` | ❌ | `127.0.0.1` | 监听地址；对外服务改为 `0.0.0.0`（必须同时设密码） |
| `WEB_PANEL_PORT` | ❌ | `18080` | 监听端口（Docker compose 默认绑定 `127.0.0.1:18080`，改端口需同步） |
| `WEB_PANEL_PASSWORD` | ❌ | 空 | 面板密码；留空 = 首次访问时在网页上初始化（15 分钟窗口），也可在此预设 |
| `WEB_PANEL_PASSWORD_SHA256` | ❌ | 空 | 密码的 SHA256 摘要（64 位十六进制），设置后优先于明文密码；登录后更改密码由面板自动维护 |
| `WEB_PANEL_TRUST_PROXY` | ❌ | `false` | 经 Nginx/Caddy 反代时设 `true`，登录限速按 `X-Forwarded-For` 识别来源 |
| `MINIAPP_PUBLIC_URL` | ❌ | 空 | 面板的 HTTPS 地址加 `/miniapp`，留空则不启用 Mini App |
| `TURNSTILE_SITE_KEY` | ❌ | 空 | Cloudflare Turnstile Site Key |
| `TURNSTILE_SECRET_KEY` | ❌ | 空 | Cloudflare Turnstile Secret Key |

## 🔑 获取必要信息

1. **Bot Token**：与 [@BotFather](https://t.me/BotFather) 对话，使用 `/newbot` 创建机器人即可获得。
2. **话题群组 ID**：创建超级群组并启用「话题」(Topics)，将机器人添加为管理员，在群组中发送 `/myid`，机器人会自动回复群组 ID。
3. **Gemini API 密钥**（可选）：访问 [Google AI Studio](https://aistudio.google.com/api-keys) 创建。
4. **Telethon StringSession**（可选，user_session 监听）：在 https://my.telegram.org 获取 API ID/Hash，再用任意 Telethon 工具生成 StringSession 填入 `TG_API_SESSION`。

## 🗺️ 功能启用速查表

| 想用哪个功能 | 需要哪些配置 | 之后用什么操作 |
| :--- | :--- | :--- |
| Web 管理面板 | 本机无需额外配置；远程访问需 SSH 隧道或预设密码并配置 HTTPS 反向代理 | 打开 `http://127.0.0.1:18080`，首次访问设置密码即可 |
| 双向聊天工单 | `BOT_TOKEN` + `FORUM_GROUP_ID` + `ADMIN_IDS` | 用户直接私聊即可 |
| AI 内容审查 | 至少一个 API Key + `ENABLE_AI_FILTER=true` | 无需操作，自动生效；面板可切换提供商/模型 |
| AI 人机验证 | 默认开启；无 Key 走本地题库 | 新用户自动触发 |
| AI 自助解封 | `AUTO_UNBLOCK_ENABLED=true` | 被拉黑用户发消息自动触发 |
| 关键词广告拦截 | 无需额外配置 | `/keywords on` → `/keywords add 广告词` |
| 审查通行证 | 无需额外配置 | `/pass <user_id> permanent\|temp <小时数>` |
| 分组与广播 | 无需额外配置 | `/groups create` → `/announce group <分组名> <内容>` |
| 网络测试 | 无需额外配置 | `/net_allow <user_id>` 授权 → `/net_server_add` 登记 → `/ping` |
| TG 监听（bot 模式） | 把 Bot 拉进目标群即可 | `/watch_tg add <名称> <chat_id> <关键词> bot` |
| TG 监听（user_session） | `TG_API_ID` + `TG_API_HASH` + `TG_API_SESSION` | `/watch_tg add <名称> <chat_id> <关键词> user_session` |
| 网页监控 | 无需额外配置 | `/watch_web add <名称> <url> [关键词]` |
| 补货监控 | 无需额外配置 | 面板「RSS / 监控 → 补货监控」，或 `/restock add <名称> <url> [商品关键词]` |
| Mini App | `MINIAPP_PUBLIC_URL`（https） | 管理员点聊天框的「管理面板」，或发 `/mobile` |
| Turnstile 人机验证 | Mini App 配置 + `TURNSTILE_SITE_KEY` + `TURNSTILE_SECRET_KEY` | 新用户自动触发 |
| RSS 推送 | `RSS_ENABLED=true`（或面板开启） | `/rss_add <url>` |
| 安全更新 | git 方式部署 | `/upgrade status` → `/upgrade apply` |
| 镜像更新（Docker） | `WATCHTOWER_HTTP_API_TOKEN` + watchtower 容器 | `/manage` → 安全更新/运行状态 → 检查镜像更新 → 确认更新 |
| Watchtower 自动更新 | 见 [watchtower/README.md](../watchtower/README.md) | `docker compose up -d` |
