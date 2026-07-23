"""Add restart-safe Maya delivery outbox.

Revision ID: 0006_maya_outbox
Revises: 0005_provider_spend
"""

from __future__ import annotations

import hashlib
import json

from alembic import context, op
import sqlalchemy as sa

revision = "0006_maya_outbox"
down_revision = "0005_provider_spend"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("delivery_intents") as batch:
        batch.alter_column("run_id", existing_type=sa.String(32), nullable=True)
        batch.alter_column("payload_json", existing_type=sa.Text(), nullable=True)
        batch.add_column(sa.Column("extraction_run_id", sa.String(32), nullable=True))
        batch.create_foreign_key(
            "fk_delivery_intents_extraction_run_id",
            "extraction_runs",
            ["extraction_run_id"],
            ["id"],
        )
        batch.create_unique_constraint(
            "uq_delivery_intents_extraction_run_id",
            ["extraction_run_id"],
        )
        batch.add_column(sa.Column("payload_sha256", sa.String(64), nullable=True))
        batch.add_column(
            sa.Column(
                "content_sha256",
                sa.String(64),
                nullable=True,
            )
        )
        batch.add_column(
            sa.Column(
                "attempt_count",
                sa.Integer(),
                nullable=False,
                server_default="0",
            )
        )
        batch.add_column(
            sa.Column(
                "max_attempts",
                sa.Integer(),
                nullable=False,
                server_default="8",
            )
        )
        batch.add_column(sa.Column("next_attempt_at", sa.DateTime(), nullable=True))
        batch.add_column(sa.Column("lease_expires_at", sa.DateTime(), nullable=True))
        batch.add_column(sa.Column("lease_token", sa.String(32), nullable=True))
        batch.add_column(sa.Column("last_attempt_at", sa.DateTime(), nullable=True))
        batch.add_column(sa.Column("delivered_at", sa.DateTime(), nullable=True))
        batch.add_column(sa.Column("updated_at", sa.DateTime(), nullable=True))
        batch.add_column(sa.Column("last_error_code", sa.String(64), nullable=True))
        batch.add_column(sa.Column("last_error_summary", sa.String(256), nullable=True))
        batch.add_column(sa.Column("response_json", sa.Text(), nullable=True))
        batch.add_column(
            sa.Column("payload_compacted_at", sa.DateTime(), nullable=True)
        )
        batch.create_check_constraint(
            "ck_delivery_intents_one_parent",
            "(run_id IS NOT NULL AND extraction_run_id IS NULL) OR "
            "(run_id IS NULL AND extraction_run_id IS NOT NULL)",
        )

    delivery_intents = sa.table(
        "delivery_intents",
        sa.column("id", sa.String(32)),
        sa.column("status", sa.String(32)),
        sa.column("payload_json", sa.Text()),
        sa.column("payload_sha256", sa.String(64)),
        sa.column("attempt_count", sa.Integer()),
        sa.column("max_attempts", sa.Integer()),
        sa.column("next_attempt_at", sa.DateTime()),
        sa.column("updated_at", sa.DateTime()),
        sa.column("payload_compacted_at", sa.DateTime()),
        sa.column("created_at", sa.DateTime()),
    )
    if context.is_offline_mode():
        op.execute(
            sa.text(
                "UPDATE delivery_intents SET "
                "status = 'suppressed', "
                "payload_sha256 = encode("
                "sha256(convert_to(COALESCE(payload_json, ''), 'UTF8')), 'hex'"
                "), "
                "payload_json = NULL, "
                "attempt_count = 0, "
                "max_attempts = 8, "
                "next_attempt_at = created_at, "
                "updated_at = created_at, "
                "payload_compacted_at = created_at"
            )
        )
    else:
        connection = op.get_bind()
        historical = connection.execute(
            sa.select(
                delivery_intents.c.id,
                delivery_intents.c.payload_json,
                delivery_intents.c.created_at,
            )
        ).all()
        for intent_id, payload_json, created_at in historical:
            raw_payload = payload_json or ""
            connection.execute(
                delivery_intents.update()
                .where(delivery_intents.c.id == intent_id)
                .values(
                    status="suppressed",
                    payload_json=None,
                    payload_sha256=hashlib.sha256(
                        raw_payload.encode("utf-8")
                    ).hexdigest(),
                    attempt_count=0,
                    max_attempts=8,
                    next_attempt_at=created_at,
                    updated_at=created_at,
                    payload_compacted_at=created_at,
                )
            )

    with op.batch_alter_table("delivery_intents") as batch:
        batch.alter_column(
            "payload_sha256",
            existing_type=sa.String(64),
            nullable=False,
        )
        batch.alter_column(
            "next_attempt_at",
            existing_type=sa.DateTime(),
            nullable=False,
        )
        batch.alter_column(
            "updated_at",
            existing_type=sa.DateTime(),
            nullable=False,
        )
    op.create_index(
        "ix_delivery_intents_dispatch",
        "delivery_intents",
        ["destination", "status", "next_attempt_at"],
    )


