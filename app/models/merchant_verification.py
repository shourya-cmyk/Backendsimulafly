import uuid
from datetime import datetime

from sqlalchemy import ForeignKey, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class PanVerification(Base):
    """Account-level PAN verification for a merchant owner.

    The PAN itself is deliberately not retained. A keyed fingerprint supports
    ownership checks against GSTINs while the final four characters are enough
    to show the merchant which PAN was verified.
    """

    __tablename__ = "pan_verifications"
    __table_args__ = (
        UniqueConstraint("user_id", name="uq_pan_verifications_user_id"),
        UniqueConstraint("pan_fingerprint", name="uq_pan_verifications_fingerprint"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    pan_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    pan_last_four: Mapped[str] = mapped_column(String(4), nullable=False)
    verified_name: Mapped[str] = mapped_column(String(255), nullable=False)
    category: Mapped[str] = mapped_column(String(64), nullable=False)
    provider_transaction_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    verified_at: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        server_default=func.now(), onupdate=func.now(), nullable=False
    )

    user = relationship("User")


class GstinVerification(Base):
    """Shop-level GSTIN verification returned by Sandbox/GSTN."""

    __tablename__ = "gstin_verifications"
    __table_args__ = (
        UniqueConstraint("merchant_id", name="uq_gstin_verifications_merchant_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    merchant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("merchants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    gstin: Mapped[str] = mapped_column(String(15), nullable=False, index=True)
    legal_name: Mapped[str] = mapped_column(String(255), nullable=False)
    business_nature: Mapped[str | None] = mapped_column(String(255), nullable=True)
    state_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    state_code: Mapped[str | None] = mapped_column(String(2), nullable=True)
    registration_status: Mapped[str] = mapped_column(String(64), nullable=False)
    registration_start_date: Mapped[str | None] = mapped_column(String(10), nullable=True)
    provider_transaction_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    verified_at: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        server_default=func.now(), onupdate=func.now(), nullable=False
    )

    merchant = relationship("Merchant")
