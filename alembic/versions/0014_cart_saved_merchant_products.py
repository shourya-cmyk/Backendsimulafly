"""Migrate cart_items and saved_items to reference merchant_products

Revision ID: 0014
Revises: 0013
Create Date: 2026-05-30
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── cart_items ──────────────────────────────────────────────────────────
    # Add new merchant_product_id column (nullable first, then backfill/set NOT NULL)
    op.add_column(
        "cart_items",
        sa.Column(
            "merchant_product_id",
            UUID(as_uuid=True),
            sa.ForeignKey("merchant_products.id", ondelete="CASCADE"),
            nullable=True,
        ),
    )
    # Drop old FK constraint and column
    op.drop_constraint("uq_cart_user_product", "cart_items", type_="unique")
    op.drop_constraint("cart_items_product_id_fkey", "cart_items", type_="foreignkey")
    op.drop_column("cart_items", "product_id")

    # Remove rows that have no merchant_product mapping (old Amazon catalog rows)
    op.execute("DELETE FROM cart_items WHERE merchant_product_id IS NULL")

    # Now make it NOT NULL
    op.alter_column("cart_items", "merchant_product_id", nullable=False)

    # Add unique constraint
    op.create_unique_constraint(
        "uq_cart_user_merchant_product",
        "cart_items",
        ["user_id", "merchant_product_id"],
    )

    # ── saved_items ──────────────────────────────────────────────────────────
    op.add_column(
        "saved_items",
        sa.Column(
            "merchant_product_id",
            UUID(as_uuid=True),
            sa.ForeignKey("merchant_products.id", ondelete="CASCADE"),
            nullable=True,
        ),
    )
    op.drop_constraint("uq_saved_user_product", "saved_items", type_="unique")
    op.drop_constraint("saved_items_product_id_fkey", "saved_items", type_="foreignkey")
    op.drop_column("saved_items", "product_id")

    # Remove rows that have no merchant_product mapping
    op.execute("DELETE FROM saved_items WHERE merchant_product_id IS NULL")

    op.alter_column("saved_items", "merchant_product_id", nullable=False)

    op.create_unique_constraint(
        "uq_saved_user_merchant_product",
        "saved_items",
        ["user_id", "merchant_product_id"],
    )


def downgrade() -> None:
    # ── saved_items ──────────────────────────────────────────────────────────
    op.drop_constraint("uq_saved_user_merchant_product", "saved_items", type_="unique")
    op.drop_column("saved_items", "merchant_product_id")
    op.add_column(
        "saved_items",
        sa.Column(
            "product_id",
            UUID(as_uuid=True),
            sa.ForeignKey("products.id", ondelete="CASCADE"),
            nullable=True,
        ),
    )
    op.create_unique_constraint(
        "uq_saved_user_product", "saved_items", ["user_id", "product_id"]
    )

    # ── cart_items ──────────────────────────────────────────────────────────
    op.drop_constraint("uq_cart_user_merchant_product", "cart_items", type_="unique")
    op.drop_column("cart_items", "merchant_product_id")
    op.add_column(
        "cart_items",
        sa.Column(
            "product_id",
            UUID(as_uuid=True),
            sa.ForeignKey("products.id", ondelete="CASCADE"),
            nullable=True,
        ),
    )
    op.create_unique_constraint(
        "uq_cart_user_product", "cart_items", ["user_id", "product_id"]
    )
