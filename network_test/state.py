import time

user_data = {}
last_ping_command_time = {}
last_nexttrace_command_time = {}

# 冷却记录最长保留时长（秒），远超 /ping 15s 与 /nexttrace 10s 的冷却时间
COOLDOWN_MAX_AGE = 120

def cleanup_cooldown(ts_dict: dict, max_age: float = COOLDOWN_MAX_AGE):
    """清理超过 max_age 秒未更新的冷却记录，防止字典无限增长。"""
    now = time.time()
    for uid in list(ts_dict.keys()):
        if now - ts_dict[uid] > max_age:
            del ts_dict[uid]
