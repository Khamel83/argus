"""
Valyu Answer API — AI-powered answer synthesis with real-time search.

POST https://api.valyu.ai/v1/answer
Returns SSE stream: search_results → content deltas → metadata → [DONE]
$0.10/request + variable search and AI costs.

This is NOT a search provider — it's an answer synthesis endpoint.
Callers opt in explicitly. Does not participate in broker routing.
"""

import json
import time
from dataclasses import dataclass, field
from typing import Optional

import httpx

from argus.config import get_config
from argus.logging import get_logger

logger = get_logger("providers.valyu_answer")

VALYU_ANSWER_URL = "https://api.valyu.ai/v1/answer"
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
    config = get_config()
    if not config.valyu.api_key:
        return ValyuAnswerResult(error="valyu_answer: no API key configured")

    headers = {
        "X-API-Key": config.valyu.api_key,
        "Content-Type": "application/json",
    }
    payload: dict = {
        "query": query,
        "search_type": search_type,
        "fast_mode": fast_mode,
    }
    if system_instructions:
        payload["system_instructions"] = system_instructions

    start = time.monotonic()
    answer_chunks = []
    sources = []

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            async with client.stream(
                "POST", VALYU_ANSWER_URL, json=payload, headers=headers
            ) as resp:
                resp.raise_for_status()
                async for line in resp.aiter_lines():
                    if not line.startswith("data: "):
                        continue
                    data_str = line[6:]
                    if data_str.strip() == "[DONE]":
                        break

                    try:
                        data = json.loads(data_str)
                    except json.JSONDecodeError:
                        continue

                    # search_results event
                    if "search_results" in data:
                        sources.extend(data["search_results"])

                    # content event (OpenAI-compatible delta)
                    elif "choices" in data:
                        delta = data["choices"][0].get("delta", {})
                        content = delta.get("content", "")
                        if content:
                            answer_chunks.append(content)

                    # metadata event
                    elif "success" in data and "cost" in data:
                        cost_info = data.get("cost", {})
                        return ValyuAnswerResult(
                            answer="".join(answer_chunks),
                            sources=data.get("search_results", sources),
                            cost_usd=cost_info.get("total_deduction_dollars", 0),
                            ai_usage=data.get("ai_usage", {}),
                            tx_id=data.get("tx_id", ""),
                        )

        latency_ms = int((time.monotonic() - start) * 1000)
        logger.info(
            "Valyu answer completed: query=%r latency=%dms sources=%d cost=$%.4f",
            query[:60], latency_ms, len(sources), 0,
        )

        return ValyuAnswerResult(
            answer="".join(answer_chunks),
            sources=sources,
            cost_usd=0,
        )

    except httpx.HTTPStatusError as e:
        logger.warning("Valyu answer failed (HTTP %s): %s", e.response.status_code, e)
        try:
            body = e.response.json()
            error_msg = body.get("error", str(e))
        except Exception:
            error_msg = str(e)
        return ValyuAnswerResult(error=f"valyu_answer: {error_msg}")

    except Exception as e:
        logger.warning("Valyu answer failed: %s", e)
        return ValyuAnswerResult(error=f"valyu_answer: {e}")
