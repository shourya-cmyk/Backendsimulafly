"""add merchant PAN and shop GSTIN verification records

Revision ID: l1m2n3o4p5q6
Revises: k1l2m3n4o5p6
Create Date: 2026-08-10
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "l1m2n3o4p5q6"
down_revision: Union[str, Sequence[str], None] = "k1l2m3n4o5p6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "pan_verifications",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("pan_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("pan_last_four", sa.String(length=4), nullable=False),
        sa.Column("verified_name", sa.String(length=255), nullable=False),
        sa.Column("category", sa.String(length=64), nullable=False),
        sa.Column("provider_transaction_id", sa.String(length=64), nullable=True),
        sa.Column("verified_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("pan_fingerprint", name="uq_pan_verifications_fingerprint"),
        sa.UniqueConstraint("user_id", name="uq_pan_verifications_user_id"),
    )
    op.create_index("ix_pan_verifications_user_id", "pan_verifications", ["user_id"])

    op.create_table(
        "gstin_verifications",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("merchant_id", sa.UUID(), nullable=False),
        sa.Column("gstin", sa.String(length=15), nullable=False),
        sa.Column("legal_name", sa.String(length=255), nullable=False),
        sa.Column("business_nature", sa.String(length=255), nullable=True),
        sa.Column("state_name", sa.String(length=100), nullable=True),
        sa.Column("state_code", sa.String(length=2), nullable=True),
        sa.Column("registration_status", sa.String(length=64), nullable=False),
        sa.Column("registration_start_date", sa.String(length=10), nullable=True),
        sa.Column("provider_transaction_id", sa.String(length=64), nullable=True),
        sa.Column("verified_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["merchant_id"], ["merchants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("merchant_id", name="uq_gstin_verifications_merchant_id"),
    )
    op.create_index("ix_gstin_verifications_gstin", "gstin_verifications", ["gstin"])
    op.create_index(
        "ix_gstin_verifications_merchant_id", "gstin_verifications", ["merchant_id"]
    )

    # The previous completion flag could be set directly by the merchant app.
    # Require every account/shop to pass the provider-backed flow once.
    op.execute("UPDATE merchants SET is_kyc_completed = FALSE")


def downgrade() -> None:
    op.drop_index("ix_gstin_verifications_merchant_id", table_name="gstin_verifications")
    op.drop_index("ix_gstin_verifications_gstin", table_name="gstin_verifications")
    op.drop_table("gstin_verifications")
    op.drop_index("ix_pan_verifications_user_id", table_name="pan_verifications")
    op.drop_table("pan_verifications")
