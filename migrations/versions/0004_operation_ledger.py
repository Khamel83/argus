"""Persist extraction and multi-turn session operations.

Revision ID: 0004_operation_ledger
Revises: 0003_request_routing_fields
"""

from alembic import op
import sqlalchemy as sa

revision = "0004_operation_ledger"
down_revision = "0003_request_routing_fields"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "extraction_runs",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("extraction_run_id", sa.String(64), nullable=False, unique=True),
        sa.Column("request_url", sa.Text(), nullable=False),
        sa.Column("domain", sa.String(255), nullable=True),
        sa.Column("mode", sa.String(50), nullable=False),
        sa.Column("caller", sa.String(100), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("selected_extractor", sa.String(64), nullable=True),
        sa.Column(
            "content_hash",
            sa.String(64),
            sa.ForeignKey("content_identities.content_hash"),
            nullable=True,
        ),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("author", sa.Text(), nullable=False),
        sa.Column("published_date", sa.String(64), nullable=True),
        sa.Column("word_count", sa.Integer(), nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=False),
        sa.Column("quality_passed", sa.Boolean(), nullable=False),
        sa.Column("quality_reason", sa.String(255), nullable=True),
        sa.Column("error_summary", sa.String(256), nullable=True),
        sa.Column("acceptance_fingerprint", sa.String(64), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("committed_at", sa.DateTime(), nullable=False),
    )
    op.create_table(
        "extractor_attempts",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column(
            "run_id",
            sa.String(32),
            sa.ForeignKey("extraction_runs.id"),
            nullable=False,
        ),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("extractor", sa.String(64), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=False),
        sa.Column("failure_summary", sa.String(256), nullable=True),
        sa.UniqueConstraint("run_id", "ordinal"),
    )
    op.create_table(
        "extraction_artifacts",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column(
            "run_id",
            sa.String(32),
            sa.ForeignKey("extraction_runs.id"),
            nullable=False,
            unique=True,
        ),
        sa.Column("canonical_url", sa.Text(), nullable=False),
        sa.Column(
            "content_hash",
            sa.String(64),
            sa.ForeignKey("content_identities.content_hash"),
            nullable=True,
        ),
        sa.Column("source_type", sa.String(50), nullable=True),
        sa.Column("egress", sa.String(50), nullable=True),
        sa.Column("machine", sa.String(100), nullable=True),
        sa.Column("auth_used", sa.Boolean(), nullable=False),
        sa.Column("cookies_used", sa.Boolean(), nullable=False),
        sa.Column("archive_used", sa.Boolean(), nullable=False),
        sa.Column("cost", sa.Float(), nullable=False),
        sa.Column("metadata_json", sa.Text(), nullable=False),
    )
    op.create_table(
        "retrieval_sessions",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_table(
        "session_queries",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column(
            "session_id",
            sa.String(64),
            sa.ForeignKey("retrieval_sessions.id"),
            nullable=False,
        ),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("query_text", sa.Text(), nullable=False),
        sa.Column("mode", sa.String(50), nullable=False),
        sa.Column("queried_at", sa.DateTime(), nullable=False),
        sa.Column("results_count", sa.Integer(), nullable=False),
        sa.UniqueConstraint("session_id", "ordinal"),
    )
    op.create_table(
        "session_extracted_urls",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column(
            "query_id",
            sa.String(32),
            sa.ForeignKey("session_queries.id"),
            nullable=False,
        ),
        sa.Column("url", sa.Text(), nullable=False),
        sa.UniqueConstraint("query_id", "url"),
    )


def downgrade() -> None:
    op.drop_table("session_extracted_urls")
    op.drop_table("session_queries")
    op.drop_table("retrieval_sessions")
    op.drop_table("extraction_artifacts")
    op.drop_table("extractor_attempts")
    op.drop_table("extraction_runs")
