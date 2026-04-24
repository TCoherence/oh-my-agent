"""Per-day JSONL trace of structured agent events.

Complements ``SessionDiaryWriter`` (which writes a human-readable markdown
log of user↔assistant turns). ``TraceWriter`` writes a machine-readable
log of every ``AgentEvent`` the CLI parsers emit — tool calls, tool
results, thinking, usage — so operators can grep or replay an entire
turn's behaviour without re-running the model.

One file per day under ``trace_dir/YYYY-MM-DD.jsonl``; one JSON object
per line. Single background worker drains the queue, so writes never
interleave even under concurrent gateway load.

This is an opt-in experiment — enable via ``experiment.tool_trace`` in
``config.yaml``. The writer is never read back by the agent.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

from oh_my_agent.agents.events import AgentEvent

logger = logging.getLogger(__name__)


class TraceWriter:
    """Queued JSONL trace writer, mirroring :class:`SessionDiaryWriter`."""

    def __init__(self, trace_dir: str | Path) -> None:
        self._trace_dir = Path(trace_dir).expanduser().resolve()
        self._queue: asyncio.Queue[dict | None] = asyncio.Queue()
        self._worker: asyncio.Task | None = None
        self._started = False
        self._stopped = False

    @property
    def trace_dir(self) -> Path:
        return self._trace_dir

    def start(self) -> None:
        if self._started or self._stopped:
            return
        self._started = True
        self._trace_dir.mkdir(parents=True, exist_ok=True)
        self._worker = asyncio.create_task(self._run(), name="trace-writer:worker")

    async def stop(self) -> None:
        if not self._started or self._stopped:
            return
        self._stopped = True
        await self._queue.put(None)
        if self._worker is not None:
            try:
                await asyncio.wait_for(self._worker, timeout=5)
            except asyncio.TimeoutError:
                self._worker.cancel()

    async def append(
        self,
        *,
        agent: str,
        thread_id: str,
        event: AgentEvent,
        ts: datetime | None = None,
    ) -> None:
        if self._stopped:
            return
        if not self._started:
            self.start()
        payload = self._event_to_payload(event)
        payload["ts"] = (ts or datetime.now()).isoformat(timespec="microseconds")
        payload["agent"] = agent
        payload["thread_id"] = thread_id
        await self._queue.put(payload)

    async def _run(self) -> None:
        while True:
            payload = await self._queue.get()
            if payload is None:
                return
            try:
                self._write_line(payload)
            except Exception:
                logger.warning("TraceWriter failed to persist event", exc_info=True)

    def _write_line(self, payload: dict) -> None:
        day = payload["ts"][:10]  # YYYY-MM-DD prefix of ISO timestamp
        path = self._trace_dir / f"{day}.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(payload, ensure_ascii=False, default=str)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")

    @staticmethod
    def _event_to_payload(event: AgentEvent) -> dict[str, Any]:
        """Render an AgentEvent as a flat JSON-friendly dict.

        The pydantic discriminator is called ``kind`` in-memory; we rename
        it to ``type`` on disk because that's the conventional field name
        for event-stream JSONL (jq filters, log viewers, etc.).
        """
        data = dict(event.model_dump(mode="json"))
        if "kind" in data:
            data["type"] = data.pop("kind")
        return data
