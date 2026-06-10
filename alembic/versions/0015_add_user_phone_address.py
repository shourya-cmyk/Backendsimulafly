"""add user phone and address fields

Revision ID: 0015
Revises: 0014
Create Date: 2026-06-02

"""
from alembic import op
import sqlalchemy as sa

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("phone", sa.String(20), nullable=True))
    op.add_column("users", sa.Column("address_line1", sa.String(255), nullable=True))
    op.add_column("users", sa.Column("city", sa.String(100), nullable=True))
    op.add_column("users", sa.Column("state", sa.String(100), nullable=True))
    op.add_column("users", sa.Column("pincode", sa.String(20), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "pincode")
    op.drop_column("users", "state")
    op.drop_column("users", "city")
    op.drop_column("users", "address_line1")
    op.drop_column("users", "phone")
