import json
import hashlib
from datetime import datetime, timedelta, timezone


NOW = datetime(2026, 7, 23, 8, tzinfo=timezone.utc)


def _owned_root(tmp_path):
    from argus.recovery.operator import initialize_backup_root

    live = tmp_path / "live"
    live.mkdir()
    root = tmp_path / "backups"
    root.mkdir()
    marker = initialize_backup_root(root, live_data=live)
    return root, live, marker["root_id"]


def _owned_snapshot(root, name, root_id):
    snapshot = root / name
    snapshot.mkdir()
    (snapshot / ".argus-backup-set.json").write_text(
        json.dumps({"schema_version": 1, "root_id": root_id}),
        encoding="utf-8",
    )
    return snapshot


def _backup_set(tmp_path, name="20260723T060000Z"):
    from argus.recovery.database import COUNTED_TABLES

    root, live, root_id = _owned_root(tmp_path)
    snapshot = root / name
    snapshot.mkdir()
    files = {}
    for filename, content in (
        ("atlas.dump", b"atlas archive"),
        ("argus.dump", b"argus archive"),
        ("globals.sql", b"CREATE ROLE example;"),
        ("SHA256SUMS", b"checksums already validated\n"),
    ):
        (snapshot / filename).write_bytes(content)
        files[filename] = hashlib.sha256(content).hexdigest()
    inventory = {
        "schema_sha256": "c" * 64,
        "tables": {"example": 1},
        "constraints_validated": True,
    }
    manifest = {
        "schema_version": 1,
        "root_id": root_id,
        "completed_at": "2026-07-23T06:00:00+00:00",
        "databases": {
            "atlas": inventory,
            "argus": {
                **inventory,
                "tables": {table: 0 for table in COUNTED_TABLES},
            },
        },
        "files": files,
        "globals_without_passwords": True,
        "archive_format": "custom",
    }
    (snapshot / ".argus-backup-set.json").write_text(
        json.dumps(manifest, sort_keys=True),
        encoding="utf-8",
    )
    return root, live, snapshot, manifest


def test_backup_manifest_binds_archives_and_source_inventories(tmp_path):
    from argus.recovery.artifacts import create_backup_manifest
    from argus.recovery.database import COUNTED_TABLES

    root, live, _ = _owned_root(tmp_path)
    stage = root / ".staging.test"
    stage.mkdir()
    for filename, content in (
        ("atlas.dump", b"atlas archive"),
        ("argus.dump", b"argus archive"),
        ("globals.sql", b"CREATE ROLE example;"),
        ("SHA256SUMS", b"validated checksums\n"),
    ):
        (stage / filename).write_bytes(content)
    inventories = {
        "atlas": {
            "schema_sha256": "a" * 64,
            "tables": {"atlas_items": 3},
            "constraints_validated": True,
        },
        "argus": {
            "schema_sha256": "b" * 64,
            "tables": {table: 0 for table in COUNTED_TABLES},
            "constraints_validated": True,
        },
    }

    manifest = create_backup_manifest(
        stage,
        root=root,
        live_data=live,
        completed_at="20260723T060000Z",
        inventory_collector=lambda database: inventories[database],
    )

    assert manifest["databases"] == inventories
    assert manifest["files"]["atlas.dump"] == hashlib.sha256(
        b"atlas archive"
    ).hexdigest()
    assert json.loads(
        (stage / ".argus-backup-set.json").read_text(encoding="utf-8")
    ) == manifest


