"""
SQLite persistence for search history.

Non-fatal: all operations are wrapped with exception handling so
persistence failures never break search.
"""

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from argus.config import get_config
from argus.logging import get_logger
from argus.models import SearchQuery, SearchResponse

logger = get_logger("persistence.db")

_db_path: Optional[str] = None


def _get_db_path() -> str:
    global _db_path
    if _db_path:
        return _db_path
    _db_path = get_config().db_path
    return _db_path


def _get_connection() -> sqlite3.Connection:
    path = _get_db_path()
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.row_factory = sqlite3.Row
    return conn


_SCHEMA = """
CREATE TABLE IF NOT EXISTS search_queries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    query_text TEXT NOT NULL,
    mode TEXT NOT NULL,
    max_results INTEGER DEFAULT 10,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS search_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    query_id INTEGER NOT NULL REFERENCES search_queries(id),
    search_run_id TEXT UNIQUE NOT NULL,
    status TEXT NOT NULL DEFAULT 'started',
    total_results INTEGER DEFAULT 0,
    cached INTEGER DEFAULT 0,
    created_at TEXT DEFAULT (datetime('now')),
    finished_at TEXT
);

CREATE TABLE IF NOT EXISTS search_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL REFERENCES search_runs(id),
    url TEXT NOT NULL,
    title TEXT DEFAULT '',
    snippet TEXT DEFAULT '',
    domain TEXT DEFAULT '',
    provider TEXT DEFAULT '',
    score REAL DEFAULT 0.0,
    final_rank INTEGER DEFAULT 0,
    metadata_json TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS provider_usage (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL REFERENCES search_runs(id),
    provider TEXT NOT NULL,
    status TEXT NOT NULL,
    results_count INTEGER DEFAULT 0,
    latency_ms INTEGER DEFAULT 0,
    error TEXT,
    budget_remaining REAL,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS argus_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

_SCHEMA_VERSION = "1"


def init_db() -> None:
    conn = _get_connection()
    try:
        conn.executescript(_SCHEMA)

        # Check schema version
        row = conn.execute("SELECT value FROM argus_meta WHERE key = 'schema_version'").fetchone()
        if row is None:
            conn.execute(
                "INSERT INTO argus_meta (key, value) VALUES ('schema_version', ?)",
                (_SCHEMA_VERSION,),
            )
            logger.info("SQLite database initialized (schema v%s): %s", _SCHEMA_VERSION, _get_db_path())
        elif row[0] != _SCHEMA_VERSION:
            logger.warning(
                "Schema version mismatch: DB has v%s, expected v%s. "
                "Delete %s to reset, or check release notes for migration steps.",
                row[0], _SCHEMA_VERSION, _get_db_path(),
            )
        else:
            logger.info("SQLite database ready (schema v%s): %s", _SCHEMA_VERSION, _get_db_path())

        conn.commit()
    finally:
        conn.close()


def persist_search(query_text: str, mode: str, response: SearchResponse) -> Optional[str]:
    run_id = response.search_run_id or uuid.uuid4().hex[:16]

    conn = _get_connection()
    try:
        conn.execute("INSERT INTO search_queries (query_text, mode, max_results) VALUES (?, ?, ?)",
                     (query_text, mode, response.total_results))
        query_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

        conn.execute(
            "INSERT INTO search_runs (query_id, search_run_id, status, total_results, cached, finished_at) "
            "VALUES (?, ?, 'completed', ?, ?, ?)",
            (query_id, run_id, len(response.results), int(response.cached),
             datetime.now(tz=timezone.utc).isoformat()),
        )
        run_db_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

        for rank, r in enumerate(response.results):
            conn.execute(
                "INSERT INTO search_results (run_id, url, title, snippet, domain, provider, score, final_rank, metadata_json) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (run_db_id, r.url, r.title, r.snippet, r.domain or "",
                 r.provider.value if r.provider else "", r.score, rank,
                 json.dumps(r.metadata) if r.metadata else None),
            )

        for trace in response.traces:
            conn.execute(
                "INSERT INTO provider_usage (run_id, provider, status, results_count, latency_ms, error, budget_remaining) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (run_db_id, trace.provider.value, trace.status, trace.results_count,
                 trace.latency_ms, trace.error, trace.budget_remaining),
            )

        conn.commit()
        logger.debug("Persisted search run %s with %d results", run_id, len(response.results))
        return run_id
    finally:
        conn.close()


class SearchPersistenceGateway:
    """Non-fatal persistence boundary for completed search responses."""

    def __init__(self):
        try:
            init_db()
        except Exception as exc:
            logger.warning("Failed to initialize search database: %s", exc)

    def record_completed_search(self, query: SearchQuery, response: SearchResponse) -> Optional[str]:
        try:
            return persist_search(query.query, query.mode.value, response)
        except Exception as exc:
            logger.warning("Failed to persist search: %s", exc)
            return None
