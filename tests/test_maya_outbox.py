import json
import hashlib
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from threading import Barrier

import httpx
import pytest
from sqlalchemy import event, func, select

from argus.extraction.models import ExtractedContent, ExtractionAttempt, ExtractorName
from argus.models import SearchQuery
from argus.persistence.search_ledger import DeliveryIntentRow
from tests.test_search_ledger import _response


def _repository(tmp_path):
    from argus.persistence.search_ledger import create_search_ledger_repository

    return create_search_ledger_repository(
        f"sqlite:///{tmp_path / 'maya-outbox.db'}",
        create_schema=True,
    )


def _outbox_row(repository):
    with repository.session_factory() as session:
        return session.scalar(select(DeliveryIntentRow))


def test_search_acceptance_atomically_enqueues_a_bounded_sanitized_maya_parent(
    tmp_path,
):
    repository = _repository(tmp_path)
    response = _response()
    response.results[0].url = "https://example.com/article?token=do-not-store"
    response.results[
        0
    ].snippet = "Authorization: Bearer do-not-store ghp_abcdefghijklmnopqrstuvwxyz"

    receipt = repository.accept(
        SearchQuery(query="find api_key=do-not-store", caller="maya"),
        response,
    )

    row = _outbox_row(repository)
    payload = json.loads(row.payload_json)
    assert row.id == receipt.delivery_intent_id
    assert row.run_id
    assert row.extraction_run_id is None
    assert row.status == "pending"
    assert payload["idempotency_key"] == "run-1"
    assert payload["pages"] == []
    assert len(payload["result_summary"]) <= 16_384
    assert "do-not-store" not in row.payload_json
    assert "ghp_abcdefghijklmnopqrstuvwxyz" not in row.payload_json
    assert "[redacted]" in row.payload_json


