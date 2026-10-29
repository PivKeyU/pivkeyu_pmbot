"""文字验证题：本地题库 + 程序化出题器（纯数据、零依赖）。

没有 AI Key、或者 AI 出题失败时，从这里出题。题型分三大类：

1. 程序化题目：算术、找规律、比大小、星期 / 月份 / 钟点推算、数符号、
   「哪个不属于 / 哪个属于」分类题（从词库随机组合）、反义词——每次现场生成，几乎不重复；
2. 固定常识题库：按主题分组的人工题目；
3. AI 出题（在 services/ai_service.py）：每次随机指定主题与题型，避免模型反复出同一类题。

所有题目都是「一个正确答案 + 三个干扰项」，选项保持中性、简短（按钮里放得下）。
"""

import random

# --------------------------------------------------------------------------- #
# 分类词库：用来拼「以下哪个不属于 X」「以下哪个属于 X」
# 同一个词只放在一个类别里，避免出现两个都对的选项
# --------------------------------------------------------------------------- #
CATEGORIES = {
    '水果': ['苹果', '香蕉', '橙子', '葡萄', '西瓜', '草莓', '桃子', '梨', '芒果', '樱桃', '菠萝', '荔枝'],
    '蔬菜': ['白菜', '胡萝卜', '黄瓜', '茄子', '菠菜', '土豆', '洋葱', '芹菜', '西兰花', '南瓜', '韭菜', '莴笋'],
    '哺乳动物': ['猫', '狗', '牛', '马', '羊', '老虎', '大象', '熊猫', '兔子', '猴子', '狮子', '狐狸'],
    '鸟类': ['麻雀', '鸽子', '燕子', '老鹰', '孔雀', '画眉', '鹦鹉', '天鹅', '乌鸦', '啄木鸟', '猫头鹰', '喜鹊'],
    '交通工具': ['汽车', '火车', '飞机', '轮船', '自行车', '地铁', '公交车', '摩托车', '高铁', '出租车'],
    '颜色': ['红色', '蓝色', '绿色', '黄色', '紫色', '白色', '黑色', '橙色', '粉色', '灰色'],
    '乐器': ['钢琴', '吉他', '小提琴', '笛子', '二胡', '鼓', '古筝', '萨克斯', '琵琶', '口琴'],
    '文具': ['铅笔', '橡皮', '尺子', '圆珠笔', '笔记本', '订书机', '胶水', '剪刀', '卷笔刀', '文件夹'],
    '家具': ['桌子', '椅子', '沙发', '床', '衣柜', '书架', '茶几', '鞋柜', '凳子', '床头柜'],
    '运动项目': ['足球', '篮球', '游泳', '跑步', '乒乓球', '羽毛球', '排球', '网球', '跳绳', '滑雪'],
    '职业': ['医生', '老师', '厨师', '警察', '司机', '护士', '律师', '消防员', '农民', '快递员'],
    '天气': ['晴天', '下雨', '下雪', '刮风', '打雷', '多云', '冰雹', '雾天'],
    '饮品': ['牛奶', '咖啡', '绿茶', '果汁', '可乐', '豆浆', '矿泉水', '奶茶'],
    '衣物': ['衬衫', '裤子', '裙子', '外套', '袜子', '毛衣', '围巾', '帽子', 'T恤', '手套'],
    '身体部位': ['眼睛', '鼻子', '耳朵', '嘴巴', '手指', '膝盖', '肩膀', '脚踝', '额头', '手腕'],
    '家用电器': ['冰箱', '洗衣机', '电视', '空调', '微波炉', '电饭煲', '吸尘器', '电风扇'],
    '餐具': ['筷子', '勺子', '叉子', '碗', '盘子', '杯子', '餐刀', '汤勺'],
    '花卉': ['玫瑰', '菊花', '荷花', '牡丹', '梅花', '向日葵', '郁金香', '茉莉'],
    '海洋生物': ['章鱼', '海星', '水母', '螃蟹', '海龟', '鲨鱼', '海马', '珊瑚'],
    '昆虫': ['蜜蜂', '蝴蝶', '蚂蚁', '蜻蜓', '蚊子', '苍蝇', '瓢虫', '蝉'],
    '中国城市': ['北京', '上海', '广州', '深圳', '成都', '杭州', '西安', '武汉', '南京', '重庆'],
    '国家': ['中国', '日本', '法国', '巴西', '美国', '英国', '德国', '印度', '埃及', '加拿大'],
}

