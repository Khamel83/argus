"""
Argus Search - Semantic search and Q&A engine.

Components:
- config: Settings (loaded from config/ask_config.yml + env vars)
- embeddings: Generate embeddings via OpenRouter (text-embedding-3-small)
- chunker: Split content into searchable chunks
- vector_store: SQLite-vec storage for embeddings + FTS5
- retriever: Hybrid search (vector 70% + keyword 30%) with RRF fusion
- synthesizer: LLM-powered answer generation
- synthesis: Multi-source cross-analysis
- annotations: User notes/reactions/importance
- indexer: Content discovery + embedding pipeline
"""

from .config import get_config, AskConfig
from .embeddings import EmbeddingClient, embed_text, embed_texts
from .chunker import ContentChunker, Chunk, chunk_content
from .vector_store import VectorStore, SearchResult
from .retriever import HybridRetriever, RetrievalResult, retrieve
from .synthesizer import AnswerSynthesizer, SynthesizedAnswer, ask

__all__ = [
    "get_config", "AskConfig",
    "EmbeddingClient", "embed_text", "embed_texts",
    "ContentChunker", "Chunk", "chunk_content",
    "VectorStore", "SearchResult",
    "HybridRetriever", "RetrievalResult", "retrieve",
    "AnswerSynthesizer", "SynthesizedAnswer", "ask",
]
