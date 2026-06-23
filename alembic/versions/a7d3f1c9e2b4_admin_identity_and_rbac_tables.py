"""admin identity and rbac tables

Revision ID: a7d3f1c9e2b4
Revises: f8a9b0c1d2e3
Create Date: 2026-05-22
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "a7d3f1c9e2b4"
down_revision: Union[str, None] = "f8a9b0c1d2e3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── admin_accounts ─────────────────────────────────────────────────────────
    op.create_table(
        "admin_accounts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("email", sa.String(255), nullable=False),
        sa.Column("hashed_password", sa.String(255), nullable=True),
        sa.Column("full_name", sa.String(255), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("failed_login_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("locked_until", postgresql.TIMESTAMP(timezone=True), nullable=True),
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
        sa.Column("deleted_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
    )
    op.create_index("ix_admin_accounts_email", "admin_accounts", ["email"], unique=True)

    # ── admin_roles ────────────────────────────────────────────────────────────
    op.create_table(
        "admin_roles",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(120), nullable=False, unique=True),
        sa.Column("is_predefined", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("description", sa.Text(), nullable=True),
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

    # ── admin_permissions ──────────────────────────────────────────────────────
    op.create_table(
        "admin_permissions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("key", sa.String(120), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
    )
    op.create_index("ix_admin_permissions_key", "admin_permissions", ["key"], unique=True)

    # ── role_permissions (association) ──────────────────────────────────────────
    op.create_table(
        "role_permissions",
        sa.Column(
            "role_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("admin_roles.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "permission_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("admin_permissions.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.UniqueConstraint("role_id", "permission_id", name="uq_role_permissions_pair"),
    )

    # ── admin_account_roles (association) ───────────────────────────────────────
    op.create_table(
        "admin_account_roles",
        sa.Column(
            "admin_account_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("admin_accounts.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "role_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("admin_roles.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.UniqueConstraint(
            "admin_account_id", "role_id", name="uq_admin_account_roles_pair"
        ),
    )

    # ── admin_invitations ───────────────────────────────────────────────────────
    op.create_table(
        "admin_invitations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("email", sa.String(255), nullable=False),
        sa.Column("token_hash", sa.String(255), nullable=False),
        sa.Column(
            "role_ids",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("expires_at", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("accepted_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.Column(
            "created_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("admin_accounts.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            postgresql.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index("ix_admin_invitations_email", "admin_invitations", ["email"])

    # ── admin_refresh_tokens ────────────────────────────────────────────────────
    op.create_table(
        "admin_refresh_tokens",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "admin_account_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("admin_accounts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("session_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("token_hash", sa.String(255), nullable=False),
        sa.Column("revoked", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("expires_at", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            postgresql.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index(
        "ix_admin_refresh_tokens_account", "admin_refresh_tokens", ["admin_account_id"]
    )
    op.create_index(
        "ix_admin_refresh_tokens_session", "admin_refresh_tokens", ["session_id"]
    )

    # ── admin_audit_logs (insert-only) ──────────────────────────────────────────
    op.create_table(
        "admin_audit_logs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "actor_admin_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("admin_accounts.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("action", sa.String(120), nullable=False),
        sa.Column("target_type", sa.String(120), nullable=False),
        sa.Column("target_id", sa.String(120), nullable=True),
        sa.Column("outcome", sa.String(32), nullable=False),
        sa.Column(
            "metadata",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "created_at",
            postgresql.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index("ix_admin_audit_logs_actor", "admin_audit_logs", ["actor_admin_id"])
    op.create_index("ix_admin_audit_logs_created", "admin_audit_logs", ["created_at"])
    op.create_index(
        "ix_admin_audit_logs_target", "admin_audit_logs", ["target_type", "target_id"]
    )

    # ── fraud_alerts ────────────────────────────────────────────────────────────
    op.create_table(
        "fraud_alerts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("subject_type", sa.String(64), nullable=False),
        sa.Column("subject_id", sa.String(120), nullable=True),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="open"),
        sa.Column(
            "resolved_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("admin_accounts.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("resolved_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            postgresql.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index("ix_fraud_alerts_status", "fraud_alerts", ["status"])
    op.create_index(
        "ix_fraud_alerts_subject", "fraud_alerts", ["subject_type", "subject_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_fraud_alerts_subject", table_name="fraud_alerts")
    op.drop_index("ix_fraud_alerts_status", table_name="fraud_alerts")
    op.drop_table("fraud_alerts")

    op.drop_index("ix_admin_audit_logs_target", table_name="admin_audit_logs")
    op.drop_index("ix_admin_audit_logs_created", table_name="admin_audit_logs")
    op.drop_index("ix_admin_audit_logs_actor", table_name="admin_audit_logs")
    op.drop_table("admin_audit_logs")

    op.drop_index("ix_admin_refresh_tokens_session", table_name="admin_refresh_tokens")
    op.drop_index("ix_admin_refresh_tokens_account", table_name="admin_refresh_tokens")
    op.drop_table("admin_refresh_tokens")

    op.drop_index("ix_admin_invitations_email", table_name="admin_invitations")
    op.drop_table("admin_invitations")

    op.drop_table("admin_account_roles")
    op.drop_table("role_permissions")

    op.drop_index("ix_admin_permissions_key", table_name="admin_permissions")
    op.drop_table("admin_permissions")

    op.drop_table("admin_roles")

    op.drop_index("ix_admin_accounts_email", table_name="admin_accounts")
    op.drop_table("admin_accounts")