def test_backup_record_is_atomic_sanitized_and_preserves_restore(tmp_path):
    from argus.recovery.records import record_verified_backup

    root, live, snapshot, _ = _backup_set(tmp_path)
    evidence = tmp_path / "recovery.json"
    evidence.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "restore": {
                    "verified_at": "2026-07-20T08:00:00+00:00",
                    "databases": ["atlas", "argus"],
                    "globals_validated": True,
                    "schema_head": "0005_provider_spend",
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

    record_verified_backup(
        evidence,
        backup_set=snapshot,
        root=root,
        live_data=live,
    )

    payload = json.loads(evidence.read_text())
    assert payload["schema_version"] == 1
    assert payload["backup"]["databases"] == ["atlas", "argus"]
    assert payload["backup"]["globals"] is True
    assert payload["backup"]["outside_live_data"] is True
    assert payload["restore"]["schema_head"] == "0005_provider_spend"
    assert "private" not in payload
    assert not list(tmp_path.glob(".recovery.json.*"))


def test_restore_record_only_claims_checks_after_successful_verifier(tmp_path):
    from argus.recovery.records import (
        record_verified_backup,
        record_verified_restore,
    )

    root, live, snapshot, manifest = _backup_set(tmp_path)
    evidence = tmp_path / "recovery.json"
    record_verified_backup(
        evidence,
        backup_set=snapshot,
        root=root,
        live_data=live,
    )
    checks = {
        "schema": True,
        "row_counts": True,
        "integrity": True,
        "argus_read_path": True,
        "migration_compatible": True,
    }
    events = []

    record_verified_restore(
        evidence,
        backup_set=snapshot,
        root=root,
        live_data=live,
        argus_database="argus_restore_issue40_record",
        atlas_database="atlas_restore_issue40_record",
        verified_at=NOW,
        verify_source=lambda database, tenant, expected: events.append(
            f"source:{tenant}"
        ),
        migrate_argus=lambda database: events.append("migrate:argus"),
        verify_argus=lambda database, expected: {
            "schema_head": "0005_provider_spend",
            "inventory": expected,
            "checks": checks,
        },
        verify_atlas=lambda database, expected: {
            "inventory": expected,
            "checks": {"schema": True, "row_counts": True, "integrity": True},
        },
    )

    restore = json.loads(evidence.read_text())["restore"]
    assert restore["databases"] == ["atlas", "argus"]
    assert restore["globals_validated"] is True
    assert all(restore["checks"].values())
    assert "scratch_database" not in restore
    assert restore["backup_manifest_sha256"]
    assert events == ["source:argus", "source:atlas", "migrate:argus"]


def test_restore_record_refuses_source_mismatch_before_migration(tmp_path):
    import pytest

    from argus.recovery.records import (
        record_verified_backup,
        record_verified_restore,
    )

    root, live, snapshot, _ = _backup_set(tmp_path)
    evidence = tmp_path / "evidence.json"
    record_verified_backup(
        evidence,
        backup_set=snapshot,
        root=root,
        live_data=live,
    )
    migrated = False

    def reject_source(database, tenant, expected):
        raise RuntimeError("restored source inventory mismatch")

    def migrate(database):
        nonlocal migrated
        migrated = True

    with pytest.raises(RuntimeError, match="source inventory"):
        record_verified_restore(
            evidence,
            backup_set=snapshot,
            root=root,
            live_data=live,
            argus_database="argus_restore_issue40_mismatch",
            atlas_database="atlas_restore_issue40_mismatch",
            verify_source=reject_source,
            migrate_argus=migrate,
        )
    assert migrated is False


def test_restore_record_refuses_manifest_rebinding_during_verification(tmp_path):
    import pytest

    from argus.recovery.records import (
        record_verified_backup,
        record_verified_restore,
    )

    root, live, snapshot, _ = _backup_set(tmp_path)
    evidence = tmp_path / "evidence.json"
    record_verified_backup(
        evidence,
        backup_set=snapshot,
        root=root,
        live_data=live,
    )

    def replace_backup_evidence(database, tenant, expected):
        payload = json.loads(evidence.read_text(encoding="utf-8"))
        payload["backup"]["manifest_sha256"] = "d" * 64
        evidence.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="changed during restore verification"):
        record_verified_restore(
            evidence,
            backup_set=snapshot,
            root=root,
            live_data=live,
            argus_database="argus_restore_issue40_race",
            atlas_database="atlas_restore_issue40_race",
            verify_source=replace_backup_evidence,
            migrate_argus=lambda database: None,
            verify_argus=lambda database, expected: {
                "schema_head": "0005_provider_spend",
                "checks": {name: True for name in (
                    "schema",
                    "row_counts",
                    "integrity",
                    "argus_read_path",
                    "migration_compatible",
                )},
            },
            verify_atlas=lambda database, expected: {
                "checks": {
                    "schema": True,
                    "row_counts": True,
                    "integrity": True,
                },
            },
        )

    payload = json.loads(evidence.read_text(encoding="utf-8"))
    assert "restore" not in payload


