"""allow prompt-only image generation jobs

Revision ID: k1l2m3n4o5p6
Revises: 828bc4cfb45b, j1k2l3m4n5o6
Create Date: 2026-08-07
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "k1l2m3n4o5p6"
down_revision: Union[str, Sequence[str], None] = (
    "828bc4cfb45b",
    "j1k2l3m4n5o6",
)
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column(
        "visualize_jobs",
        "room_image_id",
        existing_type=sa.UUID(),
        nullable=True,
    )


def downgrade() -> None:
    op.alter_column(
        "visualize_jobs",
        "room_image_id",
        existing_type=sa.UUID(),
        nullable=False,
    )
