"""Push notification provider ABC + dispatcher.

The dispatcher is the only entry point call sites use — it enforces
fire-and-forget semantics so a slow provider (5s HTTP timeout) cannot
block the main event loop, and it catches exceptions that slip past
the provider's own swallow.
"""

from __future__ import annotations

import asyncio
import logging
import time
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Literal

logger = logging.getLogger(__name__)

PushLevel = Literal["passive", "active", "timeSensitive", "critical"]

PushKind = Literal[
    "mention_owner",
    "task_draft",
    "task_waiting_merge",
    "ask_user",
    "automation_complete",
    "automation_failed",
]


@dataclass(frozen=True)
class PushNotificationEvent:
    kind: PushKind
    title: str
    body: str
    group: str
    level: PushLevel
    deep_link: str | None = None


class PushNotificationProvider(ABC):
    @abstractmethod
    async def send(self, event: PushNotificationEvent) -> None: ...

    @abstractmethod
    async def aclose(self) -> None: ...


class NoopPushProvider(PushNotificationProvider):
    async def send(self, event: PushNotificationEvent) -> None:
        return None

    async def aclose(self) -> None:
        return None


@dataclass(frozen=True)
class PushSettings:
    enabled_events: dict[str, bool] = field(default_factory=dict)
    level_map: dict[str, PushLevel] = field(default_factory=dict)

    def is_enabled(self, kind: str) -> bool:
        return self.enabled_events.get(kind, False)

    def level_for(self, kind: str) -> PushLevel:
        return self.level_map.get(kind, "active")


class PushDispatcher:
    def __init__(
        self,
        provider: PushNotificationProvider,
        settings: PushSettings,
    ) -> None:
        self._provider = provider
        self._settings = settings

    def schedule(self, event: PushNotificationEvent) -> None:
        if not self._settings.is_enabled(event.kind):
            return
        try:
            task = asyncio.create_task(self._provider.send(event))
        except RuntimeError:
            logger.debug("Push schedule outside event loop — dropping kind=%s", event.kind)
            return
        task.add_done_callback(self._on_done)

    def is_enabled(self, kind: str) -> bool:
        return self._settings.is_enabled(kind)

    def level_for(self, kind: PushKind) -> PushLevel:
        return self._settings.level_for(kind)

    @staticmethod
    def _on_done(task: asyncio.Task) -> None:
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            logger.warning("Push fan-out raised unhandled: %r", exc)

    async def aclose(self) -> None:
        await self._provider.aclose()


class PushCoolDown:
    """Per-key time-window suppression for high-frequency push sources.

    Coalesces bursts (e.g. an ``@everyone`` paste in a busy Discord
    channel that mentions the owner N times) into a single push. Caller
    picks the key shape that fits the source —
    ``f"{channel_id}:{author_id}"`` for mention peek, ``automation_name``
    for runtime terminals, etc. First call for a given key returns True;
    subsequent calls within ``cool_down_seconds`` return False. Stale
    keys are pruned lazily.
    """

    _CLEANUP_THRESHOLD = 100

    def __init__(
        self,
        cool_down_seconds: float = 60.0,
        *,
        now: Callable[[], float] = time.monotonic,
    ) -> None:
        if cool_down_seconds <= 0:
            raise ValueError("cool_down_seconds must be > 0")
        self._cool_down = float(cool_down_seconds)
        self._now = now
        self._last_fire: dict[str, float] = {}

    def should_fire(self, key: str) -> bool:
        now = self._now()
        last = self._last_fire.get(key)
        if last is not None and (now - last) < self._cool_down:
            return False
        self._last_fire[key] = now
        if len(self._last_fire) > self._CLEANUP_THRESHOLD:
            cutoff = now - self._cool_down
            self._last_fire = {
                k: v for k, v in self._last_fire.items() if v > cutoff
            }
        return True
