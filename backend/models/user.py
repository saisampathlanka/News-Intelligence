"""
User model for authentication.

Schema:
  - id, email (unique), hashed_password
  - role: viewer | admin
  - api_key: optional long-lived token for programmatic access
  - is_active: soft-delete / suspension flag
  - created_at, last_login

Passwords are NEVER stored in plaintext — always PBKDF2-SHA256 hashed.
"""
from sqlalchemy import Column, Integer, String, Boolean, DateTime, Index
from sqlalchemy.sql import func
from backend.core.database import Base


class User(Base):
    __tablename__ = "users"

    id              = Column(Integer, primary_key=True, autoincrement=True)
    email           = Column(String(256), unique=True, nullable=False, index=True)
    hashed_password = Column(String(256), nullable=False)
    role            = Column(String(32), nullable=False, default="viewer")
    # viewer  → read-only: /articles, /insights, /trending, /stocks
    # admin   → full access including /admin/* pipeline triggers

    api_key         = Column(String(64), unique=True, nullable=True, index=True)
    # Optional: set by /auth/api-key endpoint, used as Bearer token

    is_active       = Column(Boolean, default=True, nullable=False)
    created_at      = Column(DateTime, server_default=func.now())
    last_login      = Column(DateTime, nullable=True)

    __table_args__ = (
        Index("ix_users_email_active", "email", "is_active"),
    )

    def __repr__(self) -> str:
        return f"<User id={self.id} email={self.email} role={self.role}>"

    def safe_dict(self) -> dict:
        """Return user info safe for API responses — no password, no raw api_key."""
        return {
            "id":         self.id,
            "email":      self.email,
            "role":       self.role,
            "is_active":  self.is_active,
            "created_at": str(self.created_at) if self.created_at else None,
            "last_login": str(self.last_login) if self.last_login else None,
            "has_api_key": self.api_key is not None,
        }
