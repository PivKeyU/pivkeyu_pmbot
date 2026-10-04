"""pytest 公共配置：把项目根加入 sys.path，并在导入 config 之前铺好测试环境变量。"""

import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# 必须先于 config 导入设置：load_dotenv 不会覆盖已存在的环境变量
os.environ.setdefault('BOT_TOKEN', '123456:TEST-TOKEN')
os.environ.setdefault('FORUM_GROUP_ID', '-1001234567890')
os.environ.setdefault('ADMIN_IDS', '10001,10002')
os.environ.setdefault('DATABASE_PATH', str(PROJECT_ROOT / 'data' / 'test_should_not_be_used.db'))
