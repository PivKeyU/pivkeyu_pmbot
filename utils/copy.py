# -*- coding: utf-8 -*-
"""集中文案层（central copy layer）。

所有**用户可见**的固定文案都集中在这里，不要再散落到各个 handler / service 里手打。
这么做是为了：

1. 改语气只改一处，不会漏改（此前同一句话最多在项目里重复了 54 遍）；
2. 文案改动能被单独 review / diff，不会淹没在业务逻辑里；
3. 需要变语气的少数文案有统一的"状态升级函数"（见文末）。

.. important::
   本模块**只允许**依赖标准库。严禁 import telegram / database / config，
   也不要 import 本项目的其他模块——否则会形成循环导入。

为什么放在 ``utils/`` 而不是 ``handlers/``
----------------------------------------
``services/broadcast.py``、``services/tg_monitor.py``、``services/web_monitor.py``
都要用这里的文案。如果把文案模块放进 ``handlers/``，那么
``import services.broadcast`` 就会顺带执行 ``handlers/__init__.py``，而后者又
``from services.tg_monitor import ...``，于是
``python -c "import services.tg_monitor"`` 会直接抛出::

    ImportError: cannot import name 'handle_bot_group_message'
    from partially initialized module 'services.tg_monitor'

``utils/`` 是叶子包（``utils/__init__.py`` 为空），从 ``services`` 指向 ``utils``
不会成环。这一点已经用 42 个模块各自作为冷启动入口逐一验证过。

人物声线规范（硬约束，动手改文案之前先读这一节）
----------------------------------------------
本模块的每一条文案都出自同一位女仆。她是**温柔顺从、认真可靠的宅邸女仆**，
不是全程傲娇的二次元角色——**反差**才是人设：默认档必须温柔，只有下面列出的
触发点才允许切成傲娇。按 2026-09 的实测口径（全项目 608 个可见文案调用点，其中
对话类 384、导航/菜单按钮 224），傲娇只落在 17 条唯一文案上；用滥了就不值钱了。

* **称呼（两档，按读者角色选）**：
  * 读者是**管理员**（管理员女仆长）-> 称「主人」；
  * 读者是**普通用户** -> 称「客人」。
  角色由调用方算好（``await db.is_admin(user_id)`` / ``check_is_admin(...)`` /
  ``user_id in config.ADMIN_IDS``），本模块只接受 bool，见 :func:`address`。
  **同一条消息里禁止混用「主人」「客人」「您」三者中的任意两个**
  （``services/ai_service.py`` 的人设 prompt 层是同一条硬约束，硬编码层必须
  保持一致）；也不要把「用户」当第二人称用。
  管理员与普通用户**都会**读到的那几条文案，做成带 ``display_name`` 的函数，
  不要固定成其中一档。
* **自称**：默认「女仆」；撒娇、抱怨时可用「人家」；逞强时可用「本女仆」。
* **口癖白名单**：``哦 / 啦 / 呢 / 嘛 / 呀 / 哼 / 唔 / ～``。
  **一句话最多一个语气词，禁止堆砌。**
* **傲娇三句式**（必须"先抗拒/否认 -> 后照做/帮忙"，缺后半句就不算傲娇）：

  1. 否认后帮忙：「哼，又来麻烦女仆……不过既然主人开口了，就这一次哦。」
  2. 毒舌但心软：「主人的链接又写错啦。算了，女仆已经帮忙检查过了。」
  3. 别扭的关心：「不是担心主人才提醒的……这个操作不可撤销，主人自己想清楚哦。」

禁止改动的地方（和上面同样重要）
''''''''''''''''''''''''''''''
* **导航类按钮**（``上一页`` / ``下一页`` / ``返回`` / ``修改`` / ``删除`` / ``取消`` /
  ``确认撤下`` …）：高频肌肉记忆控件，加情绪只会降低可用性，保持原样。
* **技术字段名**：``群/频道：`` / ``发送者：`` / ``命中：`` / ``原因：`` / ``标题：`` /
  ``链接：`` / ``价格：`` / ``库存：`` / ``内容：`` / ``发出包数`` / ``丢包率`` /
  ``抖动(mdev)`` / ``头部信息`` / ``路由跳数``。
* logger / print / docstring / raise，以及 SSH 原始输出透传。
* 命令语法示例里的 ``/xxx <参数>`` 部分（语气词只能加在前半句）。
* 傲娇档**不允许吞掉真实信息**（``{n}`` 次机会、服务器名、错误原因都必须保留）；
  异常详情一类内部细节转 logger，只给读者简短原因。

命名约定
--------
* 固定文案 -> ``UPPER_SNAKE_CASE`` 常量。
* 带参数的文案 -> ``snake_case`` 函数，参数有名字，返回渲染好的字符串。

  这里选函数而不是 ``.format()`` 模板常量，理由：本模块的文案最多有 5 个插值位，
  位置参数写错顺序是静默错误；函数签名把每个值都命名了，调用处一读就懂，而且
  插值前的加工（``.upper()``、三元表达式）留在函数内部，调用点保持干净。
  全项目统一这一种风格，不做两套。

语气机制（状态升级函数，不做全量分身）
------------------------------------
本项目**不采用**"每种语气各来一份平行文案表"的全量分身方案：只有少数几条文案
需要换语气，全量分身的代价是两份表长期同步维护，收益却很小。实际采用
**状态升级函数**：

1. 同一语义位准备一个或多个候选字面量；（温柔档为默认，傲娇档只在下面列出的
   触发点使用）；
2. 由函数按**真实业务状态**选档并渲染，调用方只调函数，不自己写 ``if``；
3. 不引入额外的语气开关配置——语气必须由业务状态自动决定，不需要"配音色"。

称呼机制（同一套思路）
----------------------
称呼也不做"每个称呼各来一份平行文案表"。只有那几条**两种角色都会读到**的文案
才需要分档，它们统一做成带 ``display_name`` 首参的函数 + :func:`address` 决定实参；
读者单一的那几百条直接写字面量（管理员档写"主人"，访客档写"客人"）。

当前的状态升级函数（均在测试里验证过首次/第 2 次/第 3 次的返回）：

* :func:`verify_wrong` —— 小验证答错，第 1 次温柔，**第 2 次起升级为傲娇**。
* :func:`unblock_wrong` —— 解封验证答错，同一套口径。
* :func:`unblock_question` —— 解封验证重新提问，第 1 次温柔，第 2 次起傲娇。

其余文案直接使用常量，不提供傲娇变体。判断"这里到底适不适合傲娇"时，回到上面
的触发点与三句式，而不是机械替换。
"""


# ==========================================================================
# 称呼
# ==========================================================================

# 称呼规则（2026-09 定稿）：
#   读者是管理员（管理员女仆长） -> 「主人」
#   读者是普通用户               -> 「客人」
#
# 本模块是叶子模块，不能 import config / database，所以这里只认算好的 bool。
# 同一条消息里禁止混用「主人」「客人」「您」三者中的任意两个。
MASTER = '主人'

GUEST = '客人'


def address(is_admin: bool) -> str:
    """按读者角色返回称呼词（管理员 -> 主人，普通用户 -> 客人）。

    调用方负责算角色，各层的现成判断：
      * ``handlers/*`` -> ``await db.is_admin(user_id)``
      * ``network_test/*`` -> ``check_is_admin(user_id, ADMIN_USERS)``
      * ``rss/*`` -> ``user_id in config.ADMIN_IDS``
    """
    return MASTER if is_admin else GUEST


