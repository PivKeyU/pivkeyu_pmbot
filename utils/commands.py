"""机器人命令目录：名称、说明和用法都在这里维护，菜单与 /help 都由它生成。

命名规则：看名字就知道做什么。同一类功能用同一个前缀（watch_ / rss_ / net_），
TG 用户熟悉的说法优先（ban / unban）。改名后旧命令继续可用，见 LEGACY_ALIASES。
"""

import difflib
from typing import NamedTuple

USER = 'user'
ADMIN = 'admin'


class Command(NamedTuple):
    name: str
    description: str
    usage: str = ''
    private_only: bool = False


USER_COMMANDS = (
    Command('start', '唤醒女仆', private_only=True),
    Command('help', '查看命令手册', '/help [命令名]', private_only=True),
    Command('myid', '查看自己的 ID'),
)

# (分类名, 命令)。菜单按这个顺序展示，常用的放前面
ADMIN_SECTIONS = (
    ('日常', (
        Command('todo', '查看待办：待回复的客人'),
        Command('mobile', '打开手机管理页', private_only=True),
        Command('manage', '打开管理面板'),
        Command('stats', '查看宅邸统计'),
        Command('drive', '网盘：查看状态或把回复的文件存进网盘', '/drive（回复一条带文件的消息即可存入）'),
    )),
    ('用户', (
        Command('ban', '拉黑用户', '/ban [user_id] [理由]（话题里可省略 ID）'),
        Command('unban', '解除拉黑', '/unban <user_id>'),
        Command('banlist', '查看黑名单'),
        Command('intercepts', '查看拦截篮'),
        Command('pass', '管理免审查通行证', '/pass [user_id] permanent|temp <小时>|remove'),
        Command('reverify', '收回验证让用户重验', '/reverify [user_id]（话题里可省略 ID）'),
        Command('trust', '恢复用户的验证', '/trust [user_id]（话题里可省略 ID）'),
        Command('groups', '管理用户分组', '/groups list|create|delete|add|remove|members'),
    )),
    ('消息', (
        Command('announce', '向用户群发公告', '/announce all|group <分组>'),
        Command('aireply', '安排 AI 自动回复'),
        Command('keywords', '管理关键词拦截', '/keywords [on|off|autoblock on|off|add <词>|del <词>|clear]'),
    )),
    ('监控', (
        Command('restock', '管理补货监控', '/restock list|add <名称> <网址> [商品关键词]|check <ID>|delete <ID>'),
        Command('watch_web', '管理网页监控', '/watch_web add <名称> <网址> [关键词]'),
        Command('watch_tg', '管理 TG 群监听'),
        Command('watch_status', '查看监控运行状态'),
    )),
    ('RSS', (
        Command('rss_add', '添加 RSS 订阅', '/rss_add <网址>', True),
        Command('rss_del', '删除 RSS 订阅', '/rss_del <网址或编号>', True),
        Command('rss_list', '查看 RSS 订阅', private_only=True),
        Command('rss_kw_add', '添加 RSS 关键词', '/rss_kw_add <编号> <关键词>', True),
        Command('rss_kw_del', '删除 RSS 关键词', '/rss_kw_del <编号> <关键词>', True),
        Command('rss_kw_list', '查看 RSS 关键词', '/rss_kw_list <编号>', True),
        Command('rss_kw_clear', '清空 RSS 关键词', '/rss_kw_clear <编号>', True),
        Command('rss_footer', '设置 RSS 消息尾巴', '/rss_footer [文本]', True),
        Command('rss_preview', '切换 RSS 链接预览', private_only=True),
        Command('rss_allow', '授权用户使用 RSS', '/rss_allow <user_id>', True),
        Command('rss_deny', '收回 RSS 授权', '/rss_deny <user_id>', True),
    )),
    ('网络工具', (
        Command('ping', 'Ping 测试', '/ping [目标] [次数]'),
        Command('trace', '路由追踪', '/trace [目标]'),
        Command('net_allow', '授权用户使用网络工具', '/net_allow <user_id>'),
        Command('net_deny', '收回网络工具授权', '/net_deny <user_id>'),
        Command('net_server_add', '登记测试服务器'),
        Command('net_server_del', '撤下测试服务器'),
        Command('trace_install', '安装路由追踪工具'),
    )),
    ('维护', (
        Command('upgrade', '检查并更新机器人'),
        Command('restart', '验证 Token 后重启'),
    )),
)

