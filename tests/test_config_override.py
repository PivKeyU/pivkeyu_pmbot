"""配置覆盖层：类型转换、热生效、还原与边界校验。"""

import os

import pytest

from config import config, parse_config_value


def test_parse_config_value_types():
    assert parse_config_value('true', 'bool') is True
    assert parse_config_value('0', 'bool') is False
    assert parse_config_value('42', 'int') == 42
    assert parse_config_value('  1,2, 3 ', 'list') == [1, 2, 3]
    assert parse_config_value('  hello ', 'str') == 'hello'


def test_apply_override_sets_attribute_and_env():
    original = config.MAX_MESSAGES_PER_MINUTE
    try:
        parsed = config.apply_override('MAX_MESSAGES_PER_MINUTE', '77')
        assert parsed == 77
        assert config.MAX_MESSAGES_PER_MINUTE == 77
        assert os.environ['MAX_MESSAGES_PER_MINUTE'] == '77'
        assert config.has_override('MAX_MESSAGES_PER_MINUTE') is True
    finally:
        config.clear_override('MAX_MESSAGES_PER_MINUTE')
    assert config.MAX_MESSAGES_PER_MINUTE == original
    assert config.has_override('MAX_MESSAGES_PER_MINUTE') is False


def test_clear_override_removes_env_when_not_originally_set():
    os.environ.pop('WATCHTOWER_HTTP_API_URL', None)
    try:
        config.apply_override('WATCHTOWER_HTTP_API_URL', 'http://example:9999')
        assert os.environ.get('WATCHTOWER_HTTP_API_URL') == 'http://example:9999'
    finally:
        config.clear_override('WATCHTOWER_HTTP_API_URL')
    assert 'WATCHTOWER_HTTP_API_URL' not in os.environ


def test_enum_validation():
    with pytest.raises(ValueError):
        config.apply_override('TG_MONITOR_DEFAULT_SOURCE', 'telepathy')
    try:
        config.apply_override('TG_MONITOR_DEFAULT_SOURCE', 'bot')
        assert config.TG_MONITOR_DEFAULT_SOURCE == 'bot'
    finally:
        config.clear_override('TG_MONITOR_DEFAULT_SOURCE')


def test_int_bounds_validation():
    with pytest.raises(ValueError):
        config.apply_override('AI_CONFIDENCE_THRESHOLD', '140')
    with pytest.raises(ValueError):
        config.apply_override('WEB_PANEL_PORT', '0')
    config.clear_override('AI_CONFIDENCE_THRESHOLD')


def test_unknown_key_raises():
    with pytest.raises(KeyError):
        config.apply_override('NOT_A_REAL_KEY', 'x')


def test_override_priority_over_env_default():
    """覆盖值优先于 .env；还原后回到原值（不残留覆盖标记）。"""
    original = config.AI_CONFIDENCE_THRESHOLD
    try:
        config.apply_override('AI_CONFIDENCE_THRESHOLD', '55')
        assert config.AI_CONFIDENCE_THRESHOLD == 55
        assert config.get_env_value('AI_CONFIDENCE_THRESHOLD') == original
    finally:
        config.clear_override('AI_CONFIDENCE_THRESHOLD')
    assert config.AI_CONFIDENCE_THRESHOLD == original
