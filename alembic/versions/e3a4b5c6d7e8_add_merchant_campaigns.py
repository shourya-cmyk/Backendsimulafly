"""add merchant campaigns table

Revision ID: e3a4b5c6d7e8
Revises: 4abe9d7b6092
Create Date: 2026-06-18

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

# revision identifiers, used by Alembic.
revision: str = 'e3a4b5c6d7e8'
down_revision: Union[str, None] = '4abe9d7b6092'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "merchant_campaigns",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("merchant_id", UUID(as_uuid=True), nullable=False),
        sa.Column("product_name", sa.String(length=255), nullable=False),
        sa.Column("discount_percentage", sa.Integer(), nullable=False),
        sa.Column("max_customers", sa.Integer(), nullable=False),
        sa.Column("max_days", sa.Integer(), nullable=False),
        sa.Column("message_template", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["merchant_id"], ["merchants.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_merchant_campaigns_merchant", "merchant_campaigns", ["merchant_id"])


def downgrade() -> None:
    op.drop_index("ix_merchant_campaigns_merchant", table_name="merchant_campaigns")
    op.drop_table("merchant_campaigns")
