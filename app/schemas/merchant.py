import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field

MemberRoleLiteral = Literal["owner", "admin", "staff"]
MerchantStatusLiteral = Literal["active", "suspended", "trial"]


class MerchantCreate(BaseModel):
    legal_name: str = Field(min_length=2, max_length=255)
    display_name: str = Field(min_length=2, max_length=255)
    country: str = Field(default="IN", min_length=2, max_length=2)
    support_email: EmailStr | None = None
    support_phone: str | None = Field(default=None, max_length=50)
    logo_url: str | None = Field(default=None, max_length=1024)
    settings: dict | None = None
    # Location — collected at creation, immutable thereafter
    address: str | None = Field(default=None, max_length=1024)
    latitude: float | None = None
    longitude: float | None = None
    range_km: float | None = Field(default=None, ge=0)
    referred_by_code: str | None = None


class MerchantUpdate(BaseModel):
    """Fields allowed to be updated after creation.

    NOTE: address, latitude, longitude are intentionally excluded.
    Location is set once at creation and cannot be changed via the API.
    Merchants must contact support@simulafly.com to request a location change.
    """
    legal_name: str | None = Field(default=None, min_length=2, max_length=255)
    display_name: str | None = Field(default=None, min_length=2, max_length=255)
    logo_url: str | None = Field(default=None, max_length=1024)
    support_email: EmailStr | None = None
    support_phone: str | None = Field(default=None, max_length=50)
    settings: dict | None = None
    range_km: float | None = Field(default=None, ge=0)
    is_kyc_completed: bool | None = Field(default=None)


class MerchantOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    # Human-readable identifiers
    partner_id: str | None = None
    shop_id: str | None = None
    slug: str
    legal_name: str
    display_name: str
    logo_url: str | None
    support_email: str | None
    support_phone: str | None
    country: str
    status: MerchantStatusLiteral
    referral_code: str
    settings: dict
    # Location (read-only after creation)
    address: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    range_km: float | None = None
    distance: float | None = None
    is_kyc_completed: bool
    referred_by_code: str | None = None
    referral_bonus_paid: bool
    created_at: datetime
    updated_at: datetime


class MemberInvite(BaseModel):
    email: EmailStr
    role: MemberRoleLiteral = "staff"


class MemberRoleUpdate(BaseModel):
    role: MemberRoleLiteral


class MerchantMemberOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    user_id: uuid.UUID
    email: EmailStr
    full_name: str | None
    role: MemberRoleLiteral
    joined_at: datetime