# 反义词对：两边都可以做题干
ANTONYMS = [
    ('大', '小'), ('高', '矮'), ('长', '短'), ('快', '慢'), ('冷', '热'), ('黑', '白'),
    ('上', '下'), ('左', '右'), ('前', '后'), ('多', '少'), ('胖', '瘦'), ('新', '旧'),
    ('开', '关'), ('哭', '笑'), ('早', '晚'), ('远', '近'), ('轻', '重'), ('软', '硬'),
    ('真', '假'), ('甜', '苦'), ('买', '卖'), ('进', '出'), ('干', '湿'), ('深', '浅'),
    ('宽', '窄'), ('厚', '薄'), ('明亮', '黑暗'), ('安静', '吵闹'), ('勇敢', '胆小'), ('热闹', '冷清'),
]

WEEKDAYS = ['星期一', '星期二', '星期三', '星期四', '星期五', '星期六', '星期日']
MONTHS = ['一月', '二月', '三月', '四月', '五月', '六月', '七月', '八月', '九月', '十月', '十一月', '十二月']
CN_NUM = ['零', '一', '两', '三', '四', '五', '六', '七', '八', '九', '十']
SYMBOL_PAIRS = [('★', '☆'), ('●', '○'), ('▲', '△'), ('■', '□'), ('♥', '♡')]

