"""Architecture boundaries introduced by the retrieval-evidence port."""

from __future__ import annotations

import ast
from importlib.util import resolve_name
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
PRESENTER = ROOT / "argus/api/presenters.py"
FORBIDDEN = {
    "argus.providers",
    "argus.extraction",
    "argus.persistence",
    "argus.broker.cache",
}


def _module_package(path: Path) -> str:
    relative = path.relative_to(ROOT).with_suffix("")
    module = ".".join(relative.parts)
    return module.rpartition(".")[0]


def _imports(path: Path, *, package: str | None = None) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    package = _module_package(path) if package is None else package
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if node.level:
                module = resolve_name(
                    "." * node.level + module,
                    package,
                )
            if node.module is not None:
                imported.add(module)
            imported.update(
                f"{module}.{alias.name}" for alias in node.names
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


def test_import_parser_resolves_relative_reexport_seams(tmp_path):
    module = tmp_path / "presenters.py"
    module.write_text(
        "from .. import extraction\n"
        "from ..extraction import extractor\n"
        "from . import routes_search\n",
        encoding="utf-8",
    )

    assert _imports(module, package="argus.api") == {
        "argus.extraction",
        "argus.extraction.extractor",
        "argus.api.routes_search",
    }
