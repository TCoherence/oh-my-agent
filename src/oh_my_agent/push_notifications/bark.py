"""Bark push provider — POST to ``{server}/{device_key}`` with JSON body.

Bark API docs: https://github.com/Finb/Bark
"""

from __future__ import annotations

import asyncio
import json
import logging
import urllib.request

from oh_my_agent.push_notifications.base import (
    PushNotificationEvent,
    PushNotificationProvider,
)

logger = logging.getLogger(__name__)


class BarkPushProvider(PushNotificationProvider):
    def __init__(
        self,
        server: str,
        device_key: str,
        *,
        timeout: float = 5.0,
    ) -> None:
        self._server = server.rstrip("/")
        self._device_key = device_key
        self._timeout = timeout

    async def send(self, event: PushNotificationEvent) -> None:
        try:
            await asyncio.to_thread(self._post, event)
        except Exception:
            logger.warning(
                "Bark push failed kind=%s group=%s",
                event.kind,
                event.group,
                exc_info=True,
            )

    def _post(self, event: PushNotificationEvent) -> None:
        url = f"{self._server}/{self._device_key}"
        # Final-line truncation guards against pathological caller input.
        # Caller-level trim (NotificationManager / RuntimeService / on_message)
        # already keeps things tidy, so these bounds are intentionally loose.
        payload: dict[str, str] = {
            "title": event.title[:100],
            "body": event.body[:500],
            "group": event.group,
            "level": event.level,
        }
        if event.deep_link:
            payload["url"] = event.deep_link
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=self._timeout) as resp:
            resp.read()

    async def aclose(self) -> None:
        return None
