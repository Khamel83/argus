"""
SQLite-backed budget persistence.

Persists budget tracking state to a SQLite file so budgets survive restarts.
Uses the standard library sqlite3 module -- no new dependencies.
"""

import sqlite3
import time
from pathlib import Path
from typing import Optional

from argus.logging import get_logger

logger = get_logger("broker.budget_persistence")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS budget_usage (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    provider TEXT NOT NULL,
    timestamp REAL NOT NULL,
    cost_usd REAL NOT NULL DEFAULT 0.0
);
CREATE INDEX IF NOT EXISTS idx_budget_provider_ts
    ON budget_usage(provider, timestamp);

CREATE TABLE IF NOT EXISTS service_credits (
    service TEXT PRIMARY KEY,
    balance REAL NOT NULL,
    updated_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS provider_overrides (
    provider TEXT PRIMARY KEY,
    disabled INTEGER NOT NULL DEFAULT 0,
    disabled_at REAL,
    reason TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS extraction_cache (
    url_hash TEXT PRIMARY KEY,
    title TEXT DEFAULT '',
    text TEXT DEFAULT '',
    author TEXT DEFAULT '',
    date TEXT,
    word_count INTEGER DEFAULT 0,
    extractor TEXT DEFAULT '',
    cached_at REAL NOT NULL
);
"""


_SESSION_SCHEMA = """
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
CREATE INDEX IF NOT EXISTS idx_session_queries_sid ON session_queries(session_id);
CREATE TABLE IF NOT EXISTS session_extracted_urls (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    query_index INTEGER NOT NULL,
    url TEXT NOT NULL,
    FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_extracted_urls_sid ON session_extracted_urls(session_id);
"""


def _migrate_legacy_budget_db(conn: sqlite3.Connection, new_db_path: str) -> None:
    """One-time migration: merge argus_budgets.db into the unified DB if it still exists."""
    old_path = Path(new_db_path).parent / "argus_budgets.db"
    if not old_path.exists() or old_path.resolve() == Path(new_db_path).resolve():
        return

    logger.info("Migrating legacy %s → %s", old_path, new_db_path)
    try:
        conn.execute("PRAGMA foreign_keys = OFF")
        # Ensure session tables exist before copying session rows
        conn.executescript(_SESSION_SCHEMA)
        escaped = str(old_path).replace("'", "''")
        conn.execute(f"ATTACH DATABASE '{escaped}' AS legacy")

        # (legacy_table, main_table) — token_balances was renamed to service_credits
        table_map = (
            ("budget_usage", "budget_usage"),
            ("token_balances", "service_credits"),
            ("extraction_cache", "extraction_cache"),
            ("sessions", "sessions"),
            ("session_queries", "session_queries"),
            ("session_extracted_urls", "session_extracted_urls"),
        )
        for legacy_name, main_name in table_map:
            try:
                conn.execute(
                    f"INSERT OR IGNORE INTO main.{main_name} SELECT * FROM legacy.{legacy_name}"
                )
                logger.debug("Migrated legacy.%s → main.%s", legacy_name, main_name)
            except sqlite3.OperationalError as exc:
                logger.debug("Skipping legacy.%s: %s", legacy_name, exc)

        conn.commit()
        conn.execute("DETACH DATABASE legacy")
        conn.execute("PRAGMA foreign_keys = ON")

        bak_path = old_path.with_suffix(".db.bak")
        old_path.rename(bak_path)
        logger.info("Migration complete — legacy DB renamed to %s", bak_path.name)
    except Exception as exc:
        logger.warning("Legacy DB migration failed (non-fatal): %s", exc)
        try:
            conn.execute("DETACH DATABASE legacy")
        except Exception:
            pass
        conn.execute("PRAGMA foreign_keys = ON")


class BudgetStore:
    """SQLite-backed storage for budget usage records."""

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
            # Rename token_balances → service_credits in existing DBs before running schema
            tables = {
                r[0]
                for r in self._conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
            if "token_balances" in tables and "service_credits" not in tables:
                self._conn.execute(
                    "ALTER TABLE token_balances RENAME TO service_credits"
                )
                self._conn.commit()
                logger.info("Renamed token_balances → service_credits")
            self._conn.executescript(_SCHEMA)
            _migrate_legacy_budget_db(self._conn, path)
        return self._conn

    def record_usage(self, provider: str, cost_usd: float = 0.0) -> None:
        conn = self._get_conn()
        conn.execute(
            "INSERT INTO budget_usage (provider, timestamp, cost_usd) VALUES (?, ?, ?)",
            (provider, time.time(), cost_usd),
        )
        conn.commit()

    def get_monthly_usage(self, provider: str) -> float:
        cutoff = time.time() - (30 * 24 * 3600)
        conn = self._get_conn()
        row = conn.execute(
            "SELECT COALESCE(SUM(cost_usd), 0) FROM budget_usage "
            "WHERE provider = ? AND timestamp >= ?",
            (provider, cutoff),
        ).fetchone()
        return float(row[0])

    def get_usage_count(self, provider: str) -> int:
        cutoff = time.time() - (30 * 24 * 3600)
        conn = self._get_conn()
        row = conn.execute(
            "SELECT COUNT(*) FROM budget_usage "
            "WHERE provider = ? AND timestamp >= ?",
            (provider, cutoff),
        ).fetchone()
        return row[0]

    def close(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None

    def set_service_credit(self, service: str, balance: float) -> None:
        conn = self._get_conn()
        conn.execute(
            "INSERT INTO service_credits (service, balance, updated_at) VALUES (?, ?, ?) "
            "ON CONFLICT(service) DO UPDATE SET balance = ?, updated_at = ?",
            (service, balance, time.time(), balance, time.time()),
        )
        conn.commit()

    def get_service_credit(self, service: str) -> Optional[float]:
        conn = self._get_conn()
        row = conn.execute(
            "SELECT balance FROM service_credits WHERE service = ?", (service,)
        ).fetchone()
        return float(row[0]) if row else None

    def get_all_service_credits(self) -> dict:
        conn = self._get_conn()
        rows = conn.execute("SELECT service, balance, updated_at FROM service_credits").fetchall()
        return {
            service: {"balance": balance, "updated_at": updated_at}
            for service, balance, updated_at in rows
        }

    def set_provider_override(
        self, provider: str, disabled: bool, reason: str = ""
    ) -> None:
        conn = self._get_conn()
        conn.execute(
            "INSERT INTO provider_overrides (provider, disabled, disabled_at, reason) "
            "VALUES (?, ?, ?, ?) "
            "ON CONFLICT(provider) DO UPDATE SET disabled = ?, disabled_at = ?, reason = ?",
            (provider, int(disabled), time.time(), reason,
             int(disabled), time.time(), reason),
        )
        conn.commit()

    def get_provider_overrides(self) -> dict[str, dict]:
        """Return all manually disabled providers as {provider: {disabled, reason}}."""
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT provider, disabled, disabled_at, reason FROM provider_overrides "
            "WHERE disabled = 1"
        ).fetchall()
        return {
            provider: {"disabled": bool(disabled), "disabled_at": disabled_at, "reason": reason}
            for provider, disabled, disabled_at, reason in rows
        }

    def get_extraction(self, url_hash: str, ttl_seconds: float) -> Optional[dict]:
        cutoff = time.time() - ttl_seconds
        conn = self._get_conn()
        row = conn.execute(
            "SELECT title, text, author, date, word_count, extractor, cached_at "
            "FROM extraction_cache WHERE url_hash = ? AND cached_at >= ?",
            (url_hash, cutoff),
        ).fetchone()
        if not row:
            return None
        return {
            "title": row[0], "text": row[1], "author": row[2], "date": row[3],
            "word_count": row[4], "extractor": row[5], "cached_at": row[6],
        }

    def put_extraction(self, url_hash: str, title: str, text: str,
                       author: str, date: Optional[str], word_count: int,
                       extractor: str) -> None:
        conn = self._get_conn()
        conn.execute(
            "INSERT OR REPLACE INTO extraction_cache "
            "(url_hash, title, text, author, date, word_count, extractor, cached_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (url_hash, title, text, author, date, word_count, extractor, time.time()),
        )
        conn.commit()