# ==========================================================================
# 权限
# ==========================================================================

# 下面三条的读者**就是**被权限检查挡下来的普通用户，所以固定用「客人」。

def perm_denied() -> str:
    """权限：读者没有权限做这件事（被权限检查挡下来的普通用户）。"""
    return with_deco(
        '客人没有权限吩咐这项工作哦。',
        'DENY',
    )


def perm_admin_disabled() -> str:
    """权限：还没配置任何管理员，门都开不了。"""
    return with_deco(
        '这扇门还没开张呢。女仆还没被配上管理员钥匙，等配好了女仆再替客人开门。',
        'DENY',
    )


def perm_admin_only() -> str:
    """权限：管理员专属操作，读者不是管理员。"""
    return with_deco(
        '才不是谁都能碰的东西呢……这是管理员女仆长才有的钥匙，客人先收手吧。',
        'TSUNDERE',
    )


def nt_not_admin(user_id: int) -> str:
    """网络测试命令：当前账号不是管理员女仆长（读者是普通用户）。"""
    return with_deco_head(
        '客人不是管理员女仆长，不能执行这项操作哦。\n\n当前 ID：`{user_id}`'.format(user_id=user_id),
        'DENY',
    )

def nt_panel_not_admin() -> str:
    """网络测试：读者不是网络测试茶具间的管理员。"""
    return with_deco(
        '客人还不是网络测试茶具间的管理员哦。',
        'DENY',
    )



def nt_no_pass(user_id: int) -> str:
    """网络测试命令：当前账号还没有授权通行证（读者是普通用户）。"""
    return with_deco_head(
        (
            '客人还没有使用网络测试茶具的通行证\n\n当前 ID：`{user_id}`\n\n请联系管理员女仆长使用 `/adduser {user_id}` 把客人加入授权名单。'
        ).format(user_id=user_id),
        'DENY',
    )


def user_id_invalid() -> str:
    """参数：用户 ID 不是有效数字。"""
    return with_deco(
        '这个用户 ID 看起来不对劲，主人再检查一下嘛。',
        'ERROR',
    )


def rss_user_id_invalid() -> str:
    """RSS：用户 ID 填得不对。"""
    return with_deco(
        '主人，这里要填一个有效的用户 ID 哦（整数）。',
        'ERROR',
    )


def nt_user_id_invalid() -> str:
    """网络测试：user_id 填得不对。"""
    return with_deco(
        '主人，这里要填一个正确的 user_id 哦（数字）。',
        'ERROR',
    )


def no_topic_user() -> str:
    """会客厅：这个话题还没绑定哪一位。"""
    return with_deco(
        '主人，这个话题还没有绑定哪一位呢，女仆不知道从哪儿下手。',
        'ASK',
    )


def topic_create_failed(display_name: str) -> str:
    """消息递送：找不到或没能创建专属会客厅（管理员与普通用户都会遇到）。"""
    return with_deco(
        '女仆没能找到或创建专属会客厅，{who}找管理员女仆长帮个忙嘛。'.format(who=display_name),
        'ERROR',
    )


# ==========================================================================
# 导航按钮
# ==========================================================================

BTN_BACK_PANEL = '回女仆长面板'  # 48 处

BTN_BACK_NETWORK_TEST = '回网络测试茶具'  # 16 处

BTN_NEXT_PAGE = '下一页'  # 11 处

BTN_PREV_PAGE = '上一页'  # 9 处

BTN_KB_EDIT = '修改'  # 6 处

BTN_KB_DELETE = '删除'  # 6 处

BTN_KB_LIST = '整理知识小本本'  # 5 处

BTN_KB_ADD = '新增知识便签'  # 5 处

BTN_AUTOREPLY_REST = '让自动回复女仆休息'  # 5 处

BTN_AUTOREPLY_ON_DUTY = '让自动回复女仆值班'  # 5 处

BTN_CANCEL = '取消'  # 3 处

BTN_BACK_RSS = '回 RSS 控制台'  # 3 处

BTN_BACK_AI = '回模型衣柜'  # 3 处

def btn_ai_enable_gemini(is_current) -> str:
    """AI 设置按钮：把当前提供商标记为 Gemini（选中时带勾）。"""
    mark = '✅ ' if is_current else ''
    return '{mark}启用 Gemini'.format(mark=mark)

def btn_ai_enable_openai(is_current) -> str:
    """AI 设置按钮：把当前提供商标记为 OpenAI（选中时带勾）。"""
    mark = '✅ ' if is_current else ''
    return '{mark}启用 OpenAI'.format(mark=mark)

def btn_admin_unblock(first_name: str) -> str:
    """黑名单/通行证列表按钮：为某个主人开门。"""
    return '为 {first_name} 开门'.format(first_name=first_name)

BTN_REFRESH = '刷新'  # 2 处

BTN_BACK_TG_MONITOR = '回 TG 监听'  # 2 处

BTN_BACK_KB_LIST = '回小本本列表'  # 2 处

BTN_BACK_BROADCAST = '回广播与分组'  # 2 处

BTN_AI_MODELS_GEMINI = '整理 Gemini 模型'  # 2 处

BTN_AI_MODELS_OPENAI = '整理 OpenAI 模型'  # 2 处

BTN_BACK = '返回'  # 2 处

BTN_BACK_STATS = '返回统计小本本'  # 2 处


# ==========================================================================
# 面板菜单
# ==========================================================================

BTN_PANEL_STATUS = '运行状态'  # 4 处

BTN_PANEL_BLACKLIST = '黑名单小本本'  # 4 处

BTN_PANEL_AI_FILTER_MODEL = '内容审查模型'  # 3 处

BTN_PANEL_AI_VERIFY_MODEL = '小验证生成模型'  # 3 处

BTN_PANEL_AI_AUTOREPLY_MODEL = '自动回复模型'  # 3 处

BTN_PANEL_AI_SETTINGS = 'AI 模型衣柜'  # 2 处

BTN_PANEL_RSS = 'RSS 订阅茶点管理'  # 2 处

BTN_PANEL_TG_MONITOR = 'TG 监听'  # 2 处

BTN_PANEL_STATS = '客人名册'  # 2 处（名册里列的是普通用户，按称呼规则叫客人）

BTN_PANEL_SPAMRULES = '关键词拦截'  # 2 处

BTN_PANEL_UPDATEBOT = '安全更新'  # 2 处

BTN_PANEL_BROADCAST = '广播与分组'  # 2 处

BTN_STATS_ALL_USERS = '所有客人名册'  # 2 处

BTN_PANEL_FILTERED = '拦截消息篮'  # 2 处

BTN_PANEL_NETWORK_TEST = '网络测试茶具管理'  # 2 处

BTN_PANEL_WEB_MONITOR = '网页监控'  # 2 处

BTN_PANEL_AUTOREPLY = '自动回复女仆管理'  # 2 处

BTN_PANEL_EXEMPTIONS = '通行证名单管理'  # 2 处


# ==========================================================================
# 分页
# ==========================================================================

def page_invalid() -> str:
    """分页：页码不是有效数字。"""
    return with_deco(
        '这个页码不对劲哦，主人。',
        'ERROR',
    )



