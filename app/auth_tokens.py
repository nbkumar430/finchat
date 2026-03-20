"""Passcode hashing and signed auth cookies (stdlib only)."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from typing import Any, Optional

# PBKDF2 iterations (reasonable default for passcodes on server)
_PBKDF2_ITERS = 200_000


def hash_passcode(passcode: str) -> str:
    """Return ``salt$hexdigest`` suitable for storage."""
    salt = secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac("sha256", passcode.encode("utf-8"), salt.encode("ascii"), _PBKDF2_ITERS)
    return f"{salt}${dk.hex()}"


def verify_passcode(passcode: str, stored: str) -> bool:
    try:
        salt, hexhash = stored.split("$", 1)
    except ValueError:
        return False
    dk = hashlib.pbkdf2_hmac("sha256", passcode.encode("utf-8"), salt.encode("ascii"), _PBKDF2_ITERS)
    return hmac.compare_digest(dk.hex(), hexhash)


def create_auth_token(*, user_id: str, username: str, is_admin: bool, secret: str, max_age_seconds: int) -> str:
    """URL-safe signed token: ``payload_b64.signature_hex``."""
    payload: dict[str, Any] = {
        "uid": user_id,
        "sub": username,
        "adm": is_admin,
        "exp": int(time.time()) + max_age_seconds,
    }
    body = base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()).decode(
        "ascii"
    )
    body = body.rstrip("=")
    sig = hmac.new(secret.encode("utf-8"), body.encode("ascii"), hashlib.sha256).hexdigest()
    return f"{body}.{sig}"


def verify_auth_token(token: str, secret: str) -> Optional[dict[str, Any]]:  # noqa: UP007
    try:
        body, sig = token.rsplit(".", 1)
    except ValueError:
        return None
    expect = hmac.new(secret.encode("utf-8"), body.encode("ascii"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expect, sig):
        return None
    pad = "=" * (-len(body) % 4)
    try:
        payload = json.loads(base64.urlsafe_b64decode(body + pad))
    except (json.JSONDecodeError, ValueError):
        return None
    if int(payload.get("exp", 0)) < time.time():
        return None
    return payload
