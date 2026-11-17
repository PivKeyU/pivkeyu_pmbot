import asyncio
import io
import logging
from typing import Optional
from PIL import Image

logger = logging.getLogger(__name__)


def _convert_sticker_sync(file: bytes) -> Optional[bytes]:
    with Image.open(io.BytesIO(file)) as img:
        if img.mode != 'RGB':
            img = img.convert('RGB')
        output_buffer = io.BytesIO()
        img.save(output_buffer, format='PNG')
        return output_buffer.getvalue()


async def sticker_to_image(file: bytes) -> Optional[bytes]:
    try:
        return await asyncio.to_thread(_convert_sticker_sync, bytes(file))
    except Exception as e:
        logger.warning("Error converting sticker to image: %s", e)
        return None