def test_new_outbox_preserves_issue34_acceptance_fingerprint_shape():
    from argus.persistence.search_ledger import serialize_acceptance

    query = SearchQuery(query="atomic search")
    response = _response()
    serialized = serialize_acceptance(query, response)
    legacy_state = dict(serialized.state)
    legacy_state["delivery_intent"] = {
        "destination": "maya",
        "status": "pending",
        "payload": {
            "search_run_id": "run-1",
            "result_count": 1,
        },
    }
    expected = hashlib.sha256(
        json.dumps(
            legacy_state,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode()
    ).hexdigest()

    assert serialized.fingerprint == expected


def test_extraction_acceptance_atomically_enqueues_parent_before_ordered_page(tmp_path):
    repository = _repository(tmp_path)
    result = ExtractedContent(
        url="https://example.com/article",
        title="Article",
        text="the extracted page body",
        word_count=5,
        extracted_at=datetime(2026, 7, 23, 12, 0),
        extractor=ExtractorName.TRAFILATURA,
        source_type="live",
        egress="residential",
        machine="homelab",
        attempts=[
            ExtractionAttempt(
                extractor="trafilatura",
                status="success",
                latency_ms=4,
            )
        ],
    )

    receipt = repository.record_extraction(
        url=result.url,
        domain="example.com",
        mode="default",
        caller="maya",
        result=result,
        latency_ms=5,
        extraction_run_id="extract-1",
    )

    row = _outbox_row(repository)
    payload = json.loads(row.payload_json)
    assert receipt.delivery_intent_id == row.id
    assert row.run_id is None
    assert row.extraction_run_id
    assert payload["idempotency_key"] == "extract-1"
    assert [page["position"] for page in payload["pages"]] == [0]
    assert payload["pages"][0]["content"] == result.text
    assert payload["pages"][0]["content_sha256"] == row.content_sha256


def test_extraction_and_maya_outbox_roll_back_as_one_transaction(tmp_path):
    from argus.persistence.search_ledger import ExtractionRunRow

    repository = _repository(tmp_path)

    @event.listens_for(DeliveryIntentRow, "before_insert")
    def fail_outbox_insert(mapper, connection, target):
        raise RuntimeError("injected outbox failure")

    with pytest.raises(RuntimeError, match="injected outbox failure"):
        repository.record_extraction(
            url="https://example.com/rollback",
            domain=None,
            mode="default",
            caller="maya",
            result=ExtractedContent(
                url="https://example.com/rollback",
                text="must roll back",
                word_count=3,
                extractor=ExtractorName.TRAFILATURA,
                attempts=[ExtractionAttempt("trafilatura", "success", 1)],
            ),
            latency_ms=1,
            extraction_run_id="extract-outbox-rollback",
        )

    event.remove(DeliveryIntentRow, "before_insert", fail_outbox_insert)
    with repository.session_factory() as session:
        assert session.scalar(select(func.count()).select_from(ExtractionRunRow)) == 0
        assert session.scalar(select(func.count()).select_from(DeliveryIntentRow)) == 0


def test_transient_failure_is_restart_safe_and_replays_the_same_idempotency_key(
    tmp_path,
):
    from argus.persistence.maya_outbox import MayaOutboxDispatcher

    repository = _repository(tmp_path)
    repository.accept(SearchQuery(query="restart safe"), _response())
    now = datetime(2026, 7, 23, 12, 0)
    requests = []

    def unavailable(request):
        requests.append(json.loads(request.content))
        return httpx.Response(503, json={"detail": "temporarily unavailable"})

    first = MayaOutboxDispatcher(
        repository,
        endpoint="http://maya/api/orchestration/captures/retrievals",
        token="test-token",
        transport=httpx.MockTransport(unavailable),
        clock=lambda: now,
    )
    assert first.run_once() == {"retried": 1}
    assert _outbox_row(repository).status == "retry"

    def accepted(request):
        requests.append(json.loads(request.content))
        return httpx.Response(
            201,
            json={
                "capture_id": "capture-1",
                "caller": "argus",
                "page_ids": [],
                "duplicate": False,
                "children_added": 0,
                "received_at": "2026-07-23T12:01:00Z",
            },
        )

    restarted = MayaOutboxDispatcher(
        repository,
        endpoint="http://maya/api/orchestration/captures/retrievals",
        token="test-token",
        transport=httpx.MockTransport(accepted),
        clock=lambda: now + timedelta(minutes=1),
    )
    assert restarted.run_once() == {"acknowledged": 1}
    row = _outbox_row(repository)
    assert row.status == "acknowledged"
    assert row.attempt_count == 2
    assert requests[0]["idempotency_key"] == requests[1]["idempotency_key"]


def test_lost_ack_duplicate_and_partial_child_replay_are_acknowledged(tmp_path):
    from argus.persistence.maya_outbox import MayaOutboxDispatcher

    repository = _repository(tmp_path)
    result = ExtractedContent(
        url="https://example.com/article",
        text="child body",
        word_count=2,
        extractor=ExtractorName.TRAFILATURA,
        attempts=[ExtractionAttempt("trafilatura", "success", 1)],
    )
    repository.record_extraction(
        url=result.url,
        domain=None,
        mode="default",
        caller="maya",
        result=result,
        latency_ms=1,
        extraction_run_id="partial-child",
    )
    now = datetime(2026, 7, 23, 12, 0)

    first = MayaOutboxDispatcher(
        repository,
        endpoint="http://maya/captures",
        token="test-token",
        transport=httpx.MockTransport(
            lambda request: (_ for _ in ()).throw(httpx.ReadTimeout("lost ack"))
        ),
        clock=lambda: now,
    )
    assert first.run_once() == {"retried": 1}

    def duplicate(request):
        assert len(json.loads(request.content)["pages"]) == 1
        return httpx.Response(
            200,
            json={
                "capture_id": "capture-existing",
                "caller": "argus",
                "page_ids": ["page-existing"],
                "duplicate": True,
                "children_added": 0,
                "received_at": "2026-07-23T12:01:00Z",
            },
        )

    second = MayaOutboxDispatcher(
        repository,
        endpoint="http://maya/captures",
        token="test-token",
        transport=httpx.MockTransport(duplicate),
        clock=lambda: now + timedelta(minutes=1),
    )
    assert second.run_once() == {"acknowledged": 1}
    assert json.loads(_outbox_row(repository).response_json)["duplicate"] is True


def test_expired_delivery_lease_is_reclaimed_after_process_restart(tmp_path):
    from argus.persistence.maya_outbox import MayaOutboxDispatcher

    repository = _repository(tmp_path)
    repository.accept(SearchQuery(query="crash recovery"), _response())
    now = datetime(2026, 7, 23, 12, 0)
    claimed = repository.claim_maya_outbox(now=now, limit=1, lease_seconds=10)
    assert len(claimed) == 1
    assert _outbox_row(repository).status == "delivering"

    restarted = MayaOutboxDispatcher(
        repository,
        endpoint="http://maya/captures",
        token="test-token",
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                json={"capture_id": "capture-1", "duplicate": True},
            )
        ),
        clock=lambda: now + timedelta(seconds=11),
    )

    assert restarted.run_once() == {"acknowledged": 1}
    assert _outbox_row(repository).attempt_count == 2


