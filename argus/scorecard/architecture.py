"""Shared fail-closed inventory of remaining presentation authority exceptions."""

from __future__ import annotations

import ast
from pathlib import Path


LEGACY_PRESENTATION_MODULES = (
    "argus/api/routes_admin.py",
    "argus/api/routes_dashboard.py",
    "argus/api/routes_health.py",
)
FORBIDDEN_AUTHORITY_PREFIXES = (
    "argus.broker",
    "argus.extraction",
    "argus.persistence",
    "argus.providers",
)


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    return imported


def find_architecture_exceptions(repository_root: Path) -> tuple[str, ...]:
    """Return actual remaining direct-authority modules from the closed inventory."""
    exceptions: list[str] = []
    for relative in LEGACY_PRESENTATION_MODULES:
        path = repository_root / relative
        if not path.is_file():
            exceptions.append(f"{relative}:missing")
            continue
        imports = _imports(path)
        if any(
            module == prefix or module.startswith(f"{prefix}.")
            for module in imports
            for prefix in FORBIDDEN_AUTHORITY_PREFIXES
        ):
            exceptions.append(relative)
    return tuple(exceptions)
