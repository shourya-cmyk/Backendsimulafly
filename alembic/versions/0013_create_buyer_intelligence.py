"""create merchant_buyer_access and merchant_contacts tables

Revision ID: 0013
Revises: 0012
Create Date: 2026-05-22
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "merchant_buyer_access",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("merchant_id", UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", UUID(as_uuid=True), nullable=False),
        sa.Column("unlock_cost", sa.Numeric(10, 2), nullable=False, server_default="30"),
        sa.Column("unlocked_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["merchant_id"], ["merchants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("merchant_id", "user_id", name="uq_merchant_buyer_access"),
    )
    op.create_index("ix_merchant_buyer_access_merchant", "merchant_buyer_access", ["merchant_id"])

    op.create_table(
        "merchant_contacts",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("merchant_id", UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("phone", sa.String(30), nullable=True),
        sa.Column("email", sa.String(320), nullable=True),
        sa.Column("source", sa.String(32), nullable=False, server_default="manual"),
        sa.Column("last_purchase_note", sa.String(120), nullable=True),
        sa.Column("invite_status", sa.String(32), nullable=False, server_default="not_invited"),
        sa.Column("notes", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["merchant_id"], ["merchants.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_merchant_contacts_merchant", "merchant_contacts", ["merchant_id"])


def downgrade() -> None:
    op.drop_index("ix_merchant_contacts_merchant", table_name="merchant_contacts")
    op.drop_table("merchant_contacts")
    op.drop_index("ix_merchant_buyer_access_merchant", table_name="merchant_buyer_access")
    op.drop_table("merchant_buyer_access")
