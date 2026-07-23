"""Persist request routing fields.

Revision ID: 0003_request_routing_fields
Revises: 0002_acceptance_fingerprint
"""

from alembic import op
import sqlalchemy as sa

revision = "0003_request_routing_fields"
down_revision = "0002_acceptance_fingerprint"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "retrieval_requests",
        sa.Column("providers_json", sa.Text(), nullable=True),
    )
    op.add_column(
        "retrieval_requests",
        sa.Column(
            "free_only",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )


def downgrade() -> None:
    op.drop_column("retrieval_requests", "free_only")
    op.drop_column("retrieval_requests", "providers_json")
