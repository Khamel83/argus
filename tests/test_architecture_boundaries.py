"""Architecture boundaries introduced by the retrieval-evidence port."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
PRESENTER = ROOT / "argus/api/presenters.py"
FORBIDDEN = {
    "argus.providers",
    "argus.extraction.extractor",
    "argus.extraction.rejection",
    "argus.persistence",
    "argus.broker.cache",
}


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            imported.add(node.module)
            imported.update(
                f"{node.module}.{alias.name}" for alias in node.names
            )
    return imported


def test_presenter_does_not_import_execution_or_persistence_authority():
    if not PRESENTER.exists():
        pytest.skip("S7 owns argus/api/presenters.py")

    imported = _imports(PRESENTER)
    violations = {
        module
        for module in imported
        if any(
            module == forbidden or module.startswith(f"{forbidden}.")
            for forbidden in FORBIDDEN
        )
    }
    assert not violations


def test_contract_kernel_does_not_import_the_throwaway_prototype():
    outcomes = ROOT / "argus/contracts/outcomes.py"
    if not outcomes.exists():
        pytest.fail("accepted-operation contract kernel is missing")

    imported = _imports(outcomes)
    assert not any(module.startswith("docs.prototypes") for module in imported)
    assert "jsonschema" not in imported
