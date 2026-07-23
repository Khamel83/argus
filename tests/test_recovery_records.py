import json
import hashlib
import os
import stat
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


def _filesystem_state(root):
    state = {}
    for path in (root, *sorted(root.rglob("*"))):
        metadata = path.lstat()
        relative = "." if path == root else str(path.relative_to(root))
        entry = {
            "mode": metadata.st_mode,
            "uid": metadata.st_uid,
            "gid": metadata.st_gid,
            "size": metadata.st_size,
            "mtime_ns": metadata.st_mtime_ns,
            "ctime_ns": metadata.st_ctime_ns,
        }
        if stat.S_ISREG(metadata.st_mode):
            entry["content"] = path.read_bytes()
        elif stat.S_ISLNK(metadata.st_mode):
            entry["target"] = os.readlink(path)
        state[relative] = entry
    return state


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


def test_retention_plan_is_deterministic_and_byte_for_byte_read_only(
    tmp_path,
    monkeypatch,
):
    import argus.recovery.records as records

    root, live, root_id = _owned_root(tmp_path)
    for age in range(20):
        timestamp = NOW - timedelta(days=age)
        snapshot = _owned_snapshot(
            root,
            timestamp.strftime("%Y%m%dT%H%M%SZ"),
            root_id,
        )
        (snapshot / "archive.dump").write_bytes(b"database payload")
    unrelated = root / "operator-notes"
    unrelated.mkdir()
    before = _filesystem_state(root)
    for operation in (
        "rename",
        "replace",
        "unlink",
        "rmdir",
        "chmod",
        "ftruncate",
    ):
        monkeypatch.setattr(
            records.os,
            operation,
            lambda *args, _operation=operation, **kwargs: (_ for _ in ()).throw(
                AssertionError(f"retention planner called {_operation}")
            ),
        )

    first = records.plan_snapshot_retention(root, live_data=live, now=NOW)
    second = records.plan_snapshot_retention(root, live_data=live, now=NOW)

    assert first == second
    assert first["schema_version"] == 1
    assert first["policy"] == {"daily": 7, "weekly": 5, "monthly": 12}
    assert first["expire_candidates"]
    assert first["mutation_performed"] is False
    assert first["production_reclamation_required"] is True
    assert set(first) == {
        "schema_version",
        "observed_at",
        "policy",
        "owned_snapshot_count",
        "kept",
        "expire_candidates",
        "mutation_performed",
        "production_reclamation_required",
    }
    assert _filesystem_state(root) == before


def test_retention_api_rejects_apply_without_touching_filesystem(tmp_path):
    import pytest

    from argus.recovery.records import prune_snapshots

    root, live, root_id = _owned_root(tmp_path)
    _owned_snapshot(root, "20260723T000000Z", root_id)
    before = _filesystem_state(root)

    with pytest.raises(ValueError, match="read-only"):
        prune_snapshots(root, live_data=live, apply=True, now=NOW)

    assert _filesystem_state(root) == before


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


def test_retention_plan_keeps_future_dated_snapshot(tmp_path):
    from argus.recovery.records import plan_snapshot_retention

    root, live, root_id = _owned_root(tmp_path)
    future = _owned_snapshot(root, "20270101T000000Z", root_id)
    for age in range(20):
        timestamp = NOW - timedelta(days=age)
        _owned_snapshot(root, timestamp.strftime("%Y%m%dT%H%M%SZ"), root_id)

    report = plan_snapshot_retention(root, live_data=live, now=NOW)

    assert future.is_dir()
    assert future.name in report["kept"]


def test_retention_plan_ignores_unowned_timestamp_directory(tmp_path):
    from argus.recovery.records import plan_snapshot_retention

    root, live, root_id = _owned_root(tmp_path)
    unowned = root / "20240101T000000Z"
    unowned.mkdir()
    _owned_snapshot(root, "20260723T000000Z", root_id)

    report = plan_snapshot_retention(root, live_data=live, now=NOW)

    assert unowned.is_dir()
    assert unowned.name not in report["kept"]
    assert unowned.name not in report["expire_candidates"]


