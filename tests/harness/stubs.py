"""Deterministic stubs that let scenarios run offline.

- ``StubAgent`` mimics real CLI agents enough to trigger the same fallback
  behavior in ``AgentRegistry``. Critically, it models cwd-keyed sessions
  (matching ``~/.claude/projects/<cwd-hash>/`` semantics) so the L-level
  cwd-mismatch regression is catchable in tests.

- ``StubBilibiliAuthProvider`` replaces the real Bilibili provider so QR
  polling resolves immediately and credential validation skips the
  network call to ``api.bilibili.com``.

- ``seed_credential`` writes a fake cookies.txt + a ``valid`` row in the
  AuthService DB so ``get_valid_credential`` returns a credential without
  ever touching the network.
"""
from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from oh_my_agent.agents.base import AgentResponse, BaseAgent
from oh_my_agent.auth.types import (
    AUTH_CREDENTIAL_STATUS_VALID,
    AUTH_POLL_STATUS_APPROVED,
    AUTH_POLL_STATUS_EXPIRED,
    AUTH_POLL_STATUS_FAILED,
    AUTH_POLL_STATUS_PENDING,
    AUTH_SCOPE_DEFAULT,
    AuthFlow,
    AuthPollResult,
    AuthStartResult,
    CredentialHandle,
    CredentialValidation,
)
from oh_my_agent.memory.store import MemoryStore

# ---------------------------------------------------------------------------
# StubAgent — cwd-keyed session model
# ---------------------------------------------------------------------------


@dataclass
class _ResponseSpec:
    """One configured response branch.

    ``when`` is a dict of predicates ANDed together. Supported keys:
      - ``content_contains``: substring of the prompt
      - ``content_regex``: regex pattern over prompt
      - ``step_no_eq``: integer match against per-(thread, agent) call counter
      - ``default``: True → fallback when nothing else matched
    """

    when: dict[str, Any]
    text: str | None = None
    error: str | None = None
    error_kind: str | None = None
    usage: dict[str, Any] | None = None


def _parse_response_specs(raw: list[dict[str, Any]] | None) -> list[_ResponseSpec]:
    specs: list[_ResponseSpec] = []
    for item in raw or []:
        specs.append(
            _ResponseSpec(
                when=item.get("when") or {"default": True},
                text=item.get("text"),
                error=item.get("error"),
                error_kind=item.get("error_kind"),
                usage=item.get("usage"),
            )
        )
    return specs


class StubAgent(BaseAgent):
    """Stub agent that mimics real CLI agents for harness scenarios.

    Two behaviors matter for catching the PR #41 regressions:

    1. ``cwd_keyed_sessions`` — when True, sessions are keyed by
       ``(thread_id, cwd_str)``. If ``run()`` is called with the same
       ``thread_id`` but a different ``workspace_override`` than where the
       session was created, returns the EXACT error string the real claude
       CLI produces (``"<name> exited 1: No conversation found with session
       ID: <id>"``) so ``AgentRegistry`` falls through to the next agent.
       This is what catches the L-level cwd-mismatch regression.

    2. Response selection by predicates — ``responses`` is an ordered list
       of ``_ResponseSpec``; first match wins. Use this to script auth
       challenges, multi-step task progressions, and fallback errors.
    """

    def __init__(
        self,
        name: str,
        *,
        responses: list[dict[str, Any]] | None = None,
        cwd_keyed_sessions: bool = False,
        timeout_seconds: int | None = None,
        max_turns: int | None = None,
    ) -> None:
        self._name = name
        self._responses = _parse_response_specs(responses)
        self._cwd_keyed = cwd_keyed_sessions
        # (thread_id, cwd_str) → session_id
        self._sessions: dict[tuple[str, str], str] = {}
        # thread_id → cwd of latest successful invocation; mirrors what
        # AgentRegistry persists in agent_sessions for real CLI agents.
        self._last_cwd: dict[str, str] = {}
        # per-thread call counter for step_no_eq predicates
        self._step_counter: dict[str, int] = {}
        # Only used by AgentRegistry's _temporary_timeout / _temporary_max_turns
        # context managers (they look up these attrs by name; harmless to
        # have them present on a stub).
        self._timeout = timeout_seconds or 600
        self._max_turns = max_turns or 25

    @property
    def name(self) -> str:
        return self._name

    # Real claude exposes get_session_id / set_session_id / clear_session
    # so agent_session DB sync works. Mirror that surface so the runtime's
    # _restore_thread_agent_session and _sync_thread_agent_session helpers
    # behave the same way against the stub.
    def get_session_id(self, thread_id: str) -> str | None:
        if not self._cwd_keyed:
            return None
        cwd = self._last_cwd.get(thread_id)
        if cwd is None:
            return None
        return self._sessions.get((thread_id, cwd))

    def set_session_id(self, thread_id: str, session_id: str) -> None:
        if not self._cwd_keyed:
            return
        cwd = self._last_cwd.get(thread_id, "<base>")
        self._sessions[(thread_id, cwd)] = session_id

    def clear_session(self, thread_id: str) -> None:
        if not self._cwd_keyed:
            return
        for key in list(self._sessions.keys()):
            if key[0] == thread_id:
                self._sessions.pop(key, None)
        self._last_cwd.pop(thread_id, None)

    async def run(
        self,
        prompt: str,
        history: list[dict] | None = None,
        *,
        thread_id: str | None = None,
        workspace_override: Path | None = None,
        log_path: Path | None = None,
        image_paths: list[Path] | None = None,
    ) -> AgentResponse:
        del history, log_path, image_paths
        if self._cwd_keyed and thread_id:
            cwd_key = str(workspace_override.resolve()) if workspace_override else "<base>"
            stored_cwd = self._last_cwd.get(thread_id)
            if stored_cwd is not None and stored_cwd != cwd_key:
                stale_session = self._sessions.get((thread_id, stored_cwd), uuid.uuid4().hex[:36])
                return AgentResponse(
                    text="",
                    error=(
                        f"{self._name} exited 1: No conversation found with session ID: "
                        f"{stale_session}"
                    ),
                    error_kind="cli_error",
                )
            self._sessions.setdefault((thread_id, cwd_key), uuid.uuid4().hex[:36])
            self._last_cwd[thread_id] = cwd_key
        return self._select_response(prompt, thread_id)

    def _select_response(self, prompt: str, thread_id: str | None) -> AgentResponse:
        step_no = self._step_counter.get(thread_id or "<global>", 0) + 1
        self._step_counter[thread_id or "<global>"] = step_no
        for spec in self._responses:
            if not _match_when(spec.when, prompt=prompt, step_no=step_no):
                continue
            if spec.error:
                return AgentResponse(
                    text="",
                    error=spec.error,
                    error_kind=spec.error_kind or "cli_error",
                )
            return AgentResponse(text=spec.text or "", usage=spec.usage)
        # No spec matched → return a neutral "TASK_STATE: DONE" response so
        # the runtime considers the step complete. Tests that care about
        # exact output should configure a default spec explicitly.
        return AgentResponse(text="(stub) ok\nTASK_STATE: DONE")


