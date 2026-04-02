"""Session-aware search flow for the broker."""

from typing import Optional

from argus.logging import get_logger
from argus.models import SearchQuery, SearchResponse

logger = get_logger("broker.session_flow")


class SessionSearchService:
    def __init__(self, session_store=None):
        self._session_store = session_store

    async def search_with_session(
        self,
        query: SearchQuery,
        search_fn,
        session_id: Optional[str] = None,
    ) -> tuple[SearchResponse, Optional[str]]:
        from argus.sessions.refinement import refine_query
        # NOTE: refine_query uses simple concatenation, not semantic understanding.
        # It works well for straightforward follow-ups but won't handle
        # complex context shifts or pronoun resolution.

        session = None
        effective_session_id = session_id

        if self._session_store is not None:
            if effective_session_id:
                session = self._session_store.get_session(effective_session_id)
            if session is None:
                session = self._session_store.create_session(effective_session_id)
                effective_session_id = session.id

        from argus.config import get_config
        refined_text = refine_query(
            query.query, session,
            max_context_chars=get_config().session_max_context_chars,
        )
        effective_query = query
        if refined_text != query.query:
            logger.debug("Query refined: %r -> %r", query.query, refined_text)
            effective_query = SearchQuery(
                query=refined_text,
                mode=query.mode,
                max_results=query.max_results,
                providers=query.providers,
            )

        response = await search_fn(effective_query)
        if self._session_store is not None and effective_session_id:
            self._session_store.add_query(
                effective_session_id,
                query=refined_text,
                mode=query.mode.value,
                results_count=response.total_results,
            )
        return response, effective_session_id
