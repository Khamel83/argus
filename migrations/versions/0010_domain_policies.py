"""Add canonical domain policy state and durable command events.

Revision ID: 0010_domain_policies
Revises: 0009_retrieval_evidence
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0010_domain_policies"
down_revision = "0009_retrieval_evidence"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "domain_policies",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("domain", sa.String(255), nullable=False, unique=True),
        sa.Column(
            "prefer_residential_search",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        sa.Column(
            "prefer_residential_extraction",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        sa.Column(
            "datacenter_failure_count",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "residential_success_count",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column("last_datacenter_failure", sa.DateTime(), nullable=True),
        sa.Column("last_residential_success", sa.DateTime(), nullable=True),
        sa.Column("failure_reason", sa.Text(), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("version", sa.Integer(), nullable=False, server_default="0"),
    )
    op.create_index("ix_domain_policies_domain", "domain_policies", ["domain"])
    op.create_table(
        "domain_policy_events",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("event_identity", sa.String(255), nullable=False),
        sa.Column("domain", sa.String(255), nullable=False),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("result_json", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint(
            "event_identity",
            name="uq_domain_policy_event_identity",
        ),
    )
    op.create_index(
        "ix_domain_policy_events_domain",
        "domain_policy_events",
        ["domain"],
    )


def downgrade() -> None:
    # This downgrade is retained for isolated development databases only.
    op.drop_index(
        "ix_domain_policy_events_domain",
        table_name="domain_policy_events",
    )
    op.drop_table("domain_policy_events")
    op.drop_index("ix_domain_policies_domain", table_name="domain_policies")
    op.drop_table("domain_policies")
