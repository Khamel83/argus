"""Create the atomic search ledger.

Revision ID: 0001_search_ledger
Revises:
"""

from alembic import op
import sqlalchemy as sa

revision = "0001_search_ledger"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "retrieval_requests",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("query_text", sa.Text(), nullable=False),
        sa.Column("mode", sa.String(50), nullable=False),
        sa.Column("max_results", sa.Integer(), nullable=False),
        sa.Column("caller", sa.String(100), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_table(
        "retrieval_runs",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column(
            "request_id",
            sa.String(32),
            sa.ForeignKey("retrieval_requests.id"),
            nullable=False,
            unique=True,
        ),
        sa.Column("search_run_id", sa.String(64), nullable=False, unique=True),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("total_results", sa.Integer(), nullable=False),
        sa.Column("cached", sa.Boolean(), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("committed_at", sa.DateTime(), nullable=False),
    )
    op.create_table(
        "provider_attempts",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column(
            "run_id",
            sa.String(32),
            sa.ForeignKey("retrieval_runs.id"),
            nullable=False,
        ),
        sa.Column("provider", sa.String(50), nullable=False),
        sa.Column("status", sa.String(50), nullable=False),
        sa.Column("results_count", sa.Integer(), nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("budget_remaining", sa.Float(), nullable=True),
        sa.Column("egress", sa.String(50), nullable=False),
    )
    op.create_table(
        "content_identities",
        sa.Column("content_hash", sa.String(64), primary_key=True),
        sa.Column("canonical_url", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_table(
        "normalized_results",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column(
            "run_id",
            sa.String(32),
            sa.ForeignKey("retrieval_runs.id"),
            nullable=False,
        ),
        sa.Column(
            "content_hash",
            sa.String(64),
            sa.ForeignKey("content_identities.content_hash"),
            nullable=False,
        ),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("snippet", sa.Text(), nullable=False),
        sa.Column("domain", sa.String(255), nullable=False),
        sa.Column("provider", sa.String(50), nullable=False),
        sa.Column("score", sa.Float(), nullable=False),
        sa.Column("final_rank", sa.Integer(), nullable=False),
        sa.UniqueConstraint("run_id", "final_rank"),
    )
    op.create_table(
        "result_provenance",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column(
            "result_id",
            sa.String(32),
            sa.ForeignKey("normalized_results.id"),
            nullable=False,
            unique=True,
        ),
        sa.Column("provider", sa.String(50), nullable=False),
        sa.Column("egress", sa.String(50), nullable=True),
        sa.Column("machine", sa.String(100), nullable=True),
        sa.Column("source_type", sa.String(50), nullable=False),
        sa.Column("metadata_json", sa.Text(), nullable=False),
    )
    op.create_table(
        "delivery_intents",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column(
            "run_id",
            sa.String(32),
            sa.ForeignKey("retrieval_runs.id"),
            nullable=False,
            unique=True,
        ),
        sa.Column("destination", sa.String(50), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("delivery_intents")
    op.drop_table("result_provenance")
    op.drop_table("normalized_results")
    op.drop_table("content_identities")
    op.drop_table("provider_attempts")
    op.drop_table("retrieval_runs")
    op.drop_table("retrieval_requests")
