# 运维工具

[← 返回 README](../README.md) · [文档目录](../README.md#-文档)

网络测试、分组广播、更新和重启。

## 🌐 网络测试（SSH Ping / NextTrace）

通过 SSH 在远程服务器上执行 Ping 与 NextTrace 路由追踪（ICMP / TCP），授权用户可用。

```bash
/net_allow 123456789          # 管理员：授权用户使用网络测试
/net_server_add                  # 管理员：启动 5 步登记向导（或 /net_server_add "香港 - GCP" 1.2.3.4 22 user pass）
/trace_install          # 管理员：为服务器安装 NextTrace

/ping example.com 4         # 授权用户：Ping 测试（选择服务器）
/trace example.com      # 授权用户：路由追踪（选择 ICMP / TCP 模式）
```

> 目标地址经过严格校验（`validate_target` + `shlex.quote`），防止 SSH 命令注入；服务器凭据保存在 `data/network_test_config.json`。

## 📢 分组与广播

```bash
/groups create 高优用户 重要客户分组
/groups add 高优用户 123456789        # 在用户话题中可省略 user_id
/groups members 高优用户
/groups list

/announce all 大家好，系统维护公告
/announce group 高优用户 针对高优用户的通知
```

分组也可以在面板里管理：用户列表勾选多人后一次加入多个分组，分组成员弹窗支持搜索多选。广播范围还可以选用户标签。

也可以**回复**一条文本/图片/视频/文件消息后执行 `/announce all` 或 `/announce group <分组名>`，广播后编辑源消息可同步更新已投递内容。

## 🔧 安全更新与镜像更新

最省事的入口是 Web 面板和 Mini App 的「更新部署」页：它显示当前版本和最新版本，并按部署方式提供更新按钮。下面是机器人里的操作方式。

`/upgrade` 走 **git 部署**路线：

```bash
/upgrade status           # 检查 git 状态（分支、领先/落后、脏工作区）
/upgrade apply            # 执行 ff-only 更新
/upgrade rollback         # 回滚到上一次更新前
```

**镜像更新**走 **Docker 部署**路线，全程在面板中完成：`/manage` → 安全更新 / 运行状态 → **「检查镜像更新」→「确认更新」**，由 Watchtower 的 HTTP API 拉取新镜像并重建容器，无需登录服务器。

| 更新方式 | 适用场景 | 操作入口 | 依赖 |
| :--- | :--- | :--- | :--- |
| Git 更新 | 仓库部署，服务器上有 git 工作区 | `/upgrade status` → `/upgrade apply` | 本地工作区干净、远端可快进 |
| 镜像更新 | 机器人跑在 Docker 里，服务器上**没有 git 仓库**（例如镜像由 CI 构建后直接拉取） | `/manage` → 检查镜像更新 → 确认更新 | `WATCHTOWER_HTTP_API_URL` + `WATCHTOWER_HTTP_API_TOKEN` + Watchtower 容器 |

> `/upgrade apply` 会拒绝覆盖本地未提交改动，只执行 `ff-only` 更新；更新完成后发 `/restart` 重启 Bot。
>
> 面板中的「检查镜像更新」即使未配置 token 也能查看状态，但「确认更新」会提示没配口令。镜像更新的配置详见 `.env.example` 与 [watchtower/README.md](../watchtower/README.md)。

## ⏰ 自动更新

机器人启动时和每 6 小时检测一次新版本，发现后通知管理员，概览页也会提示。

默认只检测和提醒，**不会自动更新**。在「更新部署」打开「自动更新」并确认同意后才生效，同时可以设定每天的更新时间和时区，默认 UTC+8 的 04:00。

- Docker 部署由 Watchtower 拉取新镜像并重建容器，需要配置 `WATCHTOWER_HTTP_API_TOKEN`；源码部署执行 `ff-only` 更新后重启
- 只在设定时间起的 1 小时内更新。错过就等第二天，不会在白天突然重建容器
- 有广播正在发送时会等待，每天最多尝试一次，失败时通知管理员
- 可以随时关闭，关闭后立即停止自动更新

## 🔁 重启机器人

管理员可以发 `/restart`，或在网页版、Mini App 的「配置中心」点「重启机器人」。重启前会用当前保存的 `BOT_TOKEN` 向 Telegram 验证，Token 无效或连不上 Telegram 时拒绝重启；有广播正在发送时也会拒绝。

重启通过替换当前进程完成，Docker 和源码部署都能用，完成后机器人会通知发起人。网页版和 Mini App 的登录状态保存在内存里，重启后要重新打开或登录。

> [!WARNING]
> 预检只覆盖 `BOT_TOKEN`。其他配置错误仍可能导致启动失败，此时机器人和面板都打不开，需要回服务器修复。

## 🎛️ 女仆长面板 /manage

面板一站式管理：黑名单、客人名册、拦截消息篮、自动回复女仆、审查通行证、网络测试茶具、广播与分组、RSS 订阅、TG 监听、网页监控、关键词拦截、运行状态、安全更新、**AI 模型衣柜**（分别配置 Gemini / OpenAI / Claude 的内容审查、验证题生成、自动回复模型）。
