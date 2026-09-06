"""order acceptance billing and applied coupon snapshots

Revision ID: o1p2q3r4s5t6
Revises: n1o2p3q4r5s6
Create Date: 2026-09-06
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "o1p2q3r4s5t6"
down_revision: Union[str, Sequence[str], None] = "n1o2p3q4r5s6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column(
        "merchant_buyer_access",
        "unlock_cost",
        existing_type=sa.Numeric(10, 2),
        existing_nullable=False,
        server_default="50",
    )

    # Coupons must always belong to a merchant. Legacy rows without an owner
    # came from the old automatic seeder and cannot be safely redeemed.
    op.execute("DELETE FROM merchant_coupons WHERE merchant_id IS NULL")
    op.alter_column(
        "merchant_coupons",
        "merchant_id",
        existing_type=postgresql.UUID(as_uuid=True),
        nullable=False,
    )
    op.create_unique_constraint(
        "uq_merchant_coupons_merchant_code",
        "merchant_coupons",
        ["merchant_id", "code"],
    )

    op.add_column(
        "orders",
        sa.Column("subtotal_estimated", sa.Numeric(14, 2), nullable=True),
    )
    op.add_column(
        "orders",
        sa.Column(
            "coupon_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("merchant_coupons.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.add_column("orders", sa.Column("coupon_code", sa.String(64), nullable=True))
    op.add_column(
        "orders",
        sa.Column(
            "discount_amount",
            sa.Numeric(14, 2),
            nullable=False,
            server_default="0",
        ),
    )
    op.add_column(
        "orders",
        sa.Column("accepted_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
    )
    op.add_column(
        "orders",
        sa.Column("fee_charged_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
    )
    op.add_column(
        "orders",
        sa.Column(
            "platform_fee_amount",
            sa.Numeric(14, 4),
            nullable=False,
            server_default="0",
        ),
    )
    op.execute("UPDATE orders SET subtotal_estimated = total_estimated")
    op.alter_column(
        "orders",
        "subtotal_estimated",
        existing_type=sa.Numeric(14, 2),
        nullable=False,
    )


def downgrade() -> None:
    op.alter_column(
        "merchant_buyer_access",
        "unlock_cost",
        existing_type=sa.Numeric(10, 2),
        existing_nullable=False,
        server_default="30",
    )

    op.drop_column("orders", "platform_fee_amount")
    op.drop_column("orders", "fee_charged_at")
    op.drop_column("orders", "accepted_at")
    op.drop_column("orders", "discount_amount")
    op.drop_column("orders", "coupon_code")
    op.drop_column("orders", "coupon_id")
    op.drop_column("orders", "subtotal_estimated")

    op.drop_constraint(
        "uq_merchant_coupons_merchant_code",
        "merchant_coupons",
        type_="unique",
    )
    op.alter_column(
        "merchant_coupons",
        "merchant_id",
        existing_type=postgresql.UUID(as_uuid=True),
        nullable=True,
    )
