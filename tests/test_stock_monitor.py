"""补货监控：库存判断、只在补货时推送、结果记录与推送目标。"""

import asyncio
import json
from types import SimpleNamespace

import pytest

from config import config
from database import models as db
from database.db_manager import DatabaseManager
from services import monitor_push, web_monitor


def _page(body: str) -> str:
    return f'<html><head><title>店铺</title></head><body><h1>限定手办</h1>{body}</body></html>'


def _json_ld(availability: str, name: str = '限定手办') -> str:
    data = {
        '@type': 'Product', 'name': name,
        'offers': {'availability': f'https://schema.org/{availability}', 'price': '199', 'priceCurrency': 'CNY'},
    }
    return f'<script type="application/ld+json">{json.dumps(data, ensure_ascii=False)}</script>'


@pytest.mark.parametrize('body,state', [
    ('<button>加入购物车</button>', 'in'),
    ('<span>现货</span>', 'in'),
    ('<button>Add to Cart</button>', 'in'),
    ('<span>暂时缺货</span><button>到货通知</button>', 'out'),
    ('<span>Sold Out</span>', 'out'),
    ('<span>暂无货</span>', 'out'),
    ('<span>not in stock</span>', 'out'),
    ('<p>欢迎光临</p>', 'unknown'),
])
def test_detect_stock_from_page_text(body, state):
    assert web_monitor.detect_stock(_page(body), {'name': 'x'}).state == state


def test_script_text_does_not_count():
    html = _page('<script>var label = "加入购物车";</script><p>欢迎光临</p>')
    assert web_monitor.detect_stock(html, {'name': 'x'}).state == 'unknown'


def test_structured_data_beats_page_text():
    html = _page(_json_ld('InStock') + '<p>相关商品：已售罄</p>')
    result = web_monitor.detect_stock(html, {'name': 'x'})
    assert result.state == 'in'
    assert result.price == 'CNY 199'
    assert web_monitor.detect_stock(_page(_json_ld('OutOfStock')), {'name': 'x'}).state == 'out'


def test_structured_data_respects_product_keyword():
    html = _page(_json_ld('InStock', '别的商品') + _json_ld('OutOfStock', '限定手办 第二弹'))
    assert web_monitor.detect_stock(html, {'name': 'x', 'product_keyword': '第二弹'}).state == 'out'


def test_selector_narrows_the_scope():
    html = _page('<div class="stock">现货</div><div class="other">售罄</div>')
    assert web_monitor.detect_stock(html, {'name': 'x', 'stock_selector': '.stock'}).state == 'in'
    missing = web_monitor.detect_stock(html, {'name': 'x', 'stock_selector': '.nope'})
    assert missing.state == 'unknown' and '.nope' in missing.evidence


def test_product_keyword_finds_the_right_card():
    html = _page(
        '<ul><li><a>甲款</a><span>售罄</span></li>'
        '<li><a>乙款</a><span>有货</span><em>¥ 88</em></li></ul>'
    )
    result = web_monitor.detect_stock(html, {'name': 'x', 'product_keyword': '乙款'})
    assert result.state == 'in' and result.price == '¥ 88'
    assert web_monitor.detect_stock(html, {'name': 'x', 'product_keyword': '甲款'}).state == 'out'
    assert web_monitor.detect_stock(html, {'name': 'x', 'product_keyword': '丙款'}).state == 'unknown'


def test_custom_words():
    html = _page('<span>今日可订</span>')
    assert web_monitor.detect_stock(html, {'name': 'x', 'in_stock_words': '可订, 能买'}).state == 'in'


class RecordingBot:
    def __init__(self):
        self.sent = []

    async def send_message(self, **kwargs):
        self.sent.append(kwargs)


async def _stock_flow(tmp_path, monkeypatch):
    manager = DatabaseManager(str(tmp_path / 'stock.db'))
    await manager.initialize()
    monkeypatch.setattr(config, 'ADMIN_IDS', [1])
    pages = iter([
        _page('<span>售罄</span>'),       # 首次只记录
        _page('<p>页面改版了</p>'),        # 看不出来：保留缺货状态
        _page('<button>加入购物车</button>'),  # 补货：推送
        _page('<button>加入购物车</button>'),  # 没变化：不再推送
        _page('<span>售罄</span>'),       # 售罄：只记录
    ])

    async def fetch(url):
        return next(pages)

    monkeypatch.setattr(web_monitor, '_fetch_html', fetch)
    bot = RecordingBot()
    application = SimpleNamespace(bot=bot)
    try:
        monitor_id = await db.create_web_monitor(
            name='手办', url='https://shop.example/1', keywords=[], created_by=0,
            monitor_type='stock', notify_chat_ids='7,8',
        )

        async def run():
            return await web_monitor.run_monitor(application, await db.get_web_monitor(monitor_id))

        assert await run() == 0
        assert (await db.get_web_monitor_state(monitor_id, web_monitor.STOCK_STATE_KEY))['stock'] == 'out'

        assert await run() == 0
        assert (await db.get_web_monitor(monitor_id))['last_error']
        assert (await db.get_web_monitor_state(monitor_id, web_monitor.STOCK_STATE_KEY))['stock'] == 'out'

        assert await run() == 2
        assert [message['chat_id'] for message in bot.sent] == [7, 8]
        assert '补货提醒' in bot.sent[0]['text']
        assert bot.sent[0]['reply_markup'].inline_keyboard[0][0].url == 'https://shop.example/1'
        assert (await db.get_web_monitor(monitor_id))['last_error'] == ''

        assert await run() == 0
        assert await run() == 0
        assert len(bot.sent) == 2

        events = await db.get_monitor_events(source='stock')
        assert [(event['title'], event['pushed_count']) for event in events] == [('已售罄', 0), ('已补货', 2)]

        # 关闭机器人推送后只记录，不发消息
        await db.update_web_monitor(monitor_id, notify_telegram=False)
        pages_off = iter([_page('<span>现货</span>')])

        async def fetch_off(url):
            return next(pages_off)

        monkeypatch.setattr(web_monitor, '_fetch_html', fetch_off)
        await run()
        assert len(bot.sent) == 2
        assert (await db.get_monitor_events(source='stock'))[0]['pushed_count'] == 0

        # 抓取失败时把错误留给页面显示
        async def broken(url):
            raise RuntimeError('403 Forbidden')

        monkeypatch.setattr(web_monitor, '_fetch_html', broken)
        await run()
        assert '403' in (await db.get_web_monitor(monitor_id))['last_error']
    finally:
        await manager.close_all()


def test_only_restock_is_pushed(tmp_path, monkeypatch):
    asyncio.run(_stock_flow(tmp_path, monkeypatch))


def test_push_targets():
    assert monitor_push.parse_chat_ids('1, -1002，3\n4') == [1, -1002, 3, 4]
    assert monitor_push.resolve_targets({'notify_chat_ids': ''}, [9]) == [9]
    assert monitor_push.resolve_targets({'notify_chat_ids': '5'}, [9]) == [5]
    with pytest.raises(ValueError):
        monitor_push.parse_chat_ids('abc')
