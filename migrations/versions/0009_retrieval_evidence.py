"""Add normalized durable accepted-retrieval evidence.

Revision ID: 0009_retrieval_evidence
Revises: 0008_provider_readiness
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0009_retrieval_evidence"
down_revision = "0008_provider_readiness"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "retrieval_evidence_plans",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("operation_id", sa.String(128), nullable=False, unique=True),
        sa.Column("cache_fingerprint", sa.String(128), nullable=False),
        sa.Column("execution_cohort", sa.String(128), nullable=False),
        sa.Column("plan_json", sa.Text(), nullable=False),
        sa.Column("source_fingerprint", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_table(
        "retrieval_evidence_provider_batches",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("plan_id", sa.String(32), sa.ForeignKey("retrieval_evidence_plans.id"), nullable=False),
        sa.Column("provider", sa.String(64), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("batch_json", sa.Text(), nullable=False),
        sa.UniqueConstraint("plan_id", "provider", "ordinal", name="uq_retrieval_evidence_batch"),
    )
    op.create_table(
        "retrieval_evidence_provider_attempts",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("batch_id", sa.String(32), sa.ForeignKey("retrieval_evidence_provider_batches.id"), nullable=False),
        sa.Column("attempt_ref", sa.String(128), nullable=False, unique=True),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("attempt_json", sa.Text(), nullable=False),
        sa.UniqueConstraint("batch_id", "ordinal", name="uq_retrieval_evidence_attempt"),
    )
    op.create_table(
        "retrieval_evidence_observations",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("plan_id", sa.String(32), sa.ForeignKey("retrieval_evidence_plans.id"), nullable=False),
        sa.Column("observation_ref", sa.String(128), nullable=False, unique=True),
        sa.Column("observation_json", sa.Text(), nullable=False),
    )
    op.create_table(
        "retrieval_evidence_clusters",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("plan_id", sa.String(32), sa.ForeignKey("retrieval_evidence_plans.id"), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("cluster_json", sa.Text(), nullable=False),
        sa.UniqueConstraint("plan_id", "ordinal", name="uq_retrieval_evidence_cluster"),
    )
    op.create_table(
        "retrieval_evidence_contributions",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("cluster_id", sa.String(32), sa.ForeignKey("retrieval_evidence_clusters.id"), nullable=False),
        sa.Column("attempt_id", sa.String(32), sa.ForeignKey("retrieval_evidence_provider_attempts.id"), nullable=False),
        sa.Column("contribution_json", sa.Text(), nullable=False),
        sa.UniqueConstraint("cluster_id", "attempt_id", name="uq_retrieval_evidence_contribution"),
    )
    op.create_table(
        "retrieval_evidence_readiness_decisions",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("plan_id", sa.String(32), sa.ForeignKey("retrieval_evidence_plans.id"), nullable=False),
        sa.Column("provider", sa.String(64), nullable=False),
        sa.Column("decision_json", sa.Text(), nullable=False),
        sa.UniqueConstraint("plan_id", "provider", name="uq_retrieval_evidence_readiness"),
    )
    op.create_table(
        "retrieval_evidence_cache_lineage",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("plan_id", sa.String(32), sa.ForeignKey("retrieval_evidence_plans.id"), nullable=False, unique=True),
        sa.Column("cache_fingerprint", sa.String(128), nullable=False),
        sa.Column("execution_cohort", sa.String(128), nullable=False),
        sa.Column("lineage_json", sa.Text(), nullable=False),
    )
    op.create_table(
        "retrieval_evidence_accounting",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("plan_id", sa.String(32), sa.ForeignKey("retrieval_evidence_plans.id"), nullable=False, unique=True),
        sa.Column("accounting_json", sa.Text(), nullable=False),
    )
    op.create_table(
        "retrieval_evidence_trace_refs",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("attempt_id", sa.String(32), sa.ForeignKey("retrieval_evidence_provider_attempts.id"), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("trace_ref", sa.String(256), nullable=False),
        sa.UniqueConstraint("attempt_id", "ordinal", name="uq_retrieval_evidence_trace"),
    )
    op.create_table(
        "accepted_retrieval_operations",
        sa.Column("receipt_ref", sa.String(128), primary_key=True),
        sa.Column("plan_id", sa.String(32), sa.ForeignKey("retrieval_evidence_plans.id"), nullable=False, unique=True),
        sa.Column("operation_id", sa.String(128), nullable=False, unique=True),
        sa.Column("acceptance_fingerprint", sa.String(64), nullable=False),
        sa.Column("outcome", sa.String(64), nullable=False),
        sa.Column("accepted_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("receipt_ref", "plan_id", name="uq_accepted_retrieval_receipt_plan"),
    )
    op.create_table(
        "retrieval_cache_publications",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("receipt_ref", sa.String(128), sa.ForeignKey("accepted_retrieval_operations.receipt_ref"), nullable=False, unique=True),
        sa.Column("cache_fingerprint", sa.String(128), nullable=False),
        sa.Column("execution_cohort", sa.String(128), nullable=False),
        sa.Column("published_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("cache_fingerprint", "execution_cohort", name="uq_retrieval_cache_publication"),
    )


def downgrade() -> None:
    bind = op.get_bind()
    accepted_count = bind.execute(
        sa.text("SELECT COUNT(*) FROM accepted_retrieval_operations")
    ).scalar_one()
    if accepted_count:
        raise RuntimeError(
            "accepted retrieval evidence exists; export/reconcile it before downgrade"
        )

    # This revision owns only the following objects; legacy rows are untouched.
    op.drop_table("retrieval_cache_publications")
    op.drop_table("accepted_retrieval_operations")
    op.drop_table("retrieval_evidence_trace_refs")
    op.drop_table("retrieval_evidence_accounting")
    op.drop_table("retrieval_evidence_cache_lineage")
    op.drop_table("retrieval_evidence_readiness_decisions")
    op.drop_table("retrieval_evidence_contributions")
    op.drop_table("retrieval_evidence_clusters")
    op.drop_table("retrieval_evidence_observations")
    op.drop_table("retrieval_evidence_provider_attempts")
    op.drop_table("retrieval_evidence_provider_batches")
    op.drop_table("retrieval_evidence_plans")
