"""Sliding-window rate limiter with a hard cap on tracked keys (keys come from the Internet)."""

from __future__ import annotations

from collections import deque


class RateLimiter:
    def __init__(self, rate: int, per: float, max_keys: int = 10_000) -> None:
        self.rate, self.per, self.max_keys = rate, per, max_keys
        self._hits: dict[object, deque[float]] = {}

    def allow(self, key: object, now: float) -> bool:
        hits = self._hits.get(key)
        if hits is None:
            if len(self._hits) >= self.max_keys:
                self._evict(now)
            hits = self._hits[key] = deque()
        while hits and now - hits[0] >= self.per:
            hits.popleft()
        if len(hits) >= self.rate:
            return False
        hits.append(now)
        return True

    def _evict(self, now: float) -> None:
        for k in [k for k, h in self._hits.items() if not h or now - h[-1] >= self.per]:
            del self._hits[k]
        # Still full of live keys: drop the oldest-inserted ones rather than refuse newcomers.
        while len(self._hits) >= self.max_keys:
            del self._hits[next(iter(self._hits))]
