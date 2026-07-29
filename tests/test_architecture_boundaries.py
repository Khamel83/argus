"""Architecture boundaries introduced by the retrieval-evidence port."""

from __future__ import annotations

import ast
from importlib.util import resolve_name
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
PRESENTER = ROOT / "argus/api/presenters.py"
PORTED_HTTP_MODULES = {
    ROOT / "argus/api/routes_search.py": set(),
    ROOT / "argus/api/routes_v2.py": set(),
    # The same module still owns the pure assess-content and cookie-health
    # compatibility routes. Those exact imports are the closed exception.
    ROOT / "argus/api/routes_extract.py": {
        "argus.extraction.completeness",
        "argus.extraction.cookies",
    },
}
LEGACY_ADAPTER_EXCEPTIONS = {
    ROOT / "argus/api/routes_admin.py",
    ROOT / "argus/api/routes_dashboard.py",
    ROOT / "argus/api/routes_health.py",
    ROOT / "argus/mcp/http_adapter.py",
    ROOT / "argus/mcp/local_adapter.py",
    ROOT / "argus/mcp/resources.py",
    ROOT / "argus/mcp/server.py",
    ROOT / "argus/mcp/tools.py",
    ROOT / "argus/cli/main.py",
    ROOT / "argus/workflows/service.py",
}
EXPECTED_ADAPTERS = {
    *PORTED_HTTP_MODULES,
    *LEGACY_ADAPTER_EXCEPTIONS,
    ROOT / "argus/api/routes_workflows.py",
}
FORBIDDEN = {
    "argus.providers",
    "argus.extraction",
    "argus.persistence",
    "argus.broker.cache",
    "argus.broker.ranking",
    "argus.broker.dedupe",
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
            imported.update(f"{module}.{alias.name}" for alias in node.names)
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


@pytest.mark.parametrize(
    ("path", "allowed_exceptions"),
    tuple(PORTED_HTTP_MODULES.items()),
)
def test_ported_http_modules_have_no_secondary_semantic_authority(
    path,
    allowed_exceptions,
):
    imported = _imports(path)
    violations = {
        module
        for module in imported
        if any(
            module == forbidden or module.startswith(f"{forbidden}.")
            for forbidden in FORBIDDEN
        )
        and not any(
            module == allowed or module.startswith(f"{allowed}.")
            for allowed in allowed_exceptions
        )
    }
    assert not violations
    assert "argus.operations.accepted" in imported


def test_transport_adapter_inventory_has_only_closed_legacy_exceptions():
    discovered = {
        *ROOT.glob("argus/api/routes_*.py"),
        *ROOT.glob("argus/mcp/*.py"),
        *ROOT.glob("argus/cli/*.py"),
        *ROOT.glob("argus/workflows/*.py"),
    }
    non_adapters = {
        ROOT / "argus/mcp/__init__.py",
        ROOT / "argus/mcp/capabilities.py",
        ROOT / "argus/mcp/sessions.py",
        ROOT / "argus/cli/__init__.py",
        ROOT / "argus/workflows/__init__.py",
        ROOT / "argus/workflows/models.py",
        ROOT / "argus/workflows/summarizer.py",
    }
    assert discovered - non_adapters == EXPECTED_ADAPTERS

    for path in EXPECTED_ADAPTERS - LEGACY_ADAPTER_EXCEPTIONS:
        imported = _imports(path)
        violations = {
            module
            for module in imported
            if any(
                module == forbidden or module.startswith(f"{forbidden}.")
                for forbidden in FORBIDDEN
            )
        }
        if path == ROOT / "argus/api/routes_extract.py":
            violations = {
                module
                for module in violations
                if not module.startswith(
                    (
                        "argus.extraction.completeness",
                        "argus.extraction.cookies",
                    )
                )
            }
        assert not violations, f"{path.relative_to(ROOT)}: {sorted(violations)}"


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
