"""
Embedding generation via OpenRouter.

Uses openai/text-embedding-3-small through OpenRouter's API.
All billing goes through OpenRouter for single dashboard.
"""

import logging
import time
from typing import List, Optional

import requests

from .config import get_config, AskConfig

logger = logging.getLogger(__name__)

# Retry configuration
MAX_RETRIES = 3
RETRY_DELAY_BASE = 1.0  # seconds
RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}


class EmbeddingClient:
    """Generate embeddings via OpenRouter."""

    def __init__(self, config: Optional[AskConfig] = None, spend_tracker=None):
        self.config = config or get_config()
        self.api_key = self.config.api_key
        self.base_url = self.config.base_url
        self.model = self.config.embeddings.model
        self.dimensions = self.config.embeddings.dimensions
        self._spend_tracker = spend_tracker  # callable(tokens, cost_usd) or None

        if not self.api_key:
            raise ValueError("OPENROUTER_API_KEY not set")

    def embed(self, text: str) -> List[float]:
        """Generate embedding for a single text."""
        embeddings = self.embed_batch([text])
        return embeddings[0] if embeddings else []

    def embed_batch(self, texts: List[str]) -> List[List[float]]:
        """Generate embeddings for multiple texts with retry logic."""
        if not texts:
            return []

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/atlas",
            "X-Title": "Atlas Ask",
        }

        payload = {
            "model": self.model,
            "input": texts,
        }

        last_error = None

        for attempt in range(MAX_RETRIES):
            try:
                response = requests.post(
                    f"{self.base_url}/embeddings",
                    headers=headers,
                    json=payload,
                    timeout=60,
                )

                # Check for retryable errors
                if response.status_code in RETRYABLE_STATUS_CODES:
                    delay = RETRY_DELAY_BASE * (2 ** attempt)
                    logger.warning(
                        f"Embedding request got {response.status_code}, "
                        f"retrying in {delay}s (attempt {attempt + 1}/{MAX_RETRIES})"
                    )
                    time.sleep(delay)
                    continue

                response.raise_for_status()
                data = response.json()

                # Extract embeddings in order
                embeddings = [None] * len(texts)
                for item in data.get("data", []):
                    idx = item.get("index", 0)
                    embeddings[idx] = item.get("embedding", [])

                # Log usage and record spend
                usage = data.get("usage", {})
                total_tokens = usage.get("total_tokens", 0)
                logger.debug(
                    f"Embedded {len(texts)} texts, "
                    f"tokens: {total_tokens}"
                )
                if total_tokens and self._spend_tracker:
                    cost = total_tokens * 0.02 / 1_000_000
                    self._spend_tracker(total_tokens, cost)

                return embeddings

            except requests.exceptions.Timeout as e:
                last_error = e
                delay = RETRY_DELAY_BASE * (2 ** attempt)
                logger.warning(
                    f"Embedding request timed out, "
                    f"retrying in {delay}s (attempt {attempt + 1}/{MAX_RETRIES})"
                )
                time.sleep(delay)

            except requests.RequestException as e:
                last_error = e
                logger.error(f"Embedding request failed: {e}")
                raise

        # All retries exhausted
        logger.error(f"Embedding request failed after {MAX_RETRIES} retries")
        raise last_error or RuntimeError("Embedding request failed after retries")

    def embed_chunks(
        self,
        chunks: List[str],
        batch_size: Optional[int] = None
    ) -> List[List[float]]:
        """
        Embed a list of chunks in batches.

        Args:
            chunks: List of text chunks to embed
            batch_size: Optional override for batch size

        Returns:
            List of embeddings, same order as input chunks
        """
        batch_size = batch_size or self.config.embeddings.batch_size
        all_embeddings = []

        for i in range(0, len(chunks), batch_size):
            batch = chunks[i:i + batch_size]
            embeddings = self.embed_batch(batch)
            all_embeddings.extend(embeddings)
            logger.info(f"Embedded batch {i // batch_size + 1}, {len(batch)} chunks")

        return all_embeddings


# Convenience function
def embed_text(text: str) -> List[float]:
    """Embed a single piece of text."""
    client = EmbeddingClient()
    return client.embed(text)


def embed_texts(texts: List[str]) -> List[List[float]]:
    """Embed multiple texts."""
    client = EmbeddingClient()
    return client.embed_chunks(texts)
