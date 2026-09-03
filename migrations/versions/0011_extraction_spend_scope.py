"""Bind spend attempts to extraction operation, account, release, and deadline.

Revision ID: 0011_extraction_spend_scope
Revises: 0010_domain_policies
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0011_extraction_spend_scope"
down_revision = "0010_domain_policies"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add nullable columns first so existing rows can be backfilled before the
    # identity fields become mandatory.  ``execution_deadline`` remains
    # nullable for historical direct-search attempts that predate this scope.
    with op.batch_alter_table("provider_spend_attempts") as batch:
        batch.add_column(sa.Column("operation_id", sa.String(128), nullable=True))
        batch.add_column(
            sa.Column("account_fingerprint", sa.String(128), nullable=True)
        )
        batch.add_column(
            sa.Column("release_identity", sa.String(128), nullable=True)
        )
        batch.add_column(sa.Column("execution_deadline", sa.DateTime(), nullable=True))

    bind = op.get_bind()
    bind.execute(
        sa.text(
            "UPDATE provider_spend_attempts "
            "SET operation_id = idempotency_key, "
            "account_fingerprint = 'legacy-account', "
            "release_identity = 'unknown-release' "
            "WHERE operation_id IS NULL"
        )
    )
    with op.batch_alter_table("provider_spend_attempts") as batch:
        batch.alter_column(
            "operation_id",
            existing_type=sa.String(128),
            nullable=False,
            server_default="legacy-operation",
        )
        batch.alter_column(
            "account_fingerprint",
            existing_type=sa.String(128),
            nullable=False,
            server_default="legacy-account",
        )
        batch.alter_column(
            "release_identity",
            existing_type=sa.String(128),
            nullable=False,
            server_default="unknown-release",
        )
    op.create_index(
        "ix_provider_spend_attempts_account",
        "provider_spend_attempts",
        ["provider", "account_fingerprint"],
    )


def downgrade() -> None:
    # Development-only compatibility rollback.  Production promotion never
    # invokes an automatic database downgrade.
    op.drop_index("ix_provider_spend_attempts_account", table_name="provider_spend_attempts")
    with op.batch_alter_table("provider_spend_attempts") as batch:
        batch.drop_column("execution_deadline")
        batch.drop_column("release_identity")
        batch.drop_column("account_fingerprint")
        batch.drop_column("operation_id")
