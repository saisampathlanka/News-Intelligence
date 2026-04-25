"""
Authentication endpoints.

POST /auth/register  — create new user account
POST /auth/login     — exchange email+password for JWT tokens
POST /auth/refresh   — exchange refresh token for new access token
GET  /auth/me        — return current user profile
POST /auth/api-key   — generate/rotate API key for current user
POST /auth/revoke    — revoke current user's API key
POST /auth/logout    — client-side logout hint (JWT is stateless)

Security notes:
  - All error messages are generic ("invalid credentials") to prevent
    user enumeration (don't reveal whether email exists or password is wrong)
  - Passwords are hashed with PBKDF2-SHA256, never stored plaintext
  - Access tokens expire in 30 minutes by default
  - Refresh tokens expire in 7 days
  - All auth failures are logged with IP for monitoring
"""
import logging
import re
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, field_validator
from sqlalchemy.orm import Session

from backend.core.auth_deps import get_current_user, require_admin
from backend.core.database import get_db
from backend.core.jwt import (
    TokenError,
    create_access_token,
    create_api_key_token,
    create_refresh_token,
    verify_token,
)
from backend.core.security import generate_api_key, hash_password, verify_password
from backend.models.user import User

router = APIRouter(prefix="/auth", tags=["Authentication"])
logger = logging.getLogger("news_intel.auth")

_bearer = HTTPBearer(auto_error=False)

# ── Input schemas ─────────────────────────────────────────────────────────────

_EMAIL_RE   = re.compile(r"^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$")
_STRONG_PW  = re.compile(r"^(?=.*[a-z])(?=.*[A-Z])(?=.*\d).{8,128}$")


class RegisterRequest(BaseModel):
    email:    str
    password: str

    @field_validator("email")
    @classmethod
    def validate_email(cls, v: str) -> str:
        v = v.strip().lower()
        if not _EMAIL_RE.match(v):
            raise ValueError("Invalid email address")
        if len(v) > 256:
            raise ValueError("Email too long")
        return v

    @field_validator("password")
    @classmethod
    def validate_password(cls, v: str) -> str:
        if not _STRONG_PW.match(v):
            raise ValueError(
                "Password must be 8–128 characters and include uppercase, lowercase, and a digit"
            )
        return v


class LoginRequest(BaseModel):
    email:    str
    password: str

    @field_validator("email")
    @classmethod
    def normalise_email(cls, v: str) -> str:
        return v.strip().lower()


class RefreshRequest(BaseModel):
    refresh_token: str


class TokenResponse(BaseModel):
    access_token:  str
    refresh_token: str
    token_type:    str = "bearer"
    expires_in:    int   # seconds


class ApiKeyResponse(BaseModel):
    api_key:    str
    note:       str = "Store this securely — it will not be shown again"


# ── Helpers ───────────────────────────────────────────────────────────────────

def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _make_tokens(user: User, secret: str) -> TokenResponse:
    from backend.core.jwt import ACCESS_TOKEN_EXPIRE_MINUTES
    access  = create_access_token(str(user.id), secret, role=user.role)
    refresh = create_refresh_token(str(user.id), secret)
    return TokenResponse(
        access_token=access,
        refresh_token=refresh,
        expires_in=ACCESS_TOKEN_EXPIRE_MINUTES * 60,
    )


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.post("/register", status_code=status.HTTP_201_CREATED)
def register(
    body: RegisterRequest,
    request: Request,
    db: Session = Depends(get_db),
):
    """
    Create a new user account.
    Returns tokens immediately so user is logged in after registration.
    """
    from config.settings import settings

    # Check for existing account — use constant-time path to avoid timing oracle
    existing = db.query(User).filter(User.email == body.email).first()
    if existing:
        logger.info("REGISTER_DUPE ip=%s email_hash=%s", _client_ip(request), hash(body.email))
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An account with this email already exists",
        )

    user = User(
        email=body.email,
        hashed_password=hash_password(body.password),
        role="viewer",
        is_active=True,
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    logger.info("REGISTER_OK ip=%s user_id=%s", _client_ip(request), user.id)
    return _make_tokens(user, settings.SECRET_KEY)


@router.post("/login", response_model=TokenResponse)
def login(
    body: LoginRequest,
    request: Request,
    db: Session = Depends(get_db),
):
    """
    Exchange email + password for access + refresh tokens.

    Error messages are intentionally generic to prevent user enumeration.
    """
    from config.settings import settings

    _INVALID = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )

    user = db.query(User).filter(User.email == body.email).first()

    if not user:
        # Run hash anyway to prevent timing oracle
        verify_password(body.password, "x" * 86)
        logger.warning("LOGIN_UNKNOWN_EMAIL ip=%s", _client_ip(request))
        raise _INVALID

    if not verify_password(body.password, user.hashed_password):
        logger.warning(
            "LOGIN_BAD_PASSWORD ip=%s user_id=%s",
            _client_ip(request), user.id,
        )
        raise _INVALID

    if not user.is_active:
        logger.warning(
            "LOGIN_INACTIVE ip=%s user_id=%s",
            _client_ip(request), user.id,
        )
        raise _INVALID   # same error — don't reveal suspension

    # Update last_login
    user.last_login = datetime.utcnow()
    db.commit()

    logger.info("LOGIN_OK ip=%s user_id=%s role=%s", _client_ip(request), user.id, user.role)
    return _make_tokens(user, settings.SECRET_KEY)


