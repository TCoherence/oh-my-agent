from __future__ import annotations

import pytest

from oh_my_agent.control.protocol import (
    ProtocolError,
    extract_control_frame,
    parse_ask_user_challenge,
    parse_auth_challenge,
    parse_control_envelope,
    strip_control_frame_text,
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


def test_strip_control_frame_text_keeps_visible_content():
    text = (
        'before\n\n'
        '<OMA_CONTROL>{"version":1,"type":"challenge","data":{"challenge_type":"auth_required","provider":"bilibili","reason":"login_required"}}</OMA_CONTROL>'
        '\n\nafter'
    )
    assert strip_control_frame_text(text) == "before\n\nafter"


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


def test_parse_ask_user_challenge_accepts_valid_choices():
    envelope = parse_control_envelope(
        '<OMA_CONTROL>{"version":1,"type":"challenge","data":{"challenge_type":"ask_user","question":"Pick one","details":"Need your choice","choices":[{"id":"politics","label":"Politics daily","description":"Focus on geopolitics"},{"id":"finance","label":"Finance daily"}]}}</OMA_CONTROL>'
    )
    challenge = parse_ask_user_challenge(envelope)
    assert challenge is not None
    assert challenge.question == "Pick one"
    assert challenge.details == "Need your choice"
    assert len(challenge.choices) == 2
    assert challenge.choices[0].id == "politics"
    assert challenge.choices[0].label == "Politics daily"


def test_parse_ask_user_challenge_rejects_missing_question():
    envelope = parse_control_envelope(
        '<OMA_CONTROL>{"version":1,"type":"challenge","data":{"challenge_type":"ask_user","choices":[{"id":"politics","label":"Politics daily"}]}}</OMA_CONTROL>'
    )
    with pytest.raises(ProtocolError):
        parse_ask_user_challenge(envelope)


def test_parse_ask_user_challenge_rejects_too_many_choices():
    envelope = parse_control_envelope(
        '<OMA_CONTROL>{"version":1,"type":"challenge","data":{"challenge_type":"ask_user","question":"Pick one","choices":[{"id":"a","label":"A"},{"id":"b","label":"B"},{"id":"c","label":"C"},{"id":"d","label":"D"},{"id":"e","label":"E"},{"id":"f","label":"F"}]}}</OMA_CONTROL>'
    )
    with pytest.raises(ProtocolError):
        parse_ask_user_challenge(envelope)


def test_parse_ask_user_challenge_rejects_duplicate_choice_ids():
    envelope = parse_control_envelope(
        '<OMA_CONTROL>{"version":1,"type":"challenge","data":{"challenge_type":"ask_user","question":"Pick one","choices":[{"id":"finance","label":"Finance daily"},{"id":"finance","label":"Finance weekly"}]}}</OMA_CONTROL>'
    )
    with pytest.raises(ProtocolError):
        parse_ask_user_challenge(envelope)