def _match_when(when: dict[str, Any], *, prompt: str, step_no: int) -> bool:
    if when.get("default") is True:
        return True
    if (needle := when.get("content_contains")) is not None:
        if needle not in prompt:
            return False
    if (pattern := when.get("content_regex")) is not None:
        if not re.search(pattern, prompt):
            return False
    if (target_step := when.get("step_no_eq")) is not None:
        if int(target_step) != step_no:
            return False
    return True


# ---------------------------------------------------------------------------
# StubBilibiliAuthProvider
# ---------------------------------------------------------------------------


@dataclass
class StubBilibiliAuthProvider:
    """Drop-in for ``BilibiliAuthProvider`` that doesn't touch the network.

    Three modes via ``mode`` field:
      - ``"valid"``: ``validate_credential`` always returns ``valid=True``.
        Use with ``seed_credential`` for cached-cookie scenarios.
      - ``"approving"``: ``poll_qr_login`` immediately returns
        ``AUTH_POLL_STATUS_APPROVED`` with stub cookies; ``persist_credential``
        writes a fake cookies.txt. Use for fresh-login scenarios.
      - ``"failing"``: ``poll_qr_login`` returns expired/failed.

    All three modes accept ``validate_credential`` calls without HTTP.
    """

    mode: str = "valid"
    cookie_value: str = "stub-sessdata-value"
    persist_root: Path | None = None
    started_flows: list[AuthFlow] = field(default_factory=list)
    poll_count: dict[str, int] = field(default_factory=dict)

    def provider_name(self) -> str:
        return "bilibili"

    async def start_qr_login(self, owner_user_id: str) -> AuthStartResult:
        flow_uuid = uuid.uuid4().hex[:12]
        return AuthStartResult(
            provider_flow_id=f"stub-{flow_uuid}",
            qr_payload=f"https://example.invalid/stub-qr/{flow_uuid}",
            expires_at="2099-01-01 00:00:00",
        )

    async def poll_qr_login(self, flow: AuthFlow) -> AuthPollResult:
        self.poll_count[flow.id] = self.poll_count.get(flow.id, 0) + 1
        if self.mode == "approving":
            # Return PENDING on first poll so the runtime has a chance to
            # deliver the QR intro + attachment to the user before approval
            # fires. Without this, the 50ms harness poller can race ahead
            # of the auth-prompt sender and the channel sees "Login
            # confirmed" before the QR — confusing for assertions and not
            # representative of real-world timing where polls take seconds.
            if self.poll_count[flow.id] == 1:
                return AuthPollResult(status=AUTH_POLL_STATUS_PENDING, message="Stub pending")
            return AuthPollResult(
                status=AUTH_POLL_STATUS_APPROVED,
                message="Stub auto-approved",
                credential_payload={
                    "cookies": [
                        {
                            "name": "SESSDATA",
                            "value": self.cookie_value,
                            "domain": ".bilibili.com",
                            "path": "/",
                            "secure": True,
                            "http_only": True,
                            "expires": 1900000000,
                        },
                    ],
                    "refresh_token": "stub-refresh-token",
                    "timestamp": 1777878000000,
                },
            )
        if self.mode == "failing":
            return AuthPollResult(status=AUTH_POLL_STATUS_FAILED, message="Stub failure")
        if self.mode == "expiring":
            return AuthPollResult(status=AUTH_POLL_STATUS_EXPIRED, message="Stub expired")
        # "valid" mode shouldn't be polled (no QR was generated for it),
        # but if something polls anyway, just say expired so AuthService
        # cleans up.
        return AuthPollResult(status=AUTH_POLL_STATUS_EXPIRED, message="Stub valid mode — no flow")

    async def persist_credential(
        self,
        flow: AuthFlow,
        result: AuthPollResult,
        storage_root: Path,
    ) -> tuple[Path, dict]:
        provider_root = storage_root / "providers" / self.provider_name() / flow.owner_user_id
        provider_root.mkdir(parents=True, exist_ok=True)
        cookie_path = provider_root / "cookies.txt"
        meta_path = provider_root / "meta.json"
        cookie_path.write_text(_make_cookie_jar(self.cookie_value), encoding="utf-8")
        cookie_path.chmod(0o600)
        metadata = {
            "provider": self.provider_name(),
            "owner_user_id": flow.owner_user_id,
            "expires_at": "2099-01-01 00:00:00",
            "stub": True,
        }
        meta_path.write_text(_dump_meta(metadata), encoding="utf-8")
        meta_path.chmod(0o600)
        return cookie_path, metadata

    async def validate_credential(self, handle: CredentialHandle) -> CredentialValidation:
        # The whole point of the stub: NEVER make a network call. Any
        # credential the AuthService hands us is treated as valid.
        return CredentialValidation(
            valid=True,
            metadata={"stub_validated_at": _now_str()},
        )

    async def invalidate_credential(self, handle: CredentialHandle) -> None:
        cookie_path = Path(handle.storage_path)
        meta_path = cookie_path.with_name("meta.json")
        for path in (cookie_path, meta_path):
            if path.exists():
                path.unlink()