# ==========================================================================
# 知识小本本
# ==========================================================================

def kb_entry_id_invalid() -> str:
    """知识小本本：条目 ID 不是数字。"""
    return with_deco(
        '这个条目 ID 不对劲哦，主人再看一眼吧。',
        'ERROR',
    )


def kb_entry_missing() -> str:
    """知识小本本：这个条目不存在。"""
    return with_deco(
        '女仆翻遍小本本，也没找到这个条目呢。',
        'ERROR',
    )


def kb_empty() -> str:
    """知识小本本：一条都没有。"""
    return with_deco(
        '知识小本本还是空白的呢，主人。',
        'EMPTY',
    )


def kb_edit_hint(entry_id: int, entry_title: str, entry_content, kb_id: int) -> str:
    """知识小本本：修改便签的用法提示。"""
    return with_deco_head(
        (
            '修改知识便签\n\nID: {entry_id}\n标题: {entry_title}\n内容: {entry_content}\n\n主人，请这样让女仆修改：\n'
            '`/autoreply edit {kb_id} <新标题> <新内容>`\n\n示例：\n`/autoreply edit {kb_id} 新标题 新内容`'
        ).format(entry_id=entry_id, entry_title=entry_title, entry_content=entry_content, kb_id=kb_id),
        'ASK',
    )

def kb_entry_not_found(entry_id: int) -> str:
    """知识小本本：按 ID 找不到条目。"""
    return with_deco(
        '女仆翻遍小本本，也没找到条目 ID {entry_id}。'.format(entry_id=entry_id),
        'ERROR',
    )

def kb_deleted(entry_title: str) -> str:
    """知识小本本：便签已删除（不可撤销，用做娇语气）。"""
    return with_deco(
        '哼，说删就删吗？便签 {entry_title} 已经替主人收走了，可找不回来哦。'.format(entry_title=entry_title),
        'TSUNDERE',
    )

def kb_add_hint() -> str:
    """知识小本本：新增便签的用法提示（含命令语法）。"""
    return with_deco_head(
        '新增知识便签\n\n主人，请这样交给女仆新便签：\n`/autoreply add <标题> <内容>`\n\n示例：\n`/autoreply add 常见问题 这是问题的答案`',
        'ASK',
    )
  # 2 处


# ==========================================================================
# 拦截消息篮
# ==========================================================================

def filtered_empty() -> str:
    """拦截消息篮：篮子还是空的。"""
    return with_deco(
        '拦截篮里还是空空的呢，主人。',
        'EMPTY',
    )


def filtered_not_found() -> str:
    """拦截消息篮：找不到这个篮子。"""
    return with_deco(
        '女仆没找到自己的拦截篮呢。',
        'ERROR',
    )



# ==========================================================================
# 网络测试
# ==========================================================================

def nt_server_label(name, host, port) -> str:
    """网络测试按钮：服务器名称与地址的展示标签。"""
    return '{name} ({host}:{port})'.format(name=name, host=host, port=port)

def nt_server_reg_cancelled() -> str:
    """网络测试：服务器登记被取消。"""
    return with_deco(
        '服务器登记已经替主人取消啦。',
        'OK',
    )


def nt_server_remove_confirm(name, host, port) -> str:
    """网络测试：撤下服务器前的确认（不可撤销操作，用别扭的关心）。

    名称 / Host / 端口这些技术字段原样保留；只把提示语换成傲娇语气
    （「不是担心主人……只是这个操作不可撤销」）。
    """
    return with_deco_head(
        (
            '主人确定要撤下这台服务器吗？\n\n名称: {name}\nHost: {host}:{port}\n\n'
            '不是担心主人才提醒的……这个操作不可撤销，主人自己想清楚哦。'
        ).format(name=name, host=host, port=port),
        'TSUNDERE',
    )


def nt_install_running(name) -> str:
    """网络测试：NextTrace 安装已开始。"""
    return with_deco_head(
        '女仆正在服务器 {name} 上安装 NextTrace...\n请主人稍等，可能需要一点时间。'.format(name=name),
        'WAIT',
    )


def nt_install_result(name, result) -> str:
    """网络测试：NextTrace 安装结果。"""
    return with_deco_head(
        '服务器 {name} 的 NextTrace 安装结果：\n\n{result}'.format(name=name, result=result),
        'OK',
    )


def nt_install_error(name, error) -> str:
    """网络测试：NextTrace 安装失败（附原始 SSH 输出，便于主人自查）。"""
    return with_deco_head(
        '服务器 {name} 安装 NextTrace 时出错：\n\n{error}'.format(name=name, error=error),
        'ERROR',
    )


def nt_install_cancelled() -> str:
    """网络测试：NextTrace 安装中途取消（傲娇）。"""
    return with_deco(
        '哼，装到一半就不装了吗？那女仆把摊子收起来啦。',
        'TSUNDERE',
    )


# 下面三条同时被 ping / nexttrace（授权用户就能用，可能是客人）与管理员流程使用，
# 属于 SHARED，做成带 ``display_name`` 的函数。

def nt_op_not_allowed(display_name: str) -> str:
    """网络测试：当前步骤不允许点击。"""
    return with_deco(
        '这一步不该点这里哦，{who}照着上面的提示把流程走完就好。'.format(who=display_name),
        'ERROR',
    )


def nt_ping_target_missing(display_name: str) -> str:
    """网络测试：服务器或目标 IP 信息不完整。"""
    return with_deco(
        '服务器或目标 IP 信息不完整，{who}重新走一遍 /ping 流程嘛。'.format(who=display_name),
        'ERROR',
    )


def nt_ip_already_input(display_name: str) -> str:
    """网络测试：已经输入过目标 IP。"""
    return with_deco(
        '{who}已经输入过目标 IP 啦，想重测的话用对应命令就好。'.format(who=display_name),
        'OK',
    )


def nt_server_removed(name, host) -> str:
    """网络测试：服务器已撤下。"""
    return with_deco(
        '服务器已经替主人撤下啦：{name} (host={host})'.format(name=name, host=host),
        'OK',
    )

def nt_server_index_stale() -> str:
    """网络测试：服务器索引过期，列表已变。"""
    return with_deco(
        '服务器索引不对劲，可能列表已经更新，主人重新执行一次 /rmserver 嘛。',
        'ERROR',
    )


BTN_NT_ICMP = 'ICMP 模式'  # 2 处

BTN_NT_IPV4 = 'IPv4'  # 2 处

BTN_NT_IPV6 = 'IPv6'  # 2 处

BTN_NT_TCP = 'TCP 模式'  # 2 处

def nt_addserver_hint() -> str:
    """网络测试：/addserver 的两种登记方式（含命令语法）。"""
    return with_deco_head(
'主人可以用两种方式登记服务器：\n\n1. 直接输入 /addserver 启动交互式登记向导\n2. 一次性交给女仆所有参数：\n'
        '   /addserver <名称> <host> <port> <username> <password>\n\n名称可以包含空格，但需要用引号括起来，例如：\n'
        '/addserver "香港 - GCP" 1.2.3.4 22 user pass',
        'ASK',
    )
  # 2 处

