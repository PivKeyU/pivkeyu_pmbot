import random
import time
from dataclasses import dataclass
from typing import Optional

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from database import models as db
from config import config
from utils import copy as copy_text
from services import captcha_image
from services.gemini_service import gemini_service

pending_verifications = {}

# 验证方式：text = 常识文字题，image = 图片验证码，random = 每次随机二选一
VERIFICATION_MODES = ('random', 'text', 'image')


@dataclass
class VerificationPrompt:
    """一道待展示的验证题：文字 + 选项按钮，图片题额外带 PNG。"""
    text: str
    question: str
    keyboard: InlineKeyboardMarkup
    image: Optional[bytes] = None


def _cleanup_expired_verifications() -> None:
    """惰性清理过期的小验证会话，防止内存 dict 无限膨胀。"""
    now = time.time()
    expired = [uid for uid, session in pending_verifications.items() if now > session.get('expires_at', now)]
    for uid in expired:
        del pending_verifications[uid]


def _pick_mode() -> str:
    mode = str(getattr(config, 'VERIFICATION_MODE', 'random') or 'random').strip().lower()
    if mode not in VERIFICATION_MODES:
        mode = 'random'
    if mode == 'random':
        return random.choice(('text', 'image'))
    return mode


async def _new_challenge() -> dict:
    if _pick_mode() == 'image':
        return captcha_image.generate_image_challenge()
    return await gemini_service.generate_verification_challenge()


def _build_prompt(session: dict) -> VerificationPrompt:
    buttons = [InlineKeyboardButton(option, callback_data=f"verify_{option}") for option in session['options']]
    # 选项较长时排成 2×2，避免一行四个按钮把文字截断
    per_row = 4 if max(len(option) for option in session['options']) <= 4 else 2
    keyboard = InlineKeyboardMarkup([buttons[i:i + per_row] for i in range(0, len(buttons), per_row)])
    return VerificationPrompt(
        text=f"{copy_text.VERIFY_INVITE}\n\n{session['question']}",
        question=session['question'],
        keyboard=keyboard,
        image=session.get('image'),
    )


async def _start_session(user_id: int, attempts: int) -> VerificationPrompt:
    challenge = await _new_challenge()
    pending_verifications[user_id] = {
        'answer': challenge['correct_answer'],
        'question': challenge['question'],
        'options': challenge['options'],
        'image': challenge.get('image'),
        'attempts': attempts,
        'created_at': time.time(),
        'expires_at': time.time() + config.VERIFICATION_TIMEOUT
    }
    return _build_prompt(pending_verifications[user_id])


async def send_verification_prompt(message, prompt: VerificationPrompt, text: str = None):
    """回复一道验证题：图片题发图片（文字放进说明），文字题直接发文字。"""
    text = text if text is not None else prompt.text
    if prompt.image:
        return await message.reply_photo(photo=prompt.image, caption=text, reply_markup=prompt.keyboard)
    return await message.reply_text(text=text, reply_markup=prompt.keyboard)


async def create_verification(user_id: int) -> VerificationPrompt:
    _cleanup_expired_verifications()
    existing_attempts = pending_verifications.get(user_id, {}).get('attempts', 0)
    return await _start_session(user_id, existing_attempts)

async def verify_answer(user_id: int, answer: str):
    _cleanup_expired_verifications()

    if user_id not in pending_verifications:
        return False, copy_text.with_deco("小验证已经过期或不见啦，请客人重新来一次。", 'ERROR'), False, None

    verification = pending_verifications[user_id]

    if time.time() > verification['expires_at']:
        del pending_verifications[user_id]
        return False, copy_text.with_deco("小验证超时啦，请客人重新发送消息。", 'ERROR'), False, None
    
    verification['attempts'] += 1
    
    if answer == verification['answer']:
        del pending_verifications[user_id]
        await db.update_user_verification(user_id, is_verified=True)
        return True, copy_text.with_deco("验证通过啦，女仆为客人开门。", 'OK'), False, None
    
    if verification['attempts'] >= config.MAX_VERIFICATION_ATTEMPTS:
        del pending_verifications[user_id]
        
        await db.add_to_blacklist(user_id, reason="女仆小验证失败次数过多", blocked_by=config.BOT_ID)
        message = copy_text.with_deco_head(
            "哼，才不是女仆要为难客人呢……失败次数太多，女仆只能先把通道关上。\n\n"
            "如果客人觉得是误会，重新发消息走一遍解封验证就好啦。",
            'BLOCK'
        )
        return False, message, True, None
    
    # 答错换一道新题（随机模式下题型也会重新抽）
    new_prompt = await _start_session(user_id, verification['attempts'])
    remaining = config.MAX_VERIFICATION_ATTEMPTS - verification['attempts']
    # 首次答错保持温柔，第 2 次起升级为傲娇；{remaining} 次机会的真实信息不变
    new_message = copy_text.verify_wrong(remaining, verification['attempts'])
    return False, new_message, False, new_prompt

def is_verification_pending(user_id: int) -> tuple[bool, bool]:
    _cleanup_expired_verifications()

    if user_id not in pending_verifications:
        return False, True

    verification = pending_verifications[user_id]
    is_expired = time.time() > verification['expires_at']

    if is_expired:
        del pending_verifications[user_id]
        return False, True
    
    return True, False

def get_pending_verification_message(user_id: int) -> Optional[VerificationPrompt]:
    _cleanup_expired_verifications()

    if user_id not in pending_verifications:
        return None

    verification = pending_verifications[user_id]

    if time.time() > verification['expires_at']:
        del pending_verifications[user_id]
        return None

    return _build_prompt(verification)
