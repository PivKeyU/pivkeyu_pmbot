# 命令参考

[← 返回 README](../README.md) · [文档目录](../README.md#-文档)

普通用户的菜单只显示 `/start`、`/help`、`/myid`，管理员的私聊和论坛群显示完整菜单。发 `/help 命令名` 看用法，私聊里打错命令会提示最接近的命令。

> 命令菜单在启动时自动同步到 Telegram，内容来自 `utils/commands.py`。改名后的旧命令继续可用，但不再显示在菜单里。
>
> 管理员功能需要同时设置 `FORUM_GROUP_ID` 和 `ADMIN_IDS`。

## 👤 用户命令

| 命令 | 作用 | 旧名字（仍可使用） |
| :--- | :--- | :--- |
| `/start` | 唤醒女仆（仅私聊） | |
| `/help [命令名]` | 查看命令手册（仅私聊） | |
| `/myid` | 查看自己的 ID，群里同时显示群 ID | `/getid` |

## 🧑‍💼 管理员命令

| 分类 | 命令 | 作用 | 旧名字（仍可使用） |
| :--- | :--- | :--- | :--- |
| 日常 | `/todo` | 查看待回复的客人 | `/inbox` |
| 日常 | `/mobile` | 打开手机管理页（仅私聊） | `/app` |
| 日常 | `/manage` | 打开管理面板 | `/panel` |
| 日常 | `/stats` | 查看宅邸统计 | |
| 日常 | `/drive` | 查看网盘状态；回复一条带文件的消息再发，会把文件存进网盘并回复直链 | |
| 用户 | `/ban` | 拉黑用户（话题里可省略 ID） | `/block` |
| 用户 | `/unban` | 解除拉黑 | `/unblock` |
| 用户 | `/banlist` | 查看黑名单 | `/blacklist` |
| 用户 | `/intercepts` | 查看拦截篮 | `/view_filtered` |
| 用户 | `/pass` | 管理免审查通行证 | `/exempt` |
| 用户 | `/reverify` | 收回验证让用户重验 | `/unverify` |
| 用户 | `/trust` | 恢复用户的验证 | `/verify` |
| 用户 | `/groups` | 管理用户分组 | `/group` |
| 消息 | `/announce` | 向用户群发公告 | `/broadcast` |
| 消息 | `/aireply` | 安排 AI 自动回复：`on`、`off`、`list`、`add`、`del <ID>` | `/autoreply` |
| 消息 | `/keywords` | 管理关键词拦截 | `/spamrules` |
| 监控 | `/restock` | 管理补货监控：`list`、`add <名称> <网址> [商品关键词]`、`check <ID>`、`delete <ID>` | |
| 监控 | `/watch_web` | 管理网页监控：`list`、`add <名称> <网址> [关键词]`、`run <ID>`、`keywords`、`interval`、`on`/`off`、`delete` | `/webmon` |
| 监控 | `/watch_tg` | 管理 TG 群监听：`list`、`discovered`、`add`、`keywords`、`exclude`、`interval`、`on`/`off`、`delete` | `/tgmon` |
| 监控 | `/watch_status` | 查看监控运行状态 | `/monitor_status` |
| 维护 | `/upgrade` | 检查并更新机器人；面板还提供 Docker 镜像更新 | `/updatebot` |
| 维护 | `/restart` | 验证 Bot Token 后重启 | |

## 📰 RSS 命令

仅限私聊。

| 命令 | 作用 | 旧名字（仍可使用） |
| :--- | :--- | :--- |
| `/rss_add <网址>` | 添加订阅 | |
| `/rss_del <网址\|编号>` | 删除订阅 | `/rss_remove` |
| `/rss_list` | 查看订阅 | |
| `/rss_kw_add <编号> <关键词>` | 添加关键词 | `/rss_addkeyword` |
| `/rss_kw_del <编号> <关键词>` | 删除关键词 | `/rss_removekeyword` |
| `/rss_kw_list <编号>` | 查看关键词 | `/rss_listkeywords` |
| `/rss_kw_clear <编号>` | 清空关键词 | `/rss_removeallkeywords` |
| `/rss_footer [文本]` | 设置消息尾巴 | `/rss_setfooter` |
| `/rss_preview` | 切换链接预览 | `/rss_togglepreview` |
| `/rss_allow <user_id>` | 授权用户使用 RSS（管理员） | `/rss_add_user` |
| `/rss_deny <user_id>` | 收回 RSS 授权（管理员） | `/rss_rm_user` |

## 🌐 网络工具命令

| 命令 | 作用 | 旧名字（仍可使用） |
| :--- | :--- | :--- |
| `/ping [目标] [次数]` | Ping 测试 | |
| `/trace [目标]` | 路由追踪 | `/nexttrace` |
| `/net_allow <user_id>` | 授权用户使用网络工具（管理员） | `/adduser` |
| `/net_deny <user_id>` | 收回网络工具授权（管理员） | `/rmuser` |
| `/net_server_add` | 登记测试服务器（管理员） | `/addserver` |
| `/net_server_del` | 撤下测试服务器（管理员） | `/rmserver` |
| `/trace_install` | 安装路由追踪工具（管理员） | `/install_nexttrace` |
