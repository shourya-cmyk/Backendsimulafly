"""change campaign product column

Revision ID: f8a9b0c1d2e3
Revises: e3a4b5c6d7e8
Create Date: 2026-06-18

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'f8a9b0c1d2e3'
down_revision: Union[str, None] = 'e3a4b5c6d7e8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("merchant_campaigns", sa.Column("product_names", sa.Text(), nullable=True))
    # Fill in dummy values if there's any existing row (database is empty for campaigns anyway)
    op.execute("UPDATE merchant_campaigns SET product_names = product_name WHERE product_names IS NULL")
    op.alter_column("merchant_campaigns", "product_names", nullable=False)
    op.drop_column("merchant_campaigns", "product_name")


def downgrade() -> None:
    op.add_column("merchant_campaigns", sa.Column("product_name", sa.String(length=255), nullable=True))
    op.execute("UPDATE merchant_campaigns SET product_name = SUBSTRING(product_names FROM 1 FOR 255) WHERE product_name IS NULL")
    op.alter_column("merchant_campaigns", "product_name", nullable=False)
    op.drop_column("merchant_campaigns", "product_names")
