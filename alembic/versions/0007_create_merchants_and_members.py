"""create merchants and merchant_members

Revision ID: 0007
Revises: 0006
Create Date: 2026-05-22
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0007"
down_revision: Union[str, None] = "0006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "merchants",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("slug", sa.String(120), nullable=False),
        sa.Column("legal_name", sa.String(255), nullable=False),
        sa.Column("display_name", sa.String(255), nullable=False),
        sa.Column("logo_url", sa.String(1024), nullable=True),
        sa.Column("support_email", sa.String(255), nullable=True),
        sa.Column("support_phone", sa.String(50), nullable=True),
        sa.Column("country", sa.String(2), nullable=False, server_default="IN"),
        sa.Column(
            "status",
            sa.String(32),
            nullable=False,
            server_default="active",
        ),  # active|suspended|trial
        sa.Column("settings", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("referral_code", sa.String(40), nullable=False),
        sa.Column(
            "created_at",
            postgresql.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            postgresql.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_unique_constraint("uq_merchants_slug", "merchants", ["slug"])
    op.create_unique_constraint("uq_merchants_referral_code", "merchants", ["referral_code"])
    op.create_index("ix_merchants_status", "merchants", ["status"])

    op.create_table(
        "merchant_members",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "merchant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("merchants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("role", sa.String(16), nullable=False),  # owner|admin|staff
        sa.Column(
            "invited_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "joined_at",
            postgresql.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_unique_constraint(
        "uq_merchant_members_merchant_user", "merchant_members", ["merchant_id", "user_id"]
    )
    op.create_index("ix_merchant_members_merchant_id", "merchant_members", ["merchant_id"])
    op.create_index("ix_merchant_members_user_id", "merchant_members", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_merchant_members_user_id", table_name="merchant_members")
    op.drop_index("ix_merchant_members_merchant_id", table_name="merchant_members")
    op.drop_constraint("uq_merchant_members_merchant_user", "merchant_members", type_="unique")
    op.drop_table("merchant_members")

    op.drop_index("ix_merchants_status", table_name="merchants")
    op.drop_constraint("uq_merchants_referral_code", "merchants", type_="unique")
    op.drop_constraint("uq_merchants_slug", "merchants", type_="unique")
    op.drop_table("merchants")