def test_backup_evidence_update_waits_for_restore_verification_lock(tmp_path):
    import shutil
    import threading
    from concurrent.futures import ThreadPoolExecutor, TimeoutError

    import pytest

    from argus.recovery.records import (
        record_verified_backup,
        record_verified_restore,
    )

    root, live, snapshot, _ = _backup_set(tmp_path)
    newer = root / "20260724T060000Z"
    shutil.copytree(snapshot, newer)
    newer_manifest_path = newer / ".argus-backup-set.json"
    newer_manifest = json.loads(newer_manifest_path.read_text(encoding="utf-8"))
    newer_manifest["completed_at"] = "2026-07-24T06:00:00+00:00"
    newer_manifest_path.write_text(
        json.dumps(newer_manifest, sort_keys=True),
        encoding="utf-8",
    )
    evidence = tmp_path / "evidence.json"
    record_verified_backup(
        evidence,
        backup_set=snapshot,
        root=root,
        live_data=live,
    )
    original_manifest = json.loads(evidence.read_text())["backup"][
        "manifest_sha256"
    ]
    verifier_entered = threading.Event()
    release_verifier = threading.Event()

    def pause_verifier(database, tenant, expected):
        if tenant == "argus":
            verifier_entered.set()
            assert release_verifier.wait(timeout=5)

    checks = {
        "schema": True,
        "row_counts": True,
        "integrity": True,
        "argus_read_path": True,
        "migration_compatible": True,
    }
    with ThreadPoolExecutor(max_workers=2) as pool:
        restore_future = pool.submit(
            record_verified_restore,
            evidence,
            backup_set=snapshot,
            root=root,
            live_data=live,
            argus_database="argus_restore_issue40_lock",
            atlas_database="atlas_restore_issue40_lock",
            verify_source=pause_verifier,
            migrate_argus=lambda database: None,
            verify_argus=lambda database, expected: {
                "schema_head": "0005_provider_spend",
                "checks": checks,
            },
            verify_atlas=lambda database, expected: {
                "checks": {
                    "schema": True,
                    "row_counts": True,
                    "integrity": True,
                },
            },
        )
        assert verifier_entered.wait(timeout=5)
        backup_future = pool.submit(
            record_verified_backup,
            evidence,
            backup_set=newer,
            root=root,
            live_data=live,
        )
        with pytest.raises(TimeoutError):
            backup_future.result(timeout=0.1)
        release_verifier.set()
        restore_future.result(timeout=5)
        backup_future.result(timeout=5)

    payload = json.loads(evidence.read_text(encoding="utf-8"))
    assert payload["restore"]["backup_manifest_sha256"] == original_manifest
    assert payload["backup"]["manifest_sha256"] != original_manifest


def test_pruning_removes_only_unretained_timestamped_snapshot_directories(tmp_path):
    from argus.recovery.records import prune_snapshots

    root, live, root_id = _owned_root(tmp_path)
    for age in range(20):
        timestamp = NOW - timedelta(days=age)
        _owned_snapshot(root, timestamp.strftime("%Y%m%dT%H%M%SZ"), root_id)
    unrelated = root / "operator-notes"
    unrelated.mkdir()

    report = prune_snapshots(root, live_data=live, apply=True, now=NOW)

    assert report["removed"]
    assert unrelated.is_dir()
    assert all((root / name).is_dir() for name in report["kept"])
    assert all(not (root / name).exists() for name in report["removed"])


