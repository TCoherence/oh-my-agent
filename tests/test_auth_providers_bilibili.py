"""Covers BilibiliAuthProvider OAuth state machine with mocked HTTP responses."""
from __future__ import annotations

import http.cookiejar
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from oh_my_agent.auth.providers.bilibili import BilibiliAuthProvider
from oh_my_agent.auth.types import (
    AUTH_POLL_STATUS_APPROVED,
    AUTH_POLL_STATUS_EXPIRED,
    AUTH_POLL_STATUS_FAILED,
    AUTH_POLL_STATUS_PENDING,
    AUTH_POLL_STATUS_SCANNED,
    AuthFlow,
    AuthPollResult,
    CredentialHandle,
)


def _make_flow(flow_id: str = "qr-key-abc") -> AuthFlow:
    return AuthFlow(
        id="auth-1",
        provider="bilibili",
        owner_user_id="user-1",
        platform="discord",
        channel_id="100",
        thread_id="200",
        linked_task_id=None,
        status="qr_ready",
        provider_flow_id=flow_id,
        qr_payload="https://passport.bilibili.com/qr/abc",
        qr_image_path=None,
        error=None,
        expires_at=None,
    )


def _mock_headers(set_cookie_values=None):
    headers = MagicMock()
    headers.get_all = MagicMock(return_value=list(set_cookie_values or []))
    return headers


@pytest.mark.asyncio
async def test_provider_name():
    assert BilibiliAuthProvider().provider_name() == "bilibili"


@pytest.mark.asyncio
async def test_start_qr_login_success():
    provider = BilibiliAuthProvider()
    payload = {
        "code": 0,
        "data": {
            "url": "https://passport.bilibili.com/qr/xyz",
            "qrcode_key": "qr-key-xyz",
        },
    }
    with patch.object(
        BilibiliAuthProvider, "_request_json", return_value=payload
    ):
        result = await provider.start_qr_login("user-1")
    assert result.provider_flow_id == "qr-key-xyz"
    assert result.qr_payload == "https://passport.bilibili.com/qr/xyz"
    assert result.expires_at is not None


@pytest.mark.asyncio
async def test_start_qr_login_raises_on_nonzero_code():
    provider = BilibiliAuthProvider()
    with patch.object(
        BilibiliAuthProvider,
        "_request_json",
        return_value={"code": -101, "message": "ratelimited"},
    ):
        with pytest.raises(RuntimeError, match="ratelimited"):
            await provider.start_qr_login("user-1")


@pytest.mark.asyncio
async def test_start_qr_login_raises_on_incomplete_payload():
    provider = BilibiliAuthProvider()
    with patch.object(
        BilibiliAuthProvider,
        "_request_json",
        return_value={"code": 0, "data": {"url": ""}},
    ):
        with pytest.raises(RuntimeError, match="incomplete"):
            await provider.start_qr_login("user-1")


@pytest.mark.asyncio
async def test_poll_qr_login_pending():
    provider = BilibiliAuthProvider()
    payload = {"code": 0, "data": {"code": 86101, "message": "not scanned"}}
    with patch.object(
        BilibiliAuthProvider,
        "_request_json_with_headers",
        return_value=(payload, _mock_headers()),
    ):
        result = await provider.poll_qr_login(_make_flow())
    assert result.status == AUTH_POLL_STATUS_PENDING


@pytest.mark.asyncio
async def test_poll_qr_login_scanned():
    provider = BilibiliAuthProvider()
    payload = {"code": 0, "data": {"code": 86090, "message": "scanned"}}
    with patch.object(
        BilibiliAuthProvider,
        "_request_json_with_headers",
        return_value=(payload, _mock_headers()),
    ):
        result = await provider.poll_qr_login(_make_flow())
    assert result.status == AUTH_POLL_STATUS_SCANNED


