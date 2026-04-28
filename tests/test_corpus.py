"""Tests for Argus corpus path resolution and legacy import."""

import os
from pathlib import Path


class TestCorpusPaths:
    def test_resolve_data_root_override(self, monkeypatch, tmp_path):
        monkeypatch.setenv("ARGUS_DATA_ROOT", str(tmp_path / "runtime"))

        from argus.corpus import describe_corpus_paths

        paths = describe_corpus_paths()
        assert paths["data_root"] == str((tmp_path / "runtime").resolve())
        assert Path(paths["docs_cache_index"]).exists()

    def test_mirror_legacy_docs_cache(self, monkeypatch, tmp_path):
        legacy = tmp_path / "docs-cache"
        (legacy / "docs" / "cache" / "demo").mkdir(parents=True)
        (legacy / "docs" / "research" / "topic").mkdir(parents=True)
        (legacy / "docs" / "cache" / "demo" / "README.md").write_text("# Demo\n", encoding="utf-8")
        (legacy / "docs" / "research" / "topic" / "SUMMARY.md").write_text("# Topic\n", encoding="utf-8")

        monkeypatch.setenv("ARGUS_DATA_ROOT", str(tmp_path / "argus-data"))
        from argus.corpus import get_corpus_paths, mirror_legacy_docs_cache

        result = mirror_legacy_docs_cache(legacy, get_corpus_paths())
        assert result["docs_cache_files"] >= 1
        assert result["research_files"] >= 1
        assert Path(result["manifest_path"]).exists()

    def test_mirror_legacy_docs_cache_skips_broken_symlinks(self, monkeypatch, tmp_path):
        legacy = tmp_path / "docs-cache"
        (legacy / "docs" / "cache" / "demo").mkdir(parents=True)
        (legacy / "docs" / "external").mkdir(parents=True)
        (legacy / "docs" / "cache" / "demo" / "README.md").write_text("# Demo\n", encoding="utf-8")
        os.symlink(legacy / "missing-target", legacy / "docs" / "external" / "broken-link")

        monkeypatch.setenv("ARGUS_DATA_ROOT", str(tmp_path / "argus-data"))
        from argus.corpus import get_corpus_paths, mirror_legacy_docs_cache

        result = mirror_legacy_docs_cache(legacy, get_corpus_paths())
        assert result["docs_cache_files"] >= 1
        assert Path(result["manifest_path"]).exists()