def test_pruning_dry_run_does_not_mutate(tmp_path):
    from argus.recovery.records import prune_snapshots

    root, live, root_id = _owned_root(tmp_path)
    old = _owned_snapshot(root, "20250101T000000Z", root_id)
    recent = _owned_snapshot(root, "20260723T000000Z", root_id)

    report = prune_snapshots(root, live_data=live, apply=False, now=NOW)

    assert report["applied"] is False
    assert old.is_dir()
    assert recent.is_dir()


def test_record_refuses_to_overwrite_corrupt_existing_evidence(tmp_path):
    from argus.recovery.records import record_verified_backup

    root, live, snapshot, _ = _backup_set(tmp_path)
    evidence = tmp_path / "recovery.json"
    evidence.write_text('{"restore":', encoding="utf-8")
    before = evidence.read_bytes()

    try:
        record_verified_backup(
            evidence,
            backup_set=snapshot,
            root=root,
            live_data=live,
        )
    except ValueError as error:
        assert "existing recovery evidence" in str(error)
    else:
        raise AssertionError("corrupt evidence must fail closed")
    assert evidence.read_bytes() == before


def test_backup_record_rejects_tampered_archive(tmp_path):
    import pytest

    from argus.recovery.records import record_verified_backup

    root, live, snapshot, _ = _backup_set(tmp_path)
    (snapshot / "argus.dump").write_bytes(b"tampered")

    with pytest.raises(ValueError, match="checksum"):
        record_verified_backup(
            tmp_path / "evidence.json",
            backup_set=snapshot,
            root=root,
            live_data=live,
        )


def test_backup_record_rejects_replay_older_than_current_evidence(tmp_path):
    import pytest

    from argus.recovery.records import record_verified_backup

    root, live, snapshot, _ = _backup_set(tmp_path)
    evidence = tmp_path / "evidence.json"
    evidence.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "backup": {
                    "completed_at": "2026-07-24T06:00:00+00:00",
                    "databases": ["atlas", "argus"],
                    "globals": True,
                    "manifest_sha256": "d" * 64,
                    "archive_format": "custom",
                    "outside_live_data": True,
                },
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="older backup"):
        record_verified_backup(
            evidence,
            backup_set=snapshot,
            root=root,
            live_data=live,
        )


def test_backup_record_rejects_changed_manifest_for_same_timestamp(tmp_path):
    import pytest

    from argus.recovery.records import record_verified_backup

    root, live, snapshot, _ = _backup_set(tmp_path)
    evidence = tmp_path / "evidence.json"
    evidence.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "backup": {
                    "completed_at": "2026-07-23T06:00:00+00:00",
                    "databases": ["atlas", "argus"],
                    "globals": True,
                    "manifest_sha256": "d" * 64,
                    "archive_format": "custom",
                    "outside_live_data": True,
                },
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="changed backup manifest"):
        record_verified_backup(
            evidence,
            backup_set=snapshot,
            root=root,
            live_data=live,
        )


def test_pruning_never_deletes_future_dated_snapshot(tmp_path):
    from argus.recovery.records import prune_snapshots

    root, live, root_id = _owned_root(tmp_path)
    future = _owned_snapshot(root, "20270101T000000Z", root_id)
    for age in range(20):
        timestamp = NOW - timedelta(days=age)
        _owned_snapshot(root, timestamp.strftime("%Y%m%dT%H%M%SZ"), root_id)

    report = prune_snapshots(root, live_data=live, apply=True, now=NOW)

    assert future.is_dir()
    assert future.name in report["kept"]