def nt_trace_running(display_name: str, server_name, target, trace_mode) -> str:
    """网络测试：路由追踪已在后台开始（授权用户就能触发，可能是客人）。"""
    mode = 'ICMP' if trace_mode == 'icmp' else 'TCP'
    return with_deco_head(
        (
            '{who}选了 {server_name} 呢。\n目标：{target} 是 IP 地址，女仆正在后台执行{mode}模式路由追踪，稍等一下哦...'
        ).format(who=display_name, server_name=server_name, target=target, mode=mode),
        'WAIT',
    )

def nt_pick_install_target() -> str:
    """网络测试：问要往哪台服务器装 NextTrace。"""
    return with_deco(
        '主人，要往哪台服务器上装 NextTrace 呢？',
        'ASK',
    )


def nt_pick_remove_target() -> str:
    """网络测试：问要撤下哪一台服务器。"""
    return with_deco(
        '主人，要撤下的是哪一台服务器呢？',
        'ASK',
    )


def nt_arg_parse_error(error: str) -> str:
    """网络测试：命令行参数解析失败。"""
    return with_deco_head(
        '参数解析出错啦：{error}\n\n名称里有空格的话，主人用引号括起来就好了哦。'.format(error=error),
        'ERROR',
    )

def nt_ping_running() -> str:
    """网络测试：Ping 已在后台开始。"""
    return with_deco(
        '女仆收到请求啦，正在后台执行 Ping，请稍候...',
        'WAIT',
    )


def nt_no_servers(display_name: str) -> str:
    """网络测试：一台可用服务器都没有（ping / nexttrace 授权用户也能触发）。"""
    return with_deco(
        '现在还没有可用的服务器，{who}找管理员女仆长帮个忙嘛。'.format(who=display_name),
        'EMPTY',
    )

def nt_no_servers_registered() -> str:
    """网络测试：一台服务器都没登记过。"""
    return with_deco(
        '主人还没有登记过任何服务器呢。',
        'EMPTY',
    )


def nt_no_servers_registered_hint() -> str:
    """网络测试：还没登记服务器，附 /addserver 提示。"""
    return with_deco(
        '主人还没有登记过任何服务器呢。先用 /addserver 把服务器交给女仆吧。',
        'EMPTY',
    )


def nt_remove_cancelled() -> str:
    """网络测试：撤下服务器中途反悔（傲娇）。"""
    return with_deco(
        '哼，撤到一半又反悔了吗？那女仆把名册合上啦，服务器还在。',
        'TSUNDERE',
    )


def nt_server_list_stale() -> str:
    """网络测试：选中的服务器对不上号。"""
    return with_deco(
        '服务器列表已经变过，选中的那台对不上号，主人重新执行一次 /rmserver 嘛。',
        'ERROR',
    )


def nt_rmserver_hint() -> str:
    """网络测试：/rmserver 用法提示（含命令语法）。"""
    return with_deco_head(
'直接输入 /rmserver 可以查看所有服务器并选择要撤下的服务器。\n\n如果要直接指定撤下，女仆小抄：/rmserver <服务器名字>\n'
        '如果服务器名称包含空格，请用引号括起来，例如：\n/rmserver "香港 - GCP"',
        'ASK',
    )
  # 2 处


# ==========================================================================
# 监控
# ==========================================================================

def webmon_not_found() -> str:
    """网页监控：找不到这个监控。"""
    return with_deco(
        '女仆翻遍小本本，也没找到这个网页监控。',
        'ERROR',
    )


def tgmon_not_found() -> str:
    """TG 监听：找不到这个监听。"""
    return with_deco('女仆翻遍小本本，也没找到这个监听。', 'ERROR')


def tgmon_id_invalid() -> str:
    """TG 监听：监听 ID 不是数字。"""
    return with_deco(
        '监听 ID 要写成数字哦，主人。',
        'ERROR',
    )


def webmon_id_invalid() -> str:
    """网页监控：监控 ID 不是数字。"""
    return with_deco(
        '监控 ID 要写成数字哦，主人。',
        'ERROR',
    )


def tgmon_add_id_invalid() -> str:
    """TG 监听：新增时要给一串数字（chat_id）。"""
    return with_deco(
        '主人，这个位置要给女仆一串数字哦（chat_id）。',
        'ERROR',
    )


def tgmon_no_keywords() -> str:
    """TG 监听：只给了群，没给要盯的关键词。"""
    return with_deco(
        '光有群还不够，主人再给女仆一个要盯的关键词嘛。',
        'ASK',
    )


def tgmon_pick_id() -> str:
    """TG 监听：问要管哪个监听 ID。"""
    return with_deco(
        '主人，把要管的监听 ID 一起给女仆嘛。',
        'ASK',
    )


def tgmon_keywords_updated() -> str:
    """TG 监听：监听词已更新。"""
    return with_deco('监听词已经替主人抄好啦。', 'OK')


def tgmon_interval_updated() -> str:
    """TG 监听：最小推送间隔已更新。"""
    return with_deco('监听的最小推送间隔已经替主人调好啦。', 'OK')


def tgmon_id_and_seconds_invalid() -> str:
    """TG 监听：监听 ID 和秒数不是数字。"""
    return with_deco(
        '监听 ID 和秒数都要写成数字哦，主人。',
        'ERROR',
    )


def webmon_pick_id() -> str:
    """网页监控：问要管哪个监控 ID。"""
    return with_deco(
        '主人，把要管的监控 ID 一起给女仆嘛。',
        'ASK',
    )


def webmon_running() -> str:
    """网页监控：正在挨个检查网页。"""
    return with_deco(
        '女仆正在挨个敲门检查网页，请稍等。',
        'WAIT',
    )


def webmon_keywords_updated() -> str:
    """网页监控：关键词已更新。"""
    return with_deco('网页监控的关键词已经替主人抄好啦。', 'OK')


def webmon_interval_updated() -> str:
    """网页监控：检查间隔已更新。"""
    return with_deco('网页监控的检查间隔已经替主人调好啦。', 'OK')


def webmon_id_and_seconds_invalid() -> str:
    """网页监控：监控 ID 和秒数不是数字。"""
    return with_deco(
        '监控 ID 和秒数都要写成数字哦，主人。',
        'ERROR',
    )


def webmon_run_done(count) -> str:
    """网页监控：立即检查完成，回报推送条数。"""
    return with_deco(
        '检查完成，替主人推了 {count} 条。'.format(count=count),
        'OK',
    )


# ==========================================================================
# RSS
# ==========================================================================

def rss_feed_not_found(feed_identifier) -> str:
    """RSS：按标识符找不到订阅源。"""
    return with_deco(
        (
            "女仆没找到标识符为 '{feed_identifier}' 的茶点。请用 /rss_list 查看。"
        ).format(feed_identifier=feed_identifier),
        'ERROR',
    )

def rss_no_feeds(display_name: str) -> str:
    """RSS：还没有任何订阅源（RSS 命令管理员与授权用户都能用）。"""
    return with_deco(
        '{who}还没有任何 RSS 茶点。'.format(who=display_name),
        'EMPTY',
    )


