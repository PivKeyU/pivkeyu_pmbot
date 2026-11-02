"""把 Telegram 消息的转发来源整理成一行可读文字。"""

from typing import Optional


def _chat_label(chat) -> str:
    title = getattr(chat, 'title', None) or getattr(chat, 'full_name', None) or str(getattr(chat, 'id', ''))
    username = getattr(chat, 'username', None)
    return f'{title} (@{username})' if username else title


def forward_label(message) -> Optional[str]:
    """返回「频道 xxx」「用户 xxx」这样的来源；不是转发消息时返回 None。"""
    origin = getattr(message, 'forward_origin', None)
    if origin is None:
        return None
    chat = getattr(origin, 'chat', None)
    if chat is not None:
        return f'频道 {_chat_label(chat)}'
    sender_chat = getattr(origin, 'sender_chat', None)
    if sender_chat is not None:
        return f'群组 {_chat_label(sender_chat)}'
    sender_user = getattr(origin, 'sender_user', None)
    if sender_user is not None:
        return f'用户 {_chat_label(sender_user)}'
    hidden_name = getattr(origin, 'sender_user_name', None)
    return f'用户 {hidden_name}（隐藏了账号）' if hidden_name else '未知来源'
