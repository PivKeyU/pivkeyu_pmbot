import json
import os
import tempfile
from pathlib import Path

CONFIG_FILE = Path(__file__).parent.parent / 'data' / 'network_test_config.json'

config_data = {
    'AUTHORIZED_USERS': [],
    'SERVERS': []
}

try:
    from config import config as main_config
except ImportError:
    main_config = None

if CONFIG_FILE.exists():
    try:
        with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
            loaded_data = json.load(f)
            if isinstance(loaded_data, dict):
                if isinstance(loaded_data.get('AUTHORIZED_USERS'), list):
                    config_data['AUTHORIZED_USERS'] = loaded_data['AUTHORIZED_USERS']
                if isinstance(loaded_data.get('SERVERS'), list):
                    config_data['SERVERS'] = loaded_data['SERVERS']
    except Exception as e:
        print(f"加载网络测试配置失败: {e}")


def get_admin_users() -> list:
    """动态获取管理员列表，始终以主配置 ADMIN_IDS 为准（避免热更新或持久化分叉）。"""
    if main_config is not None:
        return list(main_config.ADMIN_IDS or [])
    return []


# 向后兼容别名（静态引用可能随热更新滞后，新代码请使用 get_admin_users()）
ADMIN_USERS = get_admin_users()
AUTHORIZED_USERS = config_data.get('AUTHORIZED_USERS', [])
SERVERS = config_data.get('SERVERS', [])


def save_config():
    CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)

    config_data['AUTHORIZED_USERS'] = AUTHORIZED_USERS
    config_data['SERVERS'] = SERVERS

    fd, tmp_path = tempfile.mkstemp(dir=str(CONFIG_FILE.parent), prefix='.network_test_', suffix='.tmp')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            json.dump(config_data, f, indent=2, ensure_ascii=False)
        try:
            os.chmod(tmp_path, 0o600)
        except OSError:
            pass
        os.replace(tmp_path, CONFIG_FILE)
        try:
            os.chmod(CONFIG_FILE, 0o600)
        except OSError:
            pass
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise
