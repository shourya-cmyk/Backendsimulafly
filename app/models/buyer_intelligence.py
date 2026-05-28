"""Models for Phase 6: Buyer Intelligence & My Customers."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class MerchantBuyerAccess(Base):
    """Tracks when a merchant unlocks a SimulaFly shopper's contact info."""

    __tablename__ = "merchant_buyer_access"
    __table_args__ = (
        UniqueConstraint("merchant_id", "user_id", name="uq_merchant_buyer_access"),
        Index("ix_merchant_buyer_access_merchant", "merchant_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    merchant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("merchants.id", ondelete="CASCADE"),
        nullable=False,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    unlock_cost: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False, default=30)
    unlocked_at: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False)


class MerchantContact(Base):
    """Merchant's own offline customer CRM entries."""

    __tablename__ = "merchant_contacts"
    __table_args__ = (
        Index("ix_merchant_contacts_merchant", "merchant_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    merchant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("merchants.id", ondelete="CASCADE"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    phone: Mapped[str | None] = mapped_column(String(30), nullable=True)
    email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    source: Mapped[str] = mapped_column(String(32), nullable=False, default="manual")
    # "This month", "Last week", etc. — freeform text the merchant provides
    last_purchase_note: Mapped[str | None] = mapped_column(String(120), nullable=True)
    invite_status: Mapped[str] = mapped_column(String(32), nullable=False, default="not_invited")
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        server_default=func.now(), onupdate=func.now(), nullable=False
    )
