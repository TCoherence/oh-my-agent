from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from oh_my_agent.providers.registry import normalize_provider_name

CONTROL_TAG = "OMA_CONTROL"
CONTROL_FRAME_RE = re.compile(
    rf"<{CONTROL_TAG}>\s*(\{{.*?\}})\s*</{CONTROL_TAG}>",
    re.DOTALL,
)
CONTROL_PROTOCOL_VERSION = 1
CONTROL_TYPE_CHALLENGE = "challenge"
CHALLENGE_TYPE_AUTH_REQUIRED = "auth_required"


class ProtocolError(ValueError):
    """Raised when an agent control frame is malformed."""


@dataclass(frozen=True)
class ControlEnvelope:
    version: int
    type: str
    data: dict[str, Any]
    raw_json: str


@dataclass(frozen=True)
class ChallengePayload:
    challenge_type: str
    payload: dict[str, Any]


@dataclass(frozen=True)
class AuthRequiredChallenge:
    provider: str
    reason: str
    message: str | None = None


def extract_control_frame(text: str) -> str | None:
    matches = CONTROL_FRAME_RE.findall(text or "")
    if not matches:
        return None
    if len(matches) > 1:
        raise ProtocolError("Multiple OMA_CONTROL frames are not supported.")
    return matches[0]


def strip_control_frame_text(text: str) -> str:
    """Return visible text with the single OMA_CONTROL frame removed."""
    if not text:
        return ""
    frame = extract_control_frame(text)
    if frame is None:
        return text.strip()
    stripped = CONTROL_FRAME_RE.sub("", text, count=1).strip()
    return re.sub(r"\n{3,}", "\n\n", stripped)


def parse_control_envelope(text: str) -> ControlEnvelope:
    frame = extract_control_frame(text)
    if frame is None:
        raise ProtocolError("No OMA_CONTROL frame found.")
    try:
        payload = json.loads(frame)
    except json.JSONDecodeError as exc:
        raise ProtocolError("OMA_CONTROL payload is not valid JSON.") from exc
    if not isinstance(payload, dict):
        raise ProtocolError("OMA_CONTROL payload must be a JSON object.")

    version = payload.get("version")
    if version != CONTROL_PROTOCOL_VERSION:
        raise ProtocolError(f"Unsupported OMA_CONTROL version: {version!r}")
    envelope_type = payload.get("type")
    if not isinstance(envelope_type, str) or not envelope_type.strip():
        raise ProtocolError("OMA_CONTROL.type must be a non-empty string.")
    data = payload.get("data")
    if not isinstance(data, dict):
        raise ProtocolError("OMA_CONTROL.data must be an object.")
    return ControlEnvelope(
        version=CONTROL_PROTOCOL_VERSION,
        type=envelope_type.strip(),
        data=data,
        raw_json=frame,
    )


def parse_challenge_payload(envelope: ControlEnvelope) -> ChallengePayload | None:
    if envelope.type != CONTROL_TYPE_CHALLENGE:
        return None
    challenge_type = envelope.data.get("challenge_type")
    if not isinstance(challenge_type, str) or not challenge_type.strip():
        raise ProtocolError("Challenge payload must include a non-empty challenge_type.")
    return ChallengePayload(
        challenge_type=challenge_type.strip(),
        payload=envelope.data,
    )


def parse_auth_challenge(envelope: ControlEnvelope) -> AuthRequiredChallenge | None:
    challenge = parse_challenge_payload(envelope)
    if challenge is None or challenge.challenge_type != CHALLENGE_TYPE_AUTH_REQUIRED:
        return None

    provider = normalize_provider_name(str(challenge.payload.get("provider") or ""))
    if provider is None:
        raise ProtocolError("auth_required challenge must include a known provider.")
    reason = str(challenge.payload.get("reason") or "").strip()
    if not reason:
        raise ProtocolError("auth_required challenge must include a non-empty reason.")
    message = challenge.payload.get("message")
    if message is not None:
        message = str(message)
    return AuthRequiredChallenge(provider=provider, reason=reason, message=message)


def try_parse_auth_challenge(text: str) -> AuthRequiredChallenge | None:
    envelope = parse_control_envelope(text)
    return parse_auth_challenge(envelope)