@pytest.mark.asyncio
async def test_poll_qr_login_expired():
    provider = BilibiliAuthProvider()
    payload = {"code": 0, "data": {"code": 86038, "message": "expired"}}
    with patch.object(
        BilibiliAuthProvider,
        "_request_json_with_headers",
        return_value=(payload, _mock_headers()),
    ):
        result = await provider.poll_qr_login(_make_flow())
    assert result.status == AUTH_POLL_STATUS_EXPIRED


@pytest.mark.asyncio
async def test_poll_qr_login_approved_with_cookies_from_headers():
    provider = BilibiliAuthProvider()
    payload = {
        "code": 0,
        "data": {
            "code": 0,
            "message": "ok",
            "url": "https://passport.biligame.com/x/cross-domain",
            "refresh_token": "refresh-xyz",
            "timestamp": 1700000000,
        },
    }
    headers = _mock_headers(
        set_cookie_values=[
            "SESSDATA=sess1; Domain=.bilibili.com; Path=/; HttpOnly; Secure",
            "bili_jct=jct1; Domain=.bilibili.com; Path=/",
        ]
    )
    with patch.object(
        BilibiliAuthProvider,
        "_request_json_with_headers",
        return_value=(payload, headers),
    ):
        result = await provider.poll_qr_login(_make_flow())
    assert result.status == AUTH_POLL_STATUS_APPROVED
    cookies = result.credential_payload["cookies"]
    names = {c["name"] for c in cookies}
    assert "SESSDATA" in names
    assert "bili_jct" in names
    sess = next(c for c in cookies if c["name"] == "SESSDATA")
    assert sess["secure"] is True
    assert sess["http_only"] is True


@pytest.mark.asyncio
async def test_poll_qr_login_approved_with_cross_domain_fallback():
    """If Set-Cookie is missing, fall back to parsing the cross-domain URL."""
    provider = BilibiliAuthProvider()
    payload = {
        "code": 0,
        "data": {
            "code": 0,
            "url": "https://passport.biligame.com/x/cross-domain?SESSDATA=sess2&bili_jct=jct2&DedeUserID=42",
        },
    }
    with patch.object(
        BilibiliAuthProvider,
        "_request_json_with_headers",
        return_value=(payload, _mock_headers()),
    ):
        result = await provider.poll_qr_login(_make_flow())
    assert result.status == AUTH_POLL_STATUS_APPROVED
    cookies = result.credential_payload["cookies"]
    names = {c["name"] for c in cookies}
    assert names >= {"SESSDATA", "bili_jct", "DedeUserID"}


@pytest.mark.asyncio
async def test_poll_qr_login_failed_on_unexpected_status():
    provider = BilibiliAuthProvider()
    payload = {"code": 0, "data": {"code": 99999, "message": "huh"}}
    with patch.object(
        BilibiliAuthProvider,
        "_request_json_with_headers",
        return_value=(payload, _mock_headers()),
    ):
        result = await provider.poll_qr_login(_make_flow())
    assert result.status == AUTH_POLL_STATUS_FAILED


@pytest.mark.asyncio
async def test_poll_qr_login_failed_on_outer_error_code():
    provider = BilibiliAuthProvider()
    payload = {"code": -400, "message": "bad request"}
    with patch.object(
        BilibiliAuthProvider,
        "_request_json_with_headers",
        return_value=(payload, _mock_headers()),
    ):
        result = await provider.poll_qr_login(_make_flow())
    assert result.status == AUTH_POLL_STATUS_FAILED
    assert "bad request" in (result.message or "")


