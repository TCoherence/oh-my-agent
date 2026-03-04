from __future__ import annotations

from pathlib import Path
from typing import Protocol

from oh_my_agent.auth.types import (
    AuthFlow,
    AuthPollResult,
    AuthStartResult,
    CredentialHandle,
    CredentialValidation,
)


class AuthProvider(Protocol):
    def provider_name(self) -> str: ...

    async def start_qr_login(self, owner_user_id: str) -> AuthStartResult: ...

    async def poll_qr_login(self, flow: AuthFlow) -> AuthPollResult: ...

    async def persist_credential(
        self,
        flow: AuthFlow,
        result: AuthPollResult,
        storage_root: Path,
    ) -> tuple[Path, dict]: ...

    async def validate_credential(self, handle: CredentialHandle) -> CredentialValidation: ...

    async def invalidate_credential(self, handle: CredentialHandle) -> None: ...
