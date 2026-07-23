"""Add immutable durable acceptance fingerprints.

Revision ID: 0002_acceptance_fingerprint
Revises: 0001_search_ledger
"""

from alembic import op
import sqlalchemy as sa

revision = "0002_acceptance_fingerprint"
down_revision = "0001_search_ledger"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "retrieval_runs",
        sa.Column("acceptance_fingerprint", sa.String(64), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("retrieval_runs", "acceptance_fingerprint")
