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
    ROOT / "argus/cli/main.py",
    ROOT / "argus/workflows/service.py",
}
MCP_ADAPTERS = {
    ROOT / "argus/mcp/http_adapter.py",
    ROOT / "argus/mcp/server.py",
    ROOT / "argus/mcp/v2_tools.py",
}
MCP_MODULES = frozenset(ROOT.glob("argus/mcp/*.py"))
EXPECTED_ADAPTERS = {
    *PORTED_HTTP_MODULES,
    *LEGACY_ADAPTER_EXCEPTIONS,
    *MCP_ADAPTERS,
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
MCP_FORBIDDEN = {
    "argus.broker",
    "argus.extraction",
    "argus.persistence",
    "argus.provider_controls",
}
MCP_FORBIDDEN_PREFIXES = (
    "argus.authority.development_mcp_",
    "argus.development_mcp_",
)


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


def _is_forbidden_mcp_reference(qualified_name: str) -> bool:
    return any(
        qualified_name == forbidden
        or qualified_name.startswith(f"{forbidden}.")
        for forbidden in MCP_FORBIDDEN
    ) or qualified_name.startswith(MCP_FORBIDDEN_PREFIXES)


def _resolved_import_aliases(tree: ast.AST) -> dict[str, str]:
    aliases: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for imported in node.names:
                bound = imported.asname or imported.name.split(".", 1)[0]
                aliases[bound] = imported.name if imported.asname else bound
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if node.level:
                module = resolve_name("." * node.level + module, "argus.mcp")
            for imported in node.names:
                if imported.name != "*":
                    aliases[imported.asname or imported.name] = (
                        f"{module}.{imported.name}"
                    )
    return aliases


def _qualified_name(node: ast.AST, aliases: dict[str, str]) -> str | None:
    if isinstance(node, ast.Name):
        return aliases.get(node.id, node.id)
    if isinstance(node, ast.Attribute):
        owner = _qualified_name(node.value, aliases)
        if owner is not None:
            return f"{owner}.{node.attr}"
    return None


def _mcp_boundary_violations(path: Path) -> set[str]:
    """Resolve imports, aliases, calls, and authority references fail-closed."""

    violations = {
        module
        for module in _imports(path, package="argus.mcp")
        if _is_forbidden_mcp_reference(module)
    }
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    aliases = _resolved_import_aliases(tree)
    for node in ast.walk(tree):
        qualified = _qualified_name(node, aliases)
        if qualified is not None and _is_forbidden_mcp_reference(qualified):
            violations.add(qualified)
        if not isinstance(node, ast.Call):
            continue
        function = _qualified_name(node.func, aliases)
        if function in {
            "__import__",
            "builtins.__import__",
            "importlib.import_module",
        }:
            violations.add("dynamic-import")
        if isinstance(node.func, ast.Name) and node.func.id in {
            "create_broker",
            "extract_url",
            "create_search_ledger_repository",
        }:
            violations.add(node.func.id)
        if isinstance(node.func, ast.Attribute):
            owner = node.func.value
            if isinstance(owner, ast.Name) and owner.id in {"broker", "readiness"}:
                violations.add(f"{owner.id}.{node.func.attr}")
            if isinstance(owner, ast.Attribute) and owner.attr in {
                "broker",
                "readiness_service",
            }:
                violations.add(f"{owner.attr}.{node.func.attr}")
    return violations


@pytest.mark.parametrize(
    ("source", "expected"),
    (
        (
            "from argus.authority import development_mcp_extract as run\n",
            "argus.authority.development_mcp_extract",
        ),
        (
            "from argus import development_mcp_server as launch\n",
            "argus.development_mcp_server",
        ),
        (
            "import importlib as loader\n"
            "loader.import_module('argus.' + 'development_mcp_server')\n",
            "dynamic-import",
        ),
    ),
)
def test_mcp_boundary_rejects_review_bypass_fixtures(tmp_path, source, expected):
    module = tmp_path / "bypass.py"
    module.write_text(source, encoding="utf-8")

    assert expected in _mcp_boundary_violations(module)


def test_general_authority_module_exposes_no_development_mcp_execution_helpers():
    authority = ROOT / "argus/authority.py"
    tree = ast.parse(authority.read_text(encoding="utf-8"), filename=str(authority))
    exposed = {
        node.name
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name.startswith("development_mcp_")
    }
    assert not exposed


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


@pytest.mark.parametrize("path", tuple(sorted(MCP_MODULES)))
def test_mcp_modules_have_no_execution_authority_imports_or_invocations(path):
    violations = _mcp_boundary_violations(path)
    assert not violations, (
        f"{path.relative_to(ROOT)}: MCP authority boundary violations "
        f"{sorted(violations)}"
    )


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
