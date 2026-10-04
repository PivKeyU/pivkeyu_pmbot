"""Use real, immutable Telegram objects to verify callback acknowledgement."""
import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from telegram import CallbackQuery, Chat, Message, Update, User

from handlers import callback_handler


class CallbackAcknowledgementTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.bot = SimpleNamespace(
            answer_callback_query=AsyncMock(return_value=True),
            edit_message_text=AsyncMock(return_value=True),
        )
        user = User(id=1, first_name='Admin', is_bot=False)
        message = Message(
            message_id=1, date=datetime.now(timezone.utc),
            chat=Chat(id=1, type='private'), from_user=user, text='panel',
        )
        message.set_bot(self.bot)
        self.query = CallbackQuery(
            id='callback-test', from_user=user, chat_instance='test',
            message=message, data='panel_updatebot',
        )
        self.query.set_bot(self.bot)
        self.update = Update(update_id=1, callback_query=self.query)
        self.context = SimpleNamespace(application=SimpleNamespace(bot_data={}))

    async def test_real_callback_is_acknowledged_before_slow_update_query(self):
        started, release = asyncio.Event(), asyncio.Event()

        async def slow_status():
            started.set()
            await release.wait()
            return 'update status', None

        with patch.object(callback_handler.db, 'is_admin', AsyncMock(return_value=True)), \
             patch.object(callback_handler, '_build_updatebot_view', slow_status):
            task = asyncio.create_task(callback_handler.handle_callback(self.update, self.context))
            try:
                await asyncio.wait_for(started.wait(), timeout=1)
                self.assertEqual(self.bot.answer_callback_query.await_count, 1)
                self.assertFalse(task.done())
            finally:
                release.set()
                await task
        self.assertEqual(self.bot.answer_callback_query.await_count, 1)
        self.assertEqual(self.bot.edit_message_text.await_args.kwargs['text'], 'update status')

    async def test_update_timeout_returns_feedback_and_a_back_button(self):
        async def stalled_status():
            await asyncio.Event().wait()

        with patch.object(callback_handler.db, 'is_admin', AsyncMock(return_value=True)), \
             patch.object(callback_handler, '_build_updatebot_view', stalled_status), \
             patch.object(callback_handler, 'UPDATE_STATUS_TIMEOUT', 0.01):
            await callback_handler.handle_callback(self.update, self.context)
        self.assertIn('超时', self.bot.edit_message_text.await_args.kwargs['text'])
        self.assertIsNotNone(self.bot.edit_message_text.await_args.kwargs['reply_markup'])
        self.assertEqual(self.bot.answer_callback_query.await_count, 1)

    async def test_permission_denial_keeps_alert_and_skips_update_query(self):
        builder = AsyncMock()
        with patch.object(callback_handler.db, 'is_admin', AsyncMock(return_value=False)), \
             patch.object(callback_handler, '_build_updatebot_view', builder):
            await callback_handler.handle_callback(self.update, self.context)
        self.assertTrue(self.bot.answer_callback_query.await_args.kwargs['show_alert'])
        self.assertEqual(self.bot.answer_callback_query.await_count, 1)
        builder.assert_not_awaited()

    async def test_explicit_answers_are_sent_only_once(self):
        async def implementation(update, context, answer_callback):
            await answer_callback('first')
            await answer_callback('second')

        with patch.object(callback_handler, '_handle_callback_impl', implementation):
            await callback_handler.handle_callback(self.update, self.context)
        self.assertEqual(self.bot.answer_callback_query.await_count, 1)
        self.assertEqual(self.bot.answer_callback_query.await_args.kwargs['text'], 'first')

    async def test_fallback_acknowledges_branches_without_explicit_answer(self):
        with patch.object(callback_handler, '_handle_callback_impl', AsyncMock()):
            await callback_handler.handle_callback(self.update, self.context)
        self.assertEqual(self.bot.answer_callback_query.await_count, 1)

    async def test_exception_still_acknowledges_the_callback(self):
        with patch.object(callback_handler, '_handle_callback_impl', AsyncMock(side_effect=RuntimeError('failure'))):
            with self.assertRaisesRegex(RuntimeError, 'failure'):
                await callback_handler.handle_callback(self.update, self.context)
        self.assertEqual(self.bot.answer_callback_query.await_count, 1)

    async def test_delegated_network_callback_is_acknowledged_only_once(self):
        from network_test import handlers as network_handlers

        query = CallbackQuery(
            id='network-test', from_user=self.query.from_user, chat_instance='test',
            message=self.query.message, data='nt_unknown',
        )
        query.set_bot(self.bot)
        update = Update(update_id=2, callback_query=query)
        with patch.object(network_handlers, 'user_data', {}):
            await callback_handler.handle_callback(update, self.context)
        self.assertEqual(self.bot.answer_callback_query.await_count, 1)


if __name__ == '__main__':
    unittest.main()
