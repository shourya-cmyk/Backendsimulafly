import math
import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

ProductStatusLiteral = Literal["draft", "published", "paused_insufficient_funds", "archived"]
MAX_PRODUCT_IMAGES = 5
DIMENSION_NUMBER_KEYS = ("height", "width", "depth", "weight")
DIMENSION_UNITS = {"cm", "inches"}
WEIGHT_UNITS = {"g", "kg", "oz", "lb"}


def _validate_additional_images(images: list[str] | None) -> list[str] | None:
    if images is None:
        return None
    if len(images) > MAX_PRODUCT_IMAGES - 1:
        raise ValueError("a product can have at most 5 images total")
    cleaned = [image.strip() for image in images]
    if any(not image for image in cleaned):
        raise ValueError("image URLs cannot be empty")
    if any(len(image) > 2048 for image in cleaned):
        raise ValueError("image URL must be at most 2048 characters")
    if len(set(cleaned)) != len(cleaned):
        raise ValueError("product image URLs must be unique")
    return cleaned


def _validate_dimensions(dimensions: dict | None) -> dict | None:
    if dimensions is None:
        return None
    cleaned = dict(dimensions)
    for key in DIMENSION_NUMBER_KEYS:
        value = cleaned.get(key)
        if value is None:
            continue
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"dimensions.{key} must be numeric")
        if not math.isfinite(float(value)) or value < 0:
            raise ValueError(f"dimensions.{key} must be a finite non-negative number")
    if cleaned.get("unit") is not None and cleaned["unit"] not in DIMENSION_UNITS:
        raise ValueError("dimensions.unit must be cm or inches")
    if cleaned.get("weight_unit") is not None and cleaned["weight_unit"] not in WEIGHT_UNITS:
        raise ValueError("dimensions.weight_unit must be g, kg, oz, or lb")
    return cleaned


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

    @field_validator("additional_images")
    @classmethod
    def validate_additional_images(cls, images: list[str]) -> list[str]:
        return _validate_additional_images(images) or []

    @model_validator(mode="after")
    def validate_ordered_gallery(self):
        if self.additional_images and not self.primary_image_url:
            raise ValueError("primary_image_url is required when additional images are provided")
        if self.primary_image_url and self.primary_image_url in self.additional_images:
            raise ValueError("product image URLs must be unique")
        return self

    dimensions: dict = Field(default_factory=dict)
    materials: dict = Field(default_factory=dict)
    colors: dict = Field(default_factory=dict)
    room_storytelling: dict = Field(default_factory=dict)
    custom_metadata: dict = Field(default_factory=dict)

    has_simulafly_listing: bool = False
    in_app_price: float | None = Field(default=None, ge=0)
    in_app_stock: int | None = Field(default=None, ge=0)
    shop_ids: list[uuid.UUID] | None = None

    @field_validator("dimensions")
    @classmethod
    def validate_dimensions(cls, dimensions: dict) -> dict:
        return _validate_dimensions(dimensions) or {}


class MerchantProductUpdate(BaseModel):
    sku: str | None = Field(default=None, min_length=1, max_length=64)
    title: str | None = Field(default=None, min_length=1, max_length=500)
    description: str | None = None
    category: str | None = Field(default=None, max_length=255)
    subcategory: str | None = Field(default=None, max_length=255)
    brand: str | None = Field(default=None, max_length=255)

    primary_image_url: str | None = Field(default=None, max_length=2048)
    additional_images: list[str] | None = None

    @field_validator("additional_images")
    @classmethod
    def validate_additional_images(cls, images: list[str] | None) -> list[str] | None:
        return _validate_additional_images(images)

    dimensions: dict | None = None
    materials: dict | None = None
    colors: dict | None = None
    room_storytelling: dict | None = None
    custom_metadata: dict | None = None

    has_simulafly_listing: bool | None = None
    in_app_price: float | None = Field(default=None, ge=0)
    in_app_stock: int | None = Field(default=None, ge=0)

    @field_validator("dimensions")
    @classmethod
    def validate_dimensions(cls, dimensions: dict | None) -> dict | None:
        return _validate_dimensions(dimensions)


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


    variants: list[ProductVariantOut] = []

    created_at: datetime
    updated_at: datetime
