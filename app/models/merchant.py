import enum
import uuid
from datetime import datetime

from sqlalchemy import ForeignKey, String, UniqueConstraint, func, Float, Boolean
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class MerchantStatus(str, enum.Enum):
    ACTIVE = "active"
    SUSPENDED = "suspended"
    TRIAL = "trial"


class MemberRole(str, enum.Enum):
    OWNER = "owner"
    ADMIN = "admin"
    STAFF = "staff"


class Merchant(Base):
    __tablename__ = "merchants"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    # Human-readable partner identifier: mXXXXXXX (e.g. m1234567).
    # Shared across every shop owned by the same partner (NOT unique): it is the
    # top of the hierarchy partner → shops. The individual shop is shop_id.
    partner_id: Mapped[str | None] = mapped_column(String(16), nullable=True, index=True)
    # Short shop identifier: SXXX (e.g. S123) — unique per shop.
    shop_id: Mapped[str | None] = mapped_column(String(16), unique=True, nullable=True, index=True)
    slug: Mapped[str] = mapped_column(String(120), unique=True, nullable=False)
    legal_name: Mapped[str] = mapped_column(String(255), nullable=False)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    logo_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    support_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    support_phone: Mapped[str | None] = mapped_column(String(50), nullable=True)
    country: Mapped[str] = mapped_column(String(2), nullable=False, default="IN")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default=MerchantStatus.ACTIVE.value)
    settings: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    referral_code: Mapped[str] = mapped_column(String(40), unique=True, nullable=False)
    # Location fields — set ONCE at creation, immutable thereafter
    address: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    latitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    longitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    range_km: Mapped[float | None] = mapped_column(Float, nullable=True)
    is_kyc_completed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    referred_by_code: Mapped[str | None] = mapped_column(String(40), nullable=True)
    referral_bonus_paid: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    kyc_bonus_paid: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        server_default=func.now(), onupdate=func.now(), nullable=False
    )

    members = relationship("MerchantMember", back_populates="merchant", cascade="all, delete-orphan")


class MerchantMember(Base):
    __tablename__ = "merchant_members"
    __table_args__ = (
        UniqueConstraint("merchant_id", "user_id", name="uq_merchant_members_merchant_user"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    merchant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("merchants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    invited_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    joined_at: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False)

    merchant = relationship("Merchant", back_populates="members")
    user = relationship("User", foreign_keys=[user_id])
