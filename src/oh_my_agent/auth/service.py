from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from oh_my_agent.auth.providers.base import AuthProvider
from oh_my_agent.auth.types import (
    AUTH_CREDENTIAL_STATUS_INVALID,
    AUTH_CREDENTIAL_STATUS_VALID,
    AUTH_FLOW_STATUS_APPROVED,
    AUTH_FLOW_STATUS_CANCELLED,
    AUTH_FLOW_STATUS_EXPIRED,
    AUTH_FLOW_STATUS_FAILED,
    AUTH_FLOW_STATUS_QR_READY,
    AUTH_POLL_STATUS_APPROVED,
    AUTH_POLL_STATUS_EXPIRED,
    AUTH_POLL_STATUS_FAILED,
    AUTH_POLL_STATUS_SCANNED,
    AUTH_SCOPE_DEFAULT,
    AuthFlow,
    CredentialHandle,
)

logger = logging.getLogger(__name__)

AuthFlowListener = Callable[[str, AuthFlow, CredentialHandle | None, str | None], Awaitable[None]]


class AuthService:
    def __init__(
        self,
        store,
        *,
        config: dict[str, Any] | None = None,
        providers: list[AuthProvider] | None = None,
    ) -> None:
        cfg = config or {}
        self._enabled = bool(cfg.get("enabled", True))
        self._storage_root = Path(cfg.get("storage_root", "~/.oh-my-agent/runtime/auth")).expanduser().resolve()
        self._poll_interval_seconds = float(cfg.get("qr_poll_interval_seconds", 3))
        self._default_timeout_seconds = int(cfg.get("qr_default_timeout_seconds", 180))
        providers_cfg = cfg.get("providers", {})
        self._provider_config = {
            str(name): dict(data or {})
            for name, data in providers_cfg.items()
        }
        self._providers = {provider.provider_name(): provider for provider in (providers or [])}
        self._store = store
        self._listeners: list[AuthFlowListener] = []
        self._poller_task: asyncio.Task | None = None
        self._stop_event = asyncio.Event()

    @property
    def enabled(self) -> bool:
        return self._enabled

    def add_listener(self, listener: AuthFlowListener) -> None:
        self._listeners.append(listener)

    async def start(self) -> None:
        if not self._enabled or self._poller_task is not None:
            return
        self._storage_root.mkdir(parents=True, exist_ok=True)
        self._stop_event.clear()
        self._poller_task = asyncio.create_task(self._poller_loop(), name="auth-poller")

    async def stop(self) -> None:
        self._stop_event.set()
        if self._poller_task:
            self._poller_task.cancel()
            await asyncio.gather(self._poller_task, return_exceptions=True)
            self._poller_task = None

    async def get_valid_credential(
        self,
        provider: str,
        owner_user_id: str,
    ) -> CredentialHandle | None:
        credential = await self._store.get_auth_credential(provider, owner_user_id, scope_key=AUTH_SCOPE_DEFAULT)
        if credential is None:
            return None
        provider_impl = self._require_provider(provider)
        validation = await provider_impl.validate_credential(credential)
        if not validation.valid:
            await self._store.upsert_auth_credential(
                credential_id=credential.id,
                provider=credential.provider,
                owner_user_id=credential.owner_user_id,
                scope_key=credential.scope_key,
                status=AUTH_CREDENTIAL_STATUS_INVALID,
                storage_path=credential.storage_path,
                metadata_json={**credential.metadata, **validation.metadata, "invalid_reason": validation.reason},
                last_verified_at=self._now_timestamp(),
                expires_at=validation.expires_at,
            )
            return None
        updated = await self._store.upsert_auth_credential(
            credential_id=credential.id,
            provider=credential.provider,
            owner_user_id=credential.owner_user_id,
            scope_key=credential.scope_key,
            status=AUTH_CREDENTIAL_STATUS_VALID,
            storage_path=credential.storage_path,
            metadata_json={**credential.metadata, **validation.metadata},
            last_verified_at=self._now_timestamp(),
            expires_at=validation.expires_at,
        )
        return updated

    async def start_qr_flow(
        self,
        provider: str,
        *,
        owner_user_id: str,
        platform: str,
        channel_id: str,
        thread_id: str,
        linked_task_id: str | None,
        force_new: bool = False,
    ) -> AuthFlow:
        provider_impl = self._require_provider(provider)
        if not self._provider_enabled(provider):
            raise RuntimeError(f"Auth provider `{provider}` is disabled.")

        existing = await self.get_active_flow(provider, owner_user_id)
        if existing and not force_new:
            if linked_task_id and existing.linked_task_id != linked_task_id:
                refreshed = await self._store.update_auth_flow(existing.id, linked_task_id=linked_task_id)
                return refreshed or existing
            return existing
        if existing and force_new:
            await self._store.update_auth_flow(
                existing.id,
                status=AUTH_FLOW_STATUS_CANCELLED,
                error="Superseded by a new QR flow.",
                completed_at_now=True,
            )

        started = await provider_impl.start_qr_login(owner_user_id)
        flow_id = uuid.uuid4().hex[:12]
        qr_image_path = await self._render_qr_png(flow_id, started.qr_payload)
        return await self._store.create_auth_flow(
            flow_id=flow_id,
            provider=provider,
            owner_user_id=owner_user_id,
            platform=platform,
            channel_id=channel_id,
            thread_id=thread_id,
            linked_task_id=linked_task_id,
            status=AUTH_FLOW_STATUS_QR_READY,
            provider_flow_id=started.provider_flow_id,
            qr_payload=started.qr_payload,
            qr_image_path=str(qr_image_path),
            expires_at=started.expires_at or self._expiry_timestamp(self._default_timeout_seconds),
        )

    async def get_active_flow(self, provider: str, owner_user_id: str) -> AuthFlow | None:
        return await self._store.get_active_auth_flow(provider, owner_user_id)

    async def retry_qr_flow(self, flow_id: str) -> AuthFlow:
        flow = await self._store.get_auth_flow(flow_id)
        if flow is None:
            raise RuntimeError(f"Auth flow `{flow_id}` not found.")
        return await self.start_qr_flow(
            flow.provider,
            owner_user_id=flow.owner_user_id,
            platform=flow.platform,
            channel_id=flow.channel_id,
            thread_id=flow.thread_id,
            linked_task_id=flow.linked_task_id,
            force_new=True,
        )

    async def clear_credential(self, provider: str, owner_user_id: str) -> None:
        provider_impl = self._require_provider(provider)
        credential = await self._store.get_auth_credential(provider, owner_user_id, scope_key=AUTH_SCOPE_DEFAULT)
        if credential:
            await provider_impl.invalidate_credential(credential)
            await self._store.delete_auth_credential(provider, owner_user_id, scope_key=AUTH_SCOPE_DEFAULT)
        flow = await self._store.get_active_auth_flow(provider, owner_user_id)
        if flow:
            await self._store.update_auth_flow(
                flow.id,
                status=AUTH_FLOW_STATUS_CANCELLED,
                error="Cancelled by user.",
                completed_at_now=True,
            )

    async def get_status(
        self,
        provider: str,
        owner_user_id: str,
    ) -> dict[str, Any]:
        credential = await self._store.get_auth_credential(provider, owner_user_id, scope_key=AUTH_SCOPE_DEFAULT)
        active_flow = await self._store.get_active_auth_flow(provider, owner_user_id)
        return {
            "provider": provider,
            "credential": credential,
            "active_flow": active_flow,
        }

    def _require_provider(self, provider: str) -> AuthProvider:
        impl = self._providers.get(provider)
        if impl is None:
            raise RuntimeError(f"Unsupported auth provider `{provider}`.")
        return impl

    def _provider_enabled(self, provider: str) -> bool:
        cfg = self._provider_config.get(provider, {})
        return bool(cfg.get("enabled", True))

    async def _render_qr_png(self, flow_id: str, payload: str) -> Path:
        try:
            import qrcode
        except Exception as exc:  # pragma: no cover - dependency presence varies by env
            raise RuntimeError("qrcode dependency is required for QR auth flows.") from exc

        qr_dir = self._storage_root / "qr"
        qr_dir.mkdir(parents=True, exist_ok=True)
        qr_path = qr_dir / f"{flow_id}.png"
        image = qrcode.make(payload)
        image.save(qr_path)
        qr_path.chmod(0o600)
        return qr_path

    async def _poller_loop(self) -> None:
        while not self._stop_event.is_set():
            flows = await self._store.list_active_auth_flows(limit=100)
            for flow in flows:
                try:
                    await self._poll_one(flow)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.warning("Auth flow poll failed for %s", flow.id, exc_info=True)
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=self._poll_interval_seconds)
            except asyncio.TimeoutError:
                continue

    async def _poll_one(self, flow: AuthFlow) -> None:
        provider = self._require_provider(flow.provider)
        result = await provider.poll_qr_login(flow)
        if result.status == AUTH_POLL_STATUS_SCANNED:
            return
        if result.status == AUTH_POLL_STATUS_EXPIRED:
            updated = await self._store.update_auth_flow(
                flow.id,
                status=AUTH_FLOW_STATUS_EXPIRED,
                error=result.message,
                completed_at_now=True,
            )
            if updated:
                await self._emit("expired", updated, None, result.message)
            return
        if result.status == AUTH_POLL_STATUS_FAILED:
            updated = await self._store.update_auth_flow(
                flow.id,
                status=AUTH_FLOW_STATUS_FAILED,
                error=result.message,
                completed_at_now=True,
            )
            if updated:
                await self._emit("failed", updated, None, result.message)
            return
        if result.status != AUTH_POLL_STATUS_APPROVED:
            return

        storage_root = self._storage_root
        cookie_path, metadata = await provider.persist_credential(flow, result, storage_root)
        credential = await self._store.upsert_auth_credential(
            credential_id=uuid.uuid4().hex[:12],
            provider=flow.provider,
            owner_user_id=flow.owner_user_id,
            scope_key=AUTH_SCOPE_DEFAULT,
            status=AUTH_CREDENTIAL_STATUS_VALID,
            storage_path=str(cookie_path),
            metadata_json=metadata,
            last_verified_at=self._now_timestamp(),
            expires_at=metadata.get("expires_at"),
        )
        updated = await self._store.update_auth_flow(
            flow.id,
            status=AUTH_FLOW_STATUS_APPROVED,
            error=None,
            completed_at_now=True,
        )
        if updated:
            await self._emit("approved", updated, credential, result.message)

    async def _emit(
        self,
        event_type: str,
        flow: AuthFlow,
        credential: CredentialHandle | None,
        message: str | None,
    ) -> None:
        for listener in self._listeners:
            try:
                await listener(event_type, flow, credential, message)
            except Exception:
                logger.warning("Auth flow listener failed for %s", flow.id, exc_info=True)

    @staticmethod
    def _expiry_timestamp(seconds: int) -> str:
        return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).strftime("%Y-%m-%d %H:%M:%S")

    @staticmethod
    def _now_timestamp() -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
