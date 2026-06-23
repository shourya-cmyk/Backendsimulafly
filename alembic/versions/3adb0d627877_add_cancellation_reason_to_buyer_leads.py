"""add_cancellation_reason_to_buyer_leads

Revision ID: 3adb0d627877
Revises: d5f8b3c0a2e7
Create Date: 2026-06-23 01:47:00.997520

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = '3adb0d627877'
down_revision: Union[str, None] = 'd5f8b3c0a2e7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add cancellation_reason JSONB column to buyer_leads
    op.add_column(
        'buyer_leads',
        sa.Column(
            'cancellation_reason',
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column('buyer_leads', 'cancellation_reason')
