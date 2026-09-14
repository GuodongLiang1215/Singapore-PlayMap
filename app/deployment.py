"""Deployment helpers for a shared team instance.

Local development remains unchanged when PLAYMAP_ACCESS_CODE is unset.
Production credentials are read from environment variables only and are never
returned by status endpoints.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
import time
from dataclasses import dataclass
from urllib.parse import urlsplit

from fastapi import Request

COOKIE_NAME = "playmap_team_access"
COOKIE_TTL_SECONDS = 7 * 24 * 60 * 60


def _csv_env(name: str) -> list[str]:
    return [x.strip() for x in os.environ.get(name, "").split(",") if x.strip()]


def deployment_mode() -> str:
    if os.environ.get("RENDER"):
        return "render"
    return os.environ.get("PLAYMAP_DEPLOYMENT", "local").strip().lower() or "local"


def allowed_hosts() -> list[str]:
    values = ["localhost", "127.0.0.1", "[::1]", "testserver"]
    render_host = os.environ.get("RENDER_EXTERNAL_HOSTNAME", "").strip()
    if render_host:
        values.append(render_host)
    values.extend(_csv_env("PLAYMAP_ALLOWED_HOSTS"))
    # Keep order deterministic and never silently allow '*'.
    out: list[str] = []
    for value in values:
        if value and value != "*" and value not in out:
            out.append(value)
    return out


def same_host_origin(request: Request) -> bool:
    origin = request.headers.get("origin")
    if not origin:
        return True
    try:
        parts = urlsplit(origin)
    except ValueError:
        return False
    if parts.scheme not in ("http", "https") or not parts.netloc or parts.username or parts.password:
        return False
    origin_host = parts.hostname.lower() if parts.hostname else ""
    request_host = request.headers.get("host", "").split(":", 1)[0].strip().lower()
    return bool(origin_host and request_host and origin_host == request_host)


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


@dataclass(frozen=True)
class AccessGate:
    code: str | None
    secret: str | None

    @classmethod
    def from_env(cls) -> "AccessGate":
        code = os.environ.get("PLAYMAP_ACCESS_CODE", "").strip() or None
        secret = os.environ.get("PLAYMAP_SESSION_SECRET", "").strip() or None
        # Local development is intentionally open when no access code is set.
        if code and not secret:
            raise RuntimeError("PLAYMAP_SESSION_SECRET is required when PLAYMAP_ACCESS_CODE is configured")
        if code and len(code) < 8:
            raise RuntimeError("PLAYMAP_ACCESS_CODE must contain at least 8 characters")
        if secret and len(secret) < 24:
            raise RuntimeError("PLAYMAP_SESSION_SECRET must contain at least 24 characters")
        return cls(code=code, secret=secret)

    @property
    def enabled(self) -> bool:
        return self.code is not None

    def verify_code(self, candidate: str) -> bool:
        if not self.enabled or not isinstance(candidate, str):
            return False
        if len(candidate) > 256:
            return False
        return secrets.compare_digest(candidate, self.code)

    def issue_cookie(self, now: int | None = None) -> str:
        if not self.enabled:
            raise RuntimeError("Access gate disabled")
        now = int(time.time() if now is None else now)
        payload = f"v1.{now + COOKIE_TTL_SECONDS}"
        signature = hmac.new(self.secret.encode("utf-8"), payload.encode("ascii"), hashlib.sha256).digest()
        return payload + "." + _b64(signature)

    def verify_cookie(self, value: str | None, now: int | None = None) -> bool:
        if not self.enabled:
            return True
        if not value or len(value) > 512:
            return False
        parts = value.split(".")
        if len(parts) != 3 or parts[0] != "v1":
            return False
        try:
            expiry = int(parts[1])
            sig = _unb64(parts[2])
        except (ValueError, TypeError, base64.binascii.Error):
            return False
        now = int(time.time() if now is None else now)
        if expiry <= now or expiry > now + COOKIE_TTL_SECONDS + 60:
            return False
        payload = f"v1.{expiry}"
        expected = hmac.new(self.secret.encode("utf-8"), payload.encode("ascii"), hashlib.sha256).digest()
        return hmac.compare_digest(sig, expected)

    def request_authorized(self, request: Request) -> bool:
        return self.verify_cookie(request.cookies.get(COOKIE_NAME))


def deployment_status(gate: AccessGate) -> dict:
    return {
        "deployment_mode": deployment_mode(),
        "team_access_required": gate.enabled,
        "allowed_host_count": len(allowed_hosts()),
        "credentials_exposed": False,
    }
