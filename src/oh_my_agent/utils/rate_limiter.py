from __future__ import annotations

import asyncio
import time


class TokenBucketLimiter:
    """Async token bucket limiter with serialized acquisition."""

    def __init__(self, rate: float = 5.0, burst: int = 10) -> None:
        if rate <= 0:
            raise ValueError("rate must be > 0")
        if burst <= 0:
            raise ValueError("burst must be > 0")
        self._rate = float(rate)
        self._burst = float(burst)
        self._tokens = float(burst)
        self._updated_at = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self, tokens: int = 1) -> None:
        if tokens <= 0:
            return
        needed = float(tokens)
        if needed > self._burst:
            raise ValueError("requested tokens exceed burst capacity")

        while True:
            async with self._lock:
                now = time.monotonic()
                elapsed = max(0.0, now - self._updated_at)
                self._updated_at = now
                self._tokens = min(self._burst, self._tokens + elapsed * self._rate)
                if self._tokens >= needed:
                    self._tokens -= needed
                    return
                wait_seconds = (needed - self._tokens) / self._rate
            await asyncio.sleep(wait_seconds)