def rss_more_entries(display_name: str, feed_title, remaining) -> str:
    """RSS：单轮发送条数到顶了，告诉读者本轮还有多少条没端上来。

    ``feed_title`` 必须由调用方先用 ``rss.feed_checker._escape_html()`` 转义后传入：
    本模块是叶子模块不能反向 import 转义函数；``<i>`` 标签由这条文案自带，不能省。
    RSS 订阅者可能是管理员也可能是授权客人，所以称呼由调用方算好传入。
    原文案写作「{remaining} 个更多新条目」，「个」与「更多」叠用不通顺，
    这里换成量词「条」，这层意思由句首的「还有」承担。
    """
    return with_deco(
        (
            '<i>...还有 {remaining} 条来自 {feed_title} 的新茶点，女仆先替{who}攒着，下一轮再端上来哦。</i>'
        ).format(who=display_name, feed_title=feed_title, remaining=remaining),
        'OK',
    )


# ==========================================================================
# 通行证
# ==========================================================================

def exempt_revoked(user_id: int) -> str:
    """审查通行证：已收回某位主人的通行证（收回是不可撤销动作，用做娇语气）。"""
    return with_deco(
        '哼，既然主人这么说，{user_id} 的审查通行证女仆已经收回来啦。'.format(user_id=user_id),
        'TSUNDERE',
    )

def exempt_temp_granted(user_id: int, hours: int, reason: str) -> str:
    """审查通行证：已发放限时通行证。"""
    return with_deco_head(
        (
            '主人，{user_id} 已经拿到 {hours} 小时的临时审查通行证啦。\n登记理由: {reason}'
        ).format(user_id=user_id, hours=hours, reason=reason),
        'OK',
    )

def exempt_perm_granted(user_id: int, reason: str) -> str:
    """审查通行证：已发放永久通行证。"""
    return with_deco_head(
        (
            '主人，{user_id} 已经拿到永久审查通行证啦。\n登记理由: {reason}'
        ).format(user_id=user_id, reason=reason),
        'OK',
    )

def exempt_check_holding(user_id: int, status_text: str, reason: str) -> str:
    """审查通行证：某位主人现在拿着的通行证（带小抄的完整版用这个）。"""
    return with_deco_head(
        (
            '审查通行证\n\n主人，{user_id} 现在拿着的是 {status_text} 哦。\n登记理由: {reason}'
        ).format(user_id=user_id, status_text=status_text, reason=reason),
        'OK',
    )

def exempt_check_none(user_id: int) -> str:
    """审查通行证：某位主人目前没有通行证。"""
    return with_deco(
        '主人，{user_id} 现在还没有审查通行证呢。'.format(user_id=user_id),
        'DENY',
    )

def exempt_hours_invalid() -> str:
    """审查通行证：小时数不是数字。"""
    return with_deco(
        '小时数要写成数字哦，主人。',
        'ERROR',
    )


def exempt_title(user_id: int, exemption_type: str, reason: str) -> str:
    """审查通行证：某位主人当前的通行证详情（带小抄的完整版用这个）。"""
    return with_deco_head(
        (
            '主人，{user_id} 现在拿着的是 {exemption_type} 哦。\n登记理由: {reason}'
        ).format(user_id=user_id, exemption_type=exemption_type, reason=reason),
        'OK',
    )


# ==========================================================================
# 授权名单（网络测试 / RSS 两处共用同一套口径）
# ==========================================================================

# 两处语境的名单名。只有「名单叫什么」不一样，语义完全一致，所以做成参数，
# 不做 8 条平行文案——那会变成长期双份维护（本模块最怕这个）。
#
# 注意：下面四个模板都把 ``{scope}`` 放在**句首**。这是故意的：``RSS 授权名单``
# 以拉丁字母开头，按本项目中英混排的习惯它前面需要留一个空格，而``网络测试的授权名单``
# 是纯汉字、前面不该有空格。把 scope 放句首就同时满足两者，不必在模板里猜空格。
AUTH_LIST_NETWORK_TEST = '网络测试的授权名单'
AUTH_LIST_RSS = 'RSS 授权名单'

# 四条里的 ``user_id`` 都是**被操作的那一位（第三方）**，不是正在对话的主人，
# 所以文案里不出现任何第二人称指代：改用「名单上本来就有 / 已记下 / 已划掉 / 找不到」
# 这类**以名单为主语**的说法把信息讲完。既不写「用户」（系统内部术语），
# 也绝不写「主人」（会指代错误）。真实 ID 必须原样保留，功能信息不能丢。


def auth_already(user_id: int, scope: str) -> str:
    """授权名单：这一位本来就在名单里（重复登记，未做改动）。"""
    return with_deco(
        '{scope}上本来就有 {user_id} 啦。'.format(scope=scope, user_id=user_id),
        'OK',
    )


def auth_granted(user_id: int, scope: str) -> str:
    """授权名单：已经把这一位登记好了。"""
    return with_deco(
        '{scope}上已经记下 {user_id} 啦。'.format(scope=scope, user_id=user_id),
        'OK',
    )


def auth_revoked(user_id: int, scope: str) -> str:
    """授权名单：已经把这一位从名单里划掉了。"""
    return with_deco(
        '{scope}上已经划掉 {user_id} 啦。'.format(scope=scope, user_id=user_id),
        'OK',
    )


def auth_missing(user_id: int, scope: str) -> str:
    """授权名单：这一位本来就不在名单里（无需移除）。"""
    return with_deco(
        '{scope}上找不到 {user_id} 呢。'.format(scope=scope, user_id=user_id),
        'ERROR',
    )


# ==========================================================================
# 广播与分组
# ==========================================================================


def usercard_group_header(user_id: int) -> str:
    """广播分组面板：某一位的分组页标题。

    同上面四条口径：``user_id`` 是被查看的第三方，用中性指代「这一位」。
    标题类文案不加语气词，也不做傲娇档。
    """
    return with_deco(
        '这一位的分组（{user_id}）'.format(user_id=user_id),
        'CAT',
    )


# ==========================================================================
# AI 模型
# ==========================================================================

def ai_model_set(provider_type, feature_type, model_name) -> str:
    """AI 设置：已切换某个功能使用的模型。"""
    provider = provider_type.upper()
    return with_deco(
        (
            '已经替主人把 {provider} {feature_type} 的模型换成 {model_name} 啦'
        ).format(provider=provider, feature_type=feature_type, model_name=model_name),
        'OK',
    )


# ==========================================================================
# 自动回复
# ==========================================================================

def autoreply_status_changed(new_status) -> str:
    """自动回复女仆：值班状态已切换。"""
    return with_deco(
        '自动回复女仆{new_status}啦'.format(new_status=new_status),
        'OK',
    )


# ==========================================================================
# 消息递送
# ==========================================================================

# 下面三条**管理员和普通用户都会收到**（消息递送是所有人共用的通道），所以做成
# 带 ``display_name`` 首参的函数，实参由调用方用 :func:`address` 算好。


def scan_message(display_name: str) -> str:
    """消息递送：AI 正在检查消息。"""
    return with_deco(
        '女仆正在用 AI 小扫帚检查消息，{who}稍等一下哦...'.format(who=display_name),
        'WAIT',
    )


def msg_blocked(reason: str) -> str:
    """消息递送：消息被拦截篮拦下，附拦截理由。"""
    return with_deco_head(
        '这条消息被女仆拦进小篮子啦，没有继续递送\n\n拦截理由：{reason}'.format(reason=reason),
        'CAT',
    )


def delivery_failed(display_name: str) -> str:
    """消息递送：递送过程出错。"""
    return with_deco(
        '递送消息时出了点小状况，{who}稍后再试一次嘛。'.format(who=display_name),
        'WAIT',
    )