# ---------------------------------------------------------------------------
# Seeders
# ---------------------------------------------------------------------------


async def seed_credential(
    *,
    store: MemoryStore,
    storage_root: Path,
    provider: str,
    owner_user_id: str,
    cookie_value: str = "stub-sessdata-value",
) -> Path:
    """Write a fake cookies.txt + persist a ``valid`` row in the AuthService DB.

    Use in scenarios that want to test the cached-credential path:
    ``RuntimeService.collect_provider_credential_hints`` and the chat-reply
    credential injection both go through ``MemoryStore.get_auth_credential``
    + ``provider.validate_credential`` (which the stub provider short-circuits
    to always valid).
    """
    provider_root = storage_root / "providers" / provider / owner_user_id
    provider_root.mkdir(parents=True, exist_ok=True)
    cookie_path = provider_root / "cookies.txt"
    cookie_path.write_text(_make_cookie_jar(cookie_value), encoding="utf-8")
    cookie_path.chmod(0o600)
    await store.upsert_auth_credential(
        credential_id=uuid.uuid4().hex[:12],
        provider=provider,
        owner_user_id=owner_user_id,
        scope_key=AUTH_SCOPE_DEFAULT,
        status=AUTH_CREDENTIAL_STATUS_VALID,
        storage_path=str(cookie_path),
        metadata_json={"stub": True, "owner_user_id": owner_user_id},
        last_verified_at=_now_str(),
        expires_at="2099-01-01 00:00:00",
    )
    return cookie_path


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _make_cookie_jar(sessdata_value: str) -> str:
    """Render a Mozilla-format cookies.txt with the bare minimum bilibili needs."""
    return (
        "# Netscape HTTP Cookie File\n"
        "# Stub cookies generated by harness — DO NOT USE FOR REAL ACCESS\n"
        f"bilibili.com\tFALSE\t/\tTRUE\t1900000000\tSESSDATA\t{sessdata_value}\n"
        "bilibili.com\tFALSE\t/\tFALSE\t1900000000\tbili_jct\tstub-bili-jct\n"
        "bilibili.com\tFALSE\t/\tTRUE\t1900000000\tDedeUserID\t99999999\n"
    )


def _dump_meta(payload: dict[str, Any]) -> str:
    import json

    return json.dumps(payload, ensure_ascii=False, indent=2)


def _now_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