@pytest.mark.asyncio
async def test_persist_credential_writes_cookies_and_metadata(tmp_path: Path):
    provider = BilibiliAuthProvider()
    flow = _make_flow()
    poll = AuthPollResult(
        status=AUTH_POLL_STATUS_APPROVED,
        credential_payload={
            "cookies": [
                {
                    "name": "SESSDATA",
                    "value": "sess1",
                    "domain": ".bilibili.com",
                    "path": "/",
                    "secure": True,
                    "http_only": True,
                    "expires": 1700000000,
                },
                {
                    "name": "bili_jct",
                    "value": "jct1",
                    "domain": ".bilibili.com",
                    "path": "/",
                    "secure": False,
                    "http_only": False,
                    "expires": None,
                },
            ],
            "refresh_token": "refresh-xyz",
            "timestamp": 1700000000,
        },
    )
    cookie_path, metadata = await provider.persist_credential(flow, poll, tmp_path)
    assert cookie_path.exists()
    assert cookie_path.name == "cookies.txt"

    meta_path = cookie_path.with_name("meta.json")
    assert meta_path.exists()
    assert metadata["provider"] == "bilibili"
    assert metadata["owner_user_id"] == flow.owner_user_id
    assert metadata["expires_at"] is not None

    # Reload jar and confirm names.
    jar = http.cookiejar.MozillaCookieJar(str(cookie_path))
    jar.load(ignore_discard=True, ignore_expires=True)
    names = {c.name for c in jar}
    assert names == {"SESSDATA", "bili_jct"}


@pytest.mark.asyncio
async def test_persist_credential_raises_if_no_cookies(tmp_path: Path):
    provider = BilibiliAuthProvider()
    poll = AuthPollResult(
        status=AUTH_POLL_STATUS_APPROVED,
        credential_payload={"cookies": []},
    )
    with pytest.raises(RuntimeError, match="no cookies"):
        await provider.persist_credential(_make_flow(), poll, tmp_path)


@pytest.mark.asyncio
async def test_validate_credential_missing_file(tmp_path: Path):
    provider = BilibiliAuthProvider()
    handle = CredentialHandle(
        id="c-1",
        provider="bilibili",
        owner_user_id="user-1",
        scope_key="default",
        status="valid",
        storage_path=str(tmp_path / "missing" / "cookies.txt"),
    )
    result = await provider.validate_credential(handle)
    assert result.valid is False
    assert "missing" in (result.reason or "").lower()


@pytest.mark.asyncio
async def test_validate_credential_success(tmp_path: Path):
    provider = BilibiliAuthProvider()
    # Create a valid cookie file so the SESSDATA check passes.
    cookie_path = tmp_path / "cookies.txt"
    jar = http.cookiejar.MozillaCookieJar(str(cookie_path))
    jar.set_cookie(
        http.cookiejar.Cookie(
            version=0, name="SESSDATA", value="sess1", port=None,
            port_specified=False, domain=".bilibili.com", domain_specified=True,
            domain_initial_dot=True, path="/", path_specified=True, secure=True,
            expires=None, discard=False, comment=None, comment_url=None,
            rest={}, rfc2109=False,
        )
    )
    jar.save(ignore_discard=True, ignore_expires=True)

    handle = CredentialHandle(
        id="c-1",
        provider="bilibili",
        owner_user_id="user-1",
        scope_key="default",
        status="valid",
        storage_path=str(cookie_path),
    )
    payload = {
        "code": 0,
        "data": {"isLogin": True, "mid": 42, "uname": "alice", "vipStatus": 1},
    }
    with patch.object(BilibiliAuthProvider, "_request_json", return_value=payload):
        result = await provider.validate_credential(handle)
    assert result.valid is True
    assert result.metadata["mid"] == 42
    assert result.metadata["uname"] == "alice"


@pytest.mark.asyncio
async def test_validate_credential_not_logged_in(tmp_path: Path):
    provider = BilibiliAuthProvider()
    cookie_path = tmp_path / "cookies.txt"
    jar = http.cookiejar.MozillaCookieJar(str(cookie_path))
    jar.set_cookie(
        http.cookiejar.Cookie(
            version=0, name="SESSDATA", value="sess1", port=None,
            port_specified=False, domain=".bilibili.com", domain_specified=True,
            domain_initial_dot=True, path="/", path_specified=True, secure=True,
            expires=None, discard=False, comment=None, comment_url=None,
            rest={}, rfc2109=False,
        )
    )
    jar.save(ignore_discard=True, ignore_expires=True)

    handle = CredentialHandle(
        id="c-1",
        provider="bilibili",
        owner_user_id="user-1",
        scope_key="default",
        status="valid",
        storage_path=str(cookie_path),
    )
    payload = {"code": 0, "data": {"isLogin": False}}
    with patch.object(BilibiliAuthProvider, "_request_json", return_value=payload):
        result = await provider.validate_credential(handle)
    assert result.valid is False
    assert "no longer logged in" in (result.reason or "")


