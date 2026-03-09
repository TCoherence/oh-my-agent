from __future__ import annotations

from pathlib import Path

import pytest

from oh_my_agent.auth.service import AuthService
from oh_my_agent.auth.types import (
    AUTH_POLL_STATUS_APPROVED,
    AuthPollResult,
    AuthStartResult,
    CredentialValidation,
)
from oh_my_agent.memory.store import SQLiteMemoryStore


class _FakeAuthProvider:
    def __init__(self) -> None:
        self._poll_result = AuthPollResult(
            status=AUTH_POLL_STATUS_APPROVED,
            message="approved",
            credential_payload={},
        )

    def provider_name(self) -> str:
        return "bilibili"

    async def start_qr_login(self, owner_user_id: str) -> AuthStartResult:
        return AuthStartResult(provider_flow_id=f"provider-{owner_user_id}", qr_payload="https://example.com/qr")

    async def poll_qr_login(self, flow) -> AuthPollResult:
        return self._poll_result

    async def persist_credential(self, flow, result, storage_root: Path) -> tuple[Path, dict]:
        cred_dir = storage_root / "providers" / "bilibili" / flow.owner_user_id
        cred_dir.mkdir(parents=True, exist_ok=True)
        cookie_path = cred_dir / "cookies.txt"
        cookie_path.write_text("SESSDATA=fake\n", encoding="utf-8")
        return cookie_path, {}

    async def validate_credential(self, handle) -> CredentialValidation:
        return CredentialValidation(valid=True)

    async def invalidate_credential(self, handle) -> None:
        return None


@pytest.mark.asyncio
async def test_clear_credential_cleans_up_active_qr(tmp_path):
    store = SQLiteMemoryStore(tmp_path / "auth.db")
    await store.init()

    qr_path = tmp_path / "flow-1.png"
    qr_path.write_bytes(b"png")

    await store.create_auth_flow(
        flow_id="flow-1",
        provider="bilibili",
        owner_user_id="owner-1",
        platform="discord",
        channel_id="100",
        thread_id="thread-1",
        linked_task_id=None,
        status="qr_ready",
        provider_flow_id="provider-flow-1",
        qr_payload="https://example.com/qr",
        qr_image_path=str(qr_path),
        expires_at="2026-03-09 00:03:00",
    )

    auth = AuthService(store, providers=[_FakeAuthProvider()])
    await auth.clear_credential("bilibili", "owner-1")

    flow = await store.get_auth_flow("flow-1")
    assert flow is not None
    assert flow.status == "cancelled"
    assert flow.qr_image_path is None
    assert not qr_path.exists()

    await store.close()


@pytest.mark.asyncio
async def test_poll_one_approved_cleans_up_qr(tmp_path):
    store = SQLiteMemoryStore(tmp_path / "auth.db")
    await store.init()

    qr_path = tmp_path / "flow-1.png"
    qr_path.write_bytes(b"png")

    flow = await store.create_auth_flow(
        flow_id="flow-1",
        provider="bilibili",
        owner_user_id="owner-1",
        platform="discord",
        channel_id="100",
        thread_id="thread-1",
        linked_task_id=None,
        status="qr_ready",
        provider_flow_id="provider-flow-1",
        qr_payload="https://example.com/qr",
        qr_image_path=str(qr_path),
        expires_at="2026-03-09 00:03:00",
    )

    auth = AuthService(store, config={"storage_root": str(tmp_path / "runtime" / "auth")}, providers=[_FakeAuthProvider()])
    await auth._poll_one(flow)

    updated = await store.get_auth_flow("flow-1")
    assert updated is not None
    assert updated.status == "approved"
    assert updated.qr_image_path is None
    assert not qr_path.exists()

    credential = await store.get_auth_credential("bilibili", "owner-1")
    assert credential is not None
    assert Path(credential.storage_path).exists()

    await store.close()
