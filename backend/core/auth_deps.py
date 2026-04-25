"""
FastAPI authentication dependencies.

Usage in endpoints:
    @router.get("/protected")
    def endpoint(user: User = Depends(require_auth)):
        ...

    @router.get("/admin-only")
    def admin_endpoint(user: User = Depends(require_admin)):
        ...

Token extraction order (first match wins):
  1. Authorization: Bearer <token>
  2. X-API-Key: <token>
  3. ?api_key=<token>  (query param — for simple integrations)
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

from fastapi import Depends, HTTPException, Query, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from backend.core.database import get_db
from backend.core.jwt import TokenError, verify_token, decode_token_unsafe
from backend.models.user import User

logger = logging.getLogger("news_intel.auth")

_bearer = HTTPBearer(auto_error=False)

CREDENTIALS_EXCEPTION = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Invalid or missing authentication credentials",
    headers={"WWW-Authenticate": "Bearer"},
)

FORBIDDEN_EXCEPTION = HTTPException(
    status_code=status.HTTP_403_FORBIDDEN,
    detail="Insufficient permissions for this resource",
)


# ── Token extraction ──────────────────────────────────────────────────────────

def _extract_token(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials],
    api_key_param: Optional[str],
) -> Optional[str]:
    """Extract raw token string from request — returns None if not found."""
    # 1. Authorization: Bearer header (preferred)
    if credentials and credentials.scheme.lower() == "bearer":
        return credentials.credentials

    # 2. X-API-Key header (for server-to-server)
    header_key = request.headers.get("X-API-Key")
    if header_key:
        return header_key

    # 3. ?api_key= query param (for simple integrations)
    if api_key_param:
        return api_key_param

    return None


# ── Core auth dependency ──────────────────────────────────────────────────────

def get_current_user(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(_bearer),
    api_key_param: Optional[str] = Query(default=None, alias="api_key", include_in_schema=False),
    db: Session = Depends(get_db),
) -> User:
    """
    Dependency: extract and validate token, return authenticated User.

    Supports:
      - JWT access tokens (Authorization: Bearer)
      - API key tokens (X-API-Key header or ?api_key= param)

    Raises:
      401 if token is missing, invalid, or expired
      401 if user does not exist or is inactive
    """
    from config.settings import settings

    raw_token = _extract_token(request, credentials, api_key_param)

    if not raw_token:
        # Log the attempt with IP for monitoring
        client_ip = request.client.host if request.client else "unknown"
        logger.warning(
            "AUTH_MISSING ip=%s method=%s path=%s",
            client_ip, request.method, request.url.path,
        )
        raise CREDENTIALS_EXCEPTION

    client_ip = request.client.host if request.client else "unknown"

    # ── API key path: look up directly in DB ─────────────────────────────
    if raw_token.startswith("nip_"):
        user = db.query(User).filter(
            User.api_key == raw_token,
            User.is_active == True,
        ).first()
        if not user:
            logger.warning(
                "AUTH_INVALID_APIKEY ip=%s key_prefix=%s",
                client_ip, raw_token[:8],
            )
            raise CREDENTIALS_EXCEPTION
        _update_last_login(db, user)
        return user

    # ── JWT path: verify signature and claims ─────────────────────────────
    try:
        payload = verify_token(raw_token, settings.SECRET_KEY, expected_type="access")
    except TokenError as e:
        # Log with partial token for debugging — never log full token
        unsafe = decode_token_unsafe(raw_token)
        logger.warning(
            "AUTH_TOKEN_INVALID ip=%s sub=%s reason=%s",
            client_ip, unsafe.get("sub", "?"), str(e),
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(e),
            headers={"WWW-Authenticate": "Bearer"},
        )

    user = db.query(User).filter(
        User.id == int(payload["sub"]),
        User.is_active == True,
    ).first()

    if not user:
        logger.warning(
            "AUTH_USER_NOT_FOUND ip=%s sub=%s",
            client_ip, payload.get("sub"),
        )
        raise CREDENTIALS_EXCEPTION

    return user


def get_current_user_optional(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(_bearer),
    api_key_param: Optional[str] = Query(default=None, alias="api_key", include_in_schema=False),
    db: Session = Depends(get_db),
) -> Optional[User]:
    """
    Like get_current_user but returns None instead of raising 401.
    Use for endpoints that serve different content to auth vs anonymous users.
    """
    try:
        return get_current_user(request, credentials, api_key_param, db)
    except HTTPException:
        return None


# ── Role guards ───────────────────────────────────────────────────────────────

def require_auth(user: User = Depends(get_current_user)) -> User:
    """Require any authenticated user (viewer or admin)."""
    return user


def require_admin(
    request: Request,
    user: User = Depends(get_current_user),
) -> User:
    """Require admin role."""
    if user.role != "admin":
        client_ip = request.client.host if request.client else "unknown"
        logger.warning(
            "AUTH_FORBIDDEN ip=%s user_id=%s role=%s path=%s",
            client_ip, user.id, user.role, request.url.path,
        )
        raise FORBIDDEN_EXCEPTION
    return user


# ── Helpers ───────────────────────────────────────────────────────────────────

def _update_last_login(db: Session, user: User) -> None:
    """Update last_login timestamp without interrupting the request."""
    try:
        user.last_login = datetime.utcnow()
        db.commit()
    except Exception:
        db.rollback()  # non-critical — don't fail the request
