"""create wallets, transactions, pricing_rules + backfill

Revision ID: 0009
Revises: 0008
Create Date: 2026-05-22
"""
from typing import Sequence, Union
import uuid

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0009"
down_revision: Union[str, None] = "0008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── wallets (1:1 with merchant) ────────────────────────────────────────
    op.create_table(
        "wallets",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "merchant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("merchants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("balance", sa.Numeric(14, 4), nullable=False, server_default="0"),
        sa.Column("currency", sa.String(3), nullable=False, server_default="INR"),
        sa.Column("status", sa.String(16), nullable=False, server_default="active"),
        sa.Column(
            "low_balance_threshold",
            sa.Numeric(14, 4),
            nullable=False,
            server_default="500",
        ),
        sa.Column(
            "last_recharged_at",
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
    op.create_unique_constraint("uq_wallets_merchant", "wallets", ["merchant_id"])

    # ── transactions (money IN — top-ups) ──────────────────────────────────
    op.create_table(
        "transactions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "merchant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("merchants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("amount", sa.Numeric(14, 4), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False, server_default="INR"),
        sa.Column("payment_method", sa.String(32), nullable=True),
        sa.Column("gateway", sa.String(16), nullable=False, server_default="razorpay"),
        sa.Column("gateway_ref", sa.String(120), nullable=True),
        sa.Column("razorpay_order_id", sa.String(120), nullable=True),
        sa.Column("razorpay_signature", sa.String(256), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("failure_reason", sa.Text(), nullable=True),
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
    op.create_unique_constraint(
        "uq_transactions_gateway_ref", "transactions", ["gateway_ref"]
    )
    op.create_index(
        "ix_transactions_merchant_created",
        "transactions",
        ["merchant_id", "created_at"],
    )
    op.create_index(
        "ix_transactions_razorpay_order", "transactions", ["razorpay_order_id"]
    )

    # ── pricing_rules (per-event-type rates) ───────────────────────────────
    op.create_table(
        "pricing_rules",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("event_type", sa.String(32), nullable=False),
        sa.Column(
            "merchant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("merchants.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("rate", sa.Numeric(14, 4), nullable=False),
        sa.Column("rate_type", sa.String(16), nullable=False, server_default="fixed"),
        sa.Column("currency", sa.String(3), nullable=False, server_default="INR"),
        sa.Column(
            "effective_from",
            postgresql.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "effective_until",
            postgresql.TIMESTAMP(timezone=True),
            nullable=True,
        ),
        sa.Column("notes", sa.Text(), nullable=True),
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
    op.create_index(
        "ix_pricing_rules_lookup",
        "pricing_rules",
        ["event_type", "merchant_id", "effective_from"],
    )

    # ── Backfill: one wallet per existing merchant ─────────────────────────
    conn = op.get_bind()
    rows = conn.execute(sa.text("SELECT id FROM merchants")).fetchall()
    for row in rows:
        conn.execute(
            sa.text(
                "INSERT INTO wallets (id, merchant_id, balance, currency, status, "
                "low_balance_threshold, created_at, updated_at) "
                "VALUES (:id, :mid, 0, 'INR', 'active', 500, now(), now())"
            ),
            {"id": str(uuid.uuid4()), "mid": str(row[0])},
        )

    # ── Seed default pricing rules (global, merchant_id=NULL) ──────────────
    default_rates = [
        ("impression", 0.000, "fixed"),
        ("ai_rag_mention", 0.50, "fixed"),
        ("click", 0.25, "fixed"),
        ("ai_image_generation", 2.00, "fixed"),
        ("external_redirect", 5.00, "fixed"),
        ("simulafly_purchase", 5.00, "percentage"),
        ("lead_unlocked", 50.00, "fixed"),
    ]
    for event_type, rate, rate_type in default_rates:
        conn.execute(
            sa.text(
                "INSERT INTO pricing_rules (id, event_type, merchant_id, rate, "
                "rate_type, currency, effective_from, created_at, updated_at) "
                "VALUES (:id, :et, NULL, :rate, :rt, 'INR', now(), now(), now())"
            ),
            {
                "id": str(uuid.uuid4()),
                "et": event_type,
                "rate": rate,
                "rt": rate_type,
            },
        )


def downgrade() -> None:
    op.drop_index("ix_pricing_rules_lookup", table_name="pricing_rules")
    op.drop_table("pricing_rules")

    op.drop_index("ix_transactions_razorpay_order", table_name="transactions")
    op.drop_index("ix_transactions_merchant_created", table_name="transactions")
    op.drop_constraint("uq_transactions_gateway_ref", "transactions", type_="unique")
    op.drop_table("transactions")

    op.drop_constraint("uq_wallets_merchant", "wallets", type_="unique")
    op.drop_table("wallets")
