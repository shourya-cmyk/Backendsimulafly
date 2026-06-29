"""add range_km to merchants

Revision ID: h1i2j3k4l5m6
Revises: g1h2i3j4k5l6
Create Date: 2026-06-27

Adds:
  - range_km: service/delivery range in km (optional)
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "h1i2j3k4l5m6"
down_revision: Union[str, None] = "g1h2i3j4k5l6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "merchants",
        sa.Column("range_km", sa.Float(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("merchants", "range_km")