# --------------------------------------------------------------------------- #
# 固定常识题库（按主题分组；每题答案唯一、选项同类）
# --------------------------------------------------------------------------- #
STATIC_QUESTIONS = [
    # 地理
    ('中国的首都是哪里？', '北京', ['上海', '广州', '深圳']),
    ('地球上最大的海洋是哪个？', '太平洋', ['大西洋', '印度洋', '北冰洋']),
    ('长城位于哪个国家？', '中国', ['日本', '印度', '法国']),
    ('埃菲尔铁塔在哪座城市？', '巴黎', ['伦敦', '罗马', '柏林']),
    ('世界上面积最大的大洲是哪个？', '亚洲', ['非洲', '欧洲', '南美洲']),
    ('中国最长的河流是哪条？', '长江', ['黄河', '珠江', '淮河']),
    ('企鹅主要生活在地球的哪一端？', '南极', ['北极', '赤道', '沙漠']),
    ('熊猫的故乡是哪个国家？', '中国', ['日本', '澳大利亚', '巴西']),
    ('袋鼠是哪个国家的代表动物？', '澳大利亚', ['中国', '加拿大', '埃及']),
    ('金字塔最有名的是哪个国家的？', '埃及', ['希腊', '意大利', '墨西哥']),
    # 自然与科学
    ('太阳从哪个方向升起？', '东方', ['西方', '南方', '北方']),
    ('水的化学式是什么？', 'H2O', ['CO2', 'O2', 'NaCl']),
    ('哪个是太阳系里最大的行星？', '木星', ['土星', '火星', '金星']),
    ('离太阳最近的行星是哪个？', '水星', ['金星', '地球', '火星']),
    ('水在多少摄氏度结冰？', '0度', ['10度', '50度', '100度']),
    ('标准大气压下水在多少摄氏度沸腾？', '100度', ['50度', '80度', '120度']),
    ('彩虹一共有几种颜色？', '7', ['5', '6', '8']),
    ('植物进行光合作用主要需要什么？', '阳光', ['月光', '灯光', '火光']),
    ('人类呼吸主要需要哪种气体？', '氧气', ['氮气', '氦气', '二氧化碳']),
    ('地球绕太阳转一圈大约需要多久？', '一年', ['一天', '一个月', '一周']),
    ('月亮绕地球转一圈大约需要多久？', '一个月', ['一天', '一年', '一小时']),
    ('冰化成水属于什么现象？', '融化', ['蒸发', '凝固', '燃烧']),
    # 动物
    ('蜜蜂会酿造什么？', '蜂蜜', ['牛奶', '果汁', '糖果']),
    ('哪种动物以长脖子出名？', '长颈鹿', ['河马', '企鹅', '刺猬']),
    ('蝌蚪长大后会变成什么？', '青蛙', ['鱼', '乌龟', '蛇']),
    ('毛毛虫长大后可能变成什么？', '蝴蝶', ['蜜蜂', '蜘蛛', '蚂蚁']),
    ('哪种动物会“喵喵”叫？', '猫', ['狗', '牛', '羊']),
    ('哪种动物会“汪汪”叫？', '狗', ['猫', '鸭子', '猪']),
    ('蜘蛛有几条腿？', '8', ['4', '6', '10']),
    ('昆虫通常有几条腿？', '6', ['4', '8', '10']),
    ('骆驼常见于哪里？', '沙漠', ['海底', '冰川', '雨林树顶']),
    # 生活常识
    ('一年有多少个月？', '12', ['10', '11', '13']),
    ('一周有多少天？', '7', ['5', '6', '8']),
    ('一天有多少个小时？', '24', ['12', '20', '30']),
    ('一小时有多少分钟？', '60', ['30', '50', '100']),
    ('一分钟有多少秒？', '60', ['30', '100', '120']),
    ('1 米等于多少厘米？', '100', ['10', '50', '1000']),
    ('1 千克等于多少克？', '1000', ['10', '100', '500']),
    ('红绿灯中哪种颜色表示可以通行？', '绿灯', ['红灯', '黄灯', '蓝灯']),
    ('火警电话是多少？', '119', ['110', '120', '114']),
    ('急救电话是多少？', '120', ['110', '119', '122']),
    ('报警电话是多少？', '110', ['119', '120', '114']),
    ('人体有几个心脏？', '1', ['2', '3', '4']),
    ('正常人一只手有几根手指？', '5', ['4', '6', '10']),
    ('下雨天出门通常要带什么？', '雨伞', ['墨镜', '扇子', '泳圈']),
    ('刷牙主要用什么？', '牙刷', ['梳子', '毛巾', '筷子']),
    ('一年中最冷的季节是？', '冬天', ['春天', '夏天', '秋天']),
    ('一年中最热的季节是？', '夏天', ['春天', '秋天', '冬天']),
    ('一年有几个季节？', '4', ['2', '3', '6']),
    # 节日与文化
    ('春节一般吃什么传统食物（北方）？', '饺子', ['月饼', '粽子', '汤圆']),
    ('中秋节的传统食物是什么？', '月饼', ['粽子', '饺子', '年糕']),
    ('端午节的传统食物是什么？', '粽子', ['月饼', '汤圆', '饺子']),
    ('元宵节的传统食物是什么？', '汤圆', ['粽子', '月饼', '腊八粥']),
    ('中国的国庆节是几月几日？', '10月1日', ['5月1日', '6月1日', '1月1日']),
    ('儿童节是几月几日？', '6月1日', ['5月1日', '10月1日', '3月8日']),
    ('劳动节是几月几日？', '5月1日', ['6月1日', '10月1日', '8月1日']),
    ('中国国旗上有几颗星？', '5', ['3', '4', '6']),
    ('十二生肖中排第一位的是？', '鼠', ['牛', '虎', '龙']),
    ('十二生肖一共有几个？', '12', ['10', '11', '13']),
    # 语文
    ('“一石二鸟”里有几只鸟？', '2', ['1', '3', '4']),
    ('成语“画蛇添足”里添的是什么？', '足', ['手', '眼', '尾']),
    ('成语“守株待兔”里等的是什么动物？', '兔', ['鹿', '狼', '鸟']),
    ('成语“对牛弹琴”里弹琴给谁听？', '牛', ['马', '羊', '猪']),
    ('成语“井底之蛙”里的动物是？', '青蛙', ['乌龟', '鱼', '螃蟹']),
    ('“春眠不觉晓”的下一句是？', '处处闻啼鸟', ['夜来风雨声', '花落知多少', '疑是地上霜']),
    ('“床前明月光”的下一句是？', '疑是地上霜', ['举头望明月', '低头思故乡', '处处闻啼鸟']),
    ('“白日依山尽”的下一句是？', '黄河入海流', ['欲穷千里目', '更上一层楼', '春风又绿江南岸']),
]

