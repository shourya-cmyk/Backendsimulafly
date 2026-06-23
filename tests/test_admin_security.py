"""Unit tests for admin security primitives (Task 2.2).

Covers:
- Token round-trip under the admin audience.
- Wrong-audience token rejection (a consumer token from app.core.security).
- Expired access token rejection.
- Refresh/access token type enforcement.
- Brute-force lockout helpers (register / is_locked_out / clear).

These exercise pure functions in ``app.core.admin_security`` so no DB or HTTP
client is required.

Requirements: 1.4, 1.6, 1.7, 1.8
"""

from datetime import datetime, timedelta, timezone

import pytest

from app.core import admin_security
from app.core.admin_security import (
    ADMIN_TOKEN_AUDIENCE,
    TokenError,
    clear_failed_attempts,
    create_admin_access_token,
    create_admin_refresh_token,
    decode_admin_token,
    is_locked_out,
    is_within_window,
    register_failed_attempt,
)
from app.core.config import get_settings
from app.core.security import create_access_token as create_consumer_access_token

settings = get_settings()


# ---------------------------------------------------------------------------
# Token round-trip (Requirement 1.6 — admin audience)
# ---------------------------------------------------------------------------


def test_access_token_round_trip_preserves_account_and_roles():
    account_id = "11111111-1111-1111-1111-111111111111"
    roles = ["Super Admin", "Finance"]

    token = create_admin_access_token(account_id, roles=roles)
    claims = decode_admin_token(token, "access")

    assert claims.account_id == account_id
    assert claims.roles == roles
    assert claims.token_type == "access"
    assert claims.expires_at is not None


def test_refresh_token_round_trip_preserves_account_and_session():
    account_id = "22222222-2222-2222-2222-222222222222"
    session_id = "sess-abc"

    token = create_admin_refresh_token(account_id, session_id=session_id)
    claims = decode_admin_token(token, "refresh")

    assert claims.account_id == account_id
    assert claims.session_id == session_id
    assert claims.token_type == "refresh"


# ---------------------------------------------------------------------------
# Wrong-audience rejection (Requirement 1.6)
# ---------------------------------------------------------------------------


def test_consumer_token_rejected_by_admin_decode():
    # A consumer token from app.core.security carries no admin audience claim.
    consumer_token = create_consumer_access_token("33333333-3333-3333-3333-333333333333")

    with pytest.raises(TokenError):
        decode_admin_token(consumer_token, "access")


def test_token_with_other_audience_rejected():
    # Craft a structurally valid admin-style token but with the wrong audience.
    from jose import jwt

    now = datetime.now(timezone.utc)
    payload = {
        "sub": "44444444-4444-4444-4444-444444444444",
        "type": "access",
        "aud": "some-other-audience",
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=5)).timestamp()),
        "roles": [],
    }
    token = jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.JWT_ALGORITHM)

    with pytest.raises(TokenError):
        decode_admin_token(token, "access")


# ---------------------------------------------------------------------------
# Expired access token rejection (Requirement 1.7)
# ---------------------------------------------------------------------------


def test_expired_access_token_rejected():
    # Build an admin-audience token whose exp is already in the past.
    expired = admin_security._create_token(
        "55555555-5555-5555-5555-555555555555",
        "access",
        timedelta(seconds=-30),
        roles=[],
    )

    with pytest.raises(TokenError):
        decode_admin_token(expired, "access")


# ---------------------------------------------------------------------------
# Token type enforcement (Requirement 1.4 / type isolation)
# ---------------------------------------------------------------------------


def test_refresh_token_decoded_as_access_rejected():
    token = create_admin_refresh_token("66666666-6666-6666-6666-666666666666", session_id="s1")

    with pytest.raises(TokenError):
        decode_admin_token(token, "access")


def test_access_token_decoded_as_refresh_rejected():
    token = create_admin_access_token("77777777-7777-7777-7777-777777777777", roles=[])

    with pytest.raises(TokenError):
        decode_admin_token(token, "refresh")


# ---------------------------------------------------------------------------
# Lockout helpers (Requirement 1.8)
# ---------------------------------------------------------------------------


def test_lockout_triggers_after_max_attempts_within_window():
    now = datetime.now(timezone.utc)
    count = 0
    last_failed_at: datetime | None = None
    locked_until: datetime | None = None

    # Simulate consecutive failures inside the window, advancing time slightly.
    for i in range(settings.ADMIN_LOGIN_MAX_ATTEMPTS):
        current = now + timedelta(seconds=i)
        count, last_failed_at, locked_until = register_failed_attempt(
            count, last_failed_at, now=current
        )

    # After exactly MAX_ATTEMPTS the account should be locked.
    assert count == settings.ADMIN_LOGIN_MAX_ATTEMPTS
    assert locked_until is not None
    assert is_locked_out(locked_until, now=last_failed_at) is True


def test_lockout_not_triggered_before_max_attempts():
    now = datetime.now(timezone.utc)
    count = 0
    last_failed_at: datetime | None = None
    locked_until: datetime | None = None

    for i in range(settings.ADMIN_LOGIN_MAX_ATTEMPTS - 1):
        current = now + timedelta(seconds=i)
        count, last_failed_at, locked_until = register_failed_attempt(
            count, last_failed_at, now=current
        )

    assert count == settings.ADMIN_LOGIN_MAX_ATTEMPTS - 1
    assert locked_until is None
    assert is_locked_out(locked_until, now=last_failed_at) is False


def test_counter_restarts_when_previous_failure_outside_window():
    now = datetime.now(timezone.utc)

    # First failure
    count, last_failed_at, _ = register_failed_attempt(0, None, now=now)
    assert count == 1

    # Next failure happens after the window has elapsed -> counter restarts at 1.
    later = now + timedelta(seconds=settings.ADMIN_LOGIN_WINDOW_SECONDS + 1)
    assert is_within_window(last_failed_at, now=later) is False
    count, last_failed_at, locked_until = register_failed_attempt(
        count, last_failed_at, now=later
    )
    assert count == 1
    assert locked_until is None


def test_clear_failed_attempts_resets_state():
    failed_count, last_failed_at, locked_until = clear_failed_attempts()

    assert failed_count == 0
    assert last_failed_at is None
    assert locked_until is None
    assert is_locked_out(locked_until) is False


def test_is_locked_out_false_after_lockout_expires():
    now = datetime.now(timezone.utc)
    locked_until = now + timedelta(seconds=settings.ADMIN_LOGIN_LOCKOUT_SECONDS)

    # Still locked during the window.
    assert is_locked_out(locked_until, now=now) is True
    # No longer locked once the lockout period has passed.
    after = locked_until + timedelta(seconds=1)
    assert is_locked_out(locked_until, now=after) is False
