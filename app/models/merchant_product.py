import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from pgvector.sqlalchemy import Vector
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class ProductStatus(str, enum.Enum):
    DRAFT = "draft"
    PUBLISHED = "published"
    PAUSED_INSUFFICIENT_FUNDS = "paused_insufficient_funds"
    ARCHIVED = "archived"


class ExternalLinkPlatform(str, enum.Enum):
    AMAZON = "amazon"
    SHOPIFY = "shopify"
    BRAND_SITE = "brand_site"
    WHATSAPP = "whatsapp"
    OTHER = "other"


class MerchantProduct(Base):
    __tablename__ = "merchant_products"
    __table_args__ = (
        UniqueConstraint("merchant_id", "sku", name="uq_merchant_products_merchant_sku"),
        Index("ix_merchant_products_merchant_status", "merchant_id", "status"),
        Index("ix_merchant_products_category", "category"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    merchant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("merchants.id", ondelete="CASCADE"),
        nullable=False,
    )
    sku: Mapped[str] = mapped_column(String(64), nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    category: Mapped[str | None] = mapped_column(String(255), nullable=True)
    subcategory: Mapped[str | None] = mapped_column(String(255), nullable=True)
    brand: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default=ProductStatus.DRAFT.value)

    primary_image_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    additional_images: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)

    dimensions: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    materials: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    colors: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    room_storytelling: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    custom_metadata: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)

    has_simulafly_listing: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    in_app_price: Mapped[float | None] = mapped_column(Numeric(12, 2), nullable=True)
    in_app_stock: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Phase 4: pgvector with HNSW index (replaces Phase 2's ARRAY(Float) placeholder)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(3072), nullable=True)

    ai_relevance_score: Mapped[float | None] = mapped_column(Numeric(5, 2), nullable=True)
    health_score: Mapped[str] = mapped_column(String(16), nullable=False, default="good")
    health_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        server_default=func.now(), onupdate=func.now(), nullable=False
    )

    external_links = relationship(
        "MerchantProductExternalLink",
        back_populates="merchant_product",
        cascade="all, delete-orphan",
        order_by="MerchantProductExternalLink.position",
    )


class MerchantProductExternalLink(Base):
    __tablename__ = "merchant_product_external_links"
    __table_args__ = (
        Index("ix_merchant_product_external_links_product", "merchant_product_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    merchant_product_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("merchant_products.id", ondelete="CASCADE"),
        nullable=False,
    )
    platform: Mapped[str] = mapped_column(String(32), nullable=False)
    url: Mapped[str] = mapped_column(Text, nullable=False)
    label: Mapped[str | None] = mapped_column(String(120), nullable=True)
    last_seen_price: Mapped[float | None] = mapped_column(Numeric(12, 2), nullable=True)
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    position: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False)

    merchant_product = relationship("MerchantProduct", back_populates="external_links")
