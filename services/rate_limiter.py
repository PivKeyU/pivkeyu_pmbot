import asyncio
import time
from collections import defaultdict, deque
from config import config

_MAX_TRACKED_USERS = 10000
_WARNING_COOLDOWN_SECONDS = 120.0


class RateLimiter:
    def __init__(self):
        self.user_message_timestamps = defaultdict(lambda: deque())
        self.user_warnings = {}
        self.lock = asyncio.Lock()

    def _prune_stale(self, now: float) -> None:
        cutoff = now - 60.0
        for uid in list(self.user_message_timestamps.keys()):
            q = self.user_message_timestamps[uid]
            while q and q[0] < cutoff:
                q.popleft()
            if not q:
                del self.user_message_timestamps[uid]
        for uid, warned_at in list(self.user_warnings.items()):
            if not isinstance(warned_at, (int, float)) or now - warned_at > _WARNING_COOLDOWN_SECONDS:
                self.user_warnings.pop(uid, None)

    async def check_user_rate_limit(self, user_id: int) -> tuple[bool, bool]:
        async with self.lock:
            now = time.time()
            if len(self.user_message_timestamps) > _MAX_TRACKED_USERS:
                self._prune_stale(now)

            limit = max(1, int(getattr(config, 'MAX_MESSAGES_PER_MINUTE', 30) or 30))
            timestamps = self.user_message_timestamps[user_id]
            while timestamps and timestamps[0] < now - 60.0:
                timestamps.popleft()

            is_over_limit = len(timestamps) >= limit
            if is_over_limit:
                warned_at = self.user_warnings.get(user_id)
                was_warned = bool(warned_at and now - float(warned_at) <= _WARNING_COOLDOWN_SECONDS)
                return True, was_warned
            else:
                timestamps.append(now)
                warned_at = self.user_warnings.get(user_id)
                if warned_at and (now - float(warned_at) > _WARNING_COOLDOWN_SECONDS or len(timestamps) <= max(1, limit // 2)):
                    del self.user_warnings[user_id]
                return False, False

    async def mark_user_warned(self, user_id: int):
        async with self.lock:
            self.user_warnings[user_id] = time.time()

    async def clear_user_warning(self, user_id: int):
        async with self.lock:
            self.user_warnings.pop(user_id, None)
            self.user_message_timestamps.pop(user_id, None)


rate_limiter = RateLimiter()