# 无 API Key 时这里是唯一出口：题干补一句女仆邀请语。注意：
#   1) 只加「请客人答题」的引子，**不要**再写一遍「小验证」——外层
#      services/verification.py 已经拼过 VERIFY_INVITE，再写一遍就是两句重复的验证邀请；
#   2) 措辞必须同时对「标准直接提问」和「反向排除提问（以下哪个不属于…？）」通顺。
LOCAL_QUESTION_INVITE_PREFIX = '客人来答答这一题嘛～ '


def _q(question: str, correct, incorrect) -> dict:
    return {
        'question': question,
        'correct_answer': str(correct),
        'incorrect_answers': [str(item) for item in incorrect],
    }


def _number_distractors(answer: int, spread: int = 4, low: int = 0) -> list:
    """生成三个和答案相近、互不相同的数字干扰项。"""
    pool = [n for n in range(answer - spread, answer + spread + 1) if n != answer and n >= low]
    return random.sample(pool, 3)


# --------------------------------------------------------------------------- #
# 程序化出题器
# --------------------------------------------------------------------------- #

def gen_arithmetic() -> dict:
    op = random.choice(('+', '-', '×'))
    if op == '+':
        a, b = random.randint(2, 30), random.randint(2, 30)
        answer = a + b
    elif op == '-':
        a = random.randint(10, 40)
        b = random.randint(1, a - 1)
        answer = a - b
    else:
        a, b = random.randint(2, 9), random.randint(2, 9)
        answer = a * b
    return _q(f'{a} {op} {b} 等于多少？', answer, _number_distractors(answer, low=0))


def gen_sequence() -> dict:
    start = random.randint(1, 12)
    step = random.choice((2, 3, 5, 10))
    items = [start + step * i for i in range(4)]
    answer = start + step * 4
    shown = '、'.join(str(n) for n in items)
    return _q(f'找规律：{shown}、？ 问号处应该是几？', answer, _number_distractors(answer, spread=step + 2, low=1))


def gen_compare() -> dict:
    numbers = random.sample(range(10, 100), 4)
    biggest = random.random() < 0.5
    answer = max(numbers) if biggest else min(numbers)
    others = [n for n in numbers if n != answer]
    return _q(f'下面哪个数{"最大" if biggest else "最小"}？', answer, others)


def gen_weekday() -> dict:
    start = random.randrange(7)
    offset = random.randint(1, 4)
    forward = random.random() < 0.6
    target = (start + offset) % 7 if forward else (start - offset) % 7
    others = random.sample([d for i, d in enumerate(WEEKDAYS) if i != target], 3)
    direction = '后' if forward else '前'
    return _q(f'今天是{WEEKDAYS[start]}，{CN_NUM[offset]}天{direction}是星期几？', WEEKDAYS[target], others)


def gen_month() -> dict:
    start = random.randrange(12)
    offset = random.randint(1, 3)
    target = (start + offset) % 12
    others = random.sample([m for i, m in enumerate(MONTHS) if i != target], 3)
    return _q(f'{MONTHS[start]}再过{CN_NUM[offset]}个月是几月？', MONTHS[target], others)


def gen_clock() -> dict:
    start = random.randint(1, 8)
    offset = random.randint(1, 4)
    answer = start + offset
    others = random.sample([h for h in range(1, 13) if h != answer], 3)
    return _q(f'现在是 {start} 点整，再过 {offset} 个小时是几点？', f'{answer}点', [f'{h}点' for h in others])


def gen_count_symbols() -> dict:
    solid, hollow = random.choice(SYMBOL_PAIRS)
    count = random.randint(2, 6)
    total = random.randint(count + 2, count + 5)
    symbols = [solid] * count + [hollow] * (total - count)
    random.shuffle(symbols)
    return _q(f'数一数：{"".join(symbols)} 里有几个 {solid}？', count, _number_distractors(count, spread=3, low=1))


def gen_category_outlier() -> dict:
    """以下哪个不属于 X：三个同类 + 一个外类（外类就是答案）。"""
    category, outsider_category = random.sample(list(CATEGORIES), 2)
    members = random.sample(CATEGORIES[category], 3)
    outsider = random.choice(CATEGORIES[outsider_category])
    return _q(f'以下哪个不属于{category}？', outsider, members)


