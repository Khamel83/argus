"""Add immutable extraction outcome and composition storage.

Revision ID: 0007_extraction_outcomes
Revises: 0006_maya_outbox
"""

from __future__ import annotations

from alembic import context, op
import sqlalchemy as sa

revision = "0007_extraction_outcomes"
down_revision = "0006_maya_outbox"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "extraction_outcome_plans",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("plan_ref", sa.String(128), nullable=False),
        sa.Column("extraction_run_id", sa.String(64), nullable=False, unique=True),
        sa.Column("request_id", sa.String(64), nullable=False),
        sa.Column("normalized_url", sa.Text(), nullable=False),
        sa.Column("access_scope", sa.String(128), nullable=False),
        sa.Column("mode", sa.String(64), nullable=False),
        sa.Column("plan_json", sa.Text(), nullable=False),
        sa.Column("source_fingerprint", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_table(
        "extraction_outcome_steps",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column(
            "plan_id",
            sa.String(32),
            sa.ForeignKey("extraction_outcome_plans.id"),
            nullable=False,
        ),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("extractor", sa.String(64), nullable=False),
        sa.Column("decision", sa.String(32), nullable=False),
        sa.Column("attempt_outcome", sa.String(64), nullable=True),
        sa.Column("latency_ms", sa.BigInteger(), nullable=True),
        sa.Column("provenance_json", sa.Text(), nullable=True),
        sa.Column("spend_json", sa.Text(), nullable=True),
        sa.Column("policy_rule_ref", sa.String(128), nullable=True),
        sa.UniqueConstraint(
            "plan_id",
            "ordinal",
            name="uq_extraction_outcome_steps_plan_ordinal",
        ),
    )
    op.create_table(
        "extraction_artifact_identities",
        sa.Column("artifact_ref", sa.String(128), primary_key=True),
        sa.Column("content_identity", sa.String(128), nullable=False),
        sa.Column("content_text", sa.Text(), nullable=False),
        sa.Column("evaluation_json", sa.Text(), nullable=False),
    )
    op.create_table(
        "extraction_outcome_artifacts",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column(
            "plan_id",
            sa.String(32),
            sa.ForeignKey("extraction_outcome_plans.id"),
            nullable=False,
            unique=True,
        ),
        sa.Column(
            "artifact_ref",
            sa.String(128),
            sa.ForeignKey(
                "extraction_artifact_identities.artifact_ref",
                name="fk_extraction_outcome_artifacts_identity",
            ),
            nullable=False,
        ),
        sa.Column("content_identity", sa.String(128), nullable=False),
        sa.Column("content_text", sa.Text(), nullable=False),
        sa.Column("disposition", sa.String(32), nullable=False),
        sa.Column("quality_passed", sa.Boolean(), nullable=True),
        sa.Column("is_complete", sa.Boolean(), nullable=True),
        sa.Column("evaluation_json", sa.Text(), nullable=False),
        sa.UniqueConstraint("id", "plan_id"),
    )
    op.create_index(
        "ix_extraction_outcome_artifacts_artifact_ref",
        "extraction_outcome_artifacts",
        ["artifact_ref"],
    )
    op.create_table(
        "extraction_outcome_rejections",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column(
            "plan_id",
            sa.String(32),
            sa.ForeignKey("extraction_outcome_plans.id"),
            nullable=False,
            unique=True,
        ),
        sa.Column("rejection_ref", sa.String(128), nullable=False),
        sa.Column("code", sa.String(64), nullable=False),
        sa.Column("provider", sa.String(64), nullable=True),
        sa.Column("recommended_action", sa.String(64), nullable=False),
        sa.Column("projection_json", sa.Text(), nullable=False),
        sa.UniqueConstraint("id", "plan_id"),
    )
    op.create_table(
        "extraction_outcome_acceptances",
        sa.Column("receipt_ref", sa.String(128), primary_key=True),
        sa.Column(
            "plan_id",
            sa.String(32),
            sa.ForeignKey("extraction_outcome_plans.id"),
            nullable=False,
            unique=True,
        ),
        sa.Column("outcome", sa.String(32), nullable=False),
        sa.Column("artifact_disposition", sa.String(32), nullable=False),
        sa.Column("outcome_policy_version", sa.String(128), nullable=False),
        sa.Column("projection_json", sa.Text(), nullable=False),
        sa.Column("acceptance_fingerprint", sa.String(64), nullable=False),
        sa.Column("accepted_at", sa.DateTime(), nullable=False),
        sa.Column("scope", sa.String(64), nullable=False),
        sa.UniqueConstraint("receipt_ref", "plan_id"),
    )
    op.create_table(
        "retrieval_compositions",
        sa.Column("receipt_ref", sa.String(128), primary_key=True),
        sa.Column("retrieval_acceptance_ref", sa.String(128), nullable=False),
        sa.Column("requirement_ref", sa.String(128), nullable=True),
        sa.Column("retrieval_outcome", sa.String(32), nullable=False),
        sa.Column("artifact_outcome", sa.String(32), nullable=True),
        sa.Column("composite_outcome", sa.String(32), nullable=False),
        sa.Column("projection_json", sa.Text(), nullable=False),
        sa.Column("source_fingerprint", sa.String(64), nullable=False, unique=True),
        sa.Column("accepted_at", sa.DateTime(), nullable=False),
    )
    op.create_table(
        "result_extraction_links",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column(
            "composition_ref",
            sa.String(128),
            sa.ForeignKey("retrieval_compositions.receipt_ref"),
            nullable=False,
        ),
        sa.Column("result_cluster_ref", sa.String(128), nullable=False),
        sa.Column(
            "extraction_acceptance_ref",
            sa.String(128),
            nullable=True,
        ),
        sa.Column(
            "extraction_plan_id",
            sa.String(32),
            nullable=True,
        ),
        sa.Column(
            "artifact_row_id",
            sa.String(32),
            nullable=True,
        ),
        sa.Column("artifact_plan_id", sa.String(32), nullable=True),
        sa.Column(
            "rejection_row_id",
            sa.String(32),
            nullable=True,
        ),
        sa.Column("rejection_plan_id", sa.String(32), nullable=True),
        sa.Column("reuse_origin", sa.String(128), nullable=True),
        sa.ForeignKeyConstraint(
            ["extraction_acceptance_ref", "extraction_plan_id"],
            [
                "extraction_outcome_acceptances.receipt_ref",
                "extraction_outcome_acceptances.plan_id",
            ],
            match="FULL",
            name="fk_result_extraction_links_acceptance_plan",
        ),
        sa.ForeignKeyConstraint(
            ["artifact_row_id", "artifact_plan_id"],
            [
                "extraction_outcome_artifacts.id",
                "extraction_outcome_artifacts.plan_id",
            ],
            match="FULL",
            name="fk_result_extraction_links_artifact_plan",
        ),
        sa.ForeignKeyConstraint(
            ["rejection_row_id", "rejection_plan_id"],
            [
                "extraction_outcome_rejections.id",
                "extraction_outcome_rejections.plan_id",
            ],
            match="FULL",
            name="fk_result_extraction_links_rejection_plan",
        ),
        sa.CheckConstraint(
            "(extraction_acceptance_ref IS NULL) = "
            "(extraction_plan_id IS NULL)",
            name="ck_result_extraction_links_acceptance_pair",
        ),
        sa.CheckConstraint(
            "(artifact_row_id IS NULL) = (artifact_plan_id IS NULL)",
            name="ck_result_extraction_links_artifact_pair",
        ),
        sa.CheckConstraint(
            "(rejection_row_id IS NULL) = (rejection_plan_id IS NULL)",
            name="ck_result_extraction_links_rejection_pair",
        ),
        sa.CheckConstraint(
            "artifact_plan_id IS NULL OR "
            "artifact_plan_id = extraction_plan_id",
            name="ck_result_extraction_links_artifact_same_plan",
        ),
        sa.CheckConstraint(
            "rejection_plan_id IS NULL OR "
            "rejection_plan_id = extraction_plan_id",
            name="ck_result_extraction_links_rejection_same_plan",
        ),
        sa.UniqueConstraint(
            "composition_ref",
            "result_cluster_ref",
            name="uq_result_extraction_links_composition_cluster",
        ),
    )
    op.create_table(
        "extraction_outcome_activations",
        sa.Column("receipt_ref", sa.String(128), primary_key=True),
        sa.Column("activated_at", sa.DateTime(), nullable=False),
    )


def downgrade() -> None:
    if context.is_offline_mode():
        raise RuntimeError(
            "0007 downgrade requires an online activation-receipt check"
        )
    connection = op.get_bind()
    activation_count = connection.execute(
        sa.text("SELECT COUNT(*) FROM extraction_outcome_activations")
    ).scalar_one()
    if activation_count:
        raise RuntimeError(
            "0007 downgrade refused after an extraction outcome activation receipt"
        )

    op.drop_table("extraction_outcome_activations")
    op.drop_table("result_extraction_links")
    op.drop_table("retrieval_compositions")
    op.drop_table("extraction_outcome_acceptances")
    op.drop_table("extraction_outcome_rejections")
    op.drop_table("extraction_outcome_artifacts")
    op.drop_table("extraction_artifact_identities")
    op.drop_table("extraction_outcome_steps")
    op.drop_table("extraction_outcome_plans")
