"""add_user_credit_balance

Revision ID: d2b8748359b5
Revises: adb1098fa779
Create Date: 2026-06-10 16:35:27.461406

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'd2b8748359b5'
down_revision: Union[str, None] = 'adb1098fa779'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add credit_balance column and drop credits
    op.add_column('users', sa.Column('credit_balance', sa.Float(), nullable=False, server_default='20.0'))
    op.drop_column('users', 'credits')


def downgrade() -> None:
    op.add_column('users', sa.Column('credits', sa.INTEGER(), server_default=sa.text('20'), autoincrement=False, nullable=False))
    op.drop_column('users', 'credit_balance')
