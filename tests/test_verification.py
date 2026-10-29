"""人机验证：图片验证码、文字题多样性、验证会话流程与题型切换时的消息处理。"""

import asyncio
import random
from types import SimpleNamespace

from PIL import Image
import io

from config import config
from database import models as db
from database.db_manager import DatabaseManager
from handlers import callback_handler
from services import ai_service, captcha_image, text_questions, verification


def test_every_text_generator_produces_valid_questions():
    random.seed(20261030)
    for generator, _weight in text_questions.GENERATORS:
        for _ in range(300):
            data = text_questions.validate_question_data(generator())
            options = data['incorrect_answers'] + [data['correct_answer']]
            assert len(set(options)) == 4, (generator.__name__, data)


def test_static_bank_is_clean():
    for question, correct, incorrect in text_questions.STATIC_QUESTIONS:
        text_questions.validate_question_data({
            'question': question, 'correct_answer': correct, 'incorrect_answers': incorrect,
        })


def test_category_words_are_not_shared():
    # 一个词只属于一个类别，否则「哪个属于 / 不属于」会出现两个正确答案
    seen = {}
    for category, words in text_questions.CATEGORIES.items():
        for word in words:
            assert word not in seen, f'{word} 同时在 {seen.get(word)} 和 {category}'
            seen[word] = category


def test_local_questions_are_varied():
    random.seed(7)
    challenges = [ai_service._get_local_question() for _ in range(200)]
    # 原来的固定题库只有 30 道；现在 200 次里「题目 + 选项」应该几乎不重复
    distinct = {(c['question'], tuple(sorted(c['options']))) for c in challenges}
    assert len(distinct) > 180
    assert len({c['question'] for c in challenges}) > 100


def test_ai_prompt_changes_every_time():
    prompts = {ai_service._verification_prompt() for _ in range(20)}
    assert len(prompts) > 1
    assert all(p.startswith(ai_service.VERIFICATION_CAPTCHA_PROMPT) for p in prompts)


def test_ai_question_validation_rejects_bad_output():
    bad = [
        {'question': '?', 'correct_answer': 'A', 'incorrect_answers': ['A', 'B', 'C']},
        {'question': '?', 'correct_answer': 'A', 'incorrect_answers': ['B', 'C']},
        {'question': '?', 'correct_answer': '很长' * 20, 'incorrect_answers': ['B', 'C', 'D']},
    ]
    for data in bad:
        try:
            ai_service._challenge_from_data(data)
        except ValueError:
            continue
        raise AssertionError(f'应当拒绝：{data}')


def test_image_captcha():
    challenge = captcha_image.generate_image_challenge()
    assert challenge['correct_answer'] in challenge['options']
    assert len(set(challenge['options'])) == captcha_image.OPTION_COUNT
    image = Image.open(io.BytesIO(challenge['image']))
    assert image.format == 'PNG'
    assert image.size == (captcha_image.WIDTH, captcha_image.HEIGHT)
    # 干扰项只差 1~2 个字符，逼客人真的去看图
    code = challenge['correct_answer']
    for option in challenge['options']:
        if option != code:
            assert 1 <= sum(a != b for a, b in zip(option, code)) <= 2


async def _verification_flow(tmp_path):
    db_manager = DatabaseManager(str(tmp_path / 'verify.db'))
    await db_manager.initialize()
    await db.add_user(777, 'guest', 'Guest', None)
    original_mode = config.VERIFICATION_MODE
    original_gemini = config.GEMINI_API_KEY
    config.GEMINI_API_KEY = None  # 文字题走本地题库，不发外部请求
    try:
        config.VERIFICATION_MODE = 'image'
        prompt = await verification.create_verification(777)
        assert prompt.image and prompt.text.endswith(captcha_image.IMAGE_QUESTION)
        session = verification.pending_verifications[777]

        # 答错：换一道新题，切到文字题也照样能出
        config.VERIFICATION_MODE = 'text'
        wrong = next(o for o in session['options'] if o != session['answer'])
        success, _message, banned, new_prompt = await verification.verify_answer(777, wrong)
        assert not success and not banned
        assert new_prompt.image is None and new_prompt.question

        # 未答完时重发的仍是同一道题
        pending = verification.get_pending_verification_message(777)
        assert pending.question == new_prompt.question

        answer = verification.pending_verifications[777]['answer']
        success, _message, banned, new_prompt = await verification.verify_answer(777, answer)
        assert success and new_prompt is None
        assert (await db.get_user(777))['is_verified']
    finally:
        config.VERIFICATION_MODE = original_mode
        config.GEMINI_API_KEY = original_gemini
        verification.pending_verifications.pop(777, None)
        await db_manager.close_all()


def test_verification_flow_switches_modes(tmp_path):
    asyncio.run(_verification_flow(tmp_path))


def test_random_mode_uses_both_kinds():
    original = config.VERIFICATION_MODE
    config.VERIFICATION_MODE = 'random'
    try:
        assert {verification._pick_mode() for _ in range(100)} == {'text', 'image'}
    finally:
        config.VERIFICATION_MODE = original


class FakeQuery:
    """记录回调里对验证消息做了什么操作。"""

    def __init__(self, is_photo):
        self.calls = []
        self.message = SimpleNamespace(photo=[object()] if is_photo else None, chat_id=777, delete=self._delete)
        self._bot = SimpleNamespace(send_photo=self._record('send_photo'), send_message=self._record('send_message'))
        self.edit_message_text = self._record('edit_text')
        self.edit_message_caption = self._record('edit_caption')
        self.edit_message_media = self._record('edit_media')

    def _record(self, name):
        async def call(**kwargs):
            self.calls.append(name)
        return call

    async def _delete(self):
        self.calls.append('delete')

    def get_bot(self):
        return self._bot


def _prompt(image):
    return verification.VerificationPrompt(text='题', question='题', keyboard=None, image=b'png' if image else None)


def test_next_verification_message_handling():
    cases = {
        (True, True): ['edit_media'],
        (False, False): ['edit_text'],
        (True, False): ['delete', 'send_message'],
        (False, True): ['delete', 'send_photo'],
    }
    for (old_photo, new_image), expected in cases.items():
        query = FakeQuery(old_photo)
        asyncio.run(callback_handler._show_next_verification(query, _prompt(new_image), '换题'))
        assert query.calls == expected, (old_photo, new_image, query.calls)

    query = FakeQuery(True)
    asyncio.run(callback_handler._show_verification_result(query, '通过'))
    assert query.calls == ['edit_caption']
