# 通过 Watchtower 自动更新本项目
1. 下载带有 Watchtower 配置的 docker-compose.yml
```bash
wget https://raw.githubusercontent.com/PivKeyU/pivkeyu_pmbot/main/watchtower/docker-compose.yml
```

2. 在 `.env` 里设置 Watchtower 的 HTTP API 口令（**必做，否则 `docker compose up -d` 会直接报错退出**）
```bash
# 生成一个随机口令（不要用弱口令，这个口令等于容器重建权限）
openssl rand -hex 32

nano .env
# 把生成的值填给两项：
#   WATCHTOWER_HTTP_API_URL=http://watchtower:8080   （默认值，通常不用改）
#   WATCHTOWER_HTTP_API_TOKEN=<刚才生成的那串>
```

> **为什么是必填**：只要 HTTP API 能被请求（带上正确的 Token）就能触发镜像更新与容器重建。缺失时 Compose 用 `${WATCHTOWER_HTTP_API_TOKEN:?…}` 直接报错退出，避免静默起一个没有鉴权的更新接口。
> `env_file` 不会把变量喂给 Compose 的 `${}` 插值，所以 compose 里是显式用 `environment` + 插值取值的；这份 `.env` 必须是 Compose 能读到的那一份（默认是“第一个 `-f` 指定的 compose 文件所在目录”下的 `.env`）。
> 如果你在仓库根目录用 `-f watchtower/docker-compose.yml` 启动，Compose 会去 `watchtower/.env` 找 token；这种启动方式请额外加 `--env-file .env`，或者干脆把 compose 文件拷到部署目录（与本 README 第 1 步一致）。
> **不要泄露这个 Token**：拿到它就等于拿到“拉镜像 + 重建容器”的权限；不要写进公开仓库、截图或群聊。

3. 编辑 docker-compose.yml （可选）
```
# 只有自定义过容器名时，才需进行该操作。(Watchtower 以容器名作为监控对象)
nano docker-compose.yml
```

> **配置解析**
> - `--cleanup`: 更新容器镜像并重启容器成功后，自动删除旧镜像。
> - `--interval 3600`: 每隔 3600 秒（1 小时）检查一次镜像是否有更新。
> - `--http-api-update`: 开启 HTTP API 模式，暴露 `POST /v1/update` 接口，供机器人在面板里点「确认更新」时触发更新。
> - `--http-api-periodic-polls`: **不要删掉这一项**。启用 `--http-api-update` 会默认关闭周期性轮询，原来那条 `--interval 3600` 会静默失效；加上它才能“定时检查 + 手动触发”两者共存。
> - `expose: "8080"`: 只开放给同一个 Compose 网络里的服务（机器人按 `http://watchtower:8080` 访问），不发布到宿主机网卡。
> - `pivkeyu-pmbot`: 容器名，如果自定义过，记得修改。
> - `max-size`： 单个日志文件最大 10MB
> - `max-file`： 最多保留 3 个日志文件

4. 使用 Docker Compose 运行:
```bash
docker compose up -d
```

## HTTP API 模式：从机器人面板触发更新

除了每小时定时检查，机器人还能主动触发一次更新：在私聊里发 `/updatebot`（或打开 `/panel` → 运行状态 / 安全更新），点「检查镜像更新」查询是否有新镜像，再点「确认更新」请求 Watchtower 执行 `POST /v1/update`，拉镜像并重建容器。

需要手动验证接口时，因为它只 `expose` 在 Compose 网络内（宿主机上 `curl localhost:8080` 是不通的），得再从同一个网络里发一个请求：

```bash
# 网络名默认是 <项目目录名>_default，本仓库就是 watchtower_default
docker run --rm --network watchtower_default curlimages/curl \
  -H "Authorization: Bearer <你的 WATCHTOWER_HTTP_API_TOKEN>" \
  http://watchtower:8080/v1/update
```

