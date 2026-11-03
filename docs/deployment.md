# 部署指南

[← 返回 README](../README.md) · [文档目录](../README.md#-文档)

从零部署、更新、备份和上线检查。

> [!TIP]
> 推荐使用 Docker 部署，免去环境配置的麻烦。配置文件与数据均存放在宿主机，升级容器不丢数据。

## 环境要求

- Docker Engine / Docker Desktop
- Docker Compose v2
- 可用磁盘空间（SQLite 数据库 + 监控数据）

Telegram 侧需要先准备好：

| 前置 | 说明 |
| --- | --- |
| 🤖 Bot Token | 从 [@BotFather](https://t.me/BotFather) 创建机器人并获取 |
| 🗂️ 论坛话题群 | 一个**已开启 Topics** 的 Telegram 超级群组，机器人须设为该群管理员 |
| 🧑‍💼 管理员 ID | 管理员的 Telegram 用户 ID，多个用逗号分隔 |

## 三步启动

**① 准备配置**

创建部署目录，下载 Compose 文件和配置模板：

```bash
mkdir tg-bot-data && cd tg-bot-data
wget https://raw.githubusercontent.com/PivKeyU/pivkeyu_pmbot/main/docker-compose.yml
wget https://raw.githubusercontent.com/PivKeyU/pivkeyu_pmbot/main/.env.example -O .env
nano .env   # 填入 BOT_TOKEN、FORUM_GROUP_ID、ADMIN_IDS 等配置
```

下面三个是必填项，其余变量见 [配置参考](configuration.md)：

| 变量 | 说明 |
| --- | --- |
| `BOT_TOKEN` | Telegram Bot Token，从 [@BotFather](https://t.me/BotFather) 获取 |
| `FORUM_GROUP_ID` | 论坛话题群 ID（超级群组需开启 Topics，机器人须为该群管理员） |
| `ADMIN_IDS` | 管理员 Telegram 用户 ID，多个用逗号分隔（如 `123456789,987654321`） |

**② 启动容器**

```bash
docker compose up -d
```

**③ 确认运行**

```bash
docker compose ps
docker compose logs -f --tail=100
```

容器状态为 `running`、日志无报错即启动成功。随后私聊机器人发一条消息，完成验证后，消息会出现在论坛话题群里。

## 默认容器

`docker-compose.yml` 一键拉起：

```yaml
services:
  pivkeyu-pmbot:
    container_name: pivkeyu-pmbot
    image: pivkeyu/pivkeyu_pmbot:latest
    restart: unless-stopped
    env_file:
      - .env
    environment:
      - WEB_PANEL_HOST=0.0.0.0
    ports:
      - "127.0.0.1:18080:18080"
    volumes:
      - ./data:/app/data
```

| 配置项 | 值 |
| --- | --- |
| 服务名 / 容器名 | `pivkeyu-pmbot` |
| 镜像 | `pivkeyu/pivkeyu_pmbot:latest`（Docker Hub 公开镜像，amd64 + arm64） |
| 重启策略 | `restart: unless-stopped` |
| 配置来源 | `env_file: .env` |
| 数据卷 | `./data:/app/data` |
| 面板端口 | 只绑定宿主机本地 `127.0.0.1:18080` |

关键路径：

| 路径 | 用途 |
| --- | --- |
| `.env` | 全部配置（项目使用 `python-dotenv` 加载） |
| `./data` | 宿主机数据目录：SQLite 数据库、RSS 订阅、网络测试配置、监控数据 |
| `./data/bot.db` | SQLite 数据库文件（可用 `DATABASE_PATH` 覆盖） |
| `/app/data` | 容器内数据目录，由 `./data:/app/data` 挂载 |
| `watchtower/docker-compose.yml` | 配套 Watchtower 自动更新配置（含心跳与通知示例） |

> 配置文件与数据都存放在宿主机，升级或重建容器不会丢数据。
> 数据库已启用 WAL，**不要直接 `cp` 主库文件**做备份，请用：
>
> ```bash
> sqlite3 data/bot.db ".backup backup.db"
> ```

## 更新镜像

```bash
docker compose pull && docker compose up -d
```

**也可以远程更新**：配置 `WATCHTOWER_HTTP_API_URL` / `WATCHTOWER_HTTP_API_TOKEN` 后，在 Web 面板或 Mini App 的「更新部署」页更新，或者私聊机器人打开 `/manage`，点「检查镜像更新」再点「确认更新」。Watchtower 会拉取新镜像并重建容器，不用登录服务器。详见 [watchtower/README.md](../watchtower/README.md)（仓库提供 `watchtower/docker-compose.yml`，含 HTTP API 远程更新与 shoutrrr 通知配置示例）。

## 备选部署方式

<details>
<summary><strong>Docker Run 一行命令</strong></summary>

```bash
docker run -d \
  --name pivkeyu-pmbot \
  -v $(pwd)/.env:/app/.env \
  -v $(pwd)/data:/app/data \
  -p 127.0.0.1:18080:18080 \
  -e WEB_PANEL_HOST=0.0.0.0 \
  --restart unless-stopped \
  pivkeyu/pivkeyu_pmbot:latest
```

> **命令解析：** `-d` 后台运行；`--name` 指定容器名；两个 `-v` 分别挂载 `.env` 配置与 `data` 数据目录（持久化 SQLite 数据库与监控数据）；`-p 127.0.0.1:18080:18080` 与 `-e WEB_PANEL_HOST=0.0.0.0` 让面板只通过 Docker 宿主机本地访问；从其他设备管理时请使用 SSH 隧道，或在配置密码后接入 HTTPS 反向代理；`--restart unless-stopped` 容器退出自动重启；末尾为 Docker Hub 镜像名。

</details>

<details>
<summary><strong>手动部署（源码运行）</strong></summary>

```bash
git clone https://github.com/PivKeyU/pivkeyu_pmbot.git
cd pivkeyu_pmbot

# 创建并激活虚拟环境
python -m venv venv
source venv/bin/activate        # Linux/Mac
# venv\Scripts\activate         # Windows

pip install -r requirements.txt

# 配置环境变量
cp .env.example .env
nano .env

# 启动
python bot.py
```

> **注意：** `/upgrade` 安全更新基于 git 工作区，需要以 `git clone` 方式部署（而非直接下载压缩包）才能使用该功能。

</details>

## 数据与备份

| 项目 | 说明 |
| :--- | :--- |
| 存储引擎 | SQLite，路径 `./data/bot.db`（可由 `DATABASE_PATH` 覆盖），容器内为 `/app/data/bot.db` |
| 已启用的 PRAGMA | `journal_mode = WAL`、`synchronous = NORMAL`、`foreign_keys = ON`、`busy_timeout = 5000` |
| 备份方式 | ⚠️ **WAL 下不能直接 `cp` 主库文件**——最新数据可能还在 `-wal` 里，必须用 `.backup` |
| 目录位置 | ⚠️ WAL 依赖共享内存，**不适合 NFS / SMB 或 Docker Desktop 的 WSL2 / virtiofs bind mount**，这类环境请改用 **named volume** |

正确的备份命令：

```bash
sqlite3 data/bot.db ".backup backup.db"     # 生成一致性快照，可在运行中执行
```

相关配套：

- `.gitignore` 已排除 `data/*.db`、`data/*.db-journal`、`data/*.db-wal`、`data/*.db-shm`，WAL 的临时文件不会被误提交。
- 拦截篮 `filtered_messages` 有保留策略：`FILTERED_RETENTION_DAYS = 90` 天 + `FILTERED_MAX_ROWS = 20000` 行（按 `id` 排序删除，避免同秒时间戳排序不确定的问题）。
- 恢复演练：`.backup` 出来的库文件可直接替换主库（建议先停容器再替换，替换后一并清理遗留的 `-wal` / `-shm` 文件）。

## 生产部署清单

- [ ] `.env` 不进 Git（`.gitignore` 与 `.dockerignore` 均已排除），文件权限收紧
- [ ] `ADMIN_IDS` 只填可信管理员——该名单可以触发封禁、广播、`/upgrade`、镜像更新等操作
- [ ] `data/` 使用持久化挂载（`./data:/app/data`），SQLite 数据库、RSS 订阅与监控数据都在这里
- [ ] 备份使用 `sqlite3 data/bot.db ".backup backup.db"`，不要直接 `cp` 主库文件
- [ ] 数据库目录避开 NFS / SMB 与 Docker Desktop 的 WSL2 / virtiofs bind mount，改用 named volume
- [ ] `WATCHTOWER_HTTP_API_TOKEN` 用 `openssl rand -hex 32` 生成，与 `watchtower/docker-compose.yml` 填同一个值，**不要泄露、不要用弱口令**（它等于容器重建权限）
- [ ] 使用 Watchtower 时确认 `8080` 端口**不暴露到公网**（compose 用 `expose` 只在 Compose 网络内可见，不要改成 `ports`）
- [ ] `BOT_TOKEN` 一旦外泄，立即在 [@BotFather](https://t.me/BotFather) 用 `/revoke` 重置
- [ ] AI API Key 只授予必要额度；`GEMINI_BASE_URL` / `OPENAI_BASE_URL` 指向自己信任的网关
- [ ] 定期翻一遍 `/intercepts` 拦截篮与 `/manage` 黑名单，确认拦截策略没有误伤正常用户
- [ ] 定期实测一次恢复流程（用 `.backup` 的产物在旁路环境起一遍），确认备份真的可用
- [ ] `FORUM_GROUP_ID` 指向的超级群组已开启 Topics，且机器人在群内是**管理员**
- [ ] 确认 AI API Key 额度：无 Key 时人机验证走**本地题库**兜底，但 AI 内容审查会放行、知识库自动回复不会回复
- [ ] 源码部署用 `git clone`（而不是下载压缩包），否则 `/upgrade` 无法工作；纯镜像部署改用「更新部署」页
