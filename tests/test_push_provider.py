"""Unit tests for BarkPushProvider + NoopPushProvider."""

from __future__ import annotations

import json
import urllib.error
from unittest.mock import MagicMock, patch

import pytest

from oh_my_agent.push_notifications import (
    BarkPushProvider,
    NoopPushProvider,
    PushNotificationEvent,
)


def _event(**over) -> PushNotificationEvent:
    defaults = dict(
        kind="task_draft",
        title="Action required",
        body="Please approve",
        group="hitl",
        level="timeSensitive",
        deep_link=None,
    )
    defaults.update(over)
    return PushNotificationEvent(**defaults)


@pytest.mark.asyncio
async def test_noop_provider_send_is_silent():
    p = NoopPushProvider()
    # Should not raise nor block
    await p.send(_event())
    await p.aclose()


@pytest.mark.asyncio
async def test_bark_provider_posts_expected_payload():
    p = BarkPushProvider(server="https://api.day.app/", device_key="DEV123")

    captured = {}

    class FakeResp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return b""

    def fake_urlopen(req, timeout=None):  # noqa: ARG001
        captured["url"] = req.full_url
        captured["headers"] = dict(req.header_items())
        captured["data"] = json.loads(req.data.decode("utf-8"))
        captured["method"] = req.get_method()
        return FakeResp()

    with patch("urllib.request.urlopen", side_effect=fake_urlopen):
        await p.send(_event(deep_link="https://discord.com/foo/bar"))

    assert captured["url"] == "https://api.day.app/DEV123"
    # rstrip trailing slash on server
    assert captured["method"] == "POST"
    assert any(k.lower() == "content-type" for k in captured["headers"])
    body = captured["data"]
    assert body["title"] == "Action required"
    assert body["body"] == "Please approve"
    assert body["group"] == "hitl"
    assert body["level"] == "timeSensitive"
    assert body["url"] == "https://discord.com/foo/bar"


@pytest.mark.asyncio
async def test_bark_provider_truncates_long_title_and_body():
    p = BarkPushProvider(server="https://api.day.app", device_key="DEV")
    captured = {}

    class FakeResp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return b""

    def fake_urlopen(req, timeout=None):  # noqa: ARG001
        captured["data"] = json.loads(req.data.decode("utf-8"))
        return FakeResp()

    long_title = "x" * 250
    long_body = "y" * 1000
    with patch("urllib.request.urlopen", side_effect=fake_urlopen):
        await p.send(_event(title=long_title, body=long_body))

    # Provider applies a final-line safety cap (caller-level trim already
    # keeps real-world payloads well below).
    assert len(captured["data"]["title"]) == 100
    assert len(captured["data"]["body"]) == 500


@pytest.mark.asyncio
async def test_bark_provider_omits_url_when_no_deep_link():
    p = BarkPushProvider(server="https://api.day.app", device_key="DEV")
    captured = {}

    class FakeResp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return b""

    def fake_urlopen(req, timeout=None):  # noqa: ARG001
        captured["data"] = json.loads(req.data.decode("utf-8"))
        return FakeResp()

    with patch("urllib.request.urlopen", side_effect=fake_urlopen):
        await p.send(_event(deep_link=None))

    assert "url" not in captured["data"]


@pytest.mark.asyncio
async def test_bark_provider_swallows_http_error():
    p = BarkPushProvider(server="https://api.day.app", device_key="DEV")

    def raising(*a, **k):  # noqa: ARG001
        raise urllib.error.HTTPError(
            url="x", code=500, msg="boom", hdrs=MagicMock(), fp=None
        )

    with patch("urllib.request.urlopen", side_effect=raising):
        # Must not raise — provider always swallows
        await p.send(_event())


@pytest.mark.asyncio
async def test_bark_provider_swallows_url_error():
    p = BarkPushProvider(server="https://api.day.app", device_key="DEV")

    def raising(*a, **k):  # noqa: ARG001
        raise urllib.error.URLError("network down")

    with patch("urllib.request.urlopen", side_effect=raising):
        await p.send(_event())


@pytest.mark.asyncio
async def test_bark_provider_swallows_os_error():
    p = BarkPushProvider(server="https://api.day.app", device_key="DEV")

    def raising(*a, **k):  # noqa: ARG001
        raise TimeoutError("slow")

    with patch("urllib.request.urlopen", side_effect=raising):
        await p.send(_event())


@pytest.mark.asyncio
async def test_bark_provider_aclose_is_noop():
    p = BarkPushProvider(server="https://api.day.app", device_key="DEV")
    await p.aclose()
