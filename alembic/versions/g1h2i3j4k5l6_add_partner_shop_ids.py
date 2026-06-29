"""add partner_id, shop_id, address to merchants

Revision ID: g1h2i3j4k5l6
Revises: f1a2b3c4d5e6
Create Date: 2026-06-27

Adds:
  - partner_id: human-readable partner identifier (mXXXXXXX format)
  - shop_id: human-readable shop identifier (SXXX format)
  - address: free-text shop address (set once at creation)

Backfills partner_id and shop_id for any existing merchant rows.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "g1h2i3j4k5l6"
down_revision: Union[str, None] = "f1a2b3c4d5e6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add new columns (all nullable initially for backfill)
    op.add_column(
        "merchants",
        sa.Column("partner_id", sa.String(16), nullable=True),
    )
    op.add_column(
        "merchants",
        sa.Column("shop_id", sa.String(16), nullable=True),
    )
    op.add_column(
        "merchants",
        sa.Column("address", sa.String(1024), nullable=True),
    )

    # Backfill partner_id and shop_id for existing rows using PostgreSQL random()
    # partner_id = 'm' + 7 random digits
    op.execute("""
        UPDATE merchants
        SET partner_id = 'm' || LPAD(FLOOR(RANDOM() * 10000000)::TEXT, 7, '0')
        WHERE partner_id IS NULL
    """)
    # shop_id = 'S' + 3 random digits
    op.execute("""
        UPDATE merchants
        SET shop_id = 'S' || LPAD(FLOOR(RANDOM() * 1000)::TEXT, 3, '0')
        WHERE shop_id IS NULL
    """)

    # Create unique indexes
    op.create_unique_constraint("uq_merchants_partner_id", "merchants", ["partner_id"])
    op.create_unique_constraint("uq_merchants_shop_id", "merchants", ["shop_id"])
    op.create_index("ix_merchants_partner_id", "merchants", ["partner_id"])
    op.create_index("ix_merchants_shop_id", "merchants", ["shop_id"])


def downgrade() -> None:
    op.drop_index("ix_merchants_shop_id", table_name="merchants")
    op.drop_index("ix_merchants_partner_id", table_name="merchants")
    op.drop_constraint("uq_merchants_shop_id", "merchants", type_="unique")
    op.drop_constraint("uq_merchants_partner_id", "merchants", type_="unique")
    op.drop_column("merchants", "address")
    op.drop_column("merchants", "shop_id")
    op.drop_column("merchants", "partner_id")
