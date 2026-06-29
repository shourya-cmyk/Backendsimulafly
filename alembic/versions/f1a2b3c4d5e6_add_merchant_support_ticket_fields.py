"""add merchant support ticket fields

Revision ID: f1a2b3c4d5e6
Revises: f8a9b0c1d2e3
Create Date: 2026-06-26
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "f1a2b3c4d5e6"
down_revision: Union[str, None] = "3adb0d627877"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add structured ticket metadata columns to support_tickets
    op.add_column(
        "support_tickets",
        sa.Column("reason", sa.String(64), nullable=True),
    )
    op.add_column(
        "support_tickets",
        sa.Column("sub_reason", sa.String(64), nullable=True),
    )
    op.add_column(
        "support_tickets",
        sa.Column(
            "merchant_product_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("merchant_products.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.add_column(
        "support_tickets",
        sa.Column("attachment_url", sa.String(2048), nullable=True),
    )
    op.add_column(
        "support_tickets",
        sa.Column("description", sa.Text, nullable=True),
    )
    op.create_index(
        "ix_support_tickets_merchant_product_id",
        "support_tickets",
        ["merchant_product_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_support_tickets_merchant_product_id", table_name="support_tickets")
    op.drop_column("support_tickets", "description")
    op.drop_column("support_tickets", "attachment_url")
    op.drop_column("support_tickets", "merchant_product_id")
    op.drop_column("support_tickets", "sub_reason")
    op.drop_column("support_tickets", "reason")
