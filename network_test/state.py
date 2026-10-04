"""Network test 模块的共享状态。

`user_data` 保存的是**交互式向导的会话状态**（addserver 的 5 步登记、ping /
nexttrace 的选项选择）。项目里有 20+ 个调用点按普通 dict 的语义直接操作它：

    user_data[uid] = {...}          # 写
    info = user_data[uid]           # 读
    del user_data[uid]              # 删
    if uid not in user_data: ...    # 存在性判断
    current = user_data.get(uid)    # get

所以它**必须保持是 `dict` 的子类**：这样 `[]` / `del` / `in` / `.get()` /
`.items()` 全部沿用 dict 的原生实现，那 20+ 个调用点一行都不用改。
千万别把它换成不继承 dict 的自定义 Mapping，那会一次性破坏上面全部用法。

换成 `_TTLDict` 的原因：原先它只是一个裸 `{}`，只能靠「流程正常走完」或用户
发 `/cancel` 才会删除记录。管理员登记到一半关掉 Telegram，这条记录就会**永久
驻留内存**。现在写入时打时间戳，并在写入路径里机会性清理过期记录。
"""

import time

# 交互式向导会话的最长闲置时长（秒）。超时未再被写入的记录会被机会性清理，
# 防止管理员中途关掉 Telegram 导致记录永久驻留。
# addserver 向导最多 5 步，ping/nexttrace 的交互更短，1 小时足够宽松，
# 不会误删仍在进行的流程。
SESSION_IDLE_TTL = 3600          # 1 小时

# 机会性清理的触发间隔：每 50 次写入做一次过期扫描。
# 之所以不是「每次写入都扫」，是为了让热门交互路径保持均摊 O(1)——
# 全量扫描是 O(n)，跟着每次按键跑会拖慢正常流程。
_SWEEP_EVERY = 50


class _TTLDict(dict):
    """带闲置时间戳的 dict 子类，专供向导会话状态使用。

    继承 `dict` 是硬约束（原因见模块 docstring）：所有 dict 原生语义原样保留，
    只在**写入路径**上记录 `time.time()`，并每 `_SWEEP_EVERY` 次写入机会性
    清理闲置超过 TTL 的条目。

    刻意**不覆盖** `get` / `__getitem__` / `__contains__`：这些是纯读路径，
    保持 dict 原生实现可以确保读行为与普通 dict 完全一致，不引入行为差异。
    但 `__delitem__` / `pop` / `popitem` / `clear` 必须覆盖——它们要同步清掉
    时间戳，否则时间戳会在字典里越积越多（幽灵时间戳泄漏）。
    """

    def __init__(self, *args, **kwargs):
        super().__init__()
        self._touched = {}      # uid -> 最后一次写入时间
        self._writes = 0        # 写入计数，用于机会性清理
        if args or kwargs:
            # 走 update 而不是 dict.__init__，保证初始数据也有时间戳
            self.update(*args, **kwargs)

    # ---------- 写路径：打时间戳 + 机会性清理 ----------

    def __setitem__(self, key, value):
        super().__setitem__(key, value)
        self._touched[key] = time.time()
        self._writes += 1
        if self._writes % _SWEEP_EVERY == 0 and self:
            self.sweep()

    def update(self, *args, **kwargs):
        # dict.update 会绕过 __setitem__（也就绕过时间戳），这里手动转写。
        for key, value in dict(*args, **kwargs).items():
            self[key] = value

    def __ior__(self, other):
        # `user_data |= {...}` 同样绕过 __setitem__，转成显式写入。
        self.update(other)
        return self

    def setdefault(self, key, default=None):
        if key in self:
            return self[key]
        self[key] = default
        return default

    # ---------- 删路径：同步清时间戳，不留幽灵 ----------

    def __delitem__(self, key):
        super().__delitem__(key)        # key 不存在时照常抛 KeyError
        self._touched.pop(key, None)

    def pop(self, key, *default):
        # 与 dict.pop 一样只支持 pop(key) 与 pop(key, default) 两种写法
        if len(default) > 1:
            raise TypeError(
                f"pop expected at most 2 arguments, got {len(default) + 1}"
            )
        if key in self:
            value = super().pop(key)
            self._touched.pop(key, None)
            return value
        if default:
            return default[0]
        raise KeyError(key)

    def popitem(self):
        key, value = super().popitem()
        self._touched.pop(key, None)
        return key, value

    def clear(self):
        super().clear()
        self._touched.clear()

    # ---------- 清理 ----------

    def sweep(self, ttl: float = SESSION_IDLE_TTL) -> int:
        """删除闲置超过 ttl 秒的条目，返回清理掉的条数。"""
        now = time.time()
        # 先物化快照，避免迭代中修改 _touched 触发 RuntimeError
        expired = [k for k, ts in list(self._touched.items()) if now - ts > ttl]
        removed = 0
        for key in expired:
            if super().__contains__(key):
                super().__delitem__(key)        # 用 super() 避免重复清时间戳
            self._touched.pop(key, None)
            removed += 1
        return removed


# 兼容旧用法：它仍然是一个 dict（子类），调用点无需任何改动。
user_data = _TTLDict()

last_ping_command_time = {}
last_nexttrace_command_time = {}


def cleanup_user_data(ttl: float = SESSION_IDLE_TTL) -> int:
    """清理闲置超过 ttl 秒的向导会话，返回清理条数。

    调用方不需要主动调用——`_TTLDict` 会在写入路径里机会性自动清理。
    保留这个入口是为了测试与将来的定期任务可以显式触发。
    """
    return user_data.sweep(ttl)


# 冷却记录最长保留时长（秒），远超 /ping 15s 与 /nexttrace 10s 的冷却时间
COOLDOWN_MAX_AGE = 120

def cleanup_cooldown(ts_dict: dict, max_age: float = COOLDOWN_MAX_AGE):
    """清理超过 max_age 秒未更新的冷却记录，防止字典无限增长。"""
    now = time.time()
    for uid in list(ts_dict.keys()):
        if now - ts_dict[uid] > max_age:
            del ts_dict[uid]
