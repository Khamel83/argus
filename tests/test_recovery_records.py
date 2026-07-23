import json
from datetime import datetime, timedelta, timezone


NOW = datetime(2026, 7, 23, 8, tzinfo=timezone.utc)


def test_backup_record_is_atomic_sanitized_and_preserves_restore(tmp_path):
    from argus.recovery.records import record_backup

    evidence = tmp_path / "recovery.json"
    evidence.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "restore": {
                    "verified_at": "2026-07-20T08:00:00+00:00",
                    "databases": ["atlas", "argus"],
                    "globals_validated": True,
                    "schema_head": "0004_operation_ledger",
                    "checks": {
                        "schema": True,
                        "row_counts": True,
                        "integrity": True,
                        "argus_read_path": True,
                        "migration_compatible": True,
                    },
                },
                "private": "/do/not/preserve",
            }
        ),
        encoding="utf-8",
    )

    record_backup(
        evidence,
        completed_at="20260723T060000Z",
        manifest_sha256="b" * 64,
    )

    payload = json.loads(evidence.read_text())
    assert payload["schema_version"] == 1
    assert payload["backup"]["databases"] == ["atlas", "argus"]
    assert payload["backup"]["globals"] is True
    assert payload["backup"]["outside_live_data"] is True
    assert payload["restore"]["schema_head"] == "0004_operation_ledger"
    assert "private" not in payload
    assert not list(tmp_path.glob(".recovery.json.*"))


def test_restore_record_only_claims_checks_after_successful_verifier(tmp_path):
    from argus.recovery.records import record_restore

    evidence = tmp_path / "recovery.json"
    evidence.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "backup": {
                    "completed_at": NOW.isoformat(),
                    "databases": ["atlas", "argus"],
                    "globals": True,
                    "manifest_sha256": "a" * 64,
                    "archive_format": "custom",
                    "outside_live_data": True,
                },
            }
        ),
        encoding="utf-8",
    )

    record_restore(evidence, schema_head="0004_operation_ledger", verified_at=NOW)

    restore = json.loads(evidence.read_text())["restore"]
    assert restore["databases"] == ["atlas", "argus"]
    assert restore["globals_validated"] is True
    assert all(restore["checks"].values())
    assert "scratch_database" not in restore


def test_pruning_removes_only_unretained_timestamped_snapshot_directories(tmp_path):
    from argus.recovery.records import prune_snapshots

    for age in range(20):
        timestamp = NOW - timedelta(days=age)
        (tmp_path / timestamp.strftime("%Y%m%dT%H%M%SZ")).mkdir()
    unrelated = tmp_path / "operator-notes"
    unrelated.mkdir()

    report = prune_snapshots(tmp_path, apply=True, now=NOW)

    assert report["removed"]
    assert unrelated.is_dir()
    assert all((tmp_path / name).is_dir() for name in report["kept"])
    assert all(not (tmp_path / name).exists() for name in report["removed"])


def test_pruning_dry_run_does_not_mutate(tmp_path):
    from argus.recovery.records import prune_snapshots

    old = tmp_path / "20250101T000000Z"
    recent = tmp_path / "20260723T000000Z"
    old.mkdir()
    recent.mkdir()

    report = prune_snapshots(tmp_path, apply=False, now=NOW)

    assert report["applied"] is False
    assert old.is_dir()
    assert recent.is_dir()


def test_record_refuses_to_overwrite_corrupt_existing_evidence(tmp_path):
    from argus.recovery.records import record_backup

    evidence = tmp_path / "recovery.json"
    evidence.write_text('{"restore":', encoding="utf-8")
    before = evidence.read_bytes()

    try:
        record_backup(
            evidence,
            completed_at="20260723T080000Z",
            manifest_sha256="a" * 64,
        )
    except ValueError as error:
        assert "existing recovery evidence" in str(error)
    else:
        raise AssertionError("corrupt evidence must fail closed")
    assert evidence.read_bytes() == before


def test_pruning_never_deletes_future_dated_snapshot(tmp_path):
    from argus.recovery.records import prune_snapshots

    future = tmp_path / "20270101T000000Z"
    future.mkdir()
    for age in range(20):
        timestamp = NOW - timedelta(days=age)
        (tmp_path / timestamp.strftime("%Y%m%dT%H%M%SZ")).mkdir()

    report = prune_snapshots(tmp_path, apply=True, now=NOW)

    assert future.is_dir()
    assert future.name in report["kept"]
