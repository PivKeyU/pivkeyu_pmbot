"""命令目录：名称合法、新旧命令都注册、菜单按身份区分、打错命令有建议。"""

import asyncio
import re
from types import SimpleNamespace

import pytest
from telegram.ext import Application, CommandHandler

from config import config
from handlers import command_handler, register_handlers
from rss import handlers as rss_handlers
from services import telegram_commands
from utils import commands


def test_names_follow_telegram_rules():
    names = [command.name for command in commands.all_commands()]
    assert len(names) == len(set(names))
    assert len(names) <= 100
    for name in [*names, *commands.LEGACY_ALIASES]:
        assert re.fullmatch(r'[a-z0-9_]{1,32}', name), name
    for command in commands.all_commands():
        assert 3 <= len(command.description) <= 256


def test_legacy_names_point_to_real_commands():
    primary = {command.name for command in commands.all_commands()}
    assert set(commands.LEGACY_ALIASES.values()) <= primary
    assert not set(commands.LEGACY_ALIASES) & primary


def test_every_command_and_alias_is_registered(monkeypatch):
    monkeypatch.setattr(config, 'FORUM_GROUP_ID', -1001)
    monkeypatch.setattr(config, 'ADMIN_IDS', [1])
    app = Application.builder().token('1:TEST').build()
    register_handlers(app)
    registered = set()
    for group in app.handlers.values():
        for handler in group:
            if isinstance(handler, CommandHandler):
                registered |= handler.commands
    for command in rss_handlers.COMMAND_MAP:
        registered |= set(commands.names_for(command))

    expected = {command.name for command in commands.all_commands()} | set(commands.LEGACY_ALIASES)
    assert expected <= registered


def test_ordinary_users_do_not_see_admin_commands():
    private = {command.command for command in telegram_commands.get_private_chat_commands()}
    assert private == {'start', 'help', 'myid'}
    assert {command.command for command in telegram_commands.get_group_chat_commands()} == {'myid'}
    admin = {command.command for command in telegram_commands.get_admin_private_commands()}
    assert {'ban', 'restock', 'mobile', 'rss_add'} <= admin
    group = {command.command for command in telegram_commands.get_admin_group_commands()}
    assert 'ban' in group and 'mobile' not in group and 'rss_add' not in group


@pytest.mark.parametrize('typo,expected', [('restok', 'restock'), ('bann', 'ban'), ('upgrde', 'upgrade')])
def test_suggestions_for_admin(typo, expected):
    assert expected in commands.suggest(typo, True)


def test_suggestions_hide_admin_commands_from_users():
    assert 'ban' not in commands.suggest('bann', False)


def test_old_names_still_resolve():
    assert commands.find('/block').name == 'ban'
    assert commands.is_known('rss_setfooter')
    assert commands.names_for('block') == ['ban', 'block']
    assert commands.names_for('ban') == ['ban', 'block']
    assert '/block' in commands.render_command_help(commands.find('ban'))


def test_help_text_depends_on_role():
    assert '/ban' not in commands.render_help(False)
    assert '/ban' in commands.render_help(True)
    assert '/restock' in commands.render_help(True)


class Message:
    def __init__(self, text):
        self.text = text
        self.replies = []

    async def reply_text(self, text, **kwargs):
        self.replies.append(text)


def _unknown(text, admin_ids):
    config_admins = config.ADMIN_IDS
    config.ADMIN_IDS = admin_ids
    message = Message(text)
    update = SimpleNamespace(message=message, effective_user=SimpleNamespace(id=1))
    try:
        asyncio.run(command_handler.handle_unknown_command(update, SimpleNamespace()))
    finally:
        config.ADMIN_IDS = config_admins
    return message.replies


def test_unknown_command_gets_a_suggestion():
    assert '/restock' in _unknown('/restok abc', [1])[0]
    assert '/help' in _unknown('/qwertyuiop', [])[0]


def test_known_commands_are_left_alone():
    assert _unknown('/ban 123', [1]) == []
    assert _unknown('/block@some_bot 123', [1]) == []
