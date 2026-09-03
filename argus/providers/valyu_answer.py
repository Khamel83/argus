"""
Valyu Answer API — AI-powered answer synthesis with real-time search.

POST https://api.valyu.ai/v1/answer
Returns SSE stream: search_results → content deltas → metadata → [DONE]
$0.10/request + variable search and AI costs.

This is NOT a search provider — it's an answer synthesis endpoint.
Callers opt in explicitly. Does not participate in broker routing.
"""

import os
from dataclasses import dataclass, field
from typing import Optional

from argus.broker.budgets import BudgetTracker
from argus.models import ProviderName

DEFAULT_TIMEOUT = 30


@dataclass
class ValyuAnswerResult:
    """Result from the Valyu Answer API."""
    answer: str = ""
    sources: list = field(default_factory=list)  # search result citations
    cost_usd: float = 0.0
    ai_usage: dict = field(default_factory=dict)
    tx_id: str = ""
    error: Optional[str] = None


def _record_valyu_answer_usage(cost_usd: float) -> None:
    if cost_usd <= 0:
        return

    tracker = BudgetTracker(persist_path=os.environ.get("ARGUS_BUDGET_DB_PATH"))
    try:
        tracker.record_usage(ProviderName.VALYU, cost=cost_usd)
    finally:
        tracker.close()


async def valyu_answer(
    query: str,
    *,
    search_type: str = "all",
    fast_mode: bool = False,
    system_instructions: Optional[str] = None,
    timeout: int = DEFAULT_TIMEOUT,
) -> ValyuAnswerResult:
    """Get an AI-synthesized answer grounded in real-time search results.

    This is a standalone function, not a broker provider.
    Callers opt in explicitly when they want synthesized answers.
    """
    return ValyuAnswerResult(
        error="valyu_answer disabled: durable spend reservation is required"
    )
