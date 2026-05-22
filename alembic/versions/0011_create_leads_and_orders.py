"""create buyer_leads and orders tables

Revision ID: 0011
Revises: 0010
Create Date: 2026-05-22
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0011"
down_revision: Union[str, None] = "0010"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── buyer_leads ───────────────────────────────────────────────────────────
    op.create_table(
        "buyer_leads",
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
        sa.Column("lead_type", sa.String(32), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="new"),
        sa.Column(
            "product_ids",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("ai_interactions_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("ai_generated_image_url", sa.String(1024), nullable=True),
        sa.Column("estimated_value", sa.Numeric(14, 2), nullable=False, server_default="0"),
        sa.Column("delivery_city", sa.String(100), nullable=True),
        sa.Column("delivery_phone", sa.String(50), nullable=True),
        sa.Column("merchant_notes", sa.Text(), nullable=True),
        sa.Column(
            "converted_at",
            postgresql.TIMESTAMP(timezone=True),
            nullable=True,
        ),
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
    op.create_index("ix_buyer_leads_merchant_id", "buyer_leads", ["merchant_id"])
    op.create_index("ix_buyer_leads_user_id", "buyer_leads", ["user_id"])
    op.create_index(
        "ix_buyer_leads_merchant_status_created",
        "buyer_leads",
        ["merchant_id", "status", "created_at"],
    )

    # ── orders ────────────────────────────────────────────────────────────────
    op.create_table(
        "orders",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "lead_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("buyer_leads.id", ondelete="CASCADE"),
            nullable=False,
        ),
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
        sa.Column(
            "status",
            sa.String(32),
            nullable=False,
            server_default="pending_merchant_contact",
        ),
        sa.Column(
            "items",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("total_estimated", sa.Numeric(14, 2), nullable=False, server_default="0"),
        sa.Column(
            "delivery_address",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("merchant_notes", sa.Text(), nullable=True),
        sa.Column(
            "completed_at",
            postgresql.TIMESTAMP(timezone=True),
            nullable=True,
        ),
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
    op.create_index("ix_orders_lead_id", "orders", ["lead_id"])
    op.create_index("ix_orders_merchant_id", "orders", ["merchant_id"])
    op.create_index(
        "ix_orders_merchant_status_created",
        "orders",
        ["merchant_id", "status", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_orders_merchant_status_created", table_name="orders")
    op.drop_index("ix_orders_merchant_id", table_name="orders")
    op.drop_index("ix_orders_lead_id", table_name="orders")
    op.drop_table("orders")

    op.drop_index("ix_buyer_leads_merchant_status_created", table_name="buyer_leads")
    op.drop_index("ix_buyer_leads_user_id", table_name="buyer_leads")
    op.drop_index("ix_buyer_leads_merchant_id", table_name="buyer_leads")
    op.drop_table("buyer_leads")
