from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from typing import TYPE_CHECKING

from oh_my_agent.gateway.session import ChannelSession
from oh_my_agent.runtime.types import NotificationEvent, NotificationRecord

if TYPE_CHECKING:
    from oh_my_agent.memory.store import MemoryStore

logger = logging.getLogger(__name__)


class NotificationManager:
    """Internal notification fan-out for owner-action-required states."""

    def __init__(
        self,
        store: "MemoryStore",
        *,
        owner_user_ids: set[str] | None,
        session_lookup: Callable[[str, str], ChannelSession | None],
    ) -> None:
        self._store = store
        self._owner_user_ids = set(owner_user_ids or set())
        self._session_lookup = session_lookup

    async def emit(self, event: NotificationEvent) -> list[NotificationRecord]:
        if not self._owner_user_ids:
            return []
        active = await self._store.list_active_notification_events(
            dedupe_key=event.dedupe_key,
            limit=max(10, len(self._owner_user_ids) * 2),
        )
        if active:
            return active

        session = self._session_lookup(event.platform, event.channel_id)
        if session is None:
            logger.warning(
                "Notification skipped kind=%s dedupe=%s: no live session for %s:%s",
                event.kind,
                event.dedupe_key,
                event.platform,
                event.channel_id,
            )
            return []

        thread_message_id = await self._send_thread_ping(session, event)
        records: list[NotificationRecord] = []
        for owner_user_id in sorted(self._owner_user_ids):
            dm_message_id = await self._send_owner_dm(session, owner_user_id, event)
            status = "active" if (thread_message_id or dm_message_id) else "failed"
            record = await self._store.create_notification_event(
                notification_id=uuid.uuid4().hex[:12],
                kind=event.kind,
                status=status,
                platform=event.platform,
                channel_id=event.channel_id,
                thread_id=event.thread_id,
                task_id=event.task_id,
                owner_user_id=owner_user_id,
                dedupe_key=event.dedupe_key,
                title=event.title,
                body=event.body,
                payload_json=event.payload or {},
                thread_message_id=thread_message_id,
                dm_message_id=dm_message_id,
            )
            records.append(record)
        return records

    async def resolve(self, dedupe_key: str, *, status: str = "resolved") -> int:
        return await self._store.resolve_notification_events(
            dedupe_key=dedupe_key,
            status=status,
        )

    async def _send_thread_ping(
        self,
        session: ChannelSession,
        event: NotificationEvent,
    ) -> str | None:
        channel = session.channel
        mentions = " ".join(channel.render_user_mention(owner_id) for owner_id in sorted(self._owner_user_ids))
        lines = [f"{mentions} **{event.title}**", f"Reason: {self._reason_label(event.kind)}"]
        if event.task_id:
            lines.append(f"Task: `{event.task_id}`")
        if event.body:
            lines.append(event.body)
        try:
            return await channel.send(event.thread_id, "\n".join(lines)[:1900])
        except Exception:
            logger.warning(
                "Thread notification delivery failed kind=%s thread=%s dedupe=%s",
                event.kind,
                event.thread_id,
                event.dedupe_key,
                exc_info=True,
            )
            return None

    async def _send_owner_dm(
        self,
        session: ChannelSession,
        owner_user_id: str,
        event: NotificationEvent,
    ) -> str | None:
        sender = getattr(session.channel, "send_dm", None)
        if not callable(sender):
            return None
        lines = [f"**{event.title}**", f"Reason: {self._reason_label(event.kind)}"]
        if event.task_id:
            lines.append(f"Task: `{event.task_id}`")
        lines.append(f"Thread: `{event.thread_id}`")
        if event.body:
            lines.append(event.body)
        try:
            return await sender(owner_user_id, "\n".join(lines)[:1900])
        except Exception:
            logger.warning(
                "Owner DM notification failed owner=%s kind=%s dedupe=%s",
                owner_user_id,
                event.kind,
                event.dedupe_key,
                exc_info=True,
            )
            return None

    @staticmethod
    def _reason_label(kind: str) -> str:
        if kind == "task_draft":
            return "draft"
        if kind == "task_waiting_merge":
            return "waiting_merge"
        return kind
