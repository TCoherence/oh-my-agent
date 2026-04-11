from __future__ import annotations

import asyncio
import time

import pytest

from oh_my_agent.utils.rate_limiter import TokenBucketLimiter


@pytest.mark.asyncio
async def test_single_acquire_succeeds_immediately() -> None:
    limiter = TokenBucketLimiter(rate=10.0, burst=2)
    started = time.monotonic()
    await limiter.acquire()
    assert time.monotonic() - started < 0.02


@pytest.mark.asyncio
async def test_burst_allows_immediate_acquires_up_to_capacity() -> None:
    limiter = TokenBucketLimiter(rate=100.0, burst=3)
    started = time.monotonic()
    await limiter.acquire()
    await limiter.acquire()
    await limiter.acquire()
    assert time.monotonic() - started < 0.03


@pytest.mark.asyncio
async def test_acquire_beyond_burst_waits_for_refill() -> None:
    limiter = TokenBucketLimiter(rate=20.0, burst=1)
    await limiter.acquire()
    started = time.monotonic()
    await limiter.acquire()
    assert time.monotonic() - started >= 0.04


@pytest.mark.asyncio
async def test_concurrent_acquires_are_serialized() -> None:
    limiter = TokenBucketLimiter(rate=25.0, burst=1)

    async def _worker() -> float:
        started = time.monotonic()
        await limiter.acquire()
        return time.monotonic() - started

    waits = await asyncio.gather(_worker(), _worker(), _worker())
    assert max(waits) >= 0.07
