"""Admin-side security primitives.

Mirrors :mod:`app.core.security` but issues and verifies JWTs with an explicit
admin audience (``simulafly-admin``) so that consumer/merchant tokens can never
be replayed against admin endpoints and vice versa.

Password hashing reuses bcrypt-based :func:`app.core.security.hash_password` /
:func:`app.core.security.verify_password`. Token decode/verify errors raise the
existing :class:`app.core.security.TokenError`.
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Literal

from jose import JWTError, jwt

from app.core.config import get_settings

# Reuse the consumer-side bcrypt helpers and the shared TokenError so callers
# get a single, consistent error type across both auth systems.
from app.core.security import (  # noqa: F401  (re-exported for convenience)
    TokenError,
    hash_password,
    verify_password,
)

settings = get_settings()

# Explicit admin audience. Embedded as the JWT ``aud`` claim and required when
# decoding; tokens whose ``aud`` differs are rejected.
ADMIN_TOKEN_AUDIENCE = "simulafly-admin"

TokenType = Literal["access", "refresh"]


@dataclass
class AdminClaims:
    """Verified claims extracted from an admin JWT."""

    account_id: str
    token_type: TokenType
    roles: list[str] = field(default_factory=list)
    session_id: str | None = None
    expires_at: datetime | None = None


# ---------------------------------------------------------------------------
# Token issuance
# ---------------------------------------------------------------------------


def _create_token(
    account_id: str,
    token_type: TokenType,
    expires_delta: timedelta,
    *,
    roles: list[str] | None = None,
    session_id: str | None = None,
) -> str:
    now = datetime.now(timezone.utc)
    payload: dict = {
        "sub": account_id,
        "type": token_type,
        "aud": ADMIN_TOKEN_AUDIENCE,
        "iat": int(now.timestamp()),
        "exp": int((now + expires_delta).timestamp()),
    }
    if roles is not None:
        payload["roles"] = list(roles)
    if session_id is not None:
        payload["sid"] = session_id
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


def create_admin_access_token(account_id: str, *, roles: list[str]) -> str:
    """Issue a short-lived admin access token carrying the account's roles."""
    return _create_token(
        account_id,
        "access",
        timedelta(minutes=settings.ADMIN_ACCESS_TOKEN_EXPIRE_MINUTES),
        roles=list(roles),
    )


def create_admin_refresh_token(account_id: str, *, session_id: str) -> str:
    """Issue a long-lived admin refresh token bound to a session id."""
    return _create_token(
        account_id,
        "refresh",
        timedelta(days=settings.ADMIN_REFRESH_TOKEN_EXPIRE_DAYS),
        session_id=session_id,
    )


# ---------------------------------------------------------------------------
# Token verification
# ---------------------------------------------------------------------------


def decode_admin_token(token: str, expected_type: TokenType) -> AdminClaims:
    """Decode and validate an admin JWT.

    Rejects tokens that are malformed, expired, carry the wrong ``aud`` (i.e.
    not :data:`ADMIN_TOKEN_AUDIENCE`), or whose ``type`` does not match
    ``expected_type`` by raising :class:`TokenError`.
    """
    try:
        payload = jwt.decode(
            token,
            settings.SECRET_KEY,
            algorithms=[settings.JWT_ALGORITHM],
            audience=ADMIN_TOKEN_AUDIENCE,
        )
    except JWTError as e:
        # Covers invalid signature, expired token, and a present-but-wrong audience.
        raise TokenError("invalid token") from e

    # python-jose does not reject tokens that simply omit the ``aud`` claim, so
    # enforce the admin audience explicitly. This is what keeps consumer/merchant
    # tokens (which carry no admin audience) from being replayed here.
    if payload.get("aud") != ADMIN_TOKEN_AUDIENCE:
        raise TokenError("wrong audience")

    if payload.get("type") != expected_type:
        raise TokenError("wrong token type")

    account_id = payload.get("sub")
    if not account_id:
        raise TokenError("missing subject")

    exp = payload.get("exp")
    expires_at = (
        datetime.fromtimestamp(exp, tz=timezone.utc) if exp is not None else None
    )

    return AdminClaims(
        account_id=account_id,
        token_type=expected_type,
        roles=list(payload.get("roles") or []),
        session_id=payload.get("sid"),
        expires_at=expires_at,
    )


# ---------------------------------------------------------------------------
# Brute-force lockout helpers
# ---------------------------------------------------------------------------
#
# These are pure functions that operate on primitive state (a failed-attempt
# counter, the timestamp of the last failure, and a ``locked_until`` marker) so
# the auth service can persist/read those fields on the AdminAccount without
# coupling the lockout policy to a specific model. Configuration is read from
# settings: ADMIN_LOGIN_MAX_ATTEMPTS, ADMIN_LOGIN_WINDOW_SECONDS,
# ADMIN_LOGIN_LOCKOUT_SECONDS.


def _now(now: datetime | None) -> datetime:
    return now if now is not None else datetime.now(timezone.utc)


def _as_aware(dt: datetime) -> datetime:
    """Treat naive timestamps (e.g. from a DB without tz) as UTC."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def is_locked_out(locked_until: datetime | None, *, now: datetime | None = None) -> bool:
    """Return True when an account is currently within its lockout window."""
    if locked_until is None:
        return False
    return _as_aware(locked_until) > _now(now)


def is_within_window(
    last_failed_at: datetime | None, *, now: datetime | None = None
) -> bool:
    """Return True when the last failure is inside ADMIN_LOGIN_WINDOW_SECONDS."""
    if last_failed_at is None:
        return False
    window = timedelta(seconds=settings.ADMIN_LOGIN_WINDOW_SECONDS)
    return _as_aware(last_failed_at) > (_now(now) - window)


def register_failed_attempt(
    failed_count: int,
    last_failed_at: datetime | None,
    *,
    now: datetime | None = None,
) -> tuple[int, datetime, datetime | None]:
    """Record a failed login attempt and evaluate lockout.

    Returns ``(new_failed_count, new_last_failed_at, locked_until)``.

    - If the previous failure fell outside the window, the counter restarts at 1.
    - Otherwise the counter is incremented.
    - When the (windowed) counter reaches ``ADMIN_LOGIN_MAX_ATTEMPTS`` the
      account is locked until ``now + ADMIN_LOGIN_LOCKOUT_SECONDS``.
    """
    current = _now(now)

    if is_within_window(last_failed_at, now=current):
        new_count = failed_count + 1
    else:
        new_count = 1

    locked_until: datetime | None = None
    if new_count >= settings.ADMIN_LOGIN_MAX_ATTEMPTS:
        locked_until = current + timedelta(seconds=settings.ADMIN_LOGIN_LOCKOUT_SECONDS)

    return new_count, current, locked_until


def clear_failed_attempts() -> tuple[int, None, None]:
    """Reset lockout state after a successful login.

    Returns ``(failed_count, last_failed_at, locked_until)`` = ``(0, None, None)``.
    """
    return 0, None, None
