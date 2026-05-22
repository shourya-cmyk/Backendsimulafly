"""create merchant_products and external_links

Revision ID: 0008
Revises: 0007
Create Date: 2026-05-22
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0008"
down_revision: Union[str, None] = "0007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "merchant_products",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "merchant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("merchants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("sku", sa.String(64), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("category", sa.String(255), nullable=True),
        sa.Column("subcategory", sa.String(255), nullable=True),
        sa.Column("brand", sa.String(255), nullable=True),
        sa.Column("status", sa.String(32), nullable=False, server_default="draft"),
        # draft | published | paused_insufficient_funds | archived

        sa.Column("primary_image_url", sa.Text(), nullable=True),
        sa.Column("additional_images", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),

        sa.Column("dimensions", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("materials", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("colors", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("room_storytelling", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("custom_metadata", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),

        sa.Column("has_simulafly_listing", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("in_app_price", sa.Numeric(12, 2), nullable=True),
        sa.Column("in_app_stock", sa.Integer(), nullable=True),

        # pgvector column; Phase 4 will index it (HNSW). For now, declared without index.
        sa.Column("embedding", postgresql.ARRAY(sa.Float()).with_variant(sa.Text(), "sqlite"), nullable=True),

        sa.Column("ai_relevance_score", sa.Numeric(5, 2), nullable=True),
        sa.Column("health_score", sa.String(16), nullable=False, server_default="good"),
        sa.Column("health_reason", sa.Text(), nullable=True),

        sa.Column(
            "created_at",
            postgresql.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            postgresql.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_unique_constraint(
        "uq_merchant_products_merchant_sku", "merchant_products", ["merchant_id", "sku"]
    )
    op.create_index("ix_merchant_products_merchant_status", "merchant_products", ["merchant_id", "status"])
    op.create_index("ix_merchant_products_category", "merchant_products", ["category"])

    # External links — zero-or-more per product
    op.create_table(
        "merchant_product_external_links",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "merchant_product_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("merchant_products.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("platform", sa.String(32), nullable=False),  # amazon|shopify|brand_site|whatsapp|other
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("label", sa.String(120), nullable=True),
        sa.Column("last_seen_price", sa.Numeric(12, 2), nullable=True),
        sa.Column("is_primary", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "created_at",
            postgresql.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index(
        "ix_merchant_product_external_links_product",
        "merchant_product_external_links",
        ["merchant_product_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_merchant_product_external_links_product",
        table_name="merchant_product_external_links",
    )
    op.drop_table("merchant_product_external_links")
    op.drop_index("ix_merchant_products_category", table_name="merchant_products")
    op.drop_index("ix_merchant_products_merchant_status", table_name="merchant_products")
    op.drop_constraint(
        "uq_merchant_products_merchant_sku", "merchant_products", type_="unique"
    )
    op.drop_table("merchant_products")
