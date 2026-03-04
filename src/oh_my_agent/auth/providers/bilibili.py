from __future__ import annotations

import asyncio
import http.cookiejar
import json
import logging
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from oh_my_agent.auth.types import (
    AUTH_POLL_STATUS_APPROVED,
    AUTH_POLL_STATUS_EXPIRED,
    AUTH_POLL_STATUS_FAILED,
    AUTH_POLL_STATUS_PENDING,
    AUTH_POLL_STATUS_SCANNED,
    AuthFlow,
    AuthPollResult,
    AuthStartResult,
    CredentialHandle,
    CredentialValidation,
)

logger = logging.getLogger(__name__)

_DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/123.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Referer": "https://www.bilibili.com/",
}
_QR_GENERATE_URL = "https://passport.bilibili.com/x/passport-login/web/qrcode/generate"
_QR_POLL_URL = "https://passport.bilibili.com/x/passport-login/web/qrcode/poll"
_NAV_URL = "https://api.bilibili.com/x/web-interface/nav"


class BilibiliAuthProvider:
    def provider_name(self) -> str:
        return "bilibili"

    async def start_qr_login(self, owner_user_id: str) -> AuthStartResult:
        del owner_user_id
        payload = await asyncio.to_thread(self._request_json, _QR_GENERATE_URL)
        if int(payload.get("code", -1)) != 0:
            raise RuntimeError(f"Bilibili QR generate failed: {payload.get('message') or payload}")
        data = payload.get("data") or {}
        qr_payload = str(data.get("url") or "").strip()
        flow_id = str(data.get("qrcode_key") or "").strip()
        if not qr_payload or not flow_id:
            raise RuntimeError("Bilibili QR generate returned incomplete payload.")
        return AuthStartResult(
            provider_flow_id=flow_id,
            qr_payload=qr_payload,
            expires_at=self._iso_after_seconds(180),
        )

    async def poll_qr_login(self, flow: AuthFlow) -> AuthPollResult:
        payload, headers = await asyncio.to_thread(
            self._request_json_with_headers,
            _QR_POLL_URL,
            {"qrcode_key": flow.provider_flow_id},
        )
        if int(payload.get("code", -1)) != 0:
            return AuthPollResult(
                status=AUTH_POLL_STATUS_FAILED,
                message=str(payload.get("message") or "Bilibili QR poll failed."),
            )
        data = payload.get("data") or {}
        status_code = int(data.get("code", -1))
        message = str(data.get("message") or "")
        if status_code == 86101:
            return AuthPollResult(status=AUTH_POLL_STATUS_PENDING, message=message or "QR not scanned yet.")
        if status_code == 86090:
            return AuthPollResult(status=AUTH_POLL_STATUS_SCANNED, message=message or "QR scanned, awaiting confirmation.")
        if status_code == 86038:
            return AuthPollResult(status=AUTH_POLL_STATUS_EXPIRED, message=message or "QR expired.")
        if status_code != 0:
            return AuthPollResult(
                status=AUTH_POLL_STATUS_FAILED,
                message=message or f"Unexpected Bilibili QR status {status_code}.",
            )

        credential_payload = {
            "cookies": self._extract_cookies(headers),
            "cross_domain_url": data.get("url"),
            "refresh_token": data.get("refresh_token"),
            "timestamp": data.get("timestamp"),
        }
        if not credential_payload["cookies"]:
            credential_payload["cookies"] = self._cookies_from_cross_domain_url(str(data.get("url") or ""))
        return AuthPollResult(
            status=AUTH_POLL_STATUS_APPROVED,
            message="Bilibili login confirmed.",
            credential_payload=credential_payload,
            metadata={
                "refresh_token": data.get("refresh_token"),
                "timestamp": data.get("timestamp"),
            },
        )

    async def persist_credential(
        self,
        flow: AuthFlow,
        result: AuthPollResult,
        storage_root: Path,
    ) -> tuple[Path, dict]:
        cookies = list((result.credential_payload or {}).get("cookies") or [])
        if not cookies:
            raise RuntimeError("Bilibili login succeeded but no cookies were returned.")

        provider_root = storage_root / "providers" / self.provider_name() / flow.owner_user_id
        provider_root.mkdir(parents=True, exist_ok=True)
        cookie_path = provider_root / "cookies.txt"
        meta_path = provider_root / "meta.json"

        jar = http.cookiejar.MozillaCookieJar(str(cookie_path))
        for cookie in cookies:
            jar.set_cookie(
                http.cookiejar.Cookie(
                    version=0,
                    name=str(cookie["name"]),
                    value=str(cookie["value"]),
                    port=None,
                    port_specified=False,
                    domain=str(cookie.get("domain") or ".bilibili.com"),
                    domain_specified=True,
                    domain_initial_dot=str(cookie.get("domain") or ".bilibili.com").startswith("."),
                    path=str(cookie.get("path") or "/"),
                    path_specified=True,
                    secure=bool(cookie.get("secure")),
                    expires=int(cookie["expires"]) if cookie.get("expires") not in (None, "") else None,
                    discard=False,
                    comment=None,
                    comment_url=None,
                    rest={"HttpOnly": bool(cookie.get("http_only"))},
                    rfc2109=False,
                )
            )
        jar.save(ignore_discard=True, ignore_expires=True)

        metadata = {
            "provider": self.provider_name(),
            "owner_user_id": flow.owner_user_id,
            "refresh_token": (result.credential_payload or {}).get("refresh_token"),
            "timestamp": (result.credential_payload or {}).get("timestamp"),
            "cookies": [
                {
                    "name": cookie.get("name"),
                    "domain": cookie.get("domain"),
                    "path": cookie.get("path"),
                    "expires": cookie.get("expires"),
                    "secure": cookie.get("secure"),
                    "http_only": cookie.get("http_only"),
                }
                for cookie in cookies
            ],
        }
        meta_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
        cookie_path.chmod(0o600)
        meta_path.chmod(0o600)

        expires = [int(cookie["expires"]) for cookie in cookies if cookie.get("expires") not in (None, "")]
        metadata["expires_at"] = self._iso_from_epoch(max(expires)) if expires else None
        return cookie_path, metadata

    async def validate_credential(self, handle: CredentialHandle) -> CredentialValidation:
        cookie_path = Path(handle.storage_path)
        if not cookie_path.exists():
            return CredentialValidation(valid=False, reason="Credential file missing.")

        cookies = await asyncio.to_thread(self._load_cookie_map, cookie_path)
        sessdata = cookies.get("SESSDATA")
        if not sessdata:
            return CredentialValidation(valid=False, reason="SESSDATA missing from credential.")

        try:
            payload = await asyncio.to_thread(self._request_json, _NAV_URL, cookie_header=self._cookie_header(cookies))
        except Exception as exc:
            logger.debug("Bilibili credential validation request failed", exc_info=True)
            return CredentialValidation(valid=True, reason=f"Validation deferred after request error: {exc}")
        if int(payload.get("code", -1)) != 0:
            return CredentialValidation(valid=False, reason=str(payload.get("message") or "Bilibili nav validation failed."))
        data = payload.get("data") or {}
        if not data.get("isLogin", True):
            return CredentialValidation(valid=False, reason="Bilibili session is no longer logged in.")
        return CredentialValidation(
            valid=True,
            metadata={
                "mid": data.get("mid"),
                "uname": data.get("uname"),
                "vip_status": data.get("vipStatus"),
            },
        )

    async def invalidate_credential(self, handle: CredentialHandle) -> None:
        cookie_path = Path(handle.storage_path)
        meta_path = cookie_path.with_name("meta.json")
        for path in (cookie_path, meta_path):
            try:
                if path.exists():
                    path.unlink()
            except OSError:
                logger.debug("Failed to delete Bilibili credential file %s", path, exc_info=True)

    def _request_json(
        self,
        url: str,
        query: dict[str, Any] | None = None,
        *,
        cookie_header: str | None = None,
    ) -> dict[str, Any]:
        payload, _ = self._request_json_with_headers(url, query, cookie_header=cookie_header)
        return payload

    def _request_json_with_headers(
        self,
        url: str,
        query: dict[str, Any] | None = None,
        *,
        cookie_header: str | None = None,
    ) -> tuple[dict[str, Any], Any]:
        if query:
            url = f"{url}?{urllib.parse.urlencode(query)}"
        req = urllib.request.Request(url, headers=dict(_DEFAULT_HEADERS))
        if cookie_header:
            req.add_header("Cookie", cookie_header)
        with urllib.request.urlopen(req, timeout=15) as resp:
            body = resp.read().decode("utf-8")
            return json.loads(body), resp.headers

    @staticmethod
    def _extract_cookies(headers: Any) -> list[dict[str, Any]]:
        values = list(headers.get_all("Set-Cookie", [])) if hasattr(headers, "get_all") else []
        cookies: list[dict[str, Any]] = []
        for raw in values:
            parts = [part.strip() for part in str(raw).split(";") if part.strip()]
            if not parts or "=" not in parts[0]:
                continue
            name, value = parts[0].split("=", 1)
            cookie: dict[str, Any] = {
                "name": name,
                "value": value,
                "domain": ".bilibili.com",
                "path": "/",
                "secure": False,
                "http_only": False,
                "expires": None,
            }
            for part in parts[1:]:
                if "=" in part:
                    key, val = part.split("=", 1)
                    lowered = key.lower()
                    if lowered == "domain":
                        cookie["domain"] = val
                    elif lowered == "path":
                        cookie["path"] = val
                    elif lowered == "expires":
                        try:
                            cookie["expires"] = int(time.mktime(time.strptime(val, "%a, %d %b %Y %H:%M:%S %Z")))
                        except Exception:
                            cookie["expires"] = None
                else:
                    lowered = part.lower()
                    if lowered == "secure":
                        cookie["secure"] = True
                    elif lowered == "httponly":
                        cookie["http_only"] = True
            cookies.append(cookie)
        return cookies

    @staticmethod
    def _cookies_from_cross_domain_url(url: str) -> list[dict[str, Any]]:
        if not url:
            return []
        parsed = urllib.parse.urlparse(url)
        query = urllib.parse.parse_qs(parsed.query)
        cookies: list[dict[str, Any]] = []
        for key in ("SESSDATA", "bili_jct", "DedeUserID", "DedeUserID__ckMd5", "sid"):
            values = query.get(key)
            if not values:
                continue
            cookies.append(
                {
                    "name": key,
                    "value": values[0],
                    "domain": ".bilibili.com",
                    "path": "/",
                    "secure": key == "SESSDATA",
                    "http_only": key == "SESSDATA",
                    "expires": None,
                }
            )
        return cookies

    @staticmethod
    def _load_cookie_map(cookie_path: Path) -> dict[str, str]:
        jar = http.cookiejar.MozillaCookieJar(str(cookie_path))
        jar.load(ignore_discard=True, ignore_expires=True)
        return {cookie.name: cookie.value for cookie in jar}

    @staticmethod
    def _cookie_header(cookies: dict[str, str]) -> str:
        return "; ".join(f"{name}={value}" for name, value in cookies.items())

    @staticmethod
    def _iso_after_seconds(seconds: int) -> str:
        return time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(time.time() + seconds))

    @staticmethod
    def _iso_from_epoch(epoch_seconds: int) -> str:
        return time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(epoch_seconds))
