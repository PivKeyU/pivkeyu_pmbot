"""回归：reaction 载荷必须是 ReactionType 对象，且表情在 Telegram 允许集合内。

历史 bug：载荷写成 ``[{"type": "emoji", "emoji": "👁"}]``，python-telegram-bot
在参数解析阶段就抛 ``TypeError: unhashable type: 'dict'``，Web 面板回复因此返回
500 服务器内部错误——而消息其实早已发到 Telegram。
"""

import unittest

from telegram import ReactionTypeEmoji
from telegram.constants import ReactionEmoji

from utils import reactions

# ReactionEmoji 是普通 Enum（成员不是 str 子类），必须比 value
ALLOWED_EMOJIS = {member.value for member in ReactionEmoji}


class TestReactionPayload(unittest.TestCase):
    def test_payloads_are_reaction_type_objects(self):
        for payload in (reactions.seen_reaction(), reactions.read_reaction()):
            self.assertIsInstance(payload, list)
            self.assertEqual(len(payload), 1)
            self.assertIsInstance(payload[0], ReactionTypeEmoji)

    def test_emoji_is_accepted_by_telegram(self):
        # 不在允许集合里的表情会被 Telegram 以 BadRequest 拒绝（调用方静默吞掉）
        self.assertIn(reactions.REACTION_SEEN, ALLOWED_EMOJIS)
        self.assertIn(reactions.REACTION_READ, ALLOWED_EMOJIS)

    def test_dict_payload_is_rejected_before_any_request(self):
        """记录 bug 的机理：dict 载荷在联网之前就抛 TypeError。"""
        import asyncio

        from telegram import Bot

        bot = Bot(token='123456:TEST')
        with self.assertRaises(TypeError):
            asyncio.run(bot.set_message_reaction(
                chat_id=-100123,
                message_id=7,
                reaction=[{'type': 'emoji', 'emoji': '👁'}],
            ))


if __name__ == '__main__':
    unittest.main()
