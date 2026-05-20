"""Read-only aggregation queries for the usage dashboard.

Queries the main SQLite DB directly via sqlite3 (not SQLAlchemy) for
simplicity and to avoid coupling dashboard rendering to ORM sessions.
All functions return [] on any error — the dashboard is a view, not a
critical path, so DB hiccups must not break the UI.
"""

from __future__ import annotations

import re
import sqlite3
from typing import Any

from argus.config import get_config
from argus.logging import get_logger

logger = get_logger("api.usage")


def _main_db_path() -> str | None:
    """Extract SQLite file path from configured ARGUS_DB_URL.

    Returns None for non-SQLite URLs (postgres, etc.) — dashboard queries
    will return empty lists in that case.
    """
    db_url = get_config().db_url
    match = re.match(r"sqlite(?:\+\w+)?:///(.+)$", db_url)
    if not match:
        return None
    return match.group(1)


def _connect() -> sqlite3.Connection | None:
    path = _main_db_path()
    if not path:
        return None
    try:
        conn = sqlite3.connect(path, timeout=2.0)
        conn.row_factory = sqlite3.Row
        return conn
    except sqlite3.Error as exc:
        logger.warning("usage: failed to open main DB at %s: %s", path, exc)
        return None


def get_daily_query_counts(days: int = 30) -> list[dict[str, Any]]:
    """Return per-day, per-machine search result counts for the last N days."""
    conn = _connect()
    if conn is None:
        return []
    try:
        cur = conn.execute(
            """
            SELECT DATE(created_at) AS day,
                   COALESCE(NULLIF(machine, ''), 'unknown') AS machine,
                   COUNT(*) AS count
            FROM search_results
            WHERE created_at >= datetime('now', ?)
            GROUP BY day, machine
            ORDER BY day ASC
            """,
            (f"-{int(days)} days",),
        )
        return [dict(row) for row in cur.fetchall()]
    except sqlite3.Error as exc:
        logger.warning("usage: daily_query_counts failed: %s", exc)
        return []
    finally:
        conn.close()


def get_machine_summary(days: int = 30) -> list[dict[str, Any]]:
    """Return per-machine totals: queries in window, queries today, last seen."""
    conn = _connect()
    if conn is None:
        return []
    try:
        cur = conn.execute(
            """
            SELECT COALESCE(NULLIF(machine, ''), 'unknown') AS machine,
                   COUNT(*) AS total,
                   SUM(CASE WHEN DATE(created_at) = DATE('now') THEN 1 ELSE 0 END) AS today,
                   MAX(created_at) AS last_seen
            FROM search_results
            WHERE created_at >= datetime('now', ?)
            GROUP BY machine
            ORDER BY total DESC
            """,
            (f"-{int(days)} days",),
        )
        return [dict(row) for row in cur.fetchall()]
    except sqlite3.Error as exc:
        logger.warning("usage: machine_summary failed: %s", exc)
        return []
    finally:
        conn.close()


def get_provider_activity(days: int = 7) -> list[dict[str, Any]]:
    """Return per-provider call counts, success rate, and avg latency."""
    conn = _connect()
    if conn is None:
        return []
    try:
        cur = conn.execute(
            """
            SELECT provider,
                   COUNT(*) AS calls,
                   SUM(CASE WHEN status = 'success' THEN 1 ELSE 0 END) AS successes,
                   ROUND(AVG(latency_ms)) AS avg_latency_ms
            FROM provider_usage
            WHERE created_at >= datetime('now', ?)
            GROUP BY provider
            ORDER BY calls DESC
            """,
            (f"-{int(days)} days",),
        )
        rows = []
        for row in cur.fetchall():
            d = dict(row)
            calls = d.get("calls") or 0
            successes = d.get("successes") or 0
            d["success_rate"] = round(100.0 * successes / calls, 1) if calls else 0.0
            rows.append(d)
        return rows
    except sqlite3.Error as exc:
        logger.warning("usage: provider_activity failed: %s", exc)
        return []
    finally:
        conn.close()
