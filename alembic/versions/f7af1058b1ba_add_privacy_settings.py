"""add_privacy_settings

Revision ID: f7af1058b1ba
Revises: 0af5b61d6cba
Create Date: 2026-06-10 12:36:31.016362

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'f7af1058b1ba'
down_revision: Union[str, None] = '0af5b61d6cba'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('users', sa.Column('model_improvement_consent', sa.Boolean(), nullable=False, server_default=sa.text('false')))
    op.add_column('users', sa.Column('buyer_signal_sharing', sa.Boolean(), nullable=False, server_default=sa.text('true')))
    op.add_column('users', sa.Column('nominee_name', sa.String(length=255), nullable=True))
    op.add_column('users', sa.Column('nominee_contact', sa.String(length=255), nullable=True))


def downgrade() -> None:
    op.drop_column('users', 'nominee_contact')
    op.drop_column('users', 'nominee_name')
    op.drop_column('users', 'buyer_signal_sharing')
    op.drop_column('users', 'model_improvement_consent')
