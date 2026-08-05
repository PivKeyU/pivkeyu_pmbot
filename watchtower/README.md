# 通过 Watchtower 自动更新本项目
1. 下载带有 Watchtower 配置的 docker-compose.yml
```bash
wget https://raw.githubusercontent.com/Hamster-Prime/Telegram_Anti-harassment_two-way_chatbot/main/watchtower/docker-compose.yml
```

2. 编辑 docker-compose.yml （可选）
```
# 只有自定义过容器名时，才需进行该操作。(Watchtower 以容器名作为监控对象)
nano docker-compose.yml
```

> **配置解析**
> - `--cleanup`: 更新容器镜像并重启容器成功后，自动删除旧镜像。
> - `--interval 3600`: 每隔 3600 秒（1 小时）检查一次镜像是否有更新。
> - `pivkeyu-pmbot`: 容器名，如果自定义过，记得修改。
> - `max-size`： 单个日志文件最大 10MB
> - `max-file`： 最多保留 3 个日志文件

3. 使用 Docker Compose 运行:
```bash
docker compose up -d
```

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

