"""
SQLite-backed budget persistence.

Persists budget tracking state to a SQLite file so budgets survive restarts.
Uses the standard library sqlite3 module -- no new dependencies.
"""

import os
import sqlite3
import time
from pathlib import Path
from typing import Optional

from argus.logging import get_logger

logger = get_logger("broker.budget_persistence")

DEFAULT_DB_PATH = "argus_budgets.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS budget_usage (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    provider TEXT NOT NULL,
    timestamp REAL NOT NULL,
    cost_usd REAL NOT NULL DEFAULT 0.0
);
CREATE INDEX IF NOT EXISTS idx_budget_provider_ts
    ON budget_usage(provider, timestamp);

CREATE TABLE IF NOT EXISTS token_balances (
    service TEXT PRIMARY KEY,
    balance REAL NOT NULL,
    updated_at REAL NOT NULL
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


class BudgetStore:
    """SQLite-backed storage for budget usage records."""

    def __init__(self, db_path: Optional[str] = None):
        self._db_path = db_path or os.environ.get(
            "ARGUS_BUDGET_DB_PATH", DEFAULT_DB_PATH
        )
        self._conn: Optional[sqlite3.Connection] = None

    def _get_conn(self) -> sqlite3.Connection:
        if self._conn is None:
            Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(self._db_path)
            self._conn.executescript(_SCHEMA)
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

    def set_token_balance(self, service: str, balance: float) -> None:
        conn = self._get_conn()
        conn.execute(
            "INSERT INTO token_balances (service, balance, updated_at) VALUES (?, ?, ?) "
            "ON CONFLICT(service) DO UPDATE SET balance = ?, updated_at = ?",
            (service, balance, time.time(), balance, time.time()),
        )
        conn.commit()

    def get_token_balance(self, service: str) -> Optional[float]:
        conn = self._get_conn()
        row = conn.execute(
            "SELECT balance FROM token_balances WHERE service = ?", (service,)
        ).fetchone()
        return float(row[0]) if row else None

    def get_all_token_balances(self) -> dict:
        conn = self._get_conn()
        rows = conn.execute("SELECT service, balance, updated_at FROM token_balances").fetchall()
        return {
            service: {"balance": balance, "updated_at": updated_at}
            for service, balance, updated_at in rows
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
