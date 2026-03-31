"""
Content chunking for embedding and retrieval.

Splits documents into chunks suitable for embedding while preserving
context through paragraph boundaries and overlap.
"""

import re
import logging
from typing import List, Tuple, Optional
from dataclasses import dataclass

import tiktoken

from .config import get_config, AskConfig

logger = logging.getLogger(__name__)

# Use cl100k_base tokenizer (same as OpenAI embeddings)
_tokenizer = None


def get_tokenizer():
    """Get or create the tokenizer."""
    global _tokenizer
    if _tokenizer is None:
        _tokenizer = tiktoken.get_encoding("cl100k_base")
    return _tokenizer


def count_tokens(text: str) -> int:
    """Count tokens in text."""
    return len(get_tokenizer().encode(text))


@dataclass
class Chunk:
    """A chunk of content ready for embedding."""
    content_id: str
    chunk_index: int
    text: str
    token_count: int
    metadata: dict


class ContentChunker:
    """Split content into chunks for embedding."""

    def __init__(self, config: Optional[AskConfig] = None):
        self.config = config or get_config()
        self.max_tokens = self.config.chunking.max_chunk_tokens
        self.overlap_tokens = self.config.chunking.overlap_tokens
        self.tokenizer = get_tokenizer()

    def chunk_text(
        self,
        text: str,
        content_id: str,
        metadata: Optional[dict] = None
    ) -> List[Chunk]:
        """
        Split text into chunks.

        Strategy:
        1. Split on paragraph boundaries (double newlines)
        2. Merge small paragraphs
        3. Split large paragraphs if needed
        4. Add overlap between chunks for context continuity
        """
        if not text or not text.strip():
            return []

        metadata = metadata or {}

        # Split into paragraphs
        paragraphs = self._split_paragraphs(text)

        # Build chunks from paragraphs
        chunks = []
        current_chunk = []
        current_tokens = 0

        for para in paragraphs:
            para_tokens = count_tokens(para)

            # If single paragraph exceeds max, split it
            if para_tokens > self.max_tokens:
                # Flush current chunk first
                if current_chunk:
                    chunks.append(self._make_chunk(
                        current_chunk, content_id, len(chunks), metadata
                    ))
                    current_chunk = []
                    current_tokens = 0

                # Split large paragraph
                sub_chunks = self._split_long_text(para)
                for sub in sub_chunks:
                    chunks.append(self._make_chunk(
                        [sub], content_id, len(chunks), metadata
                    ))
                continue

            # Would adding this paragraph exceed limit?
            if current_tokens + para_tokens > self.max_tokens:
                # Flush current chunk
                if current_chunk:
                    chunks.append(self._make_chunk(
                        current_chunk, content_id, len(chunks), metadata
                    ))

                # Start new chunk with overlap
                overlap_text = self._get_overlap(current_chunk)
                current_chunk = [overlap_text] if overlap_text else []
                current_tokens = count_tokens(overlap_text) if overlap_text else 0

            current_chunk.append(para)
            current_tokens += para_tokens

        # Don't forget the last chunk
        if current_chunk:
            chunks.append(self._make_chunk(
                current_chunk, content_id, len(chunks), metadata
            ))

        logger.debug(f"Split content {content_id} into {len(chunks)} chunks")
        return chunks

    def _split_paragraphs(self, text: str) -> List[str]:
        """Split text into paragraphs."""
        # Split on double newlines, collapse whitespace
        paragraphs = re.split(r'\n\s*\n', text)
        return [p.strip() for p in paragraphs if p.strip()]

    def _split_long_text(self, text: str) -> List[str]:
        """Split a long text that exceeds max tokens."""
        # Split on sentences first
        sentences = re.split(r'(?<=[.!?])\s+', text)

        chunks = []
        current = []
        current_tokens = 0

        for sentence in sentences:
            sent_tokens = count_tokens(sentence)

            if current_tokens + sent_tokens > self.max_tokens:
                if current:
                    chunks.append(' '.join(current))
                current = [sentence]
                current_tokens = sent_tokens
            else:
                current.append(sentence)
                current_tokens += sent_tokens

        if current:
            chunks.append(' '.join(current))

        return chunks

    def _get_overlap(self, paragraphs: List[str]) -> str:
        """Get overlap text from end of paragraphs."""
        if not paragraphs:
            return ""

        # Take last paragraph(s) up to overlap_tokens
        overlap_parts = []
        total_tokens = 0

        for para in reversed(paragraphs):
            para_tokens = count_tokens(para)
            if total_tokens + para_tokens <= self.overlap_tokens:
                overlap_parts.insert(0, para)
                total_tokens += para_tokens
            else:
                break

        return '\n\n'.join(overlap_parts)

    def _make_chunk(
        self,
        paragraphs: List[str],
        content_id: str,
        index: int,
        metadata: dict
    ) -> Chunk:
        """Create a Chunk from paragraphs."""
        text = '\n\n'.join(paragraphs)
        return Chunk(
            content_id=content_id,
            chunk_index=index,
            text=text,
            token_count=count_tokens(text),
            metadata=metadata,
        )


def chunk_content(
    text: str,
    content_id: str,
    metadata: Optional[dict] = None
) -> List[Chunk]:
    """Convenience function to chunk content."""
    chunker = ContentChunker()
    return chunker.chunk_text(text, content_id, metadata)
