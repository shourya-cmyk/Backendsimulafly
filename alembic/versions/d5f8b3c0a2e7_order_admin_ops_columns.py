"""order admin-ops columns (dispute, fulfillment, soft-delete)

Additive, nullable / server-defaulted columns on the existing `orders` table.
Server defaults are used so existing rows are backfilled by Postgres without a
separate data migration or downtime.

Revision ID: d5f8b3c0a2e7
Revises: c4e7a2b9f1d6
Create Date: 2026-05-23
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "d5f8b3c0a2e7"
down_revision: Union[str, None] = "c4e7a2b9f1d6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "orders",
        sa.Column(
            "dispute_status",
            sa.String(16),
            nullable=False,
            server_default="none",
        ),
    )
    op.add_column(
        "orders",
        sa.Column("dispute_reason", sa.Text(), nullable=True),
    )
    op.add_column(
        "orders",
        sa.Column("dispute_resolution", sa.Text(), nullable=True),
    )
    op.add_column(
        "orders",
        sa.Column(
            "fulfillment_status",
            sa.String(16),
            nullable=False,
            server_default="pending",
        ),
    )
    op.add_column(
        "orders",
        sa.Column("deleted_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("orders", "deleted_at")
    op.drop_column("orders", "fulfillment_status")
    op.drop_column("orders", "dispute_resolution")
    op.drop_column("orders", "dispute_reason")
    op.drop_column("orders", "dispute_status")
