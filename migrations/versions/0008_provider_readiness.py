"""Add the durable provider-readiness authority.

Revision ID: 0008_provider_readiness
Revises: 0007_extraction_outcomes
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0008_provider_readiness"
down_revision = "0007_extraction_outcomes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "provider_readiness_observations",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("provider", sa.String(50), nullable=False),
        sa.Column("dimension", sa.String(32), nullable=False),
        sa.Column("state", sa.String(64), nullable=False),
        sa.Column("source", sa.String(64), nullable=False),
        sa.Column("scope_key", sa.String(64), nullable=False),
        sa.Column("scope_json", sa.Text(), nullable=False),
        sa.Column("producer_observed_at", sa.DateTime(), nullable=False),
        sa.Column("ingested_at", sa.DateTime(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=True),
        sa.Column("evidence_ref", sa.String(128), nullable=True),
        sa.Column("safe_reason", sa.String(160), nullable=True),
        sa.Column(
            "protected", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
        sa.UniqueConstraint(
            "provider",
            "scope_key",
            "dimension",
            name="uq_provider_readiness_current_dimension",
        ),
    )
    op.create_index(
        "ix_provider_readiness_observations_provider",
        "provider_readiness_observations",
        ["provider"],
    )
    op.create_index(
        "ix_provider_readiness_observations_scope_key",
        "provider_readiness_observations",
        ["scope_key"],
    )
    op.create_table(
        "provider_readiness_snapshots",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("provider", sa.String(50), nullable=False),
        sa.Column("scope_key", sa.String(64), nullable=False),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column("snapshot_json", sa.Text(), nullable=False),
        sa.Column("valid_until", sa.DateTime(), nullable=True),
        sa.Column("materialized_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint(
            "provider",
            "scope_key",
            name="uq_provider_readiness_snapshot_scope",
        ),
    )
    op.create_index(
        "ix_provider_readiness_snapshots_provider",
        "provider_readiness_snapshots",
        ["provider"],
    )
    op.create_table(
        "provider_readiness_evidence_refs",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("provider", sa.String(50), nullable=False),
        sa.Column(
            "observation_id",
            sa.String(32),
            nullable=False,
        ),
        sa.Column("evidence_ref", sa.String(128), nullable=False),
        sa.Column(
            "protected", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint(
            "provider",
            "evidence_ref",
            name="uq_provider_readiness_evidence_ref",
        ),
    )
    op.create_index(
        "ix_provider_readiness_evidence_refs_provider",
        "provider_readiness_evidence_refs",
        ["provider"],
    )
    op.create_table(
        "provider_readiness_leases",
        sa.Column("scope_key", sa.String(128), primary_key=True),
        sa.Column("owner", sa.String(64), nullable=False),
        sa.Column("fencing_token", sa.Integer(), nullable=False),
        sa.Column("attempt_id", sa.String(64), nullable=False),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("execution_deadline", sa.DateTime(), nullable=False),
        sa.Column("outcome", sa.String(64), nullable=True),
        sa.Column(
            "uncertain_charge",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
    )
    op.create_table(
        "provider_readiness_alert_dedupe",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("provider", sa.String(50), nullable=False),
        sa.Column("account_fingerprint", sa.String(128), nullable=False),
        sa.Column("alert_kind", sa.String(64), nullable=False),
        sa.Column("emitted_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint(
            "provider",
            "account_fingerprint",
            "alert_kind",
            name="uq_provider_readiness_alert",
        ),
    )


def downgrade() -> None:
    bind = op.get_bind()
    evidence_tables = (
        "provider_readiness_observations",
        "provider_readiness_snapshots",
        "provider_readiness_evidence_refs",
        "provider_readiness_leases",
        "provider_readiness_alert_dedupe",
    )
    for table in evidence_tables:
        count = bind.execute(sa.text(f"SELECT COUNT(*) FROM {table}")).scalar_one()
        if count:
            raise RuntimeError(
                "provider readiness evidence exists; export/reconcile it before downgrade"
            )

    op.drop_table("provider_readiness_alert_dedupe")
    op.drop_table("provider_readiness_leases")
    op.drop_index(
        "ix_provider_readiness_evidence_refs_provider",
        table_name="provider_readiness_evidence_refs",
    )
    op.drop_table("provider_readiness_evidence_refs")
    op.drop_index(
        "ix_provider_readiness_snapshots_provider",
        table_name="provider_readiness_snapshots",
    )
    op.drop_table("provider_readiness_snapshots")
    op.drop_index(
        "ix_provider_readiness_observations_scope_key",
        table_name="provider_readiness_observations",
    )
    op.drop_index(
        "ix_provider_readiness_observations_provider",
        table_name="provider_readiness_observations",
    )
    op.drop_table("provider_readiness_observations")
