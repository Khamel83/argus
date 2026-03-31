"""
Hybrid retriever combining vector search with keyword search.

Uses RRF (Reciprocal Rank Fusion) to merge results from:
1. Semantic vector search (sqlite-vec cosine similarity)
2. Keyword search (FTS5 full-text search)

This gives the best of both worlds:
- Vector search finds semantically similar content
- Keyword search catches exact matches and rare terms
"""

import logging
from typing import List, Optional, Dict, Any
from dataclasses import dataclass
import sqlite3
import json

from .config import get_config, AskConfig
from .embeddings import EmbeddingClient
from .vector_store import VectorStore, SearchResult

logger = logging.getLogger(__name__)


@dataclass
class RetrievalResult:
    """A retrieved chunk with combined score."""
    chunk_id: str
    content_id: str
    chunk_index: int
    text: str
    score: float  # Combined RRF score
    vector_rank: Optional[int] = None
    keyword_rank: Optional[int] = None
    metadata: dict = None

    def __post_init__(self):
        if self.metadata is None:
            self.metadata = {}


class HybridRetriever:
    """
    Combines vector and keyword search using Reciprocal Rank Fusion.

    RRF score = sum(1 / (k + rank)) across all ranking sources
    Default k=60 (standard RRF constant)
    """

    RRF_K = 60  # Standard RRF constant

    def __init__(self, config: Optional[AskConfig] = None, vector_store: Optional[VectorStore] = None):
        self.config = config or get_config()
        self.vector_store = vector_store or VectorStore(self.config)
        self.embedding_client = EmbeddingClient(self.config)

        self.vector_weight = self.config.retrieval.vector_weight
        self.keyword_weight = self.config.retrieval.keyword_weight
        self.max_results = self.config.retrieval.max_results

        # Ensure FTS5 table exists
        self._ensure_fts_table()

    def _ensure_fts_table(self):
        """Create FTS5 table for keyword search if not exists."""
        conn = self.vector_store._get_connection()
        cursor = conn.cursor()

        # Check if FTS table exists
        cursor.execute("""
            SELECT name FROM sqlite_master
            WHERE type='table' AND name='chunks_fts'
        """)

        if not cursor.fetchone():
            # Create FTS5 virtual table
            cursor.execute("""
                CREATE VIRTUAL TABLE chunks_fts USING fts5(
                    chunk_id,
                    text,
                    content='chunks',
                    content_rowid='rowid'
                )
            """)

            # Populate from existing chunks
            cursor.execute("""
                INSERT INTO chunks_fts(chunk_id, text)
                SELECT chunk_id, text FROM chunks
            """)

            # Create triggers to keep FTS in sync
            cursor.execute("""
                CREATE TRIGGER IF NOT EXISTS chunks_ai AFTER INSERT ON chunks BEGIN
                    INSERT INTO chunks_fts(chunk_id, text) VALUES (new.chunk_id, new.text);
                END
            """)

            cursor.execute("""
                CREATE TRIGGER IF NOT EXISTS chunks_ad AFTER DELETE ON chunks BEGIN
                    INSERT INTO chunks_fts(chunks_fts, rowid, chunk_id, text)
                    VALUES('delete', old.rowid, old.chunk_id, old.text);
                END
            """)

            cursor.execute("""
                CREATE TRIGGER IF NOT EXISTS chunks_au AFTER UPDATE ON chunks BEGIN
                    INSERT INTO chunks_fts(chunks_fts, rowid, chunk_id, text)
                    VALUES('delete', old.rowid, old.chunk_id, old.text);
                    INSERT INTO chunks_fts(chunk_id, text) VALUES (new.chunk_id, new.text);
                END
            """)

            conn.commit()
            logger.info("Created FTS5 table for keyword search")

    def retrieve(
        self,
        query: str,
        limit: Optional[int] = None,
        content_ids: Optional[List[str]] = None,
        vector_only: bool = False,
        keyword_only: bool = False,
    ) -> List[RetrievalResult]:
        """
        Retrieve relevant chunks using hybrid search.

        Args:
            query: Natural language query
            limit: Max results to return (default from config)
            content_ids: Optional filter to specific content
            vector_only: Use only vector search
            keyword_only: Use only keyword search

        Returns:
            List of RetrievalResult sorted by relevance
        """
        limit = limit or self.max_results

        # Get results from both sources
        vector_results = []
        keyword_results = []

        if not keyword_only:
            vector_results = self._vector_search(query, limit * 2, content_ids)

        if not vector_only:
            keyword_results = self._keyword_search(query, limit * 2, content_ids)

        # If only one source, return directly
        if vector_only:
            return self._to_retrieval_results(vector_results, limit)
        if keyword_only:
            return self._keyword_to_retrieval_results(keyword_results, limit)

        # Combine with RRF
        combined = self._rrf_fusion(vector_results, keyword_results, limit)

        logger.info(
            f"Retrieved {len(combined)} results "
            f"(vector: {len(vector_results)}, keyword: {len(keyword_results)})"
        )

        return combined

    def _vector_search(
        self,
        query: str,
        limit: int,
        content_ids: Optional[List[str]] = None
    ) -> List[SearchResult]:
        """Perform vector similarity search."""
        query_embedding = self.embedding_client.embed(query)
        results = self.vector_store.search(query_embedding, limit, content_ids)
        return results

    def _keyword_search(
        self,
        query: str,
        limit: int,
        content_ids: Optional[List[str]] = None
    ) -> List[Dict[str, Any]]:
        """Perform FTS5 keyword search."""
        conn = self.vector_store._get_connection()
        cursor = conn.cursor()

        # Build FTS5 query - use MATCH with boolean operators
        # Escape special characters and build query
        fts_query = self._build_fts_query(query)

        if content_ids:
            placeholders = ','.join(['?' for _ in content_ids])
            sql = f"""
                SELECT
                    c.chunk_id,
                    c.content_id,
                    c.chunk_index,
                    c.text,
                    c.metadata,
                    bm25(chunks_fts) as score
                FROM chunks_fts f
                JOIN chunks c ON f.chunk_id = c.chunk_id
                WHERE chunks_fts MATCH ?
                AND c.content_id IN ({placeholders})
                ORDER BY score
                LIMIT ?
            """
            params = [fts_query] + content_ids + [limit]
        else:
            sql = """
                SELECT
                    c.chunk_id,
                    c.content_id,
                    c.chunk_index,
                    c.text,
                    c.metadata,
                    bm25(chunks_fts) as score
                FROM chunks_fts f
                JOIN chunks c ON f.chunk_id = c.chunk_id
                WHERE chunks_fts MATCH ?
                ORDER BY score
                LIMIT ?
            """
            params = [fts_query, limit]

        try:
            cursor.execute(sql, params)
            rows = cursor.fetchall()

            results = []
            for row in rows:
                chunk_id, content_id, chunk_index, text, metadata_str, score = row
                results.append({
                    'chunk_id': chunk_id,
                    'content_id': content_id,
                    'chunk_index': chunk_index,
                    'text': text,
                    'metadata': json.loads(metadata_str) if metadata_str else {},
                    'score': -score,  # BM25 returns negative scores, lower is better
                })

            return results

        except sqlite3.OperationalError as e:
            # FTS query syntax error - fallback to simpler query
            logger.warning(f"FTS query failed: {e}, falling back to simple search")
            return self._simple_keyword_search(query, limit, content_ids)

    def _build_fts_query(self, query: str) -> str:
        """Build FTS5 query from natural language."""
        # Remove special FTS characters
        special_chars = ['*', '"', '(', ')', '+', '-', 'AND', 'OR', 'NOT', 'NEAR']
        cleaned = query
        for char in special_chars:
            cleaned = cleaned.replace(char, ' ')

        # Split into words and join with OR for broader matching
        words = [w.strip() for w in cleaned.split() if w.strip() and len(w.strip()) > 2]

        if not words:
            return query  # Fallback to original

        # Use OR to match any word, with wildcards for partial matches
        return ' OR '.join(f'"{w}"' for w in words)

    def _simple_keyword_search(
        self,
        query: str,
        limit: int,
        content_ids: Optional[List[str]] = None
    ) -> List[Dict[str, Any]]:
        """Fallback keyword search using LIKE."""
        conn = self.vector_store._get_connection()
        cursor = conn.cursor()

        # Simple LIKE-based search
        words = query.lower().split()
        like_clauses = ' OR '.join(['LOWER(text) LIKE ?' for _ in words])
        like_params = [f'%{w}%' for w in words]

        if content_ids:
            placeholders = ','.join(['?' for _ in content_ids])
            sql = f"""
                SELECT chunk_id, content_id, chunk_index, text, metadata
                FROM chunks
                WHERE ({like_clauses})
                AND content_id IN ({placeholders})
                LIMIT ?
            """
            params = like_params + content_ids + [limit]
        else:
            sql = f"""
                SELECT chunk_id, content_id, chunk_index, text, metadata
                FROM chunks
                WHERE ({like_clauses})
                LIMIT ?
            """
            params = like_params + [limit]

        cursor.execute(sql, params)
        rows = cursor.fetchall()

        return [{
            'chunk_id': row[0],
            'content_id': row[1],
            'chunk_index': row[2],
            'text': row[3],
            'metadata': json.loads(row[4]) if row[4] else {},
            'score': 1.0,  # No ranking for LIKE search
        } for row in rows]

    def _rrf_fusion(
        self,
        vector_results: List[SearchResult],
        keyword_results: List[Dict[str, Any]],
        limit: int
    ) -> List[RetrievalResult]:
        """
        Combine results using Reciprocal Rank Fusion.

        RRF score = w1 * (1/(k+r1)) + w2 * (1/(k+r2))
        """
        scores: Dict[str, Dict[str, Any]] = {}

        # Add vector results with ranks
        for rank, result in enumerate(vector_results, 1):
            chunk_id = result.chunk_id
            rrf_score = self.vector_weight * (1.0 / (self.RRF_K + rank))

            scores[chunk_id] = {
                'chunk_id': chunk_id,
                'content_id': result.content_id,
                'chunk_index': result.chunk_index,
                'text': result.text,
                'metadata': result.metadata,
                'score': rrf_score,
                'vector_rank': rank,
                'keyword_rank': None,
            }

        # Add keyword results with ranks
        for rank, result in enumerate(keyword_results, 1):
            chunk_id = result['chunk_id']
            rrf_score = self.keyword_weight * (1.0 / (self.RRF_K + rank))

            if chunk_id in scores:
                # Combine scores
                scores[chunk_id]['score'] += rrf_score
                scores[chunk_id]['keyword_rank'] = rank
            else:
                scores[chunk_id] = {
                    'chunk_id': chunk_id,
                    'content_id': result['content_id'],
                    'chunk_index': result['chunk_index'],
                    'text': result['text'],
                    'metadata': result['metadata'],
                    'score': rrf_score,
                    'vector_rank': None,
                    'keyword_rank': rank,
                }

        # Sort by combined RRF score
        sorted_results = sorted(scores.values(), key=lambda x: x['score'], reverse=True)

        # Convert to RetrievalResult objects
        return [
            RetrievalResult(
                chunk_id=r['chunk_id'],
                content_id=r['content_id'],
                chunk_index=r['chunk_index'],
                text=r['text'],
                score=r['score'],
                vector_rank=r['vector_rank'],
                keyword_rank=r['keyword_rank'],
                metadata=r['metadata'],
            )
            for r in sorted_results[:limit]
        ]

    def _to_retrieval_results(
        self,
        results: List[SearchResult],
        limit: int
    ) -> List[RetrievalResult]:
        """Convert vector SearchResults to RetrievalResults."""
        return [
            RetrievalResult(
                chunk_id=r.chunk_id,
                content_id=r.content_id,
                chunk_index=r.chunk_index,
                text=r.text,
                score=r.score,
                vector_rank=i + 1,
                metadata=r.metadata,
            )
            for i, r in enumerate(results[:limit])
        ]

    def _keyword_to_retrieval_results(
        self,
        results: List[Dict[str, Any]],
        limit: int
    ) -> List[RetrievalResult]:
        """Convert keyword results to RetrievalResults."""
        return [
            RetrievalResult(
                chunk_id=r['chunk_id'],
                content_id=r['content_id'],
                chunk_index=r['chunk_index'],
                text=r['text'],
                score=r['score'],
                keyword_rank=i + 1,
                metadata=r['metadata'],
            )
            for i, r in enumerate(results[:limit])
        ]

    def close(self):
        """Close the vector store connection."""
        self.vector_store.close()


# Convenience function
def retrieve(query: str, limit: int = 20) -> List[RetrievalResult]:
    """Retrieve relevant chunks for a query."""
    retriever = HybridRetriever()
    try:
        return retriever.retrieve(query, limit)
    finally:
        retriever.close()
