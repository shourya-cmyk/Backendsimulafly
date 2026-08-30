"""add six-character support ticket reference

Revision ID: n1o2p3q4r5s6
Revises: m1n2o3p4q5r6
Create Date: 2026-08-30
"""

from typing import Sequence, Union
import secrets
import string

import sqlalchemy as sa
from alembic import op


revision: str = "n1o2p3q4r5s6"
down_revision: Union[str, Sequence[str], None] = "m1n2o3p4q5r6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_ALPHABET = string.ascii_uppercase + string.digits


def _reference(used: set[str]) -> str:
    while True:
        candidate = "".join(secrets.choice(_ALPHABET) for _ in range(6))
        if candidate not in used:
            used.add(candidate)
            return candidate


def upgrade() -> None:
    op.add_column("support_tickets", sa.Column("reference", sa.String(length=6), nullable=True))

    connection = op.get_bind()
    rows = connection.execute(sa.text("SELECT id FROM support_tickets")).fetchall()
    used: set[str] = set()
    for row in rows:
        connection.execute(
            sa.text("UPDATE support_tickets SET reference = :reference WHERE id = :id"),
            {"reference": _reference(used), "id": row[0]},
        )

    op.alter_column("support_tickets", "reference", nullable=False)
    op.create_index(
        "ix_support_tickets_reference",
        "support_tickets",
        ["reference"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("ix_support_tickets_reference", table_name="support_tickets")
    op.drop_column("support_tickets", "reference")