# 旧命令名 -> 新命令名
LEGACY_ALIASES = {
    'getid': 'myid',
    'inbox': 'todo',
    'app': 'mobile',
    'panel': 'manage',
    'block': 'ban',
    'unblock': 'unban',
    'blacklist': 'banlist',
    'view_filtered': 'intercepts',
    'exempt': 'pass',
    'unverify': 'reverify',
    'verify': 'trust',
    'group': 'groups',
    'broadcast': 'announce',
    'autoreply': 'aireply',
    'spamrules': 'keywords',
    'webmon': 'watch_web',
    'tgmon': 'watch_tg',
    'monitor_status': 'watch_status',
    'rss_remove': 'rss_del',
    'rss_addkeyword': 'rss_kw_add',
    'rss_removekeyword': 'rss_kw_del',
    'rss_listkeywords': 'rss_kw_list',
    'rss_removeallkeywords': 'rss_kw_clear',
    'rss_setfooter': 'rss_footer',
    'rss_togglepreview': 'rss_preview',
    'rss_add_user': 'rss_allow',
    'rss_rm_user': 'rss_deny',
    'nexttrace': 'trace',
    'adduser': 'net_allow',
    'rmuser': 'net_deny',
    'addserver': 'net_server_add',
    'rmserver': 'net_server_del',
    'install_nexttrace': 'trace_install',
    'updatebot': 'upgrade',
}


def admin_commands() -> tuple:
    return tuple(command for _, commands in ADMIN_SECTIONS for command in commands)


def all_commands() -> tuple:
    return USER_COMMANDS + admin_commands()


def names_for(name: str) -> list[str]:
    """注册处理器用：传新名或旧名，都返回新名和它的全部旧名。"""
    primary = LEGACY_ALIASES.get(name, name)
    return [primary, *(old for old, new in LEGACY_ALIASES.items() if new == primary)]


def find(name: str):
    primary = LEGACY_ALIASES.get(name.lower().lstrip('/'), name.lower().lstrip('/'))
    return next((command for command in all_commands() if command.name == primary), None)


def is_known(name: str) -> bool:
    return find(name) is not None


def suggest(name: str, is_admin: bool) -> list[str]:
    """打错命令时猜用户想用哪条。"""
    commands = all_commands() if is_admin else USER_COMMANDS
    candidates = [command.name for command in commands]
    return difflib.get_close_matches(name.lower(), candidates, n=3, cutoff=0.6)


def render_help(is_admin: bool) -> str:
    lines = ['客人可以用：']
    lines += [f'/{command.name} - {command.description}' for command in USER_COMMANDS]
    if is_admin:
        for title, commands in ADMIN_SECTIONS:
            lines += ['', f'【{title}】']
            lines += [f'/{command.name} - {command.description}' for command in commands]
        lines += ['', '想看某条命令怎么用：/help 命令名，例如 /help restock', '旧命令名仍然可以继续用。']
    return '\n'.join(lines)


def render_command_help(command: Command) -> str:
    lines = [f'/{command.name} - {command.description}']
    if command.usage:
        lines.append(f'用法：{command.usage}')
    aliases = [old for old, new in LEGACY_ALIASES.items() if new == command.name]
    if aliases:
        lines.append('旧名字：' + '、'.join(f'/{old}' for old in aliases))
    return '\n'.join(lines)
