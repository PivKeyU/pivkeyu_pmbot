# 监控

[← 返回 README](../README.md) · [文档目录](../README.md#-文档)

TG 群、网页、补货和 RSS 的配置方法。

TG 群/频道、网页、补货和 RSS 四类监控，都能在面板的「RSS / 监控」标签管理；机器人里用 `/watch_status` 查看运行状态：

```bash
/watch_status             # 查看全部监控运行状态
/watch_status rss
/watch_status tg
/watch_status web
```

输出包含各监控任务的运行状态、最近失败原因与耗时。

## TG 群 / 频道关键词监听

支持关键词、排除词、去重与最小推送间隔。两种监听来源：

- **`bot` 模式**：机器人已在目标群/频道中，零配置直接监听；
- **`user_session` 模式**：通过 Telethon 使用用户会话监听 Bot 无法加入的群/频道，需要完整配置 `TG_API_ID`、`TG_API_HASH`、`TG_API_SESSION`（可选 `TG_PROXY`）。

```bash
/watch_tg add 监听名称 -1001234567890 关键词1,关键词2 user_session
/watch_tg list
/watch_tg discovered        # 查看用户会话发现的群/频道
/watch_tg on 1
/watch_tg off 1
/watch_tg keywords 1 新关键词1,新关键词2    # 更新监听词
/watch_tg exclude 1 排除词1,排除词2        # 设置排除词
/watch_tg interval 1 300                    # 设置最小推送间隔（秒）
/watch_tg delete 1
```

> 论坛话题群自身（`FORUM_GROUP_ID`）不会被记录进发现列表，避免污染 `/watch_tg discovered`。

## 网页监控

CSS 选择器自动解析页面条目（标题、链接、正文、价格、库存），检测以下变化并推送管理员：

- **新条目**：出现此前未见过的条目（首次添加只建立基线，不推送已有内容）；
- **关键词命中**：条目标题/正文命中关键词；
- **内容变化 / 价格变化 / 库存变化**：已有条目内容哈希、价格或库存发生变化。

任务防重叠，可通过 `interval` 控制检查频率。

```bash
/watch_web add 监控名称 https://example.com 关键词1,关键词2
/watch_web list
/watch_web run 1        # 立即手动检查一次
/watch_web keywords 1 新关键词1,新关键词2
/watch_web interval 1 300
/watch_web off 1
/watch_web delete 1
```

## RSS 订阅推送

RSS 功能默认关闭，先在 `.env` 设置 `RSS_ENABLED=true`（或 `/manage` → RSS 订阅茶点管理 中开启），重启后：

```bash
/rss_add https://example.com/feed.xml
/rss_list
/rss_kw_add 1 口味词          # 只推送命中口味词的条目
/rss_footer 来自女仆的问候       # 自定义页脚
/rss_preview                # 切换链接预览
```

订阅源、关键词、页脚与链接预览均在私聊中管理。

> 首次添加订阅只建立基线，不推送已有文章；每个周期最多推送 5 条新条目，超出部分以摘要提示，防刷屏。

## 🛒 补货监控

在 Mini App 或 Web 面板的「RSS / 监控 → 补货监控」添加商品，填商品网址和检查间隔（分钟）即可，保存前可以点「先试一次」看判断结果。商品从缺货变成有货时，机器人推送带「去看看」按钮的消息；售罄只记录，不推送。

判断顺序：库存选择器（填了才用）→ 页面的 schema.org 商品数据 → 商品关键词附近的文字 → 整页文字，缺货字样优先于有货字样。同一页面看不出状态时保留上次结果，不会误报，错误原因显示在列表里。

也可以在机器人里管理：

```bash
/restock add 手办 https://shop.example.com/item/42 第二弹
/restock list
/restock check 1
/restock delete 1
```

只能读取服务器直接返回的网页内容。库存靠浏览器脚本加载，或者网站有反爬验证时判断不了。

## 📬 监控结果与推送

所有监控的命中记录都在「监控结果」标签，保留最近 2000 条，那里也有测试推送按钮。

网页、补货和 TG 监听可以单独开关机器人推送，并单独设置推送目标；留空时网页和补货推送给管理员，TG 监听优先用 `TG_MONITOR_NOTIFY_CHAT_IDS`。RSS 推送到订阅所属的聊天。