def test_crash_during_final_attempt_expires_to_recoverable_dead_letter(tmp_path):
    repository = _repository(tmp_path)
    repository.accept(SearchQuery(query="final crash"), _response())
    row = _outbox_row(repository)
    with repository.session_factory.begin() as session:
        session.get(DeliveryIntentRow, row.id).max_attempts = 1
    now = datetime(2026, 7, 23, 12, 0)

    assert repository.claim_maya_outbox(
        now=now,
        limit=1,
        lease_seconds=10,
    )
    assert (
        repository.claim_maya_outbox(
            now=now + timedelta(seconds=11),
            limit=1,
            lease_seconds=10,
        )
        == []
    )

    row = _outbox_row(repository)
    assert row.status == "dead_letter"
    assert row.last_error_code == "retry_exhausted"
    assert repository.recover_maya_dead_letter(
        row.id,
        now=now + timedelta(seconds=12),
    )


def test_permanent_rejection_dead_letters_safely_and_can_be_recovered(tmp_path):
    from argus.persistence.maya_outbox import MayaOutboxDispatcher

    repository = _repository(tmp_path)
    repository.accept(SearchQuery(query="dead letter"), _response())
    now = datetime(2026, 7, 23, 12, 0)
    rejected = MayaOutboxDispatcher(
        repository,
        endpoint="http://maya/captures",
        token="secret-token",
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                422,
                json={
                    "detail": {
                        "code": "invalid_capture_request",
                        "message": (
                            "Authorization: Bearer must-not-persist "
                            "ghp_abcdefghijklmnopqrstuvwxyz"
                        ),
                    }
                },
            )
        ),
        clock=lambda: now,
    )

    assert rejected.run_once() == {"dead_lettered": 1}
    row = _outbox_row(repository)
    assert row.status == "dead_letter"
    assert row.last_error_code == "invalid_capture_request"
    assert "must-not-persist" not in row.last_error_summary
    assert "ghp_abcdefghijklmnopqrstuvwxyz" not in row.last_error_summary
    assert repository.list_maya_dead_letters() == [
        {
            "id": row.id,
            "attempt_count": 1,
            "last_error_code": "invalid_capture_request",
            "last_error_summary": row.last_error_summary,
            "created_at": row.created_at,
            "updated_at": row.updated_at,
        }
    ]

    repository.recover_maya_dead_letter(row.id, now=now + timedelta(minutes=1))
    recovered = _outbox_row(repository)
    assert recovered.status == "pending"
    assert recovered.last_error_code is None


def test_transient_retries_are_bounded_and_exhaust_into_dead_letter(tmp_path):
    from argus.persistence.maya_outbox import MayaOutboxDispatcher

    repository = _repository(tmp_path)
    repository.accept(SearchQuery(query="bounded retries"), _response())
    row = _outbox_row(repository)
    with repository.session_factory.begin() as session:
        session.get(DeliveryIntentRow, row.id).max_attempts = 1
    dispatcher = MayaOutboxDispatcher(
        repository,
        endpoint="http://maya/captures",
        token="test-token",
        transport=httpx.MockTransport(
            lambda request: httpx.Response(503, json={"detail": "unavailable"})
        ),
        clock=lambda: datetime(2026, 7, 23, 12, 0),
    )

    assert dispatcher.run_once() == {"dead_lettered": 1}
    row = _outbox_row(repository)
    assert row.status == "dead_letter"
    assert row.last_error_code == "retry_exhausted"


def test_acknowledged_payload_compaction_is_bounded_and_keeps_audit(tmp_path):
    from argus.persistence.search_ledger import acceptance_fingerprint

    repository = _repository(tmp_path)
    repository.accept(SearchQuery(query="compact"), _response())
    row = _outbox_row(repository)
    old = datetime(2026, 7, 1)
    with repository.session_factory.begin() as session:
        stored = session.get(DeliveryIntentRow, row.id)
        stored.status = "acknowledged"
        stored.delivered_at = old
        stored.response_json = json.dumps({"capture_id": "capture-1"})

    compacted = repository.compact_maya_outbox(
        acknowledged_before=datetime(2026, 7, 8),
        limit=1,
        now=datetime(2026, 7, 23),
    )

    row = _outbox_row(repository)
    assert compacted == 1
    assert row.payload_json is None
    assert row.payload_sha256
    assert row.response_json == json.dumps({"capture_id": "capture-1"})
    assert row.payload_compacted_at == datetime(2026, 7, 23)
    snapshot = repository.load_acceptance_snapshot("run-1")
    assert acceptance_fingerprint(snapshot.state) == snapshot.stored_fingerprint