# ==========================================================================
# 会客厅（用户专属话题）被关闭后重新小验证
# ==========================================================================

# 读者是私聊里的普通用户（会客厅关门后重新验证），统一写「客人」。
THREAD_CLOSED_REVERIFY = (
    '客人，之前的会客厅已经关门啦。请重新完成小验证，女仆再为客人递送消息。\n\n'
)  # 2 处（handlers/callback_handler.py + handlers/user_handler.py）


# ==========================================================================
# 查看 ID
# ==========================================================================

# ``/getid`` 对所有人开放，管理员也会用同一条命令，所以这两条属于 SHARED：
# 跟消息递送那三条一样做成带 ``display_name`` 的函数，不做成单一档。


def getid_self(display_name: str, user_id: int) -> str:
    """查看 ID：自己的用户 ID（私聊与群聊共用同一条文案）。"""
    return with_deco(
        '{who}，这就是{who}自己的用户 ID：`{user_id}`'.format(who=display_name, user_id=user_id),
        'OK',
    )


def getid_group(display_name: str, chat_id: int) -> str:
    """查看 ID：当前群组的 ID。"""
    return with_deco(
        '{who}，这个群组的 ID 是：`{chat_id}`'.format(who=display_name, chat_id=chat_id),
        'OK',
    )


# ==========================================================================
# 小验证 / 解封验证（状态升级函数：第 2 次错误起才升级为傲娇）
# ==========================================================================

# 小验证 / 解封验证的读者**一定是普通用户**（管理员早在权限层就通过了，
# 不会被要求答题），所以这一整段固定用「客人」。

VERIFY_INVITE = '来做个人家的小验证嘛。答对了才放客人进去哦：'

VERIFY_INVITE_PENDING = '客人还有一个小验证没做完，先点选答案再继续发消息嘛。'

VERIFY_WRONG = '答案不对哦，客人还有 {remaining} 次机会。'

VERIFY_WRONG_AGAIN = '哼，又错啦…才不是女仆没说清楚。客人还有 {remaining} 次机会，好好想嘛。'


def verify_wrong(remaining: int, attempts: int) -> str:
    """小验证：答错时按累计错误次数选语气（首次温柔，第 2 次起升级为傲娇）。

    ``attempts`` 是**累计**错误次数，调用方传入的值必须已经包含本次错误。
    两种语气都完整保留 ``{remaining}`` 次机会这一真实信息。
    """
    template = VERIFY_WRONG_AGAIN if attempts >= 2 else VERIFY_WRONG
    return with_deco(template.format(remaining=remaining), 'ERROR')


UNBLOCK_WRONG = '答案不对哦，客人还有 {remaining} 次机会，重新发一条消息可以再试。'

# 下面是本模块内部使用的「候选档位」，由 verify_wrong / unblock_wrong / unblock_question
# 按累计错误次数挑选；外部不需要、也不应该直接引用它们。
UNBLOCK_WRONG_AGAIN = (
    '哼，又错啦…才不是女仆没讲清楚。客人还有 {remaining} 次机会，重新发一条消息好好想嘛。'
)


def unblock_wrong(remaining: int, attempts: int) -> str:
    """解封验证：答错时按累计错误次数选语气（首次温柔，第 2 次起升级为傲娇）。

    语义与 :func:`verify_wrong` 完全一致，两者共同构成一套"状态升级"口径。
    """
    template = UNBLOCK_WRONG_AGAIN if attempts >= 2 else UNBLOCK_WRONG
    return with_deco(template.format(remaining=remaining), 'ERROR')


UNBLOCK_QUESTION = '如果客人觉得这是误会，请回答下面的问题，女仆会帮客人自动开门：'  # 2 处
UNBLOCK_QUESTION_STERN = (
    '哼，客人又答错啦。算了，女仆再给客人一次机会——答对下面的问题，就帮客人把门打开哦。\n\n{question}'
)


def unblock_question(question: str, attempts: int) -> str:
    """解封验证：重新提问时按累计错误次数选语气（首次温柔，第 2 次起升级为傲娇）。

    ``attempts`` 语义与 :func:`verify_wrong` 一致。两种语气都完整保留 ``question``
    本身，不能吞掉题目。
    """
    if attempts >= 2:
        return with_deco_head(
            UNBLOCK_QUESTION_STERN.format(attempts=attempts, question=question),
            'ERROR',
        )
    return with_deco_head(
        '{invite}\n\n{question}'.format(invite=UNBLOCK_QUESTION, question=question),
        'ERROR',
    )


# ==========================================================================
# 镜像更新（Docker 部署时的「检查镜像更新」）
# ==========================================================================
#
# 与「安全更新」（services/safe_update.py 的 git 分支）是**两套并行**的更新方式：
# 容器里的机器人自己没有 docker.sock，所以镜像更新只能委托给外部的 watchtower，
# 由 watchtower 拉新镜像并重建容器。
#
# 因此这里有一条与别的分区都不同的规矩：
# **触发更新 = 女仆随即被杀掉**，主人点下去之后有几十秒是收不到任何回话的。
#   * :func:`image_update_confirm_prompt` 必须先把「会短暂下线」说清楚，
#     不允许用傲娇语气糊过去；
#   * :func:`image_update_started` 也必须把「主人可能看不到后续消息」讲明白，
#     并给出「一直没回来就去看容器日志」这条退路。
#
# ``IMAGE_UPDATE_NOT_IN_DOCKER`` 虽然是场景性的提示，但它**是常量、不能带装饰**：
# 装饰每次渲染重新随机（见文末装饰层规矩），而模块级常量在 import 时就固定了。
# 本文件所有 ``UPPER_SNAKE_CASE`` 常量都是纯文本，这条也不例外；调用方若确实
# 想要装饰，请在发送点包 ``with_deco(IMAGE_UPDATE_NOT_IN_DOCKER, 'ERROR')``。
#
# 另外：本分区刻意不用「茶具」这个宅邸比喻——它在本文件里已经被「网络测试茶具」
# 占用了（6 处），再拿来说镜像会混淆。
#
# 读者固定是管理员（能进女仆长面板的都是主人），所以整段统一用「主人」，
# 不提供 ``display_name`` 参数。

# 按钮文案：导航/操作控件不加装饰，保持纯文本。
BTN_IMAGE_CHECK = '检查镜像更新'

BTN_IMAGE_APPLY = '确认更新'

IMAGE_UPDATE_NOT_IN_DOCKER = (
    '当前不是 Docker 容器环境，镜像更新这条路走不通哦。\n\n'
    '主人可以用 Git 更新：/updatebot status 查看、/updatebot apply 执行、'
    '/updatebot rollback 回滚。'
)


def image_update_confirm_prompt(image: str) -> str:
    """镜像更新：二次确认（不可逆的运维操作，语气必须清楚可靠，不用傲娇）。

    必须让主人先知道后果：容器会被重建，机器人在这期间不在线。
    """
    return with_deco_head(
        (
            '主人确认要把镜像更新到 {image} 吗？\n\n'
            '这一下点下去就不能回头了，女仆说清楚后果：watchtower 会拉取新镜像并重建容器，'
            '女仆会跟着容器一起下线一会儿，重启完成才回来。\n'
            '更新期间主人发的消息女仆收不到，更新完也不会有额外的完成通知，'
            '所以主人稍等一两分钟，再回来看看女仆在不在。\n'
            '如果一直没有回来，请到宿主机上看一眼容器日志。'
        ).format(image=image),
        'ASK',
    )


