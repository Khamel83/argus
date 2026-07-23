"""Add crash-safe provider spending.

Revision ID: 0005_provider_spend
Revises: 0004_operation_ledger
"""

from alembic import op
import sqlalchemy as sa

revision = "0005_provider_spend"
down_revision = "0004_operation_ledger"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "provider_spend_attempts",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("idempotency_key", sa.String(255), nullable=False, unique=True),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("provider", sa.String(50), nullable=False),
        sa.Column("tier", sa.Integer(), nullable=False),
        sa.Column("is_paid", sa.Boolean(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("outcome", sa.String(100), nullable=True),
        sa.Column("reserved_charge", sa.Float(), nullable=False),
        sa.Column("actual_charge", sa.Float(), nullable=True),
        sa.Column("usage", sa.Float(), nullable=False),
        sa.Column("caller_identity", sa.String(100), nullable=False),
        sa.Column("caller_label", sa.String(100), nullable=False),
        sa.Column("resolution_source", sa.String(32), nullable=True),
        sa.Column("resolution_reference", sa.String(32), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index(
        "ix_provider_spend_attempts_provider",
        "provider_spend_attempts",
        ["provider"],
    )
    op.create_table(
        "provider_balance_snapshots",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("provider", sa.String(50), nullable=False),
        sa.Column("balance", sa.Float(), nullable=False),
        sa.Column("source", sa.String(32), nullable=False),
        sa.Column("observed_at", sa.DateTime(), nullable=False),
        sa.Column("actor_identity", sa.String(100), nullable=False),
        sa.Column("idempotency_key", sa.String(255), nullable=False, unique=True),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index(
        "ix_provider_balance_snapshots_provider",
        "provider_balance_snapshots",
        ["provider"],
    )
    op.create_table(
        "provider_spend_audit",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("attempt_id", sa.String(32), nullable=True),
        sa.Column("provider", sa.String(50), nullable=False),
        sa.Column("action", sa.String(32), nullable=False),
        sa.Column("actor_identity", sa.String(100), nullable=False),
        sa.Column("idempotency_key", sa.String(255), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("before_json", sa.Text(), nullable=True),
        sa.Column("after_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint(
            "action",
            "idempotency_key",
            name="uq_spend_audit_action_key",
        ),
    )


def downgrade() -> None:
    op.drop_table("provider_spend_audit")
    op.drop_index(
        "ix_provider_balance_snapshots_provider",
        table_name="provider_balance_snapshots",
    )
    op.drop_table("provider_balance_snapshots")
    op.drop_index(
        "ix_provider_spend_attempts_provider",
        table_name="provider_spend_attempts",
    )
    op.drop_table("provider_spend_attempts")
