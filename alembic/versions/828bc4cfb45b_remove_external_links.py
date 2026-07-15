"""remove_external_links

Revision ID: 828bc4cfb45b
Revises: i1j2k3l4m5n6
Create Date: 2026-07-15 02:54:14.834605

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '828bc4cfb45b'
down_revision: Union[str, None] = 'i1j2k3l4m5n6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_table("merchant_product_external_links")


def downgrade() -> None:
    op.create_table(
        "merchant_product_external_links",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("merchant_product_id", sa.UUID(), nullable=False),
        sa.Column("platform", sa.String(length=32), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("label", sa.String(length=120), nullable=True),
        sa.Column("last_seen_price", sa.Numeric(precision=12, scale=2), nullable=True),
        sa.Column("is_primary", sa.Boolean(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["merchant_product_id"],
            ["merchant_products.id"],
            name="fk_merchant_product_external_links_product",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_merchant_product_external_links_product",
        "merchant_product_external_links",
        ["merchant_product_id"],
        unique=False,
    )

