"""create_visualize_jobs

Revision ID: 4abe9d7b6092
Revises: d2b8748359b5
Create Date: 2026-06-12 17:37:45.637786

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = '4abe9d7b6092'
down_revision: Union[str, None] = 'd2b8748359b5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('visualize_jobs',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('session_id', sa.UUID(), nullable=False),
    sa.Column('product_id', sa.UUID(), nullable=True),
    sa.Column('room_image_id', sa.UUID(), nullable=False),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('image_id', sa.UUID(), nullable=True),
    sa.Column('message_id', sa.UUID(), nullable=True),
    sa.Column('error', sa.String(length=500), nullable=True),
    sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['image_id'], ['room_images.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['message_id'], ['messages.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['room_image_id'], ['room_images.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['session_id'], ['design_sessions.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_visualize_jobs_session_id'), 'visualize_jobs', ['session_id'], unique=False)
    op.create_index(op.f('ix_visualize_jobs_user_id'), 'visualize_jobs', ['user_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_visualize_jobs_user_id'), table_name='visualize_jobs')
    op.drop_index(op.f('ix_visualize_jobs_session_id'), table_name='visualize_jobs')
    op.drop_table('visualize_jobs')
