# 常见问题

[← 返回 README](../README.md) · [文档目录](../README.md#-文档)

排查问题时先看这里。

<details>
<summary><strong>为什么管理员收不到用户消息？</strong></summary>

消息要经过「限速 → 黑名单 → 关键词拦截 → 人机验证 → AI 审查」多道关卡，任一关被拦下管理员就收不到：

1. **触发限速**：收到限速提醒后继续刷屏会被自动拉黑；
2. **被拉黑**：永久拉黑直接拒绝，临时拉黑需完成 AI 解封挑战；
3. **命中关键词广告拦截**：消息进入拦截篮，管理员可用 `/intercepts` 查看；
4. **未通过 AI 人机验证**：新用户首次发消息会收到验证题，答对后才递送；
5. **AI 审查判为垃圾**：同样进入拦截篮，不会转发到话题群。

</details>

<details>
<summary><strong>如何获取群组 ID？</strong></summary>

把机器人加为群组管理员，在群里发送 `/myid`，机器人会回复群组 ID 和用户 ID。

</details>

<details>
<summary><strong><code>user_session</code> 监听需要满足哪些条件？</strong></summary>

必须同时满足：

- Telethon 已安装（`requirements.txt` 自带）；
- `TG_API_ID`、`TG_API_HASH`、`TG_API_SESSION` 三项完整配置（缺任一项监听都不会启动）；
- `TG_MONITOR_ENABLED` 为 `true`；
- 至少存在一个启用中、来源为 `user_session` 的监听。

</details>

<details>
<summary><strong><code>/upgrade apply</code> 失败了怎么办？</strong></summary>

失败是保护机制生效：本地有未提交改动、本地分支领先远端、或没有可用的回滚点。先提交 / 清理本地改动再重试；`/upgrade rollback` 可回滚到上一次更新前。

如果机器人跑在 Docker 里、服务器上没有 git 仓库（例如镜像是 CI 构建后直接拉取的），`/upgrade` 会没有可用更新点。这种情况改用面板里的**「检查镜像更新」→「确认更新」**，由 Watchtower 拉新镜像并重建容器，详见 [watchtower/README.md](../watchtower/README.md)。

</details>

<details>
<summary><strong>验证失败被拉黑，还能解封吗？</strong></summary>

临时拉黑可重新发消息触发 AI 解封挑战自动开门；永久拉黑（管理员 `/ban`、关键词拦截自动拉黑、多次超限等）只能由管理员 `/unban` 解封。

</details>

<details>
<summary><strong>为什么 <code>/watch_tg discovered</code> 看不到我的论坛群？</strong></summary>

这是刻意设计：论坛话题群自身（`FORUM_GROUP_ID`）不会再被记录进发现列表，避免污染 `/watch_tg discovered`；其余群 / 频道照常被发现与监听。

</details>

<details>
<summary><strong>网页监控添加后为什么不推送？</strong></summary>

首次检查只建立基线、不推送已有内容；之后出现新条目、关键词命中或内容 / 价格 / 库存变化才会推送。可先用 `/watch_web run <ID>` 手动触发一次检查验证。

</details>

<details>
<summary><strong>RSS 命令没反应怎么办？</strong></summary>

RSS 命令仅限私聊使用，且只有 `ADMIN_IDS` 与 `RSS_AUTHORIZED_USER_IDS` 中的用户可用；另外 `RSS_ENABLED` 默认 `false`，未开启时不会轮询推送（可在 `/manage` → RSS 功能管理中开启）。

</details>

<details>
<summary><strong>不配置任何 API Key 能跑起来吗？</strong></summary>

可以。人机验证会自动使用内置本地题库兜底（AI 生成的题在无 Key 或调用失败时也会回退）；但 AI 内容审查会放行（`No AI provider configured`）、知识库自动回复不会回复。要获得完整的 AI 能力，至少配置一个 API Key。

</details>

<details>
<summary><strong>补货监控一直显示「未判断」怎么办？</strong></summary>

点「先试一次」看判断依据。常见原因：页面没有有货或缺货字样，可以填库存选择器或自定义字样；页面由浏览器脚本加载，或者网站有反爬验证，这类页面目前判断不了。

</details>

<details>
<summary><strong>Docker 升级容器会丢数据吗？</strong></summary>

不会。`.env` 配置与 `data` 目录均通过卷挂载在宿主机上（`-v ./data:/app/data`），SQLite 数据库、RSS 订阅、网络测试配置、监控数据都会保留。

</details>

<details>
<summary><strong>如何让用户使用网络测试功能？</strong></summary>

管理员执行 `/net_allow <user_id>` 把用户加入授权名单（数据保存在 `data/network_test_config.json`）；授权用户即可使用 `/ping` 与 `/trace`。移除授权用 `/net_deny <user_id>`。

</details>