def image_update_started() -> str:
    """镜像更新：已把请求交给 watchtower（同一个操作的「已发出」态，不用傲娇）。"""
    return with_deco_head(
        (
            '好的，女仆已经把更新请求交给 watchtower 了。\n\n'
            '接下来 watchtower 会自己拉新镜像、重建容器，女仆会离开一会儿，'
            '主人过一两分钟再回来看看。\n'
            '如果主人一会儿没收到任何反应，就到宿主机上看一眼容器日志。'
        ),
        'WAIT',
    )


def image_update_failed(reason: str) -> str:
    """镜像更新：触发失败，附简短原因（原因原样保留，主人自查要用）。

    只给简短原因，不要把内部堆栈或 token 拼进来。
    """
    return with_deco(
        '镜像更新没能发出去呢，女仆把原因原样留在这里：{reason}'.format(reason=reason),
        'ERROR',
    )


def image_update_found(local_version: str, image: str) -> str:
    """镜像更新：检测到新版本（``local_version`` 可能是 ``-``，非容器/未注入时）。

    这里只写人类可读的引导语，**不排** ``字段: 值`` 的清单——状态清单是
    ``services/image_update.py`` 的 :func:`format_image_status` 的职责。
    """
    return with_deco_head(
        (
            '主人，远端有新镜像啦。\n\n'
            '镜像是 {image}，本地跑着的版本是 {local_version}。\n\n'
            '主人要现在就换上新的，就点下面的按钮。'
        ).format(image=image, local_version=local_version or '-'),
        'OK',
    )


def image_update_up_to_date(local_version: str, image: str) -> str:
    """镜像更新：已是最新（与 :func:`image_update_found` 同一套说法）。"""
    return with_deco_head(
        (
            '主人，镜像是 {image}，本地跑的版本是 {local_version}。\n\n'
            '已经是最新的啦，不用更新。'
        ).format(image=image, local_version=local_version or '-'),
        'OK',
    )


# ==========================================================================
# 装饰层（emoji / 颜文字）
# ==========================================================================
#
# 规矩（2026-09 定稿）：
#   1. **一条消息最多一个装饰**——一个 emoji 或一个颜文字，不叠加、不铺满。
#   2. 装饰按**场景**选池，同一场景每次渲染**重新随机**（同一条消息被反复渲染
#      时也会换，所以翻页、重复点按钮不会看到固定不变的那一个）。
#   3. **导航/菜单按钮一律不加**（`上一页` / `返回` / `取消` / `运行状态` …），
#      高频肌肉记忆控件加装饰只会降低可用性。
#   4. 技术字段名、logger、命令语法示例一律不加。
#
# .. danger::
#   下面每个颜文字都**不能含** ``*`` ``_`` ``` ` `` ``[`` ``]``。
#   本项目的 12 个发送点带 ``parse_mode='Markdown'``，Telegram 的 legacy
#   Markdown 遇到**未成对**的实体字符会直接拒收整条消息
#   （``BadRequest: Can't parse entities``），也就是主人什么都收不到。
#   候选清单里原本有 22 个含这些字符（如 ``(T_T)`` ``>ω<`` ``>ᯅ<`` ``(｡•ᴗ-)_⁺``），
#   已全部剔除；新增颜文字时必须先跑 ``tools`` 里的检查再放进来。

import random as _random

#: 难过 / 委屈 / 道歉 / 失败
MARK_CRY = [
    '߹𖥦߹', 'っ ̯ -｡', 'つ﹏ɵ̷̥̥᷅', 'T^T', '°¯᷄◠¯᷅°', '•̥ ̫ •̥',
    '(⑉꒦ິ꒦ິ꒦ິ꒦ິ^꒦ິ꒦ິ꒦ິ꒦ິ⑉)', 'ㅠ ₃ ㅠ', '৹ᵒ̴̶̷᷄﹏ᵒ̴̶̷᷅৹',
    '꒦ິ꒦ິ꒦ິ꒦ິ^꒦ິ꒦ິ꒦ິ꒦ິ', '߹ᯅ߹', '≥﹏≤', '(ᐡ т  ̫ т ᐡ)',
    '.ᐟ.ᐟᯅ̈ᯅ̈ᯅ̈ᯅ̈', '•᷄ࡇ•᷅', '⑉꒦ິ꒦ິ꒦ິ꒦ິ^꒦ິ꒦ິ꒦ິ꒦ິ⑉',
    '૮₍つ﹏ɵ̷̥̥᷅ ₎ა', '૮₍ ⸝⸝•̥𖥦•̥⸝⸝ ₎ა', '૮₍っ ̫ •̥⸝⸝ ₎ა',
    '(ᐡっ ̫ •̥⸝⸝ᐡ)', '૮ •̥ ·̫ •̥ ა', '(ू(ू˃o˂ ू )', '⑅ = ㅅ •= ⑅',
    '(͒⑅ = ㅅ •= ⑅)͒', '⸝⸝っ·̫ •⸝⸝', '˙ ꒳ ˙', '〃•ω‹〃',
    '₍ᐢ⸝⸝›  ‹⸝⸝ᐢ₎', '₍ᐢ⸝⸝• ֊ •⸝⸝ᐢ₎', '⸝⸝•｡•⸝⸝',
    'ᐡo̴̶̷̤o̴̶̷̤o̴̶̷̤ ﻌ o̴̶̷̤o̴̶̷̤o̴̶̷̤ᐡ',
]