def test_pruning_never_deletes_unowned_timestamp_directory(tmp_path):
    from argus.recovery.records import prune_snapshots

    root, live, root_id = _owned_root(tmp_path)
    unowned = root / "20240101T000000Z"
    unowned.mkdir()
    _owned_snapshot(root, "20260723T000000Z", root_id)

    report = prune_snapshots(root, live_data=live, apply=True, now=NOW)

    assert unowned.is_dir()
    assert unowned.name not in report["removed"]


def test_pruning_rejects_timestamp_symlink(tmp_path):
    import pytest

    from argus.recovery.records import prune_snapshots

    root, live, _ = _owned_root(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    (root / "20240101T000000Z").symlink_to(outside, target_is_directory=True)

    with pytest.raises(ValueError, match="symlink"):
        prune_snapshots(root, live_data=live, apply=True, now=NOW)
    assert outside.is_dir()


def test_pruning_revalidates_ownership_after_atomic_quarantine(tmp_path, monkeypatch):
    import argus.recovery.records as records

    root, live, root_id = _owned_root(tmp_path)
    target = _owned_snapshot(root, "20260701T000000Z", root_id)
    for age in range(20):
        timestamp = NOW - timedelta(days=age)
        _owned_snapshot(root, timestamp.strftime("%Y%m%dT%H%M%SZ"), root_id)
    original = records._read_snapshot_owner_at
    quarantined_calls = 0

    def changed_owner(directory_fd, name):
        nonlocal quarantined_calls
        if name.startswith(f".pruning.{target.name}."):
            quarantined_calls += 1
            return "different-root"
        return original(directory_fd, name)

    monkeypatch.setattr(records, "_read_snapshot_owner_at", changed_owner)

    report = records.prune_snapshots(root, live_data=live, apply=True, now=NOW)

    assert target.name not in report["removed"]
    assert target.is_dir()
    assert quarantined_calls == 1


def test_pruning_refuses_backup_root_path_swap_after_open(tmp_path, monkeypatch):
    from pathlib import Path

    import pytest

    import argus.recovery.records as records

    root, live, root_id = _owned_root(tmp_path)
    target = _owned_snapshot(root, "20240101T000000Z", root_id)
    moved = tmp_path / "moved-backups"
    original_open = records.os.open
    swapped = False

    def swap_after_open(path, flags, mode=0o777, *, dir_fd=None):
        nonlocal swapped
        descriptor = original_open(path, flags, mode, dir_fd=dir_fd)
        if dir_fd is None and Path(path) == root and not swapped:
            swapped = True
            root.rename(moved)
            root.mkdir()
        return descriptor

    monkeypatch.setattr(records.os, "open", swap_after_open)

    with pytest.raises(ValueError, match="changed after it was opened"):
        records.prune_snapshots(root, live_data=live, apply=True, now=NOW)

    assert (moved / target.name).is_dir()


def test_pruning_refuses_nested_device_boundary(tmp_path, monkeypatch):
    import os

    import pytest

    import argus.recovery.records as records

    root, live, root_id = _owned_root(tmp_path)
    target = _owned_snapshot(root, "20260701T000000Z", root_id)
    boundary = target / "mounted-data"
    boundary.mkdir()
    for age in range(20):
        timestamp = NOW - timedelta(days=age)
        _owned_snapshot(root, timestamp.strftime("%Y%m%dT%H%M%SZ"), root_id)
    original_stat = records.os.stat

    def report_other_device(path, *args, **kwargs):
        metadata = original_stat(path, *args, **kwargs)
        if path == "mounted-data" and kwargs.get("dir_fd") is not None:
            values = list(metadata)
            values[2] = metadata.st_dev + 1
            return os.stat_result(values)
        return metadata

    monkeypatch.setattr(records.os, "stat", report_other_device)

    with pytest.raises(ValueError, match="device boundary"):
        records.prune_snapshots(root, live_data=live, apply=True, now=NOW)

    assert target.is_dir()
    assert boundary.is_dir()
