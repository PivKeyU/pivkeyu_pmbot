import time
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from database import models as db
from config import config
from utils import copy as copy_text
from services.gemini_service import gemini_service

pending_verifications = {}


def _cleanup_expired_verifications() -> None:
    """惰性清理过期的小验证会话，防止内存 dict 无限膨胀。"""
    now = time.time()
    expired = [uid for uid, session in pending_verifications.items() if now > session.get('expires_at', now)]
    for uid in expired:
        del pending_verifications[uid]

async def create_verification(user_id: int):
    _cleanup_expired_verifications()

    challenge = await gemini_service.generate_verification_challenge()
    question = challenge['question']
    correct_answer = challenge['correct_answer']
    options = challenge['options']
    
    existing_attempts = pending_verifications.get(user_id, {}).get('attempts', 0)
    
    pending_verifications[user_id] = {
        'answer': correct_answer,
        'question': question,
        'options': options,
        'attempts': existing_attempts,
        'created_at': time.time(),
        'expires_at': time.time() + config.VERIFICATION_TIMEOUT
    }
    
    keyboard = [
        [InlineKeyboardButton(option, callback_data=f"verify_{option}") for option in options]
    ]
    
    return f"{copy_text.VERIFY_INVITE}\n\n{question}", InlineKeyboardMarkup(keyboard)

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
    
    challenge = await gemini_service.generate_verification_challenge()
    new_question = challenge['question']
    new_correct_answer = challenge['correct_answer']
    new_options = challenge['options']
    
    pending_verifications[user_id] = {
        'answer': new_correct_answer,
        'question': new_question,
        'options': new_options,
        'attempts': verification['attempts'],
        'created_at': time.time(),
        'expires_at': time.time() + config.VERIFICATION_TIMEOUT
    }
    
    keyboard = [
        [InlineKeyboardButton(option, callback_data=f"verify_{option}") for option in new_options]
    ]
    
    new_question_text = f"{copy_text.VERIFY_INVITE}\n\n{new_question}"
    remaining = config.MAX_VERIFICATION_ATTEMPTS - verification['attempts']
    # 首次答错保持温柔，第 2 次起升级为傲娇；{remaining} 次机会的真实信息不变
    new_message = copy_text.verify_wrong(remaining, verification['attempts'])
    return False, new_message, False, (new_question_text, InlineKeyboardMarkup(keyboard))

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

def get_pending_verification_message(user_id: int):
    _cleanup_expired_verifications()

    if user_id not in pending_verifications:
        return None

    verification = pending_verifications[user_id]

    if time.time() > verification['expires_at']:
        del pending_verifications[user_id]
        return None
    
    question = verification['question']
    options = verification['options']
    
    keyboard = [
        [InlineKeyboardButton(option, callback_data=f"verify_{option}") for option in options]
    ]
    
    return question, InlineKeyboardMarkup(keyboard)