def downgrade() -> None:
    delivery_intents = sa.table(
        "delivery_intents",
        sa.column("id", sa.String(32)),
        sa.column("run_id", sa.String(32)),
        sa.column("extraction_run_id", sa.String(32)),
        sa.column("destination", sa.String(50)),
        sa.column("status", sa.String(32)),
        sa.column("payload_json", sa.Text()),
    )
    retrieval_runs = sa.table(
        "retrieval_runs",
        sa.column("id", sa.String(32)),
        sa.column("search_run_id", sa.String(64)),
        sa.column("total_results", sa.Integer()),
    )
    if context.is_offline_mode():
        op.execute(
            sa.text(
                "UPDATE delivery_intents AS delivery SET "
                "status = 'pending', "
                "payload_json = jsonb_build_object("
                "'result_count', run.total_results, "
                "'search_run_id', run.search_run_id"
                ")::text "
                "FROM retrieval_runs AS run "
                "WHERE run.id = delivery.run_id"
            )
        )
        op.execute(
            sa.text("DELETE FROM delivery_intents WHERE extraction_run_id IS NOT NULL")
        )
    else:
        connection = op.get_bind()
        search_rows = connection.execute(
            sa.select(
                delivery_intents.c.id,
                retrieval_runs.c.search_run_id,
                retrieval_runs.c.total_results,
            ).join(
                retrieval_runs,
                retrieval_runs.c.id == delivery_intents.c.run_id,
            )
        ).all()
        for intent_id, search_run_id, total_results in search_rows:
            placeholder = json.dumps(
                {
                    "search_run_id": search_run_id,
                    "result_count": total_results,
                },
                sort_keys=True,
                separators=(",", ":"),
            )
            connection.execute(
                delivery_intents.update()
                .where(delivery_intents.c.id == intent_id)
                .values(status="pending", payload_json=placeholder)
            )
        connection.execute(
            delivery_intents.delete().where(
                delivery_intents.c.extraction_run_id.is_not(None)
            )
        )

    op.drop_index("ix_delivery_intents_dispatch", table_name="delivery_intents")
    with op.batch_alter_table("delivery_intents") as batch:
        batch.drop_constraint(
            "ck_delivery_intents_one_parent",
            type_="check",
        )
        batch.drop_constraint(
            "uq_delivery_intents_extraction_run_id",
            type_="unique",
        )
        batch.drop_constraint(
            "fk_delivery_intents_extraction_run_id",
            type_="foreignkey",
        )
        batch.drop_column("payload_compacted_at")
        batch.drop_column("response_json")
        batch.drop_column("last_error_summary")
        batch.drop_column("last_error_code")
        batch.drop_column("updated_at")
        batch.drop_column("delivered_at")
        batch.drop_column("last_attempt_at")
        batch.drop_column("lease_token")
        batch.drop_column("lease_expires_at")
        batch.drop_column("next_attempt_at")
        batch.drop_column("max_attempts")
        batch.drop_column("attempt_count")
        batch.drop_column("content_sha256")
        batch.drop_column("payload_sha256")
        batch.drop_column("extraction_run_id")
        batch.alter_column(
            "payload_json",
            existing_type=sa.Text(),
            nullable=False,
        )
        batch.alter_column(
            "run_id",
            existing_type=sa.String(32),
            nullable=False,
        )
