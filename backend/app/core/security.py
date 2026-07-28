"""Password hashing and JWT issuing/verification."""

from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

from jose import JWTError, jwt

from app.core.config import settings

# passlib+bcrypt is the production hasher; if the wheel is unavailable we fall
# back to PBKDF2-HMAC-SHA256, which is still a sound password KDF.
try:  # pragma: no cover - depends on the install
    from passlib.context import CryptContext

    _pwd_context: CryptContext | None = CryptContext(
        schemes=["pbkdf2_sha256"], deprecated="auto"
    )
except Exception:  # pragma: no cover
    _pwd_context = None

_PBKDF2_ROUNDS = 260_000


def hash_password(password: str) -> str:
    if _pwd_context is not None:
        return _pwd_context.hash(password)
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode(), salt.encode(), _PBKDF2_ROUNDS
    ).hex()
    return f"pbkdf2${_PBKDF2_ROUNDS}${salt}${digest}"


def verify_password(password: str, hashed: str) -> bool:
    if hashed.startswith("pbkdf2$"):
        _, rounds, salt, digest = hashed.split("$", 3)
        candidate = hashlib.pbkdf2_hmac(
            "sha256", password.encode(), salt.encode(), int(rounds)
        ).hex()
        return hmac.compare_digest(candidate, digest)
    if _pwd_context is not None:
        try:
            return _pwd_context.verify(password, hashed)
        except Exception:
            return False
    return False


def create_access_token(
    *,
    subject: str,
    tenant_id: str,
    role: str,
    is_superuser: bool = False,
    extra: dict[str, Any] | None = None,
    ttl: timedelta | None = None,
) -> str:
    now = datetime.now(timezone.utc)
    ttl = ttl or timedelta(minutes=settings.access_token_ttl_minutes)
    payload: dict[str, Any] = {
        "sub": subject,
        "tid": tenant_id,
        "role": role,
        "su": is_superuser,
        "iat": int(now.timestamp()),
        "exp": int((now + ttl).timestamp()),
        "jti": secrets.token_urlsafe(12),
    }
    if extra:
        payload.update(extra)
    return jwt.encode(payload, settings.secret_key, algorithm=settings.jwt_algorithm)


def decode_access_token(token: str) -> dict[str, Any] | None:
    try:
        return jwt.decode(
            token, settings.secret_key, algorithms=[settings.jwt_algorithm]
        )
    except JWTError:
        return None


def new_api_key() -> tuple[str, str]:
    """Return ``(plaintext, sha256)``. Only the digest is ever persisted."""
    raw = "em_" + secrets.token_urlsafe(32)
    return raw, hashlib.sha256(raw.encode()).hexdigest()


def api_key_digest(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()
