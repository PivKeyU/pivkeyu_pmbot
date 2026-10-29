"""图片验证码：本地用 Pillow 画一张扭曲的字符图，客人从按钮里挑出图上的字符。

不依赖任何 AI 接口，没配 API Key 也能用。按钮里放 4 个长得很像的候选
（只差一两个字符），逼客人真的去看图，而不是随手乱点。
"""

import io
import math
import random

from PIL import Image, ImageDraw, ImageFilter, ImageFont

# 去掉 0/O、1/I/L 这类肉眼容易混淆的字符
CHARSET = '23456789ABCDEFGHJKMNPQRSTUVWXYZ'
CODE_LENGTH = 4
OPTION_COUNT = 4

WIDTH, HEIGHT = 320, 120
FONT_SIZE = 64

IMAGE_QUESTION = '请看图片，点选和图中字符完全一致的那一项～'


def random_code(length: int = CODE_LENGTH) -> str:
    return ''.join(random.choice(CHARSET) for _ in range(length))


def similar_codes(code: str, count: int) -> list:
    """生成 count 个与 code 只差 1~2 个字符的干扰项（互不相同，也不等于 code）。"""
    result = set()
    while len(result) < count:
        chars = list(code)
        for index in random.sample(range(len(chars)), random.choice((1, 2))):
            chars[index] = random.choice(CHARSET.replace(chars[index], ''))
        candidate = ''.join(chars)
        if candidate != code:
            result.add(candidate)
    return list(result)


def _load_font(size: int):
    """优先用 Pillow 内置的可缩放字体（Pillow>=10.1 + FreeType），否则退回位图字体。"""
    try:
        return ImageFont.load_default(size=size), True
    except (TypeError, OSError):
        return ImageFont.load_default(), False


def _random_dark_color():
    return tuple(random.randint(20, 120) for _ in range(3))


def _random_light_color():
    return tuple(random.randint(225, 250) for _ in range(3))


def _draw_char(char: str, font, scalable: bool) -> Image.Image:
    """把单个字符画在透明小画布上并随机旋转，粘回主图时会有错位感。"""
    canvas_size = FONT_SIZE + 30
    layer = Image.new('RGBA', (canvas_size, canvas_size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    if scalable:
        draw.text((canvas_size / 2, canvas_size / 2), char, font=font, fill=_random_dark_color(), anchor='mm')
    else:
        # 位图字体很小：先画小图再放大
        small = Image.new('RGBA', (16, 16), (0, 0, 0, 0))
        ImageDraw.Draw(small).text((3, 2), char, font=font, fill=_random_dark_color())
        small = small.resize((FONT_SIZE, FONT_SIZE), Image.NEAREST)
        layer.paste(small, (15, 15), small)
    return layer.rotate(random.uniform(-28, 28), resample=Image.BICUBIC, expand=False)


def render_captcha(code: str) -> bytes:
    """渲染验证码 PNG：渐变底色 + 旋转错位字符 + 干扰曲线与噪点。"""
    image = Image.new('RGB', (WIDTH, HEIGHT), _random_light_color())
    draw = ImageDraw.Draw(image)

    # 底层噪点
    for _ in range(WIDTH * HEIGHT // 25):
        xy = (random.randrange(WIDTH), random.randrange(HEIGHT))
        draw.point(xy, fill=tuple(random.randint(150, 230) for _ in range(3)))

    font, scalable = _load_font(FONT_SIZE)
    slot = (WIDTH - 40) / len(code)
    for index, char in enumerate(code):
        layer = _draw_char(char, font, scalable)
        x = int(20 + slot * index + random.uniform(-6, 6) + (slot - layer.width) / 2)
        y = int((HEIGHT - layer.height) / 2 + random.uniform(-10, 10))
        image.paste(layer, (x, y), layer)

    # 穿过字符的正弦干扰线
    for _ in range(3):
        amplitude = random.uniform(8, 22)
        period = random.uniform(60, 140)
        phase = random.uniform(0, math.pi * 2)
        offset = random.uniform(HEIGHT * 0.3, HEIGHT * 0.7)
        points = [
            (x, offset + amplitude * math.sin(x / period * math.pi * 2 + phase))
            for x in range(0, WIDTH + 1, 4)
        ]
        draw.line(points, fill=_random_dark_color(), width=random.choice((2, 3)))

    # 前景噪点
    for _ in range(120):
        x, y = random.randrange(WIDTH), random.randrange(HEIGHT)
        draw.ellipse((x, y, x + 2, y + 2), fill=_random_dark_color())

    image = image.filter(ImageFilter.SMOOTH)
    buffer = io.BytesIO()
    image.save(buffer, format='PNG')
    return buffer.getvalue()


def generate_image_challenge() -> dict:
    """返回与文字题同结构的验证题，多一个 image 字段（PNG 字节）。"""
    code = random_code()
    options = similar_codes(code, OPTION_COUNT - 1) + [code]
    random.shuffle(options)
    return {
        'question': IMAGE_QUESTION,
        'correct_answer': code,
        'options': options,
        'image': render_captcha(code),
    }
