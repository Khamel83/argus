"""Session store backed by the authoritative persistence repository."""

import uuid
from typing import Dict, Optional

from argus.logging import get_logger
from argus.sessions.models import QueryRecord, Session

logger = get_logger("sessions")


class SessionStore:
    """Process-local session cache with authoritative durable persistence."""

    def __init__(
        self,
        persist: bool = True,
        db_path: Optional[str] = None,
        repository=None,
    ):
        self._sessions: Dict[str, Session] = {}
        self._persist = persist
        self._db = repository
        if persist:
            if self._db is None:
                from argus.persistence.search_ledger import (
                    create_search_ledger_repository,
                )

                db_url = f"sqlite:///{db_path}" if db_path else None
                self._db = create_search_ledger_repository(db_url)
            logger.info("Session persistence enabled")

    def _load_session(self, session_id: str) -> Optional[Session]:
        if not self._db:
            return None
        session = self._db.load_session(session_id)
        if session is None:
            return None
        cached = self._sessions.get(session_id)
        if cached is not None:
            cached.created_at = session.created_at
            cached.queries = session.queries
            return cached
        self._sessions[session_id] = session
        return session

    def create_session(self, session_id: Optional[str] = None) -> Session:
        sid = session_id or str(uuid.uuid4())[:8]
        if self._persist and self._db:
            candidate = Session(id=sid)
            self._db.create_session(sid, candidate.created_at)
            loaded = self._load_session(sid)
            if loaded is not None:
                return loaded
            session = candidate
        elif sid in self._sessions:
            return self._sessions[sid]
        else:
            session = Session(id=sid)
        self._sessions[sid] = session
        logger.debug("Created session %s", sid)
        return session

    def get_session(self, session_id: str) -> Optional[Session]:
        if self._persist and self._db:
            return self._load_session(session_id)
        return self._sessions.get(session_id)

    def add_query(
        self,
        session_id: str,
        query: str,
        mode: str = "discovery",
        results_count: int = 0,
    ) -> Optional[Session]:
        session = self._sessions.get(session_id)
        if session is None:
            return None
        record = QueryRecord(
            query=query,
            mode=mode,
            results_count=results_count,
        )
        if self._persist and self._db:
            self._db.append_session_query(
                session_id,
                query=query,
                mode=mode,
                timestamp=record.timestamp,
                results_count=results_count,
            )
            session = self._load_session(session_id)
        else:
            session.queries.append(record)
        logger.debug(
            "Session %s: added query #%d: %s",
            session_id,
            len(session.queries),
            query[:50],
        )
        return session

    def add_extracted_url(self, session_id: str, query_index: int, url: str) -> None:
        session = self._sessions.get(session_id)
        if session is None:
            return
        if not 0 <= query_index < len(session.queries):
            return
        if self._persist and self._db:
            self._db.append_session_extracted_url(session_id, query_index, url)
            self._load_session(session_id)
        else:
            session.queries[query_index].extracted_urls.append(url)

    def list_sessions(self) -> list[Session]:
        if self._persist and self._db:
            for session_id in self._db.list_session_ids():
                if session_id not in self._sessions:
                    self._load_session(session_id)
        return list(self._sessions.values())