def test_synthetic_probe_is_operationally_recorded_without_maya_artifact(tmp_path):
    repository = _repository(tmp_path)
    repository.accept(
        SearchQuery(query="deployment canary", caller="deployment-probe"),
        _response(run_id="probe-1"),
    )

    row = _outbox_row(repository)
    assert row.status == "suppressed"
    assert row.payload_json is None


def test_reachability_probe_is_recorded_without_maya_artifact(tmp_path):
    repository = _repository(tmp_path)

    repository.accept(
        SearchQuery(query="provider reachability", caller="argus-reachability"),
        _response(run_id="reachability-probe"),
    )

    row = _outbox_row(repository)
    assert row.status == "suppressed"
    assert row.payload_json is None


def test_outbox_observability_reports_bounded_counts_and_oldest_age(tmp_path):
    repository = _repository(tmp_path)
    repository.accept(SearchQuery(query="observable"), _response())
    row = _outbox_row(repository)
    with repository.session_factory.begin() as session:
        session.get(DeliveryIntentRow, row.id).created_at = datetime(2026, 7, 23, 11, 0)

    status = repository.maya_outbox_status(now=datetime(2026, 7, 23, 12, 0))

    assert status == {
        "counts": {"pending": 1},
        "oldest_pending_age_seconds": 3600,
        "dead_letter_oldest_age_seconds": None,
        "dead_letter_payload_bytes": 0,
    }


def test_maya_capture_config_uses_dedicated_secret_and_bounded_worker_settings():
    from argus.config import load_config

    config = load_config(
        environ={
            "ARGUS_DISABLE_SECRET_RESOLUTION": "true",
            "ARGUS_MAYA_CAPTURE_URL": "http://maya/api/orchestration/captures/retrievals",
            "ARGUS_MAYA_CAPTURE_TOKEN": "dedicated-token",
            "ARGUS_MAYA_OUTBOX_BATCH_SIZE": "5000",
            "ARGUS_MAYA_OUTBOX_POLL_SECONDS": "0",
        }
    )

    assert config.maya_capture.endpoint.endswith("/captures/retrievals")
    assert config.maya_capture.token == "dedicated-token"
    assert config.maya_capture.batch_size == 100
    assert config.maya_capture.poll_seconds == 1


def test_migration_suppresses_and_compacts_historical_placeholder_intents(
    tmp_path,
):
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import create_engine, inspect, text

    database = tmp_path / "historical-outbox.db"
    url = f"sqlite:///{database}"
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", url)
    command.upgrade(config, "0005_provider_spend")
    historical_payload = json.dumps(
        {"search_run_id": "historical-run", "result_count": 2},
        sort_keys=True,
        separators=(",", ":"),
    )
    engine = create_engine(url)
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO retrieval_requests "
                "(id, query_text, mode, max_results, caller, created_at, "
                "providers_json, free_only) "
                "VALUES "
                "('request-1', 'historical query', 'discovery', 10, 'maya', "
                "'2026-07-01 12:00:00', NULL, false)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO retrieval_runs "
                "(id, request_id, search_run_id, status, total_results, cached, "
                "started_at, committed_at, acceptance_fingerprint) "
                "VALUES "
                "('run-1', 'request-1', 'historical-run', 'accepted', 2, false, "
                "'2026-07-01 12:00:00', '2026-07-01 12:00:01', :fingerprint)"
            ),
            {"fingerprint": "a" * 64},
        )
        connection.execute(
            text(
                "INSERT INTO delivery_intents "
                "(id, run_id, destination, status, payload_json, created_at) "
                "VALUES "
                "('intent-1', 'run-1', 'maya', 'pending', :payload, "
                "'2026-07-01 12:00:01')"
            ),
            {"payload": historical_payload},
        )

    command.upgrade(config, "head")

    columns = {
        column["name"] for column in inspect(engine).get_columns("delivery_intents")
    }
    assert {
        "extraction_run_id",
        "payload_sha256",
        "attempt_count",
        "max_attempts",
        "next_attempt_at",
        "updated_at",
        "payload_compacted_at",
    } <= columns
    with engine.connect() as connection:
        row = (
            connection.execute(
                text(
                    "SELECT run_id, extraction_run_id, status, payload_json, "
                    "payload_sha256, attempt_count, max_attempts, next_attempt_at, "
                    "updated_at, payload_compacted_at "
                    "FROM delivery_intents WHERE id = 'intent-1'"
                )
            )
            .mappings()
            .one()
        )
    assert row["run_id"] == "run-1"
    assert row["extraction_run_id"] is None
    assert row["status"] == "suppressed"
    assert row["payload_json"] is None
    assert (
        row["payload_sha256"]
        == hashlib.sha256(historical_payload.encode("utf-8")).hexdigest()
    )
    assert row["attempt_count"] == 0
    assert row["max_attempts"] == 8
    assert row["next_attempt_at"] == row["updated_at"]
    assert row["payload_compacted_at"] == row["updated_at"]

    from argus.persistence.search_ledger import create_search_ledger_repository

    repository = create_search_ledger_repository(url, create_schema=False)
    assert (
        repository.claim_maya_outbox(
            now=datetime(2026, 7, 23, 12, 0),
            limit=10,
        )
        == []
    )

    command.downgrade(config, "0005_provider_spend")
    downgraded_columns = {
        column["name"] for column in inspect(engine).get_columns("delivery_intents")
    }
    assert "extraction_run_id" not in downgraded_columns
    with engine.connect() as connection:
        downgraded = (
            connection.execute(
                text(
                    "SELECT status, payload_json "
                    "FROM delivery_intents WHERE id = 'intent-1'"
                )
            )
            .mappings()
            .one()
        )
    assert downgraded == {
        "status": "pending",
        "payload_json": historical_payload,
    }


