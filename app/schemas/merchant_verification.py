from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator


PAN_PATTERN = r"^[A-Z]{5}[0-9]{4}[A-Z]$"
GSTIN_PATTERN = r"^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z]$"


class PanVerificationRequest(BaseModel):
    pan: str = Field(pattern=PAN_PATTERN)
    name_as_per_pan: str = Field(min_length=2, max_length=255)
    date_of_birth: date
    consent: Literal[True]

    @field_validator("pan", mode="before")
    @classmethod
    def normalize_pan(cls, value: str) -> str:
        return str(value).strip().upper()

    @field_validator("name_as_per_pan", mode="before")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        return " ".join(str(value).strip().split())


class GstinVerificationRequest(BaseModel):
    gstin: str = Field(pattern=GSTIN_PATTERN)

    @field_validator("gstin", mode="before")
    @classmethod
    def normalize_gstin(cls, value: str) -> str:
        return str(value).strip().upper()


class PanVerificationStatusOut(BaseModel):
    status: Literal["not_started", "verified"]
    masked_pan: str | None = None
    verified_name: str | None = None
    category: str | None = None
    verified_at: datetime | None = None


class GstinVerificationStatusOut(BaseModel):
    status: Literal["not_started", "verified"]
    gstin: str | None = None
    legal_name: str | None = None
    business_nature: str | None = None
    state_name: str | None = None
    registration_status: str | None = None
    verified_at: datetime | None = None


class VerificationCheckOut(BaseModel):
    status: Literal["pending", "verified"]


class OtherVerificationChecksOut(BaseModel):
    authorized_person: VerificationCheckOut
    business_address: VerificationCheckOut
    shop_location: VerificationCheckOut


class AgreementStatusOut(BaseModel):
    accepted: bool
    accepted_at: datetime | None = None
    versions: dict[str, str] = Field(default_factory=dict)


class AgreementAcceptanceRequest(BaseModel):
    merchant_agreement: Literal[True]
    terms_and_conditions: Literal[True]
    privacy_policy: Literal[True]
    marketplace_rules: Literal[True]
    product_listing_policy: Literal[True]
    cancellation_return_rules: Literal[True]
    merchant_obligations_and_fees: Literal[True]


class MerchantVerificationOut(BaseModel):
    pan: PanVerificationStatusOut
    gstin: GstinVerificationStatusOut
    other_checks: OtherVerificationChecksOut
    agreement: AgreementStatusOut
    is_kyc_completed: bool
    approval_status: Literal["draft", "pending_verification", "approved", "rejected"]
    can_activate: bool
