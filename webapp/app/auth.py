"""Single-user admin auth using signed session cookies.

Why this instead of JWT or OAuth: there's only one user, no API clients,
and the portal is a single browser app. A signed session cookie is the
simplest correct option.

The session cookie carries just `{"sub": "admin", "exp": <unix-ts>}`,
signed with SECRET_KEY. The dependency `require_admin` raises 401 if
the cookie is missing/invalid/expired.
"""
from __future__ import annotations
from typing import Optional

import bcrypt
from fastapi import Cookie, HTTPException, status
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired

from app.config import settings


SESSION_COOKIE_NAME = "solvit_session"
_serializer = URLSafeTimedSerializer(settings.secret_key, salt="solvit-portal")


def hash_password(plain: str) -> str:
    """Hash a plaintext password. Use this to generate ADMIN_PASSWORD_HASH."""
    # bcrypt has a 72-byte limit; truncate defensively
    return bcrypt.hashpw(plain.encode("utf-8")[:72], bcrypt.gensalt(rounds=12)).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(plain.encode("utf-8")[:72], hashed.encode("utf-8"))
    except Exception:
        return False


def authenticate(username: str, password: str) -> bool:
    if username != settings.admin_username:
        return False
    return verify_password(password, settings.admin_password_hash)


def create_session_token() -> str:
    """Create a signed token for the admin session."""
    return _serializer.dumps({"sub": "admin"})


def session_max_age_seconds() -> int:
    return settings.session_hours * 3600


def require_admin(
    solvit_session: Optional[str] = Cookie(default=None, alias=SESSION_COOKIE_NAME),
) -> str:
    """FastAPI dependency: 401 unless the cookie is a valid, unexpired session."""
    if not solvit_session:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
        )
    try:
        data = _serializer.loads(
            solvit_session,
            max_age=session_max_age_seconds(),
        )
    except SignatureExpired:
        raise HTTPException(status_code=401, detail="Session expired")
    except BadSignature:
        raise HTTPException(status_code=401, detail="Invalid session")

    if not isinstance(data, dict) or data.get("sub") != "admin":
        raise HTTPException(status_code=401, detail="Invalid session payload")
    return "admin"

