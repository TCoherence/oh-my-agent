from __future__ import annotations

import pytest

from oh_my_agent.control.protocol import (
    ProtocolError,
    extract_control_frame,
    parse_auth_challenge,
    parse_control_envelope,
)


def test_extract_control_frame_returns_payload():
    text = 'before <OMA_CONTROL>{"version":1,"type":"challenge","data":{"challenge_type":"auth_required","provider":"bilibili","reason":"login_required"}}</OMA_CONTROL> after'
    frame = extract_control_frame(text)
    assert frame is not None
    assert '"provider":"bilibili"' in frame


def test_extract_control_frame_rejects_multiple_frames():
    text = (
        '<OMA_CONTROL>{"version":1,"type":"challenge","data":{"challenge_type":"auth_required","provider":"bilibili","reason":"login_required"}}</OMA_CONTROL>'
        '<OMA_CONTROL>{"version":1,"type":"challenge","data":{"challenge_type":"auth_required","provider":"youtube","reason":"login_required"}}</OMA_CONTROL>'
    )
    with pytest.raises(ProtocolError):
        extract_control_frame(text)


def test_parse_auth_challenge_validates_known_provider():
    envelope = parse_control_envelope(
        '<OMA_CONTROL>{"version":1,"type":"challenge","data":{"challenge_type":"auth_required","provider":"bilibili","reason":"login_required"}}</OMA_CONTROL>'
    )
    challenge = parse_auth_challenge(envelope)
    assert challenge is not None
    assert challenge.provider == "bilibili"
    assert challenge.reason == "login_required"


def test_parse_auth_challenge_rejects_unknown_provider():
    envelope = parse_control_envelope(
        '<OMA_CONTROL>{"version":1,"type":"challenge","data":{"challenge_type":"auth_required","provider":"mystery","reason":"login_required"}}</OMA_CONTROL>'
    )
    with pytest.raises(ProtocolError):
        parse_auth_challenge(envelope)
