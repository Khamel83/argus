"""
Argus multi-turn session management.

Remember previous queries in a session, refine results based on context.
Conversational search that gets better as you narrow down.

Usage:
    from argus.sessions import SessionStore
    store = SessionStore()
    session = store.create_session()
    store.add_query(session.id, query="python web frameworks", mode="discovery")
    store.add_query(session.id, query="fastapi vs django", mode="discovery")
    refined = store.get_refined_context(session.id)
"""

from argus.sessions.models import QueryRecord, Session
from argus.sessions.store import SessionStore

__all__ = ["SessionStore", "Session", "QueryRecord"]
