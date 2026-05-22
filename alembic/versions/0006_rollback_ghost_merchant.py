"""rollback ghost merchant tables

Revision ID: 0006
Revises: b5cfded5c7a2
Create Date: 2026-05-22

Drops the auto-generated tables from b5cfded5c7a2 that don't match the
locked spec (merchants/leads/merchant_products/lead_items + a few
spurious timestamp type changes). Phase 1's real schema is in revision 0007.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0006"
down_revision: Union[str, None] = "b5cfded5c7a2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Use IF EXISTS for idempotency in case the ghost was never applied locally.
    for table in ("lead_items", "merchant_products", "leads", "merchants"):
        op.execute(f"DROP TABLE IF EXISTS {table} CASCADE")

    # Best-effort restore of TIMESTAMP(timezone=True) the ghost stripped.
    # Wrapped to ignore failures (DB may already have correct types).
    for table, cols in [
        ("cart_items", ["added_at", "updated_at"]),
        ("design_sessions", ["created_at", "updated_at"]),
        ("messages", ["created_at"]),
        ("notifications", ["created_at"]),
        ("products", ["created_at"]),
        ("room_images", ["created_at"]),
        ("saved_items", ["added_at"]),
        ("styles", ["created_at", "updated_at"]),
        ("users", ["created_at", "updated_at"]),
    ]:
        for col in cols:
            try:
                op.alter_column(
                    table, col,
                    existing_type=sa.DateTime(),
                    type_=postgresql.TIMESTAMP(timezone=True),
                    existing_nullable=False,
                    existing_server_default=sa.text("now()"),
                )
            except Exception:
                pass  # column may already be correct type

    # Restore the indexes the ghost dropped, idempotently.
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_notifications_user_unread_created "
        "ON notifications (user_id, unread, created_at)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_saved_user_added "
        "ON saved_items (user_id, added_at)"
    )


def downgrade() -> None:
    # Intentional no-op: ghost tables are abandoned, not coming back.
    pass