def test_postgresql_concurrent_workers_claim_each_intent_once(postgres_ledger_url):
    from alembic import command
    from alembic.config import Config

    from argus.persistence.search_ledger import create_search_ledger_repository

    config = Config("alembic.ini")
    config.set_main_option(
        "sqlalchemy.url",
        postgres_ledger_url.replace("%", "%%"),
    )
    command.downgrade(config, "base")
    command.upgrade(config, "head")
    repository = create_search_ledger_repository(postgres_ledger_url)
    repository.accept(
        SearchQuery(query="single claim", caller="maya"),
        _response(run_id="postgres-single-claim"),
    )
    barrier = Barrier(2)
    now = datetime(2026, 7, 23, 12, 0)

    def claim():
        barrier.wait()
        return repository.claim_maya_outbox(now=now, limit=1)

    with ThreadPoolExecutor(max_workers=2) as pool:
        claims = list(pool.map(lambda _: claim(), range(2)))

    claimed_ids = [row["id"] for batch in claims for row in batch]
    assert len(claimed_ids) == 1
    assert len(set(claimed_ids)) == 1


def test_postgresql_migration_suppresses_historical_placeholder_intents(
    postgres_ledger_url,
):
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import create_engine, text

    config = Config("alembic.ini")
    config.set_main_option(
        "sqlalchemy.url",
        postgres_ledger_url.replace("%", "%%"),
    )
    command.downgrade(config, "base")
    command.upgrade(config, "0005_provider_spend")
    historical_payload = json.dumps(
        {"search_run_id": "postgres-historical-run", "result_count": 2},
        sort_keys=True,
        separators=(",", ":"),
    )
    engine = create_engine(postgres_ledger_url)
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO retrieval_requests "
                "(id, query_text, mode, max_results, caller, created_at, "
                "providers_json, free_only) "
                "VALUES "
                "('request-1', 'historical query', 'discovery', 10, 'maya', "
                "'2026-07-01 12:00:00', NULL, false)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO retrieval_runs "
                "(id, request_id, search_run_id, status, total_results, cached, "
                "started_at, committed_at, acceptance_fingerprint) "
                "VALUES "
                "('run-1', 'request-1', 'postgres-historical-run', 'accepted', "
                "2, false, '2026-07-01 12:00:00', '2026-07-01 12:00:01', "
                ":fingerprint)"
            ),
            {"fingerprint": "a" * 64},
        )
        connection.execute(
            text(
                "INSERT INTO delivery_intents "
                "(id, run_id, destination, status, payload_json, created_at) "
                "VALUES "
                "('intent-1', 'run-1', 'maya', 'pending', :payload, "
                "'2026-07-01 12:00:01')"
            ),
            {"payload": historical_payload},
        )

    command.upgrade(config, "head")

    with engine.connect() as connection:
        row = (
            connection.execute(
                text(
                    "SELECT status, payload_json, payload_sha256, attempt_count, "
                    "max_attempts, next_attempt_at, updated_at, payload_compacted_at "
                    "FROM delivery_intents WHERE id = 'intent-1'"
                )
            )
            .mappings()
            .one()
        )
    assert row["status"] == "suppressed"
    assert row["payload_json"] is None
    assert (
        row["payload_sha256"]
        == hashlib.sha256(historical_payload.encode("utf-8")).hexdigest()
    )
    assert row["attempt_count"] == 0
    assert row["max_attempts"] == 8
    assert row["next_attempt_at"] == row["updated_at"]
    assert row["payload_compacted_at"] == row["updated_at"]
