"""管理员回复和广播应进入 Web 会话记录。"""

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from handlers import admin_handler
from services import broadcast
from utils.message_sender import get_message_media_info


def sent_message(message_id=902, **attributes):
    defaults = {
        "message_id": message_id,
        "photo": None,
        "animation": None,
        "video": None,
        "document": None,
        "audio": None,
        "voice": None,
        "video_note": None,
        "sticker": None,
    }
    defaults.update(attributes)
    return SimpleNamespace(**defaults)


class TestOutgoingMessageHistory(unittest.IsolatedAsyncioTestCase):
    async def test_admin_reply_is_saved_with_private_and_forum_message_ids(self):
        forum_message = SimpleNamespace(
            reply_to_message=None,
            text="管理员回复",
            caption=None,
            chat_id=-100456,
            message_id=701,
            message_thread_id=42,
        )
        update = SimpleNamespace(message=forum_message)
        context = SimpleNamespace(bot=SimpleNamespace())
        delivered = sent_message()

        with (
            patch.object(admin_handler, "send_message_by_type", new=AsyncMock(return_value=delivered)),
            patch.object(admin_handler.db, "save_message_mapping", new=AsyncMock()) as save_mapping,
            patch.object(admin_handler.db, "save_message", new=AsyncMock()) as save_message,
        ):
            result = await admin_handler._send_reply_to_user(update, context, 123)

        self.assertIs(result, delivered)
        save_mapping.assert_awaited_once()
        save_message.assert_awaited_once_with(
            user_id=123,
            message_id=902,
            content="管理员回复",
            direction="admin_to_user",
            media_type=None,
            media_file_id=None,
            dest_message_id=701,
            thread_id=42,
        )

    async def test_admin_media_reply_saves_sent_media_file(self):
        forum_message = SimpleNamespace(
            reply_to_message=None,
            text=None,
            caption="图片说明",
            chat_id=-100456,
            message_id=702,
            message_thread_id=42,
        )
        update = SimpleNamespace(message=forum_message)
        context = SimpleNamespace(bot=SimpleNamespace())
        delivered = sent_message(photo=[SimpleNamespace(file_id="telegram-photo-id")])

        with (
            patch.object(admin_handler, "send_message_by_type", new=AsyncMock(return_value=delivered)),
            patch.object(admin_handler.db, "save_message_mapping", new=AsyncMock()),
            patch.object(admin_handler.db, "save_message", new=AsyncMock()) as save_message,
        ):
            await admin_handler._send_reply_to_user(update, context, 123)

        self.assertEqual(get_message_media_info(delivered), ("photo", "telegram-photo-id"))
        self.assertEqual(save_message.await_args.kwargs["content"], "图片说明")
        self.assertEqual(save_message.await_args.kwargs["media_type"], "photo")
        self.assertEqual(save_message.await_args.kwargs["media_file_id"], "telegram-photo-id")

    async def test_text_broadcast_is_saved_and_published(self):
        delivered = sent_message()
        context = SimpleNamespace(bot=SimpleNamespace(send_message=AsyncMock(return_value=delivered)))

        with (
            patch.object(broadcast.db, "create_broadcast", new=AsyncMock(return_value=55)),
            patch.object(broadcast.db, "save_broadcast_delivery", new=AsyncMock()),
            patch.object(broadcast.db, "save_message_mapping", new=AsyncMock()),
            patch.object(broadcast.db, "save_message", new=AsyncMock()) as save_message,
            patch.object(broadcast.db, "update_broadcast_counts", new=AsyncMock()),
            patch.object(broadcast, "_send_text_mirror", new=AsyncMock(return_value=None)),
            patch.object(broadcast.panel_events, "publish") as publish,
        ):
            result = await broadcast.send_text_broadcast(
                context, [{"user_id": 123, "thread_id": 42}], "公告内容", 99, "all"
            )

        self.assertEqual(result.success, 1)
        self.assertEqual(save_message.await_args.kwargs["direction"], "broadcast_to_user")
        self.assertEqual(save_message.await_args.kwargs["content"], "公告内容")
        publish.assert_called_once_with(
            "message",
            user_id=123,
            direction="broadcast_to_user",
            preview="公告内容",
            message_id=902,
        )

    async def test_media_broadcast_is_saved_and_published(self):
        source = SimpleNamespace(
            chat_id=-100456,
            message_id=703,
            text=None,
            caption="广播图片",
            photo=[SimpleNamespace(file_id="source-photo")],
            animation=None,
            video=None,
            document=None,
            audio=None,
            voice=None,
            sticker=None,
        )
        delivered = sent_message(photo=[SimpleNamespace(file_id="delivered-photo")])
        context = SimpleNamespace(bot=SimpleNamespace())
        mirror = SimpleNamespace(message_id=803, message_thread_id=42)

        with (
            patch.object(broadcast.db, "create_broadcast", new=AsyncMock(return_value=56)),
            patch.object(broadcast.db, "save_broadcast_delivery", new=AsyncMock()),
            patch.object(broadcast.db, "save_message_mapping", new=AsyncMock()),
            patch.object(broadcast.db, "save_message", new=AsyncMock()) as save_message,
            patch.object(broadcast.db, "update_broadcast_counts", new=AsyncMock()),
            patch.object(broadcast, "send_message_by_type", new=AsyncMock(return_value=delivered)),
            patch.object(broadcast, "_send_message_mirror", new=AsyncMock(return_value=mirror)),
            patch.object(broadcast.panel_events, "publish") as publish,
        ):
            result = await broadcast.send_message_broadcast(
                context, [{"user_id": 123, "thread_id": 42}], source, 99, "all"
            )

        self.assertEqual(result.success, 1)
        self.assertEqual(save_message.await_args.kwargs["direction"], "broadcast_to_user")
        self.assertEqual(save_message.await_args.kwargs["media_type"], "photo")
        self.assertEqual(save_message.await_args.kwargs["media_file_id"], "delivered-photo")
        self.assertEqual(save_message.await_args.kwargs["dest_message_id"], 803)
        publish.assert_called_once()
        self.assertEqual(publish.call_args.kwargs["media_type"], "photo")


if __name__ == "__main__":
    unittest.main()
