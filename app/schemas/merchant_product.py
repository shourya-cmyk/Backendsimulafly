import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl

ProductStatusLiteral = Literal["draft", "published", "paused_insufficient_funds", "archived"]
ExternalLinkPlatformLiteral = Literal["amazon", "shopify", "brand_site", "whatsapp", "other"]


class ExternalLinkCreate(BaseModel):
    platform: ExternalLinkPlatformLiteral
    url: HttpUrl
    label: str | None = Field(default=None, max_length=120)
    last_seen_price: float | None = Field(default=None, ge=0)
    is_primary: bool = False
    position: int = Field(default=0, ge=0)


class ExternalLinkUpdate(BaseModel):
    platform: ExternalLinkPlatformLiteral | None = None
    url: HttpUrl | None = None
    label: str | None = Field(default=None, max_length=120)
    last_seen_price: float | None = Field(default=None, ge=0)
    is_primary: bool | None = None
    position: int | None = Field(default=None, ge=0)


class ExternalLinkOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    merchant_product_id: uuid.UUID
    platform: ExternalLinkPlatformLiteral
    url: str
    label: str | None
    last_seen_price: float | None
    is_primary: bool
    position: int
    created_at: datetime


class ProductVariantCreate(BaseModel):
    sku_suffix: str = Field(min_length=1, max_length=64)
    label: str = Field(min_length=1, max_length=255)
    color: str | None = Field(default=None, max_length=100)
    size: str | None = Field(default=None, max_length=100)
    material: str | None = Field(default=None, max_length=100)
    price_modifier: float = Field(default=0)
    in_app_stock: int | None = Field(default=None, ge=0)
    primary_image_url: str | None = Field(default=None, max_length=2048)
    is_default: bool = False
    position: int = Field(default=0, ge=0)


class ProductVariantUpdate(BaseModel):
    sku_suffix: str | None = Field(default=None, min_length=1, max_length=64)
    label: str | None = Field(default=None, min_length=1, max_length=255)
    color: str | None = Field(default=None, max_length=100)
    size: str | None = Field(default=None, max_length=100)
    material: str | None = Field(default=None, max_length=100)
    price_modifier: float | None = None
    in_app_stock: int | None = Field(default=None, ge=0)
    primary_image_url: str | None = Field(default=None, max_length=2048)
    is_default: bool | None = None
    position: int | None = Field(default=None, ge=0)


class ProductVariantOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    merchant_product_id: uuid.UUID
    sku_suffix: str
    label: str
    color: str | None
    size: str | None
    material: str | None
    price_modifier: float
    in_app_stock: int | None
    primary_image_url: str | None
    is_default: bool
    position: int
    created_at: datetime
    updated_at: datetime


class MerchantProductCreate(BaseModel):
    sku: str = Field(min_length=1, max_length=64)
    title: str = Field(min_length=1, max_length=500)
    description: str | None = None
    category: str | None = Field(default=None, max_length=255)
    subcategory: str | None = Field(default=None, max_length=255)
    brand: str | None = Field(default=None, max_length=255)
    status: ProductStatusLiteral = "draft"

    primary_image_url: str | None = Field(default=None, max_length=2048)
    additional_images: list[str] = Field(default_factory=list)

    dimensions: dict = Field(default_factory=dict)
    materials: dict = Field(default_factory=dict)
    colors: dict = Field(default_factory=dict)
    room_storytelling: dict = Field(default_factory=dict)
    custom_metadata: dict = Field(default_factory=dict)

    has_simulafly_listing: bool = False
    in_app_price: float | None = Field(default=None, ge=0)
    in_app_stock: int | None = Field(default=None, ge=0)


class MerchantProductUpdate(BaseModel):
    sku: str | None = Field(default=None, min_length=1, max_length=64)
    title: str | None = Field(default=None, min_length=1, max_length=500)
    description: str | None = None
    category: str | None = Field(default=None, max_length=255)
    subcategory: str | None = Field(default=None, max_length=255)
    brand: str | None = Field(default=None, max_length=255)

    primary_image_url: str | None = Field(default=None, max_length=2048)
    additional_images: list[str] | None = None

    dimensions: dict | None = None
    materials: dict | None = None
    colors: dict | None = None
    room_storytelling: dict | None = None
    custom_metadata: dict | None = None

    has_simulafly_listing: bool | None = None
    in_app_price: float | None = Field(default=None, ge=0)
    in_app_stock: int | None = Field(default=None, ge=0)


class MerchantProductOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    merchant_id: uuid.UUID
    sku: str
    title: str
    description: str | None
    category: str | None
    subcategory: str | None
    brand: str | None
    status: ProductStatusLiteral

    primary_image_url: str | None
    additional_images: list[str]

    dimensions: dict
    materials: dict
    colors: dict
    room_storytelling: dict
    custom_metadata: dict

    has_simulafly_listing: bool
    in_app_price: float | None
    in_app_stock: int | None

    ai_relevance_score: float | None
    health_score: str
    health_reason: str | None

    external_links: list[ExternalLinkOut] = []
    variants: list[ProductVariantOut] = []

    created_at: datetime
    updated_at: datetime
