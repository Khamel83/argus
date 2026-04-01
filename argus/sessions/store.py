"""
Session store with optional SQLite persistence.

In-memory dict for fast reads. Persists to SQLite on writes.
Loads from SQLite on cache miss (restart recovery).
"""

import uuid
from datetime import datetime
from typing import Dict, Optional

from argus.logging import get_logger
from argus.sessions.models import QueryRecord, Session
from argus.sessions.persistence import SessionPersistence

logger = get_logger("sessions")


class SessionStore:
    """Store for search sessions with optional SQLite persistence."""

    def __init__(self, persist: bool = True, db_path: Optional[str] = None):
        self._sessions: Dict[str, Session] = {}
        self._persist = persist
        self._db: Optional[SessionPersistence] = None
        if persist:
            try:
                self._db = SessionPersistence(db_path=db_path)
                self._load_all()
                logger.info("Session persistence enabled, loaded %d sessions", len(self._sessions))
            except Exception as e:
                logger.warning("Session persistence failed to init, using in-memory only: %s", e)
                self._persist = False

    def _load_all(self) -> None:
        """Load all persisted sessions from SQLite into memory."""
        if not self._db:
            return
        for row in self._db.list_sessions():
            self._load_session(row["id"])

    def _load_session(self, session_id: str) -> Optional[Session]:
        """Load a single session from SQLite into memory."""
        if not self._db:
            return None
        data = self._db.load_session(session_id)
        if data is None:
            return None
        session = Session(
            id=data["id"],
            created_at=datetime.fromtimestamp(data["created_at"]),
        )
        for qd in data["queries"]:
            record = QueryRecord(
                query=qd["query"],
                mode=qd["mode"],
                timestamp=datetime.fromtimestamp(qd["timestamp"]),
                results_count=qd["results_count"],
                extracted_urls=qd["extracted_urls"],
            )
            session.queries.append(record)
        self._sessions[session_id] = session
        return session

    def create_session(self, session_id: Optional[str] = None) -> Session:
        """Create a new session or return existing one."""
        sid = session_id or str(uuid.uuid4())[:8]
        if sid in self._sessions:
            return self._sessions[sid]

        # Check persistence layer (in case created in a prior process)
        if self._persist and sid in [s["id"] for s in (self._db.list_sessions() if self._db else [])]:
            loaded = self._load_session(sid)
            if loaded:
                return loaded

        session = Session(id=sid)
        self._sessions[sid] = session
        if self._persist and self._db:
            self._db.save_session(sid, session.created_at.timestamp())
        logger.debug("Created session %s", sid)
        return session

    def get_session(self, session_id: str) -> Optional[Session]:
        """Get a session by ID, loading from persistence if not in memory."""
        if session_id in self._sessions:
            return self._sessions[session_id]
        # Cache miss — try loading from SQLite
        if self._persist:
            return self._load_session(session_id)
        return None

    def add_query(
        self,
        session_id: str,
        query: str,
        mode: str = "discovery",
        results_count: int = 0,
    ) -> Optional[Session]:
        """Add a query to a session. Returns the updated session or None."""
        session = self._sessions.get(session_id)
        if session is None:
            return None
        record = QueryRecord(
            query=query,
            mode=mode,
            results_count=results_count,
        )
        session.queries.append(record)
        logger.debug("Session %s: added query #%d: %s", session_id, len(session.queries), query[:50])

        if self._persist and self._db:
            self._db.save_query(
                session_id,
                query=query,
                mode=mode,
                timestamp=record.timestamp.timestamp(),
                results_count=results_count,
            )
        return session

    def add_extracted_url(self, session_id: str, query_index: int, url: str) -> None:
        """Record that a URL was extracted for a specific query in a session."""
        session = self._sessions.get(session_id)
        if session is None:
            return
        if 0 <= query_index < len(session.queries):
            session.queries[query_index].extracted_urls.append(url)
        if self._persist and self._db:
            self._db.save_extracted_url(session_id, query_index, url)

    def list_sessions(self) -> list:
        """List all active sessions."""
        return list(self._sessions.values())