def gen_category_member() -> dict:
    """以下哪个属于 X：一个同类（答案）+ 三个来自不同外类的干扰项。"""
    category = random.choice(list(CATEGORIES))
    others = random.sample([c for c in CATEGORIES if c != category], 3)
    return _q(
        f'以下哪个属于{category}？',
        random.choice(CATEGORIES[category]),
        [random.choice(CATEGORIES[c]) for c in others],
    )


def gen_antonym() -> dict:
    pair = random.choice(ANTONYMS)
    word, answer = pair if random.random() < 0.5 else pair[::-1]
    pool = [w for p in ANTONYMS if p != pair for w in p if len(w) == len(answer)]
    return _q(f'“{word}”的反义词是？', answer, random.sample(pool, 3))


def gen_static() -> dict:
    question, correct, incorrect = random.choice(STATIC_QUESTIONS)
    return _q(question, correct, incorrect)


# (出题器, 权重)：权重越大越常出现
GENERATORS = [
    (gen_static, 6),
    (gen_category_outlier, 4),
    (gen_category_member, 3),
    (gen_arithmetic, 3),
    (gen_antonym, 2),
    (gen_sequence, 2),
    (gen_compare, 2),
    (gen_weekday, 2),
    (gen_month, 1),
    (gen_clock, 1),
    (gen_count_symbols, 2),
]


def random_question_data() -> dict:
    """随机挑一个出题器出题，返回 {question, correct_answer, incorrect_answers}。"""
    generators, weights = zip(*GENERATORS)
    return random.choices(generators, weights=weights, k=1)[0]()


# --------------------------------------------------------------------------- #
# 给 AI 出题用的随机主题与题型：每次请求都不一样，避免模型老出同一类题
# --------------------------------------------------------------------------- #
AI_TOPICS = [
    '水果与蔬菜', '动物习性', '鸟类与昆虫', '海洋生物', '天气与季节', '交通工具', '交通规则',
    '厨房与烹饪', '家用电器', '家具与房间', '身体与健康', '体育运动', '乐器与音乐', '颜色与形状',
    '中国传统节日', '十二生肖', '中国城市与名胜', '世界国家与地标', '太阳系与天文', '简单物理现象',
    '植物与花卉', '日常计量单位', '时间与日历', '职业与工作', '学校与文具', '衣物与穿搭',
    '饮品与食物', '成语故事', '常见反义词', '古诗名句（极常见的）', '简单算术', '数字规律',
]
AI_FORMATS = [
    '标准直接提问（例如“XX 是什么？”）',
    '反向排除提问（“以下哪个不属于……？”）',
    '归类提问（“以下哪个属于……？”）',
    '简单推理或计算（答案是一个数字或短词）',
]


def ai_topic_hint() -> str:
    """附在 AI 出题 prompt 末尾的随机约束。"""
    return '\n'.join([
        '# 本次出题约束（每次随机，务必遵守）',
        f'- 主题：{random.choice(AI_TOPICS)}',
        f'- 题型：{random.choice(AI_FORMATS)}',
        '- 不要出“中国的首都是哪里”“一周有几天”这类被用烂的题。',
        '- 每个选项不超过 8 个汉字。',
        f'- 随机编号：{random.randint(1000, 9999)}（仅用于打散，不要写进题目）',
    ])


def validate_question_data(data: dict) -> dict:
    """校验题目结构：一个正确答案 + 三个互不相同的干扰项，选项要短（按钮放得下）。"""
    question = str(data.get('question') or '').strip()
    correct = str(data.get('correct_answer') or '').strip()
    incorrect = [str(item).strip() for item in (data.get('incorrect_answers') or [])]
    options = incorrect + [correct]
    if not question or not correct or len(incorrect) != 3:
        raise ValueError('验证题结构不完整')
    if len(set(options)) != 4 or any(not item for item in options):
        raise ValueError('验证题选项有重复或空值')
    # Telegram callback_data 最多 64 字节（含 "verify_" 前缀），中文一个字 3 字节
    if any(len(('verify_' + item).encode('utf-8')) > 64 for item in options):
        raise ValueError('验证题选项过长')
    return {'question': question, 'correct_answer': correct, 'incorrect_answers': incorrect}