> **注意**
> 1、`WATCHTOWER_HTTP_API_TOKEN` **必须设置**（见上面第 2 步），否则 `docker compose up -d` 会直接失败。
> 2、`command` 里的 `--http-api-periodic-polls` **不能删**：Watchtower 官方文档明确写了“启用 `--http-api-update` 会默认关闭周期性轮询（`--interval` / `--schedule`）”，删掉就等于只剩手动触发，定时自动更新静默消失。
> 3、这个 Token 等于容器重建权限，**不要泄露**、不要用 `123456` 这类弱口令；怀疑泄露就用 `openssl rand -hex 32` 重新生成，`.env` 与 compose 两边同步替换。
> 4、仓库里的 `expose: "8080"` 只对同一 Compose 网络生效；如果你自己改成了 `ports: 8080:8080`，就等于把“带 Token 即可重建容器”的接口挂到宿主机所有网卡上，不要这么做。
>
> 官方依据：[HTTP API Mode — containrrr/watchtower](https://containrrr.dev/watchtower/http-api-mode/)

# 在容器完成更新后，通过 Telegram 进行通知（可选）
> [!IMPORTANT]\
> 1、请注意区分 私聊机器人（Chatbot）和 Watchtower 通知机器人!  
> 2、请不要将私聊机器人的 Token 应用到 Watchtower 通知机器人！  
> 3、请新建一个机器人专用于通知用途！

### 一、在 .env 配置中，删除下列参数的#注释。
- WATCHTOWER_NOTIFICATIONS
- WATCHTOWER_NOTIFICATION_URL

```bash
nano .env
```

### 二、获取 BOT_TOKEN 和 CHAT_ID

#### BOT_TOKEN  
用 [BotFather](https://t.me/BotFather) 创建 bot 后收到的 Token，如：
```yml
123456789:ABCDEF_xxxxx-yyyy
```

#### CHAT_ID  
> [!NOTE]\
> 1、CHAT_ID 指向的是 Watchtower 通知应该发给谁，可以是 Telegram User ID / Channels ID / Channels Username(公开频道)  [^1][^2][^3]  
> 2、你可以填入多个 CHAT_ID，机器人将同时向这些 CHAT_ID 发送通知。[^4]

- 向 [@Getidsbot](https://t.me/getidsbot) 发送任意消息 ，可获取 Telegram User ID。
- 向 [@Getidsbot](https://t.me/getidsbot) 转发频道的任意消息，可获取 Channels ID 和 Username 。

```yml
# Telegram User ID
👤 You
ID: 123456789

# Telegram Channels ID / Username
💬 Origin chat
id: -1xxxxxx
username: xxxxx
```
正确格式：
```yml
# Telegram User ID
WATCHTOWER_NOTIFICATION_URL=telegram://123456789:ABCDEF_xxxxx-yyyy@telegram?chats=123456789

# Channels ID
WATCHTOWER_NOTIFICATION_URL=telegram://123456789:ABCDEF_xxxxx-yyyy@telegram?chats=-1xxxxxxx

# Channels Username
WATCHTOWER_NOTIFICATION_URL=telegram://123456789:ABCDEF_xxxxx-yyyy@telegram?chats=@username

# 多个 Chat_ID
WATCHTOWER_NOTIFICATION_URL=telegram://123456789:ABCDEF_xxxxx-yyyy@telegram?chats=123456789,-1xxxxxxx,@username
```

### 三、启用 
```bash
docker compose up -d
```
如果你的配置正确，你将会收到一条来自 Watchtower 通知机器人的消息。
<img width="956" height="250" alt="CleanShot " src="https://github.com/user-attachments/assets/e3dc9cbc-2de8-4a07-934f-3eca289c0e63" />

[^1]: 你需要将 Watchtower 通知机器人添加到频道，并提拔为管理员。
[^2]: 如果你希望通过私聊收到通知，那么应该填写你的 Telegram USER ID  
[^3]: 如果你希望创建一个频道，把通知发在频道里，那么应该填写 Channels ID / Username（例如： -1xxxxxxxx / @xxxxx） 
[^4]: telegram://token@telegram?chats=@channel-1[,chat-id-1,...]