def test_retention_plan_rejects_timestamp_symlink_without_mutation(tmp_path):
    import pytest

    from argus.recovery.records import plan_snapshot_retention

    root, live, _ = _owned_root(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    (root / "20240101T000000Z").symlink_to(outside, target_is_directory=True)

    before = _filesystem_state(root)
    with pytest.raises(ValueError, match="symlink"):
        plan_snapshot_retention(root, live_data=live, now=NOW)
    assert _filesystem_state(root) == before


def test_retention_plan_fails_if_owned_snapshot_changes_during_validation(
    tmp_path,
    monkeypatch,
):
    import pytest

    import argus.recovery.records as records

    root, live, root_id = _owned_root(tmp_path)
    target = _owned_snapshot(root, "20260701T000000Z", root_id)
    payload = target / "payload.dump"
    payload.write_bytes(b"original")
    for age in range(20):
        timestamp = NOW - timedelta(days=age)
        _owned_snapshot(root, timestamp.strftime("%Y%m%dT%H%M%SZ"), root_id)
    original = records._validate_snapshot_tree
    changed_state = None

    def change_after_validation(directory_fd, expected_device, **kwargs):
        nonlocal changed_state
        result = original(directory_fd, expected_device, **kwargs)
        if records.os.fstat(directory_fd).st_ino == target.stat().st_ino:
            target.rename(root / "relocated-by-adversary")
            target.mkdir()
            (target / "added-by-adversary.dump").write_bytes(b"new")
            changed_state = _filesystem_state(root)
        return result

    monkeypatch.setattr(records, "_validate_snapshot_tree", change_after_validation)

    with pytest.raises(ValueError, match="changed during retention planning"):
        records.plan_snapshot_retention(root, live_data=live, now=NOW)

    assert changed_state is not None
    assert _filesystem_state(root) == changed_state


def test_retention_plan_refuses_backup_root_path_swap_after_open(
    tmp_path,
    monkeypatch,
):
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
        records.plan_snapshot_retention(root, live_data=live, now=NOW)

    assert (moved / target.name).is_dir()


def test_retention_plan_refuses_nested_device_boundary_without_mutation(
    tmp_path,
    monkeypatch,
):
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

    before = _filesystem_state(root)
    with pytest.raises(ValueError, match="device boundary"):
        records.plan_snapshot_retention(root, live_data=live, now=NOW)

    assert _filesystem_state(root) == before


def test_retention_plan_fails_closed_on_concurrent_payload_addition(
    tmp_path,
    monkeypatch,
):
    import pytest

    import argus.recovery.records as records

    root, live, root_id = _owned_root(tmp_path)
    target = _owned_snapshot(root, "20260701T000000Z", root_id)
    (target / "validated-data").write_text("original", encoding="utf-8")
    for age in range(20):
        timestamp = NOW - timedelta(days=age)
        _owned_snapshot(root, timestamp.strftime("%Y%m%dT%H%M%SZ"), root_id)
    original = records._validate_snapshot_tree
    changed_state = None

    def add_after_validation(directory_fd, expected_device, **kwargs):
        nonlocal changed_state
        result = original(directory_fd, expected_device, **kwargs)
        if records.os.fstat(directory_fd).st_ino == target.stat().st_ino:
            (target / "added-by-adversary.dump").write_bytes(b"new payload")
            changed_state = _filesystem_state(root)
        return result

    monkeypatch.setattr(
        records,
        "_validate_snapshot_tree",
        add_after_validation,
    )

    with pytest.raises(ValueError, match="changed during retention planning"):
        records.plan_snapshot_retention(root, live_data=live, now=NOW)

    assert changed_state is not None
    assert _filesystem_state(root) == changed_state


def test_retention_plan_refuses_regular_file_swap_without_mutation(
    tmp_path,
    monkeypatch,
):
    import pytest

    import argus.recovery.records as records

    root, live, root_id = _owned_root(tmp_path)
    target = _owned_snapshot(root, "20260701T000000Z", root_id)
    payload = target / "payload.dump"
    payload.write_bytes(b"owned payload")
    unrelated = root / "unrelated.txt"
    unrelated.write_bytes(b"unrelated payload")
    for age in range(20):
        timestamp = NOW - timedelta(days=age)
        _owned_snapshot(root, timestamp.strftime("%Y%m%dT%H%M%SZ"), root_id)
    original_open = records.os.open
    swapped = False

    def swap_between_stat_and_open(path, flags, mode=0o777, *, dir_fd=None):
        nonlocal swapped
        if path == "payload.dump" and dir_fd is not None and not swapped:
            swapped = True
            payload.rename(target / "relocated-owned.dump")
            unrelated.rename(payload)
        return original_open(path, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(records.os, "open", swap_between_stat_and_open)

    with pytest.raises(ValueError, match="changed during retention planning"):
        records.plan_snapshot_retention(root, live_data=live, now=NOW)

    assert (target / "payload.dump").read_bytes() == b"unrelated payload"
    assert (target / "relocated-owned.dump").read_bytes() == b"owned payload"


def test_retention_plan_refuses_directory_swap_without_mutation(
    tmp_path,
    monkeypatch,
):
    import pytest

    import argus.recovery.records as records

    root, live, root_id = _owned_root(tmp_path)
    target = _owned_snapshot(root, "20260701T000000Z", root_id)
    owned_directory = target / "archives"
    owned_directory.mkdir()
    (owned_directory / "owned.dump").write_bytes(b"owned payload")
    unrelated = root / "unrelated-directory"
    unrelated.mkdir()
    (unrelated / "sentinel").write_bytes(b"unrelated payload")
    for age in range(20):
        timestamp = NOW - timedelta(days=age)
        _owned_snapshot(root, timestamp.strftime("%Y%m%dT%H%M%SZ"), root_id)
    original_open = records.os.open
    swapped = False

    def swap_between_stat_and_open(path, flags, mode=0o777, *, dir_fd=None):
        nonlocal swapped
        if path == "archives" and dir_fd is not None and not swapped:
            swapped = True
            owned_directory.rename(target / "relocated-archives")
            unrelated.rename(owned_directory)
        return original_open(path, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(records.os, "open", swap_between_stat_and_open)

    with pytest.raises(ValueError, match="changed during retention planning"):
        records.plan_snapshot_retention(root, live_data=live, now=NOW)

    assert (target / "archives" / "sentinel").read_bytes() == b"unrelated payload"
    assert (
        target / "relocated-archives" / "owned.dump"
    ).read_bytes() == b"owned payload"


def test_retention_plan_rejects_hard_links_without_mutation(
    tmp_path,
):
    import os

    import pytest

    from argus.recovery.records import plan_snapshot_retention

    root, live, root_id = _owned_root(tmp_path)
    target = _owned_snapshot(root, "20260701T000000Z", root_id)
    payload = target / "payload.dump"
    payload.write_bytes(b"must remain")
    os.link(payload, target / "payload-hardlink.dump")
    for age in range(20):
        timestamp = NOW - timedelta(days=age)
        _owned_snapshot(root, timestamp.strftime("%Y%m%dT%H%M%SZ"), root_id)

    before = _filesystem_state(root)
    with pytest.raises(ValueError, match="link count"):
        plan_snapshot_retention(root, live_data=live, now=NOW)

    assert _filesystem_state(root) == before


def test_retention_plan_rejects_nested_symlink_without_mutation(tmp_path):
    import pytest

    from argus.recovery.records import plan_snapshot_retention

    root, live, root_id = _owned_root(tmp_path)
    target = _owned_snapshot(root, "20260701T000000Z", root_id)
    payload = target / "payload.dump"
    payload.write_bytes(b"must remain")
    outside = root / "unrelated.txt"
    outside.write_bytes(b"unrelated payload")
    (target / "outside-link").symlink_to(outside)
    for age in range(20):
        timestamp = NOW - timedelta(days=age)
        _owned_snapshot(root, timestamp.strftime("%Y%m%dT%H%M%SZ"), root_id)

    before = _filesystem_state(root)
    with pytest.raises(ValueError, match="links or special files"):
        plan_snapshot_retention(root, live_data=live, now=NOW)

    assert _filesystem_state(root) == before


def test_retention_plan_rejects_fifo_without_mutation(tmp_path):
    import os

    import pytest

    from argus.recovery.records import plan_snapshot_retention

    root, live, root_id = _owned_root(tmp_path)
    target = _owned_snapshot(root, "20260701T000000Z", root_id)
    payload = target / "payload.dump"
    payload.write_bytes(b"must remain")
    os.mkfifo(target / "unexpected-pipe")
    for age in range(20):
        timestamp = NOW - timedelta(days=age)
        _owned_snapshot(root, timestamp.strftime("%Y%m%dT%H%M%SZ"), root_id)

    before = _filesystem_state(root)
    with pytest.raises(ValueError, match="links or special files"):
        plan_snapshot_retention(root, live_data=live, now=NOW)

    assert _filesystem_state(root) == before
