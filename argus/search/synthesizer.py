"""
Answer synthesis using LLM.

Takes retrieved context chunks and generates a coherent answer
using google/gemini-2.5-flash-lite via OpenRouter.
"""

import logging
import time
from typing import List, Optional
from dataclasses import dataclass

import requests

from .config import get_config, AskConfig
from .retriever import RetrievalResult

logger = logging.getLogger(__name__)

# Retry configuration
MAX_RETRIES = 3
RETRY_DELAY_BASE = 1.0  # seconds
RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}


@dataclass
class SynthesizedAnswer:
    """An answer generated from retrieved context."""
    answer: str
    sources: List[str]  # Content IDs used
    confidence: str  # "high", "medium", "low"
    model: str
    tokens_used: int


class AnswerSynthesizer:
    """Generate answers from retrieved context using LLM."""

    SYSTEM_PROMPT = """You are a helpful research assistant. Answer questions based on the provided context.

Rules:
1. Only use information from the provided context
2. If the context doesn't contain enough information, say so clearly
3. Cite sources by referencing the content titles when possible
4. Be concise but complete
5. If asked about something not in the context, acknowledge the limitation

Format your answer as clear, readable text. Do not use excessive formatting."""

    def __init__(self, config: Optional[AskConfig] = None):
        self.config = config or get_config()
        self.api_key = self.config.api_key
        self.base_url = self.config.base_url
        self.model = self.config.llm.model
        self.max_context_tokens = self.config.llm.max_context_tokens

        if not self.api_key:
            raise ValueError("OPENROUTER_API_KEY not set")

    def synthesize(
        self,
        query: str,
        context: List[RetrievalResult],
        max_tokens: int = 1000,
    ) -> SynthesizedAnswer:
        """
        Generate an answer from query and context.

        Args:
            query: The user's question
            context: Retrieved chunks to use as context
            max_tokens: Max tokens for the response

        Returns:
            SynthesizedAnswer with the generated response
        """
        if not context:
            return SynthesizedAnswer(
                answer="I couldn't find any relevant information to answer your question.",
                sources=[],
                confidence="low",
                model=self.model,
                tokens_used=0,
            )

        # Build context string from chunks
        context_text = self._build_context(context)
        sources = list(set(r.content_id for r in context))

        # Build the prompt
        user_prompt = f"""Context:
{context_text}

Question: {query}

Answer based on the context above:"""

        # Call the LLM
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/atlas",
            "X-Title": "Atlas Ask",
        }

        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": self.SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            "max_tokens": max_tokens,
            "temperature": 0.3,  # Lower temp for factual responses
        }

        last_error = None

        for attempt in range(MAX_RETRIES):
            try:
                response = requests.post(
                    f"{self.base_url}/chat/completions",
                    headers=headers,
                    json=payload,
                    timeout=60,
                )

                # Check for retryable errors
                if response.status_code in RETRYABLE_STATUS_CODES:
                    delay = RETRY_DELAY_BASE * (2 ** attempt)
                    logger.warning(
                        f"Synthesis request got {response.status_code}, "
                        f"retrying in {delay}s (attempt {attempt + 1}/{MAX_RETRIES})"
                    )
                    time.sleep(delay)
                    continue

                response.raise_for_status()
                data = response.json()

                answer = data["choices"][0]["message"]["content"]
                usage = data.get("usage", {})
                tokens_used = usage.get("total_tokens", 0)

                # Estimate confidence based on context quality
                confidence = self._estimate_confidence(context)

                logger.info(
                    f"Synthesized answer using {len(context)} chunks, "
                    f"{tokens_used} tokens"
                )

                return SynthesizedAnswer(
                    answer=answer,
                    sources=sources,
                    confidence=confidence,
                    model=self.model,
                    tokens_used=tokens_used,
                )

            except requests.exceptions.Timeout as e:
                last_error = e
                delay = RETRY_DELAY_BASE * (2 ** attempt)
                logger.warning(
                    f"Synthesis request timed out, "
                    f"retrying in {delay}s (attempt {attempt + 1}/{MAX_RETRIES})"
                )
                time.sleep(delay)

            except requests.RequestException as e:
                last_error = e
                logger.error(f"Synthesis request failed: {e}")
                raise

        # All retries exhausted
        logger.error(f"Synthesis request failed after {MAX_RETRIES} retries")
        raise last_error or RuntimeError("Synthesis request failed after retries")

    def _build_context(self, results: List[RetrievalResult]) -> str:
        """Build context string from retrieval results."""
        parts = []

        for i, result in enumerate(results, 1):
            # Get title from metadata if available
            title = result.metadata.get("title", result.content_id)

            # Format each chunk with source info
            chunk_text = f"[Source {i}: {title}]\n{result.text}"
            parts.append(chunk_text)

        return "\n\n---\n\n".join(parts)

    def _estimate_confidence(self, context: List[RetrievalResult]) -> str:
        """Estimate answer confidence based on context quality."""
        if not context:
            return "low"

        # Check average score
        avg_score = sum(r.score for r in context) / len(context)

        # Check if results came from both vector and keyword search
        has_vector = any(r.vector_rank is not None for r in context)
        has_keyword = any(r.keyword_rank is not None for r in context)
        has_both = has_vector and has_keyword

        # High confidence: good scores and both search types agree
        if avg_score > 0.015 and has_both and len(context) >= 3:
            return "high"

        # Medium confidence: decent scores or one strong search type
        if avg_score > 0.01 or len(context) >= 2:
            return "medium"

        return "low"

    def summarize_content(
        self,
        content: str,
        max_tokens: Optional[int] = None,
    ) -> str:
        """
        Summarize a piece of content.

        Used for enrichment during ingestion.
        """
        max_tokens = max_tokens or self.config.llm.summary_max_tokens

        prompt = f"""Summarize the following content in 2-3 sentences.
Focus on the key points and main arguments.

Content:
{content[:8000]}  # Truncate very long content

Summary:"""

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/atlas",
            "X-Title": "Atlas Ask",
        }

        payload = {
            "model": self.model,
            "messages": [
                {"role": "user", "content": prompt},
            ],
            "max_tokens": max_tokens,
            "temperature": 0.3,
        }

        try:
            response = requests.post(
                f"{self.base_url}/chat/completions",
                headers=headers,
                json=payload,
                timeout=30,
            )
            response.raise_for_status()
            data = response.json()
            return data["choices"][0]["message"]["content"]

        except requests.RequestException as e:
            logger.error(f"Summarization failed: {e}")
            return ""

    def extract_tags(
        self,
        content: str,
        max_tags: Optional[int] = None,
    ) -> List[str]:
        """
        Extract topic tags from content.

        Used for enrichment during ingestion.
        """
        max_tags = max_tags or self.config.llm.max_tags

        prompt = f"""Extract up to {max_tags} topic tags from this content.
Return only the tags as a comma-separated list, nothing else.
Tags should be lowercase, single words or short phrases.

Content:
{content[:6000]}

Tags:"""

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/atlas",
            "X-Title": "Atlas Ask",
        }

        payload = {
            "model": self.model,
            "messages": [
                {"role": "user", "content": prompt},
            ],
            "max_tokens": 100,
            "temperature": 0.3,
        }

        try:
            response = requests.post(
                f"{self.base_url}/chat/completions",
                headers=headers,
                json=payload,
                timeout=30,
            )
            response.raise_for_status()
            data = response.json()
            tags_text = data["choices"][0]["message"]["content"]

            # Parse comma-separated tags
            tags = [t.strip().lower() for t in tags_text.split(",")]
            return [t for t in tags if t and len(t) < 50][:max_tags]

        except requests.RequestException as e:
            logger.error(f"Tag extraction failed: {e}")
            return []


# Convenience function
def ask(query: str, context: List[RetrievalResult]) -> SynthesizedAnswer:
    """Generate an answer from query and context."""
    synthesizer = AnswerSynthesizer()
    return synthesizer.synthesize(query, context)
