from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, cast

AUTH_SCOPE_DEFAULT = "default"

AuthCredentialStatus = Literal["valid", "invalid", "expired"]
AuthFlowStatus = Literal["pending", "qr_ready", "approved", "expired", "failed", "cancelled"]
AuthPollStatus = Literal["pending", "scanned", "approved", "expired", "failed"]

AUTH_CREDENTIAL_STATUS_VALID: AuthCredentialStatus = "valid"
AUTH_CREDENTIAL_STATUS_INVALID: AuthCredentialStatus = "invalid"
AUTH_CREDENTIAL_STATUS_EXPIRED: AuthCredentialStatus = "expired"

AUTH_FLOW_STATUS_PENDING: AuthFlowStatus = "pending"
AUTH_FLOW_STATUS_QR_READY: AuthFlowStatus = "qr_ready"
AUTH_FLOW_STATUS_APPROVED: AuthFlowStatus = "approved"
AUTH_FLOW_STATUS_EXPIRED: AuthFlowStatus = "expired"
AUTH_FLOW_STATUS_FAILED: AuthFlowStatus = "failed"
AUTH_FLOW_STATUS_CANCELLED: AuthFlowStatus = "cancelled"

AUTH_POLL_STATUS_PENDING: AuthPollStatus = "pending"
AUTH_POLL_STATUS_SCANNED: AuthPollStatus = "scanned"
AUTH_POLL_STATUS_APPROVED: AuthPollStatus = "approved"
AUTH_POLL_STATUS_EXPIRED: AuthPollStatus = "expired"
AUTH_POLL_STATUS_FAILED: AuthPollStatus = "failed"


@dataclass(frozen=True)
class CredentialHandle:
    id: str
    provider: str
    owner_user_id: str
    scope_key: str
    status: AuthCredentialStatus
    storage_path: str
    metadata: dict[str, Any] = field(default_factory=dict)
    last_verified_at: str | None = None
    expires_at: str | None = None
    created_at: str | None = None
    updated_at: str | None = None

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> "CredentialHandle":
        return cls(
            id=str(row["id"]),
            provider=str(row["provider"]),
            owner_user_id=str(row["owner_user_id"]),
            scope_key=str(row.get("scope_key", AUTH_SCOPE_DEFAULT)),
            status=cast(AuthCredentialStatus, str(row["status"])),
            storage_path=str(row["storage_path"]),
            metadata=row.get("metadata_json") or {},
            last_verified_at=row.get("last_verified_at"),
            expires_at=row.get("expires_at"),
            created_at=row.get("created_at"),
            updated_at=row.get("updated_at"),
        )


@dataclass(frozen=True)
class AuthFlow:
    id: str
    provider: str
    owner_user_id: str
    platform: str
    channel_id: str
    thread_id: str
    linked_task_id: str | None
    status: AuthFlowStatus
    provider_flow_id: str
    qr_payload: str
    qr_image_path: str | None
    error: str | None
    expires_at: str | None
    created_at: str | None = None
    updated_at: str | None = None
    completed_at: str | None = None

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> "AuthFlow":
        return cls(
            id=str(row["id"]),
            provider=str(row["provider"]),
            owner_user_id=str(row["owner_user_id"]),
            platform=str(row["platform"]),
            channel_id=str(row["channel_id"]),
            thread_id=str(row["thread_id"]),
            linked_task_id=row.get("linked_task_id"),
            status=cast(AuthFlowStatus, str(row["status"])),
            provider_flow_id=str(row["provider_flow_id"]),
            qr_payload=str(row["qr_payload"]),
            qr_image_path=row.get("qr_image_path"),
            error=row.get("error"),
            expires_at=row.get("expires_at"),
            created_at=row.get("created_at"),
            updated_at=row.get("updated_at"),
            completed_at=row.get("completed_at"),
        )


@dataclass(frozen=True)
class AuthStartResult:
    provider_flow_id: str
    qr_payload: str
    expires_at: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class AuthPollResult:
    status: AuthPollStatus
    message: str | None = None
    credential_payload: dict[str, Any] | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    expires_at: str | None = None


@dataclass(frozen=True)
class CredentialValidation:
    valid: bool
    reason: str | None = None
    expires_at: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
