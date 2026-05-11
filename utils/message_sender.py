from telegram import (
    InputMediaAnimation,
    InputMediaAudio,
    InputMediaDocument,
    InputMediaPhoto,
    InputMediaVideo,
)

async def send_message_by_type(
    bot,
    message,
    chat_id,
    thread_id=None,
    disable_web_page_preview=False,
    reply_to_message_id=None,
):
    common_kwargs = {
        "chat_id": chat_id,
        "message_thread_id": thread_id,
    }
    if reply_to_message_id:
        common_kwargs["reply_to_message_id"] = reply_to_message_id
        common_kwargs["allow_sending_without_reply"] = True

    if message.text:
        return await bot.send_message(
            **common_kwargs,
            text=message.text,
            entities=message.entities,
            disable_web_page_preview=disable_web_page_preview
        )
    elif message.photo:
        return await bot.send_photo(
            **common_kwargs,
            photo=message.photo[-1].file_id,
            caption=message.caption,
            caption_entities=message.caption_entities
        )
    elif message.animation:
        return await bot.send_animation(
            **common_kwargs,
            animation=message.animation.file_id,
            caption=message.caption,
            caption_entities=message.caption_entities
        )
    elif message.video:
        return await bot.send_video(
            **common_kwargs,
            video=message.video.file_id,
            caption=message.caption,
            caption_entities=message.caption_entities
        )
    elif message.document:
        return await bot.send_document(
            **common_kwargs,
            document=message.document.file_id,
            caption=message.caption,
            caption_entities=message.caption_entities
        )
    elif message.audio:
        return await bot.send_audio(
            **common_kwargs,
            audio=message.audio.file_id,
            caption=message.caption,
            caption_entities=message.caption_entities
        )
    elif message.voice:
        return await bot.send_voice(
            **common_kwargs,
            voice=message.voice.file_id,
            caption=message.caption,
            caption_entities=message.caption_entities
        )
    elif message.video_note:
        return await bot.send_video_note(
            **common_kwargs,
            video_note=message.video_note.file_id,
        )
    elif message.sticker:
        return await bot.send_sticker(
            **common_kwargs,
            sticker=message.sticker.file_id,
        )
    return None


def _build_input_media(message):
    caption_kwargs = {
        "caption": message.caption,
        "caption_entities": message.caption_entities,
    }

    if message.photo:
        return InputMediaPhoto(message.photo[-1].file_id, **caption_kwargs)
    if message.animation:
        return InputMediaAnimation(message.animation.file_id, **caption_kwargs)
    if message.video:
        return InputMediaVideo(message.video.file_id, **caption_kwargs)
    if message.document:
        return InputMediaDocument(message.document.file_id, **caption_kwargs)
    if message.audio:
        return InputMediaAudio(message.audio.file_id, **caption_kwargs)
    return None


async def edit_message_by_type(bot, message, chat_id, message_id, disable_web_page_preview=False):
    if message.text is not None:
        return await bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=message.text,
            entities=message.entities,
            disable_web_page_preview=disable_web_page_preview,
        )

    media = _build_input_media(message)
    if media:
        return await bot.edit_message_media(
            chat_id=chat_id,
            message_id=message_id,
            media=media,
        )

    if message.caption is not None and (message.voice or message.video_note):
        return await bot.edit_message_caption(
            chat_id=chat_id,
            message_id=message_id,
            caption=message.caption,
            caption_entities=message.caption_entities,
        )

    return None
