"""partner_id shared per owner (drop unique) + backfill

Revision ID: i1j2k3l4m5n6
Revises: h1i2j3k4l5m6
Create Date: 2026-06-29

Establishes the partner → shops hierarchy:

  * ``partner_id`` is the partner/owner identifier and is now SHARED by every
    shop a single user owns, so its UNIQUE constraint is dropped (kept as a
    plain index for lookups). ``shop_id`` stays unique per shop.
  * Backfills existing rows so all shops owned by the same user adopt the
    ``partner_id`` of that owner's oldest (primary) shop.
"""
from typing import Sequence, Union

from alembic import op

revision: str = "i1j2k3l4m5n6"
down_revision: Union[str, None] = "h1i2j3k4l5m6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1) Drop the UNIQUE constraint + its index so partner_id can repeat across
    #    the shops of one owner.
    op.drop_constraint("uq_merchants_partner_id", "merchants", type_="unique")
    op.drop_index("ix_merchants_partner_id", table_name="merchants")

    # 2) Backfill: every shop owned by a user takes the partner_id of that
    #    owner's oldest (primary) shop. Owners are identified via
    #    merchant_members.role = 'owner'.
    op.execute(
        """
        WITH owner_primary AS (
            SELECT DISTINCT ON (mm.user_id)
                   mm.user_id,
                   m.partner_id AS primary_partner_id
              FROM merchant_members mm
              JOIN merchants m ON m.id = mm.merchant_id
             WHERE mm.role = 'owner'
               AND m.partner_id IS NOT NULL
             ORDER BY mm.user_id, m.created_at ASC
        )
        UPDATE merchants AS m
           SET partner_id = op.primary_partner_id
          FROM merchant_members mm
          JOIN owner_primary op ON op.user_id = mm.user_id
         WHERE mm.merchant_id = m.id
           AND mm.role = 'owner'
           AND m.partner_id IS DISTINCT FROM op.primary_partner_id
        """
    )

    # 3) Recreate a NON-unique index for partner_id lookups.
    op.create_index("ix_merchants_partner_id", "merchants", ["partner_id"])


def downgrade() -> None:
    # Best-effort reverse: restore the unique index/constraint. This will fail
    # if any partner_id is shared by more than one shop (which is the whole
    # point of this migration), so de-duplicate before downgrading.
    op.drop_index("ix_merchants_partner_id", table_name="merchants")
    op.create_index("ix_merchants_partner_id", "merchants", ["partner_id"])
    op.create_unique_constraint("uq_merchants_partner_id", "merchants", ["partner_id"])
