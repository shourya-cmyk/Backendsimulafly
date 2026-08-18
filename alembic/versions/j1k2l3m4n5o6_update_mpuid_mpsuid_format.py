"""update partner_id and shop_id columns to VARCHAR(32) for MPUID and MPSUID formats

Revision ID: j1k2l3m4n5o6
Revises: i1j2k3l4m5n6
Create Date: 2026-07-29

Updates:
  - merchants.partner_id: VARCHAR(16) -> VARCHAR(32) to support MPUID (SIM-M-{STATE}-{SEQUENCE}-{CITY})
  - merchants.shop_id: VARCHAR(16) -> VARCHAR(32) to support MPSUID (SIM-S-{MERCHANT_SEQ}-{SHOP_SEQ}-{CITY})
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "j1k2l3m4n5o6"
down_revision: Union[str, None] = "i1j2k3l4m5n6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Alter column lengths from VARCHAR(16) to VARCHAR(32)
    op.alter_column(
        "merchants",
        "partner_id",
        existing_type=sa.String(16),
        type_=sa.String(32),
        existing_nullable=True,
    )
    op.alter_column(
        "merchants",
        "shop_id",
        existing_type=sa.String(16),
        type_=sa.String(32),
        existing_nullable=True,
    )

    # 2. Backfill legacy partner_id to MPUID format: SIM-M-DL-{SEQUENCE}-N
    op.execute("""
        WITH partner_ranks AS (
            SELECT id,
                   DENSE_RANK() OVER (ORDER BY COALESCE(partner_id, id::text), created_at ASC) AS p_seq
            FROM merchants
            WHERE partner_id IS NULL OR partner_id NOT LIKE 'SIM-M-%'
        )
        UPDATE merchants m
        SET partner_id = 'SIM-M-DL-' || LPAD(pr.p_seq::text, 6, '0') || '-N'
        FROM partner_ranks pr
        WHERE m.id = pr.id;
    """)

    # 3. Backfill legacy shop_id to MPSUID format: SIM-S-{MERCHANT_SEQUENCE}-{SHOP_SEQUENCE}-N
    op.execute("""
        WITH shop_ranks AS (
            SELECT id,
                   partner_id,
                   ROW_NUMBER() OVER (PARTITION BY partner_id ORDER BY created_at ASC) AS s_seq
            FROM merchants
            WHERE shop_id IS NULL OR shop_id NOT LIKE 'SIM-S-%'
        )
        UPDATE merchants m
        SET shop_id = 'SIM-S-' || 
            COALESCE(
                CASE WHEN m.partner_id LIKE 'SIM-M-%-%-%' 
                     THEN SPLIT_PART(m.partner_id, '-', 4) 
                     ELSE '000001' 
                END, 
                '000001'
            ) || '-' || LPAD(sr.s_seq::text, 2, '0') || '-N'
        FROM shop_ranks sr
        WHERE m.id = sr.id;
    """)


def downgrade() -> None:
    op.alter_column(
        "merchants",
        "partner_id",
        existing_type=sa.String(32),
        type_=sa.String(16),
        existing_nullable=True,
    )
    op.alter_column(
        "merchants",
        "shop_id",
        existing_type=sa.String(32),
        type_=sa.String(16),
        existing_nullable=True,
    )