#: 开心 / 温柔 / 感谢 / 成功 / 撒娇
MARK_HAPPY = [
    '♡̶₊⁺', '˗ˋˏ♡ˎˊ˗', '°ʚ♡ɞ°', '₊⁺♡₊⁺', '♡+♡=♡²', '૮꒰⸝⸝´ᜊ ˋ⸝⸝꒱ა',
    'ᕱ⑅ᕱ', '=͟͟͞♡', '˗ˏˋ ꒰ ♡ ꒱ ˎˊ˗', 'ᙏ̤̫ᙏ̤̫ᙏ̤̫ᙏ̤̫', '૮ .  ̫ . ა',
    '•᷅ᯅ•᷄', '^⌯𖥦⌯^ ੭', "⌯'ㅅ'⌯", 'ᜊ•×•ᜊ', '૮꒰ ˶• ༝ •˶꒱ა',
    "°꒰๑'ꀾ'๑꒱°", "૮ ฅ'ㅅ'ฅ ა", '๑•͈ᴗ•͈  ♡', '꒰´•͈⌔•͈⑅꒱',
    '૮ต•̤ ༝ •̤ตაིྀ', '⑅ ୨୧', '♡·✧⁺', '〃⊃•𖥦•⊂〃', 'ᜊ•ᴗ•ᜊ',
    '(♡˃ꇴ˂♡)', 'ᜊ๑•×•๑ᜊ', 'ฅ^•ﻌ•^ฅ', '(///ㅅ///)♡', '⁽⁽ଘ(ˊᵕˋ)ଓ⁾⁾',
    '૮꒰˶ฅ́ฅ́ฅ́ฅ́˘ฅ̀ฅ̀ฅ̀ฅ̀˶꒱ა', '(⑉• •⑉)‥♡', 'ど⁰̷̴͈⁰̷̴͈꒨⁰̷̴͈⁰̷̴͈う～',
    '♡=•ㅅ＜=)☆', '♡²', '(⸝⸝•‧̫‧̫•⸝⸝)♡', '૮₍ ⸝⸝ᴗ͈ ‸ ᴗ͈⸝⸝ ₎ა',
    '૮₍  .  ̫ .  ₎ა', 'ᰔᩚᰔᩚᰔᩚᰔᩚ', '♡̷⸝⸝•༝•⸝⸝♡̷', 'づ♡ど',
    '˖˚˳ଘ꒰ ⸝⸝ᴗ͈ ̫  ᴗ͈⸝⸝꒱♡', '⋆⁺₊⋆ ☾', 'ʚ(◜𖥦◝ )ɞ',
    'ᥫᩣᥫᩣᥫᩣᥫᩣ', 'ᐢ ›̥̥̥ ༝ ‹̥̥̥ ᐢ', '₍ᐢ..ᐢ₎', '₊⁺♡̶₊⁺', 'ʚ ɞ',
    'ʚ✞ɞ', '˙˚ʚ✞ɞ˚˙', '⋆͛⋆͛⋆͛⋆͛♥︎⋆͛⋆͛⋆͛⋆͛', '૮₍ ๑ • ᵜ ก ๑ ₎ა࣪',
    '૮₍ ˶• ˔ ต ₎ა', '૮₍ ˃ ⤙ ˂ ₎ა',
    '໒꒰ྀི꒰ྀི꒰ྀི꒰ྀི -᷅ ⤙ -᷄ ꒱ྀ꒱ྀ꒱ྀ꒱ྀ',
    '໒꒰ྀི꒰ྀི꒰ྀི꒰ྀི∩˃ᵕ˂∩꒱ྀི꒱ྀི꒱ྀི꒱ྀ',
    '- ̗̀ ̗̀ ꪔ̤̥ꪔ̤̥ꪔ̤̥ꪔ̤̥ꪔ̤̮ꪔ̤̮ꪔ̤̮ꪔ̤̮ꪔ̤̫ꪔ̤̫ꪔ̤̫ꪔ̤̫  ̖́ ̖́-',
    '꒷꒦꒦꒷꒦꒷꒦꒦꒷꒷꒦꒦꒷꒦', 'ᙏ̤̫͚ᙏ̤̫͚ᙏ̤̫͚ᙏ̤̫͚', '₍ᐢ｡•༝•｡ᐢ₎໒꒱·',
    '₍ᐢ｡•༝•｡ᐢ₎ଓ⑅°', 'ᐢ. ̫.ᐢ₎っ⌁', '₍ᐢ.ˬ.⑅ᐢ₎', 'ο(=•ω＜=)ρ⌒☆',
    '(っ˘ω˘c )', '↜૮₍ ՞. ̫.՞₎ა', '♡(˃͈ દ ˂͈ ༶ )', '(･༥･´) ̑̑',
    '՞• •՞', 'つω• )', '(⑉･-･⑉)♡', "!⌯'▾'⌯°♡⑅", "⌯'▾'⌯",
    '(⋆ ͒•‧•⑅ ͒)', '₍ᐢ｡•༝•｡ᐢ₎',
]

#: 宅邸猫娘 —— 俏皮、拦东西、盯关键词时用
MARK_CAT = [
    '⦮ ⦯', '🐾', 'ฅ', '∪^ェ^∪', 'U^ェ^U', '੯‧̀͡‧̀͡⬮\\', '(=^･ω･^=)',
    'ฅ^•ﻌ•^ฅ', "૮ ฅ'ㅅ'ฅ ა", 'ㅇㅅㅇ', '₍ᐢ..ᐢ₎', 'ᐢ. ̫.ᐢ₎っ⌁',
    '₍ᐢ.ˬ.⑅ᐢ₎',
]

#: 场景 -> emoji 池。跟 MARK_* 一样每次渲染重新抽。
DECO_EMOJI = {
    'OK':        ['✅', '🎀', '✨', '💐', '🎉', '🌟'],
    'ERROR':     ['😿', '💧', '🫠', '😖', '🌧️'],
    'WAIT':      ['⏳', '🫖', '🍵', '⌛'],
    'GREET':     ['🎀', '👋', '🏠', '🧹'],
    'ASK':       ['❓', '🤔', '📎', '🔍'],
    'DENY':      ['🚫', '🔒', '🙅'],
    'BLOCK':     ['🔒', '⛔', '🚪'],
    'EMPTY':     ['🍃', '🫧', '📭'],
    'TSUNDERE':  ['😤', '💢', '🙄'],
    'LOVE':      ['💗', '🌷', '🫶', '☺️'],
    'CAT':       ['🐾', '🐱', '😺'],
}

#: 场景 -> 颜文字池（难过 / 开心 / 猫）
DECO_MARK = {
    'OK':        MARK_HAPPY,
    'ERROR':     MARK_CRY,
    'WAIT':      MARK_HAPPY,
    'GREET':     MARK_HAPPY,
    'ASK':       MARK_HAPPY,
    'DENY':      MARK_CRY,
    'BLOCK':     MARK_CRY,
    'EMPTY':     MARK_CRY,
    'TSUNDERE':  MARK_HAPPY,
    'LOVE':      MARK_HAPPY,
    'CAT':       MARK_CAT,
}


def emoji(scene: str) -> str:
    """按场景随机取一个 emoji（场景未定义时返回空串）。"""
    pool = DECO_EMOJI.get(scene)
    return _random.choice(pool) if pool else ''


def mark(scene: str) -> str:
    """按场景随机取一个颜文字（场景未定义时返回空串）。"""
    pool = DECO_MARK.get(scene)
    return _random.choice(pool) if pool else ''


def deco(scene: str) -> str:
    """按场景随机取一个装饰，emoji 与颜文字二选一（约各占一半）。

    这是给"一条消息最多一个装饰"用的统一入口：调用方只说场景，
    至于是 emoji 还是颜文字、具体是哪一个，由这里每次随机决定。
    """
    if scene not in DECO_EMOJI and scene not in DECO_MARK:
        return ''
    if _random.random() < 0.5 and scene in DECO_EMOJI:
        return emoji(scene)
    return mark(scene) or emoji(scene)


def with_deco(text: str, scene: str) -> str:
    """把随机装饰缀到文案末尾（前面留一个空格）。

    用于单行/短文案。多行文案请用 :func:`with_deco_head`，
    避免装饰跑到最后一行（如字段列表）后面。
    """
    d = deco(scene)
    return '{text} {deco}'.format(text=text, deco=d) if d else text


def with_deco_head(text: str, scene: str) -> str:
    """把随机装饰缀到**首行**末尾，多行文案用这个。

    例：``审查通行证\n\n主人，...`` -> 装饰加在「审查通行证」后面，
    而不是跑到最后的「登记理由: ...」后面去。
    """
    d = deco(scene)
    if not d:
        return text
    head, sep, tail = text.partition('\n')
    return '{head} {deco}{sep}{tail}'.format(head=head, deco=d, sep=sep, tail=tail)
