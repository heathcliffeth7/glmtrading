from collections import deque
from datetime import datetime, timedelta
from typing import Deque


class SlidingWindowRateLimiter:
    def __init__(self, window_seconds: int, max_requests: int) -> None:
        self._max_calls = max_requests
        self._window = timedelta(seconds=window_seconds)
        self._events: Deque[datetime] = deque()

    def allow(self) -> bool:
        now = datetime.utcnow()
        self._purge(now)
        if len(self._events) < self._max_calls:
            self._events.append(now)
            return True
        return False

    def hit(self) -> bool:
        return self.allow()

    def _purge(self, current: datetime) -> None:
        while self._events and current - self._events[0] > self._window:
            self._events.popleft()