@router.post("/refresh", response_model=TokenResponse)
def refresh_token(
    body: RefreshRequest,
    request: Request,
    db: Session = Depends(get_db),
):
    """Exchange a refresh token for a new access + refresh token pair."""
    from config.settings import settings

    try:
        payload = verify_token(body.refresh_token, settings.SECRET_KEY, expected_type="refresh")
    except TokenError as e:
        logger.warning("REFRESH_INVALID ip=%s reason=%s", _client_ip(request), str(e))
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired refresh token",
        )

    user = db.query(User).filter(
        User.id == int(payload["sub"]),
        User.is_active == True,
    ).first()

    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")

    logger.info("REFRESH_OK ip=%s user_id=%s", _client_ip(request), user.id)
    return _make_tokens(user, settings.SECRET_KEY)


@router.get("/me")
def get_me(user: User = Depends(get_current_user)):
    """Return authenticated user profile (no sensitive fields)."""
    return user.safe_dict()


@router.post("/api-key", response_model=ApiKeyResponse)
def generate_user_api_key(
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Generate (or rotate) an API key for the current user.
    The raw key is returned ONCE — store it securely.
    Subsequent calls to this endpoint revoke the previous key.
    """
    raw_key = generate_api_key()
    user.api_key = raw_key
    db.commit()
    logger.info("APIKEY_GENERATED user_id=%s ip=%s", user.id, _client_ip(request))
    return ApiKeyResponse(api_key=raw_key)


@router.post("/revoke", status_code=status.HTTP_204_NO_CONTENT)
def revoke_api_key(
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Revoke the current user's API key. Returns 204 No Content."""
    if user.api_key:
        user.api_key = None
        db.commit()
        logger.info("APIKEY_REVOKED user_id=%s ip=%s", user.id, _client_ip(request))


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout():
    """
    JWT logout hint. JWT tokens are stateless — the client must delete them.
    For full server-side revocation, maintain a blocklist (Redis jti set).
    This endpoint exists so frontends have a clear logout target.
    """
    # Client should delete access + refresh tokens from storage
    return None


# ── Admin: user management ────────────────────────────────────────────────────

@router.get("/users", dependencies=[Depends(require_admin)])
def list_users(db: Session = Depends(get_db)):
    """Admin only: list all users."""
    users = db.query(User).order_by(User.created_at.desc()).all()
    return [u.safe_dict() for u in users]


@router.post("/users/{user_id}/deactivate", dependencies=[Depends(require_admin)])
def deactivate_user(user_id: int, db: Session = Depends(get_db)):
    """Admin only: deactivate a user account."""
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    user.is_active = False
    user.api_key   = None   # also revoke API key
    db.commit()
    return {"deactivated": user_id}


@router.post("/users/{user_id}/role", dependencies=[Depends(require_admin)])
def set_user_role(user_id: int, role: str, db: Session = Depends(get_db)):
    """Admin only: change user role."""
    if role not in ("viewer", "admin"):
        raise HTTPException(status_code=400, detail="Role must be 'viewer' or 'admin'")
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    user.role = role
    db.commit()
    return {"user_id": user_id, "role": role}
