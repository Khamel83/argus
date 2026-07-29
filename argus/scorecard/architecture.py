"""Shared fail-closed inventory of remaining presentation authority exceptions."""

from __future__ import annotations

import ast
from pathlib import Path


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
    """Scan the complete route/presenter surface for direct authority imports."""
    exceptions: list[str] = []
    api_root = repository_root / "argus" / "api"
    paths = {
        *api_root.glob("routes_*.py"),
        *api_root.glob("*present*.py"),
    }
    for path in sorted(paths):
        relative = path.relative_to(repository_root).as_posix()
        imports = _imports(path)
        if any(
            module == prefix or module.startswith(f"{prefix}.")
            for module in imports
            for prefix in FORBIDDEN_AUTHORITY_PREFIXES
        ):
            exceptions.append(relative)
    return tuple(exceptions)
