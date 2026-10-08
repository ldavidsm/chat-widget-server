"""Rate limiting, because every message is an API call you pay for.

An unprotected chat endpoint is a stranger's budget: one loop in a browser
console can spend a month of credit in an afternoon. The limiter below is
in-process, which is correct for a single worker. With several workers each
holds its own counters, so the effective limit multiplies by worker count —
put the real limit in your proxy, or back this with Redis, before you scale
out.
"""

from __future__ import annotations

import time
from collections import deque
from typing import Protocol

__all__ = ["RateLimiter", "Limiter"]


class Limiter(Protocol):
    """Anything that can answer "has this key had enough?"."""

    def check(self, key: str) -> float | None:
        """Return seconds to wait, or None when the request may proceed."""
        ...


class RateLimiter:
    """Sliding window over the last `window_seconds` for each key.

    A sliding window rather than a fixed one so a visitor cannot send the whole
    allowance twice by straddling a minute boundary.
    """

    def __init__(self, limit: int, *, window_seconds: float = 60.0, max_keys: int = 50_000):
        if limit <= 0:
            raise ValueError("limit must be positive; pass 0 to the router to disable instead")

        self.limit = limit
        self.window_seconds = window_seconds
        self.max_keys = max_keys
        self._hits: dict[str, deque[float]] = {}

    def check(self, key: str) -> float | None:
        now = time.monotonic()
        cutoff = now - self.window_seconds

        hits = self._hits.get(key)
        if hits is None:
            hits = self._hits[key] = deque()
            self._evict_if_needed()

        while hits and hits[0] < cutoff:
            hits.popleft()

        if len(hits) >= self.limit:
            # The oldest hit is what has to age out before there is room.
            return max(0.0, round(hits[0] + self.window_seconds - now, 1))

        hits.append(now)
        return None

    def reset(self, key: str | None = None) -> None:
        if key is None:
            self._hits.clear()
        else:
            self._hits.pop(key, None)

    def _evict_if_needed(self) -> None:
        """Drop idle keys so a flood of distinct ids cannot grow this forever."""
        if len(self._hits) <= self.max_keys:
            return

        cutoff = time.monotonic() - self.window_seconds
        for key in [k for k, hits in self._hits.items() if not hits or hits[-1] < cutoff]:
            self._hits.pop(key, None)

        # Still over budget: evict least-recently-used.
        if len(self._hits) > self.max_keys:
            ordered = sorted(self._hits.items(), key=lambda kv: kv[1][-1] if kv[1] else 0.0)
            for key, _ in ordered[: len(self._hits) - self.max_keys]:
                self._hits.pop(key, None)
