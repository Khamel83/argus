"""Recovery completeness is derived from the verified catalog and source."""
from copy import deepcopy

import pytest

from argus.recovery.database import expected_argus_schema_manifest, metadata_registry_coverage


def test_current_registry_covers_verified_schema():
    manifest = expected_argus_schema_manifest("0011_extraction_spend_scope")
    assert metadata_registry_coverage(manifest) == {
        "complete": True, "missing_tables": [], "extra_tables": [],
        "column_differences": {},
    }


@pytest.mark.parametrize("drift", ["table", "column", "extra_source_table"])
def test_catalog_drift_cannot_inherit_completeness(drift):
    manifest = deepcopy(expected_argus_schema_manifest("0011_extraction_spend_scope"))
    if drift == "table":
        manifest["tables"].append("unregistered_production_table")
    elif drift == "column":
        manifest["columns"]["retrieval_runs"]["unregistered_column"] = {}
    else:
        manifest["tables"].remove("retrieval_runs")
    assert metadata_registry_coverage(manifest)["complete"] is False


def test_omitted_cli_attestation_uses_verifier_evidence():
    import importlib.util
    from pathlib import Path
    path = Path(__file__).resolve().parents[1] / 'ops/postgres/postgres_recovery.py'
    spec = importlib.util.spec_from_file_location('recovery_operator', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    parsed = module._parser().parse_args([
        'record-restore', '--evidence', '/tmp/evidence', '--backup-set', '/tmp/set',
        '--root', '/tmp/backups', '--live-data', '/tmp/live',
        '--argus-database', 'argus_restore_test', '--atlas-database', 'atlas_restore_test',
    ])
    assert parsed.metadata_registry_complete is None
