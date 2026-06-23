"""Request/response schemas for the admin authentication endpoints (R1).

These mirror the consumer-side :mod:`app.schemas.auth` contract (a bearer
``TokenPair``) but live under the ``admin`` package so the admin API keeps a
self-contained schema surface. The Admin Panel consumes ``access_token`` /
``refresh_token`` exactly as the consumer client does.
"""

from pydantic import BaseModel, EmailStr, Field


class LoginRequest(BaseModel):
    """Credentials submitted to ``POST /admin/auth/login`` (R1.1, R1.2)."""

    email: EmailStr
    password: str = Field(min_length=1, max_length=128)


class RefreshRequest(BaseModel):
    """A refresh token presented to ``POST /admin/auth/refresh`` (R1.3, R1.4)."""

    refresh_token: str = Field(min_length=1)


class LogoutRequest(BaseModel):
    """The refresh token to revoke at ``POST /admin/auth/logout`` (R1.5)."""

    refresh_token: str = Field(min_length=1)


class TokenPair(BaseModel):
    """An issued admin access/refresh token pair (R1.1, R1.3)."""

    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class LogoutResponse(BaseModel):
    """Result of a logout request (R1.5)."""

    revoked: bool = True


class PermissionsResponse(BaseModel):
    """Effective permission set for the current admin (R2.7)."""

    permissions: list[str]
