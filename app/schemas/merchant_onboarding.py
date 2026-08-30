from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, EmailStr, Field, field_validator, model_validator

from app.schemas.merchant_verification import GSTIN_PATTERN, PAN_PATTERN


BusinessType = Literal[
    "sole_proprietorship",
    "partnership",
    "llp",
    "private_limited",
    "public_limited",
    "one_person_company",
    "other",
]
RelationshipType = Literal["owner", "partner", "director", "authorized_representative"]
FulfilmentMethod = Literal[
    "merchant_delivery",
    "customer_pickup",
    "third_party_delivery",
    "installation_service",
]


class AddressInput(BaseModel):
    line1: str = Field(min_length=5, max_length=255)
    line2: str | None = Field(default=None, max_length=255)
    city: str = Field(min_length=2, max_length=100)
    state: str = Field(min_length=2, max_length=100)
    postal_code: str = Field(pattern=r"^[1-9][0-9]{5}$")


class PersonalDetailsInput(BaseModel):
    full_name: str = Field(min_length=2, max_length=255)
    email: EmailStr
    phone: str = Field(pattern=r"^\+?[0-9]{10,15}$")
    relationship: RelationshipType


class BusinessDetailsInput(BaseModel):
    business_type: BusinessType
    other_business_type: str | None = Field(default=None, max_length=100)
    business_name: str = Field(min_length=2, max_length=255)
    registered_business_name: str = Field(min_length=2, max_length=255)
    business_pan: str = Field(pattern=PAN_PATTERN)
    registered_address: AddressInput

    @field_validator("business_pan", mode="before")
    @classmethod
    def normalize_pan(cls, value: str) -> str:
        return str(value).strip().upper()

    @model_validator(mode="after")
    def validate_other_type(self):
        if self.business_type == "other" and not (self.other_business_type or "").strip():
            raise ValueError("other_business_type is required when business_type is other")
        return self


class ShopDetailsInput(BaseModel):
    shop_name: str = Field(min_length=2, max_length=255)
    shop_address: AddressInput
    gstin: str = Field(pattern=GSTIN_PATTERN)
    operating_location: str = Field(min_length=2, max_length=255)
    contact_number: str = Field(pattern=r"^\+?[0-9]{10,15}$")
    operating_hours: str = Field(min_length=3, max_length=255)
    service_radius_km: int | None = Field(default=None, ge=0, le=99)

    @field_validator("gstin", mode="before")
    @classmethod
    def normalize_gstin(cls, value: str) -> str:
        return str(value).strip().upper()


class FulfilmentInput(BaseModel):
    methods: list[FulfilmentMethod] = Field(min_length=1, max_length=4)
    delivery_service_radius_km: int | None = Field(default=None, ge=0, le=99)
    estimated_fulfilment_time: int = Field(ge=1, le=99)

    @field_validator("methods")
    @classmethod
    def validate_unique_methods(cls, values: list[FulfilmentMethod]):
        if len(set(values)) != len(values):
            raise ValueError("fulfilment methods must be unique")
        return values


class MerchantOnboardingSubmission(BaseModel):
    personal: PersonalDetailsInput
    business: BusinessDetailsInput
    shop: ShopDetailsInput
    fulfilment: FulfilmentInput
    information_accurate: Literal[True]


class MerchantOnboardingStatusOut(BaseModel):
    submitted: bool
    submitted_at: str | None = None
    checks: dict[str, bool]
