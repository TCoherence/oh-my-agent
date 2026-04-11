from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from oh_my_agent import main as oma_main


@pytest.mark.asyncio
async def test_shutdown_calls_components_in_order() -> None:
    calls: list[str] = []
    logger = MagicMock()
    gateway = MagicMock()
    gateway.stop = AsyncMock(side_effect=lambda: calls.append("gateway"))
    scheduler = MagicMock()
    scheduler.stop = MagicMock(side_effect=lambda: calls.append("scheduler"))
    runtime = MagicMock()
    runtime.stop = AsyncMock(side_effect=lambda: calls.append("runtime"))
    store = MagicMock()
    store.close = AsyncMock(side_effect=lambda: calls.append("store"))

    await oma_main._shutdown(
        gateway,
        scheduler,
        runtime,
        store,
        logger,
        reason="test",
    )

    assert calls == ["scheduler", "gateway", "runtime", "store"]


def test_register_shutdown_signal_handlers_registers_sigint_and_sigterm() -> None:
    registered: list[str] = []

    class _Loop:
        def add_signal_handler(self, sig, callback, *args):
            del callback, args
            registered.append(sig.name)

    oma_main._register_shutdown_signal_handlers(_Loop(), lambda *_args: None, MagicMock())

    assert registered == ["SIGINT", "SIGTERM"]
