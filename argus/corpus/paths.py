"""Argus-owned runtime corpus layout."""

from __future__ import annotations

import json
import os
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

try:
    from platformdirs import user_data_dir
except ImportError:  # pragma: no cover - fallback for minimal environments
    user_data_dir = None


def resolve_data_root() -> Path:
    """Resolve the writable Argus data root.

    Priority:
      1. ARGUS_DATA_ROOT override
      2. platformdirs user data directory
      3. ~/.argus fallback
    """
    override = os.environ.get("ARGUS_DATA_ROOT", "").strip()
    if override:
        return Path(override).expanduser().resolve()

    if user_data_dir is not None:
        return Path(user_data_dir("argus", "argus")).expanduser().resolve()

    return (Path.home() / ".argus").resolve()


def slugify(value: str, *, default: str = "item") -> str:
    """Filesystem-safe slug for corpus targets."""
    cleaned = re.sub(r"[^a-zA-Z0-9]+", "-", value.strip().lower()).strip("-")
    return cleaned or default


@dataclass(frozen=True)
class CorpusPaths:
    """Resolved Argus runtime storage paths."""

    data_root: Path
    docs_root: Path
    docs_cache_dir: Path
    docs_cache_index: Path
    research_dir: Path
    workflow_runs_dir: Path
    snapshots_dir: Path
    imports_dir: Path

    def ensure(self) -> "CorpusPaths":
        for path in (
            self.data_root,
            self.docs_root,
            self.docs_cache_dir,
            self.research_dir,
            self.workflow_runs_dir,
            self.snapshots_dir,
            self.imports_dir,
        ):
            path.mkdir(parents=True, exist_ok=True)

        if not self.docs_cache_index.exists():
            self.docs_cache_index.write_text(
                "# Argus Cached Documentation Catalog\n\n"
                "| Name | URL | Cached | Path |\n"
                "|------|-----|--------|------|\n",
                encoding="utf-8",
            )
        return self

    def as_dict(self) -> dict[str, str]:
        return {
            "data_root": str(self.data_root),
            "docs_root": str(self.docs_root),
            "docs_cache_dir": str(self.docs_cache_dir),
            "docs_cache_index": str(self.docs_cache_index),
            "research_dir": str(self.research_dir),
            "workflow_runs_dir": str(self.workflow_runs_dir),
            "snapshots_dir": str(self.snapshots_dir),
            "imports_dir": str(self.imports_dir),
        }


def get_corpus_paths() -> CorpusPaths:
    """Build and create the default Argus corpus layout."""
    data_root = resolve_data_root()
    paths = CorpusPaths(
        data_root=data_root,
        docs_root=data_root / "docs",
        docs_cache_dir=data_root / "docs" / "cache",
        docs_cache_index=data_root / "docs" / "cache" / ".index.md",
        research_dir=data_root / "docs" / "research",
        workflow_runs_dir=data_root / "workflows" / "runs",
        snapshots_dir=data_root / "snapshots",
        imports_dir=data_root / "imports",
    )
    return paths.ensure()


def describe_corpus_paths() -> dict[str, Any]:
    """Return resolved path metadata for CLI/API/MCP inspection."""
    paths = get_corpus_paths()
    return {
        **paths.as_dict(),
        "env_override": os.environ.get("ARGUS_DATA_ROOT", "").strip() or None,
        "uses_platformdirs": user_data_dir is not None and not os.environ.get("ARGUS_DATA_ROOT"),
    }


def _copy_tree(source: Path, destination: Path) -> int:
    if not source.exists():
        return 0

    copied = 0
    for path in source.rglob("*"):
        if path.is_symlink() and not path.exists():
            continue
        relative = path.relative_to(source)
        target = destination / relative
        if path.is_dir():
            target.mkdir(parents=True, exist_ok=True)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
        copied += 1
    return copied


def mirror_legacy_docs_cache(source_root: str | Path, destination: CorpusPaths | None = None) -> dict[str, Any]:
    """Import an older docs-cache tree into the Argus-owned corpus."""
    source = Path(source_root).expanduser().resolve()
    if not source.exists():
        raise FileNotFoundError(f"legacy docs-cache source not found: {source}")

    paths = destination or get_corpus_paths()
    imported_to = paths.imports_dir / slugify(source.name or "docs-cache", default="docs-cache")
    imported_to.mkdir(parents=True, exist_ok=True)

    docs_dir = source / "docs"
    cache_count = _copy_tree(docs_dir / "cache", paths.docs_cache_dir)
    research_count = _copy_tree(docs_dir / "research", paths.research_dir)
    raw_copy_count = _copy_tree(source, imported_to)

    metadata = {
        "source": str(source),
        "import_root": str(imported_to),
        "docs_cache_files": cache_count,
        "research_files": research_count,
        "raw_copied_files": raw_copy_count,
    }
    manifest_path = imported_to / "import-manifest.json"
    manifest_path.write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
    metadata["manifest_path"] = str(manifest_path)
    return metadata
