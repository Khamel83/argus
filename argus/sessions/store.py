"""
In-memory session store.

Simple dict-backed store for multi-turn search sessions.
"""

import uuid
from typing import Dict, Optional

from argus.logging import get_logger
from argus.sessions.models import QueryRecord, Session

logger = get_logger("sessions")


class SessionStore:
    """In-memory store for search sessions."""

    def __init__(self):
        self._sessions: Dict[str, Session] = {}

    def create_session(self, session_id: Optional[str] = None) -> Session:
        """Create a new session or return existing one."""
        sid = session_id or str(uuid.uuid4())[:8]
        if sid in self._sessions:
            return self._sessions[sid]
        session = Session(id=sid)
        self._sessions[sid] = session
        logger.debug("Created session %s", sid)
        return session

    def get_session(self, session_id: str) -> Optional[Session]:
        """Get a session by ID."""
        return self._sessions.get(session_id)

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
        return session

    def add_extracted_url(self, session_id: str, query_index: int, url: str) -> None:
        """Record that a URL was extracted for a specific query in a session."""
        session = self._sessions.get(session_id)
        if session is None:
            return
        if 0 <= query_index < len(session.queries):
            session.queries[query_index].extracted_urls.append(url)

    def list_sessions(self) -> list:
        """List all active sessions."""
        return list(self._sessions.values())
