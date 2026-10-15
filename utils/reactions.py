"""Telegram 消息表情回执：统一构造合法的 reaction 载荷。

python-telegram-bot 的 ``set_message_reaction`` 只接受 ``ReactionType`` 对象或字符串：

* 传 ``[{"type": "emoji", "emoji": "👁"}]`` 这类 dict 会在**参数解析阶段**就抛
  ``TypeError: unhashable type: 'dict'``（PTB 用 ``entry in set(ReactionEmoji)``
  判断是否标准表情，dict 不可哈希），请求根本发不出去；
* 传字符串时，只有 Telegram 允许集合内的表情才会被识别成 ``ReactionTypeEmoji``，
  其它字符串会被当成 custom emoji id，服务端回 BadRequest 并被静默吞掉。

所以这里统一构造 ``ReactionTypeEmoji`` 对象，并把表情限制在 Telegram 允许的
固定集合内（见 Bot API / PTB ``ReactionEmoji``）。
"""

from telegram import ReactionTypeEmoji

# Telegram 允许的 reaction 表情是固定集合，不在集合里的表情服务端会拒绝。
REACTION_SEEN = '👀'  # 论坛话题：管理员 / Web 面板的回复已被看到
REACTION_READ = '👍'  # 私聊：用户已读管理员的历史消息


def seen_reaction() -> list:
    """论坛话题里的「已阅」回执载荷。"""
    return [ReactionTypeEmoji(emoji=REACTION_SEEN)]


def read_reaction() -> list:
    """用户已读管理员消息的回执载荷。"""
    return [ReactionTypeEmoji(emoji=REACTION_READ)]
