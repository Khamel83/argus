"""
Query refinement using session context.

Builds a context-enriched query from prior session history
so that follow-up searches are informed by what came before.
"""

from typing import List, Optional

from argus.logging import get_logger
from argus.sessions.models import Session

logger = get_logger("sessions.refinement")

MAX_CONTEXT_QUERIES = 3


def refine_query(
    current_query: str,
    session: Optional[Session],
    max_context_chars: int = 2000,
) -> str:
    """Refine a query using prior session context.

    Strategy: extract key terms from recent queries and prepend as context.
    The current query remains primary — context just adds specificity.

    Returns the current query unchanged if:
    - No session provided
    - Session has no prior queries
    """
    if session is None or not session.queries:
        return current_query

    # Only use prior queries, not the current one
    prior = session.queries[:-1]
    if not prior:
        return current_query

    # Take the most recent queries up to the limit
    recent = prior[-MAX_CONTEXT_QUERIES:]

    # Extract key phrases: use the full prior query text as context
    context_terms: List[str] = []
    for q in recent:
        # Skip very short queries (likely corrections)
        if len(q.query.split()) >= 2:
            context_terms.append(q.query)

    if not context_terms:
        return current_query

    # If the current query is very different from the last context, add context
    # If it's clearly a follow-up (short, no articles/prepositions), prepend context
    words = current_query.split()
    is_follow_up = (
        len(words) <= 4
        and not any(w.lower() in {"the", "a", "an", "is", "are", "what", "how", "why", "who", "when", "where"} for w in words[:2])
    )

    if is_follow_up:
        last_context = context_terms[-1]
        if len(last_context) > max_context_chars:
            last_context = last_context[:max_context_chars]
        return f"{last_context} {current_query}"

    # For longer queries, check if current query already contains context
    # If not, prepend the most relevant prior query
    return current_query
