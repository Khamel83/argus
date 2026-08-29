"""Add the domain_policies table backing residential-preference memory.

The DomainPolicyRow model has existed in argus/persistence/models.py without a
corresponding migration. SQLite installs get the table from
Base.metadata.create_all(); Postgres does not, because init_db() defers schema
to Alembic there. So on Postgres the table was simply absent.

Effect: any extraction passing a domain hit
_extract_url_unpersisted -> DomainMemory.should_prefer_residential ->
get_policy(), which queried a table that did not exist:

    psycopg2.errors.UndefinedTable: relation "domain_policies" does not exist

The failure surfaced only as 503 "Extraction could not be durably recorded",
because a bare except in operations/accepted.py discarded the cause.

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
        sa.Column("prefer_residential_search", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("prefer_residential_extraction", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("datacenter_failure_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("residential_success_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_datacenter_failure", sa.DateTime(), nullable=True),
        sa.Column("last_residential_success", sa.DateTime(), nullable=True),
        sa.Column("failure_reason", sa.Text(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_domain_policies_domain", "domain_policies", ["domain"])


def downgrade() -> None:
    op.drop_index("ix_domain_policies_domain", table_name="domain_policies")
    op.drop_table("domain_policies")
