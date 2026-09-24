"""Short-lived signed grants consumed by the isolated legacy bridge."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from dataclasses import dataclass
from datetime import datetime, timedelta

from app.shared.clock import as_utc
from app.shared.http_auth import Principal


AUDIENCE = "erp-legacy-bridge"
GRANT_TTL = timedelta(minutes=2)
ALLOWED_SCOPES = frozenset({"projects", "calculation"})


@dataclass(frozen=True)
class LegacyGrant:
    token: str
    expires_at: datetime


def _base64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def issue_legacy_grant(
    secret: str,
    principal: Principal,
    scope: str,
    mode: str,
    now: datetime,
) -> LegacyGrant:
    secret_bytes = secret.encode("utf-8")
    if len(secret_bytes) < 32:
        raise ValueError("ERP_LEGACY_BRIDGE_SECRET must contain at least 32 bytes")
    if scope not in ALLOWED_SCOPES:
        raise ValueError("Unsupported legacy scope")
    if mode not in {"view", "edit"}:
        raise ValueError("Unsupported legacy access mode")
    issued_at = as_utc(now)
    expires_at = issued_at + GRANT_TTL
    payload = {
        "aud": AUDIENCE,
        "exp": int(expires_at.timestamp()),
        "iat": int(issued_at.timestamp()),
        "mode": mode,
        "scope": scope,
        "sub": str(principal.actor.id),
    }
    encoded = _base64url(
        json.dumps(payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True).encode()
    )
    signature = _base64url(
        hmac.new(secret_bytes, encoded.encode("ascii"), hashlib.sha256).digest()
    )
    return LegacyGrant(token=f"{encoded}.{signature}", expires_at=expires_at)
