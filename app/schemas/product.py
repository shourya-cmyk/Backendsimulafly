import uuid
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ProductOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    asin: str
    title: str
    category: str | None = None
    price: float | None = None
    image_url: str | None = None
    product_url: str | None = None
    rating: float | None = None
    metadata: dict[str, Any] = Field(default_factory=dict, alias="product_metadata")


class MerchantProductOut(BaseModel):
    """Serialised view of MerchantProduct for Flutter chat carousels."""
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    sku: str
    title: str
    category: str | None = None
    in_app_price: float | None = None
    primary_image_url: str | None = None
    description: str | None = None
    brand: str | None = None
    has_simulafly_listing: bool = False


class ProductListQuery(BaseModel):
    category: str | None = None
    max_price: float | None = Field(default=None, ge=0)
    limit: int = Field(default=20, ge=1, le=100)
    offset: int = Field(default=0, ge=0)
