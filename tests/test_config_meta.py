"""配置元数据完整性：Config 属性与 config_meta 注册表必须一一对应。"""

import config_meta
from config import Config

# 运行期注入、不需要面板管理的属性
SKIP_KEYS = {'BOT_ID', 'BOT_USERNAME'}

# 由各自的专属页面接管（RSS 页 / 关键词页），不走通用配置覆盖层：
# - RSS_*：运行期状态保存在 rss/settings.py 的 JSON 状态里，覆盖 config 不会生效
# - SPAM_KEYWORD_*：实际生效值来自 settings 表，.env 只是首次默认值，覆盖 config 同样不生效
MANAGED_ELSEWHERE = {
    'RSS_ENABLED', 'RSS_DATA_FILE', 'RSS_CHECK_INTERVAL', 'RSS_AUTHORIZED_USER_IDS',
    'SPAM_KEYWORD_FILTER_ENABLED', 'SPAM_KEYWORD_AUTO_BLOCK',
}


def _config_uppercase_attrs():
    return {
        name
        for name in vars(Config)
        if name.isupper() and not name.startswith('_')
    }


def test_every_config_attr_is_registered_or_intentionally_excluded():
    missing = _config_uppercase_attrs() - SKIP_KEYS - MANAGED_ELSEWHERE - set(config_meta.META_BY_KEY)
    assert not missing, f'config.py 中这些配置项没有登记到 config_meta：{sorted(missing)}'


def test_every_meta_key_exists_on_config():
    unknown = set(config_meta.META_BY_KEY) - _config_uppercase_attrs()
    assert not unknown, f'config_meta 中这些键在 Config 类里不存在：{sorted(unknown)}'


def test_meta_fields_valid():
    valid_types = {'bool', 'int', 'str', 'list', 'enum'}
    categories = {key for key, _ in config_meta.CATEGORIES}
    for item in config_meta.META:
        key = item['key']
        assert item['type'] in valid_types, f'{key} 类型非法: {item["type"]}'
        assert item['category'] in categories, f'{key} 分组非法: {item["category"]}'
        assert item['label'] and item['hint'], f'{key} 缺少 label/hint'
        assert isinstance(item['restart'], bool), f'{key} restart 必须是 bool'
        assert isinstance(item.get('default', ''), str), f'{key} default 必须是字符串'
        if item['type'] == 'enum':
            assert item.get('options'), f'{key} 是 enum 却没有 options'
            assert str(item.get('default')) in item['options'], f'{key} 默认值不在 options 中'
        if item['type'] == 'int':
            if 'min' in item and 'max' in item:
                assert item['min'] <= item['max'], f'{key} min/max 颠倒'


def test_meta_keys_unique():
    keys = [item['key'] for item in config_meta.META]
    assert len(keys) == len(set(keys)), 'config_meta 存在重复键'


def test_secret_items_are_strings():
    for item in config_meta.META:
        if item.get('secret'):
            assert item['type'] == 'str', f'{item["key"]} 标记为敏感但类型不是 str'
