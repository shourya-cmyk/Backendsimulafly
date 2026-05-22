"""create buyer_events + ledger_entries + dedup; swap merchant_products.embedding to pgvector

Revision ID: 0010
Revises: 0009
Create Date: 2026-05-22
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0010"
down_revision: Union[str, None] = "0009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── buyer_events ───────────────────────────────────────────────────────
    op.create_table(
        "buyer_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "merchant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("merchants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "merchant_product_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("merchant_products.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("event_type", sa.String(32), nullable=False),
        sa.Column(
            "context",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("user_session_id", sa.String(120), nullable=True),
        sa.Column("client_ip", postgresql.INET(), nullable=True),
        sa.Column("billed", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column(
            "created_at",
            postgresql.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index(
        "ix_buyer_events_merchant_created",
        "buyer_events",
        ["merchant_id", "created_at"],
    )
    op.create_index(
        "ix_buyer_events_product_created",
        "buyer_events",
        ["merchant_product_id", "created_at"],
    )
    op.create_index(
        "ix_buyer_events_user_created", "buyer_events", ["user_id", "created_at"]
    )

    # ── ledger_entries ─────────────────────────────────────────────────────
    op.create_table(
        "ledger_entries",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "merchant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("merchants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "wallet_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("wallets.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "related_event_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("buyer_events.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "related_txn_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("transactions.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("entry_type", sa.String(16), nullable=False),
        sa.Column("amount", sa.Numeric(14, 4), nullable=False),
        sa.Column("reason", sa.String(64), nullable=False),
        sa.Column("balance_after", sa.Numeric(14, 4), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            postgresql.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index(
        "ix_ledger_entries_merchant_created",
        "ledger_entries",
        ["merchant_id", "created_at"],
    )

    # ── buyer_event_dedup (per spec §2.5 B1) ──────────────────────────────
    op.create_table(
        "buyer_event_dedup",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("event_type", sa.String(32), nullable=False),
        sa.Column("user_session_id", sa.String(120), nullable=False),
        sa.Column("merchant_product_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("hour_bucket", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            postgresql.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_unique_constraint(
        "uq_buyer_event_dedup",
        "buyer_event_dedup",
        ["event_type", "user_session_id", "merchant_product_id", "hour_bucket"],
    )

    # ── Swap merchant_products.embedding from ARRAY(Float) to Vector(3072) ─
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.drop_column("merchant_products", "embedding")
    op.execute("ALTER TABLE merchant_products ADD COLUMN embedding vector(3072)")
    # HNSW supports max 2000 dims directly; for vector(3072) cast to halfvec at
    # index time (pgvector >= 0.7.0 / installed: 0.8.2).
    op.execute(
        "CREATE INDEX ix_merchant_products_embedding_hnsw "
        "ON merchant_products USING hnsw ((embedding::halfvec(3072)) halfvec_cosine_ops)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_merchant_products_embedding_hnsw")
    op.drop_column("merchant_products", "embedding")
    op.execute("ALTER TABLE merchant_products ADD COLUMN embedding REAL[]")

    op.drop_constraint("uq_buyer_event_dedup", "buyer_event_dedup", type_="unique")
    op.drop_table("buyer_event_dedup")

    op.drop_index("ix_ledger_entries_merchant_created", table_name="ledger_entries")
    op.drop_table("ledger_entries")

    op.drop_index("ix_buyer_events_user_created", table_name="buyer_events")
    op.drop_index("ix_buyer_events_product_created", table_name="buyer_events")
    op.drop_index("ix_buyer_events_merchant_created", table_name="buyer_events")
    op.drop_table("buyer_events")
