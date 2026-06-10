import uuid
from datetime import datetime

from sqlalchemy import ForeignKey, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class SavedItem(Base):
    """User wishlist entry — one row per (user, merchant_product). Optional `note` lets
    the user attach a short reason ("for the bedroom") and `room_session_id`
    optionally pins the save to the design session it came out of."""

    __tablename__ = "saved_items"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # References merchant_products — the live product catalog served to users.
    merchant_product_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("merchant_products.id", ondelete="CASCADE"),
        nullable=False,
    )
    note: Mapped[str | None] = mapped_column(String(280))
    room_session_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("design_sessions.id", ondelete="SET NULL"),
    )

    added_at: Mapped[datetime] = mapped_column(
        server_default=func.now(), nullable=False
    )

    user = relationship("User", back_populates="saved_items")
    merchant_product = relationship("MerchantProduct")
    session = relationship("DesignSession")

    __table_args__ = (
        UniqueConstraint("user_id", "merchant_product_id", name="uq_saved_user_merchant_product"),
    )
