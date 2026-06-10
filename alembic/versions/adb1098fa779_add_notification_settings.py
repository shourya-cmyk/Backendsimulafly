"""add_notification_settings

Revision ID: adb1098fa779
Revises: f7af1058b1ba
Create Date: 2026-06-10 12:41:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'adb1098fa779'
down_revision: Union[str, None] = 'f7af1058b1ba'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('users', sa.Column('push_notifications', sa.Boolean(), nullable=False, server_default=sa.text('true')))
    op.add_column('users', sa.Column('marketing_consent', sa.Boolean(), nullable=False, server_default=sa.text('true')))


def downgrade() -> None:
    op.drop_column('users', 'marketing_consent')
    op.drop_column('users', 'push_notifications')