@pytest.mark.asyncio
async def test_validate_credential_missing_sessdata(tmp_path: Path):
    provider = BilibiliAuthProvider()
    cookie_path = tmp_path / "cookies.txt"
    # Only a useless cookie, no SESSDATA.
    jar = http.cookiejar.MozillaCookieJar(str(cookie_path))
    jar.set_cookie(
        http.cookiejar.Cookie(
            version=0, name="foo", value="bar", port=None,
            port_specified=False, domain=".bilibili.com", domain_specified=True,
            domain_initial_dot=True, path="/", path_specified=True, secure=False,
            expires=None, discard=False, comment=None, comment_url=None,
            rest={}, rfc2109=False,
        )
    )
    jar.save(ignore_discard=True, ignore_expires=True)

    handle = CredentialHandle(
        id="c-1",
        provider="bilibili",
        owner_user_id="user-1",
        scope_key="default",
        status="valid",
        storage_path=str(cookie_path),
    )
    result = await provider.validate_credential(handle)
    assert result.valid is False
    assert "SESSDATA" in (result.reason or "")


@pytest.mark.asyncio
async def test_validate_credential_deferred_on_request_error(tmp_path: Path):
    """When the validation HTTP call fails, we defer and keep the credential valid."""
    provider = BilibiliAuthProvider()
    cookie_path = tmp_path / "cookies.txt"
    jar = http.cookiejar.MozillaCookieJar(str(cookie_path))
    jar.set_cookie(
        http.cookiejar.Cookie(
            version=0, name="SESSDATA", value="sess1", port=None,
            port_specified=False, domain=".bilibili.com", domain_specified=True,
            domain_initial_dot=True, path="/", path_specified=True, secure=True,
            expires=None, discard=False, comment=None, comment_url=None,
            rest={}, rfc2109=False,
        )
    )
    jar.save(ignore_discard=True, ignore_expires=True)

    handle = CredentialHandle(
        id="c-1",
        provider="bilibili",
        owner_user_id="user-1",
        scope_key="default",
        status="valid",
        storage_path=str(cookie_path),
    )
    with patch.object(
        BilibiliAuthProvider, "_request_json", side_effect=RuntimeError("timeout")
    ):
        result = await provider.validate_credential(handle)
    assert result.valid is True  # deferred
    assert "deferred" in (result.reason or "").lower()


@pytest.mark.asyncio
async def test_invalidate_credential_removes_files(tmp_path: Path):
    provider = BilibiliAuthProvider()
    cookie_path = tmp_path / "cookies.txt"
    cookie_path.write_text("# dummy\n")
    meta_path = tmp_path / "meta.json"
    meta_path.write_text("{}")

    handle = CredentialHandle(
        id="c-1",
        provider="bilibili",
        owner_user_id="user-1",
        scope_key="default",
        status="valid",
        storage_path=str(cookie_path),
    )
    await provider.invalidate_credential(handle)
    assert not cookie_path.exists()
    assert not meta_path.exists()


@pytest.mark.asyncio
async def test_invalidate_credential_missing_files_is_noop(tmp_path: Path):
    provider = BilibiliAuthProvider()
    handle = CredentialHandle(
        id="c-1",
        provider="bilibili",
        owner_user_id="user-1",
        scope_key="default",
        status="valid",
        storage_path=str(tmp_path / "nothing.txt"),
    )
    # Should not raise.
    await provider.invalidate_credential(handle)
