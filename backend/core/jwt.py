"""
JWT utility — HS256 implementation using Python stdlib only.
No jose, pyjwt, or other external dependencies required.

Token types:
  access  — short-lived (default 30 min), authorises API calls
  refresh — long-lived (default 7 days), exchanges for new access tokens
  api_key — no expiry, scoped to a specific user/service

Security properties:
  - HMAC-SHA256 signature (constant-time comparison)
  - exp, iat, jti claims validated on every verification
  - Separate secret recommended for refresh vs access tokens
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from typing import Optional


# ── Constants ─────────────────────────────────────────────────────────────────

ACCESS_TOKEN_EXPIRE_MINUTES  = 30
REFRESH_TOKEN_EXPIRE_DAYS    = 7
ALGORITHM                    = "HS256"


# ── Base64url helpers ─────────────────────────────────────────────────────────

def _b64enc(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _b64dec(s: str) -> bytes:
    pad = 4 - len(s) % 4
    if pad != 4:
        s += "=" * pad
    return base64.urlsafe_b64decode(s)


# ── Token creation ────────────────────────────────────────────────────────────

def create_access_token(
    subject: str,
    secret: str,
    role: str = "viewer",
    extra_claims: Optional[dict] = None,
    expires_minutes: int = ACCESS_TOKEN_EXPIRE_MINUTES,
) -> str:
    """
    Create a signed JWT access token.

    Args:
        subject:    user id or email — stored in 'sub' claim
        secret:     signing key (from settings.SECRET_KEY)
        role:       viewer | admin — controls endpoint access
        extra_claims: additional payload fields
        expires_minutes: token lifetime

    Returns:
        Signed JWT string (header.payload.signature)
    """
    now = int(time.time())
    payload = {
        "sub":  subject,
        "role": role,
        "type": "access",
        "jti":  secrets.token_hex(16),   # unique token id (for future revocation)
        "iat":  now,
        "exp":  now + expires_minutes * 60,
    }
    if extra_claims:
        payload.update(extra_claims)
    return _sign(payload, secret)


def create_refresh_token(subject: str, secret: str) -> str:
    """Long-lived refresh token — does NOT carry role claims."""
    now = int(time.time())
    payload = {
        "sub":  subject,
        "type": "refresh",
        "jti":  secrets.token_hex(16),
        "iat":  now,
        "exp":  now + REFRESH_TOKEN_EXPIRE_DAYS * 86400,
    }
    return _sign(payload, secret)


def create_api_key_token(subject: str, secret: str, role: str = "viewer") -> str:
    """
    Non-expiring API key token.
    Intended for server-to-server / long-term integrations.
    Has 'api_key' type so it can be distinguished from browser JWTs.
    """
    now = int(time.time())
    payload = {
        "sub":  subject,
        "role": role,
        "type": "api_key",
        "jti":  secrets.token_hex(16),
        "iat":  now,
        # No 'exp' claim — does not expire (revocation via jti blocklist)
    }
    return _sign(payload, secret)


# ── Token verification ────────────────────────────────────────────────────────

class TokenError(Exception):
    """Raised when a token is invalid, expired, or tampered."""
    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


def verify_token(token: str, secret: str, expected_type: str = "access") -> dict:
    """
    Verify and decode a JWT token.

    Args:
        token:         raw JWT string
        secret:        signing key used during creation
        expected_type: 'access' | 'refresh' | 'api_key'

    Returns:
        Decoded payload dict

    Raises:
        TokenError: on any validation failure (tampered, expired, wrong type, malformed)
    """
    if not token or not isinstance(token, str):
        raise TokenError("Token must be a non-empty string")

    parts = token.strip().split(".")
    if len(parts) != 3:
        raise TokenError("Malformed token: expected header.payload.signature")

    # ── Signature verification (constant-time) ─────────────────────────────
    signing_input = f"{parts[0]}.{parts[1]}".encode()
    try:
        expected_sig = hmac.new(secret.encode(), signing_input, hashlib.sha256).digest()
        actual_sig   = _b64dec(parts[2])
    except Exception:
        raise TokenError("Malformed token: cannot decode signature")

    if not hmac.compare_digest(expected_sig, actual_sig):
        raise TokenError("Invalid token signature")

    # ── Payload decode ─────────────────────────────────────────────────────
    try:
        payload = json.loads(_b64dec(parts[1]))
    except Exception:
        raise TokenError("Malformed token: cannot decode payload")

    # ── Claims validation ──────────────────────────────────────────────────
    now = int(time.time())

    if "exp" in payload and payload["exp"] < now:
        raise TokenError("Token has expired")

    if payload.get("iat", now) > now + 60:   # allow 60s clock skew
        raise TokenError("Token issued in the future")

    if expected_type != "api_key" and payload.get("type") != expected_type:
        raise TokenError(
            f"Wrong token type: expected '{expected_type}', got '{payload.get('type')}'"
        )

    if not payload.get("sub"):
        raise TokenError("Token missing 'sub' claim")

    return payload


# ── Helpers ───────────────────────────────────────────────────────────────────

def _sign(payload: dict, secret: str) -> str:
    header = {"alg": ALGORITHM, "typ": "JWT"}
    h = _b64enc(json.dumps(header, separators=(",", ":")).encode())
    p = _b64enc(json.dumps(payload, separators=(",", ":")).encode())
    signing_input = f"{h}.{p}".encode()
    sig = hmac.new(secret.encode(), signing_input, hashlib.sha256).digest()
    return f"{h}.{p}.{_b64enc(sig)}"


def decode_token_unsafe(token: str) -> dict:
    """
    Decode payload WITHOUT verifying signature.
    ONLY use this for non-security purposes (e.g. logging the subject on failure).
    """
    try:
        parts = token.split(".")
        if len(parts) != 3:
            return {}
        return json.loads(_b64dec(parts[1]))
    except Exception:
        return {}
