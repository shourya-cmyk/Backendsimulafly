"""create merchant_product_variants table

Revision ID: 0012
Revises: 0011
Create Date: 2026-05-22
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "merchant_product_variants",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("merchant_product_id", UUID(as_uuid=True), nullable=False),
        sa.Column("sku_suffix", sa.String(64), nullable=False),
        sa.Column("label", sa.String(255), nullable=False),
        sa.Column("color", sa.String(100), nullable=True),
        sa.Column("size", sa.String(100), nullable=True),
        sa.Column("material", sa.String(100), nullable=True),
        sa.Column("price_modifier", sa.Numeric(12, 2), nullable=False, server_default="0"),
        sa.Column("in_app_stock", sa.Integer, nullable=True),
        sa.Column("primary_image_url", sa.Text, nullable=True),
        sa.Column("is_default", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("position", sa.Integer, nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(
            ["merchant_product_id"],
            ["merchant_products.id"],
            ondelete="CASCADE",
        ),
    )
    op.create_index(
        "ix_merchant_product_variants_product",
        "merchant_product_variants",
        ["merchant_product_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_merchant_product_variants_product", table_name="merchant_product_variants")
    op.drop_table("merchant_product_variants")
