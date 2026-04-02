"""
SQLite-backed session persistence.

Persists sessions and query history so they survive restarts.
Mirrors BudgetStore pattern from argus/broker/budget_persistence.py.
"""

import sqlite3
import time
from pathlib import Path
from typing import Optional

from argus.logging import get_logger

logger = get_logger("sessions.persistence")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS session_queries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    query TEXT NOT NULL,
    mode TEXT NOT NULL DEFAULT 'discovery',
    timestamp REAL NOT NULL,
    results_count INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_session_queries_sid
    ON session_queries(session_id);

CREATE TABLE IF NOT EXISTS session_extracted_urls (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    query_index INTEGER NOT NULL,
    url TEXT NOT NULL,
    FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_extracted_urls_sid
    ON session_extracted_urls(session_id);

CREATE TABLE IF NOT EXISTS argus_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

_SCHEMA_VERSION = "1"


class SessionPersistence:
    """SQLite-backed storage for session data."""

    def __init__(self, db_path: Optional[str] = None):
        self._db_path: Optional[str] = db_path  # None = resolve lazily from config
        self._conn: Optional[sqlite3.Connection] = None

    def _resolved_db_path(self) -> str:
        if self._db_path is None:
            from argus.config import get_config
            self._db_path = get_config().db_path
        return self._db_path

    def _get_conn(self) -> sqlite3.Connection:
        if self._conn is None:
            path = self._resolved_db_path()
            Path(path).parent.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(path)
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA foreign_keys=ON")
            self._conn.executescript(_SCHEMA)

            # Check schema version
            row = self._conn.execute(
                "SELECT value FROM argus_meta WHERE key = 'schema_version'"
            ).fetchone()
            if row is None:
                self._conn.execute(
                    "INSERT INTO argus_meta (key, value) VALUES ('schema_version', ?)",
                    (_SCHEMA_VERSION,),
                )
                self._conn.commit()
                logger.info("Session store initialized (schema v%s)", _SCHEMA_VERSION)
            elif row[0] != _SCHEMA_VERSION:
                logger.warning(
                    "Session store schema version mismatch: DB has v%s, expected v%s. "
                    "Delete the database file to reset.",
                    row[0], _SCHEMA_VERSION,
                )

        return self._conn

    def save_session(self, session_id: str, created_at: float) -> None:
        conn = self._get_conn()
        conn.execute(
            "INSERT OR IGNORE INTO sessions (id, created_at) VALUES (?, ?)",
            (session_id, created_at),
        )
        conn.commit()

    def save_query(
        self,
        session_id: str,
        query: str,
        mode: str = "discovery",
        timestamp: float = 0.0,
        results_count: int = 0,
    ) -> int:
        """Save a query record. Returns the row index (query_index)."""
        conn = self._get_conn()
        ts = timestamp or time.time()
        cursor = conn.execute(
            "INSERT INTO session_queries (session_id, query, mode, timestamp, results_count) "
            "VALUES (?, ?, ?, ?, ?)",
            (session_id, query, mode, ts, results_count),
        )
        conn.commit()
        # Return 0-based index for this query in the session
        row = conn.execute(
            "SELECT COUNT(*) - 1 FROM session_queries WHERE session_id = ?",
            (session_id,),
        ).fetchone()
        return row[0]

    def save_extracted_url(
        self, session_id: str, query_index: int, url: str
    ) -> None:
        conn = self._get_conn()
        conn.execute(
            "INSERT INTO session_extracted_urls (session_id, query_index, url) "
            "VALUES (?, ?, ?)",
            (session_id, query_index, url),
        )
        conn.commit()

    def load_session(self, session_id: str) -> Optional[dict]:
        """Load a session with all its queries and extracted URLs."""
        conn = self._get_conn()

        row = conn.execute(
            "SELECT id, created_at FROM sessions WHERE id = ?",
            (session_id,),
        ).fetchone()
        if row is None:
            return None

        queries = []
        for qr in conn.execute(
            "SELECT query, mode, timestamp, results_count "
            "FROM session_queries WHERE session_id = ? ORDER BY id",
            (session_id,),
        ).fetchall():
            q_idx = len(queries)
            extracted_urls = [
                r[0]
                for r in conn.execute(
                    "SELECT url FROM session_extracted_urls "
                    "WHERE session_id = ? AND query_index = ?",
                    (session_id, q_idx),
                ).fetchall()
            ]
            queries.append({
                "query": qr[0],
                "mode": qr[1],
                "timestamp": qr[2],
                "results_count": qr[3],
                "extracted_urls": extracted_urls,
            })

        return {
            "id": row[0],
            "created_at": row[1],
            "queries": queries,
        }

    def session_exists(self, session_id: str) -> bool:
        conn = self._get_conn()
        row = conn.execute(
            "SELECT 1 FROM sessions WHERE id = ? LIMIT 1",
            (session_id,),
        ).fetchone()
        return row is not None

    def list_session_ids(self) -> list[str]:
        conn = self._get_conn()
        rows = conn.execute("SELECT id FROM sessions ORDER BY created_at DESC").fetchall()
        return [row[0] for row in rows]

    def list_sessions(self) -> list[dict]:
        """List all persisted sessions."""
        conn = self._get_conn()
        rows = conn.execute("SELECT id, created_at FROM sessions ORDER BY created_at DESC").fetchall()
        return [{"id": r[0], "created_at": r[1]} for r in rows]

    def delete_session(self, session_id: str) -> bool:
        """Delete a session and cascade to queries/extracted_urls. Returns True if it existed."""
        conn = self._get_conn()
        conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
        conn.commit()
        # row_count > 0 means the session existed (CASCADE handles child rows)
        return conn.execute("SELECT changes()").fetchone()[0] > 0

    def close(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None
