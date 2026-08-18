"""create merchant_coupons table

Revision ID: m1n2o3p4q5r6
Revises: l1m2n3o4p5q6
Create Date: 2026-08-18
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "m1n2o3p4q5r6"
down_revision: Union[str, Sequence[str], None] = "l1m2n3o4p5q6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "merchant_coupons",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "merchant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("merchants.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("code", sa.String(length=64), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("discount_type", sa.String(length=20), nullable=False, server_default="flat"),
        sa.Column("discount_value", sa.Numeric(14, 4), nullable=False),
        sa.Column("min_order_amount", sa.Numeric(14, 4), nullable=False, server_default="0.0"),
        sa.Column("max_discount_amount", sa.Numeric(14, 4), nullable=True),
        sa.Column("usage_limit", sa.Integer(), nullable=True),
        sa.Column("used_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("expires_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index("ix_merchant_coupons_merchant_id", "merchant_coupons", ["merchant_id"])
    op.create_index("ix_merchant_coupons_code", "merchant_coupons", ["code"])


def downgrade() -> None:
    op.drop_index("ix_merchant_coupons_code", table_name="merchant_coupons")
    op.drop_index("ix_merchant_coupons_merchant_id", table_name="merchant_coupons")
    op.drop_table("merchant_coupons")
