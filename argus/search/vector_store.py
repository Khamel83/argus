"""
Vector storage using SQLite-vec.

Stores embeddings alongside content metadata for semantic search.
Single SQLite file that can scale to 100K+ vectors.
"""

import json
import logging
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Tuple
from dataclasses import dataclass

import sqlite_vec

from .config import get_config, AskConfig
from .chunker import Chunk

logger = logging.getLogger(__name__)


@dataclass
class SearchResult:
    """A search result with similarity score."""
    chunk_id: str
    content_id: str
    chunk_index: int
    text: str
    score: float
    metadata: dict


class VectorStore:
    """SQLite-vec based vector storage."""

    def __init__(self, config: Optional[AskConfig] = None, db_path: Optional[Path] = None):
        self.config = config or get_config()
        self.db_path = db_path or self.config.vector_db_path
        self.dimensions = self.config.embeddings.dimensions
        self._conn: Optional[sqlite3.Connection] = None

        # Ensure directory exists
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

    def _get_connection(self) -> sqlite3.Connection:
        """Get or create database connection with proper concurrency settings."""
        if self._conn is None:
            self._conn = sqlite3.connect(
                str(self.db_path),
                timeout=30.0,                    # Don't block forever on locks
                check_same_thread=False,         # Allow multi-thread access
                isolation_level="DEFERRED"       # Better transaction handling
            )
            # Enable WAL mode for concurrent reads during writes
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=NORMAL")
            self._conn.execute("PRAGMA cache_size=-64000")  # 64MB cache
            self._conn.execute("PRAGMA busy_timeout=30000")  # 30s busy timeout

            self._conn.enable_load_extension(True)
            sqlite_vec.load(self._conn)
            self._conn.enable_load_extension(False)
            self._init_schema()
        return self._conn

    def _init_schema(self):
        """Initialize database schema."""
        conn = self._conn
        cursor = conn.cursor()

        # Chunks table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS chunks (
                chunk_id TEXT PRIMARY KEY,
                content_id TEXT NOT NULL,
                chunk_index INTEGER NOT NULL,
                text TEXT NOT NULL,
                token_count INTEGER,
                metadata TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(content_id, chunk_index)
            )
        """)

        # Create index on content_id for faster lookups
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_chunks_content_id
            ON chunks(content_id)
        """)

        # Vector table using sqlite-vec
        cursor.execute(f"""
            CREATE VIRTUAL TABLE IF NOT EXISTS chunk_vectors USING vec0(
                chunk_id TEXT PRIMARY KEY,
                embedding FLOAT[{self.dimensions}]
            )
        """)

        # Enrichments table for future use
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS enrichments (
                content_id TEXT PRIMARY KEY,
                summary TEXT,
                tags TEXT,
                key_topics TEXT,
                model_used TEXT,
                enriched_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Monthly spend tracking
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS monthly_spend (
                month TEXT PRIMARY KEY,
                tokens_used INTEGER DEFAULT 0,
                cost_usd REAL DEFAULT 0.0,
                budget_usd REAL DEFAULT 1.00,
                updated_at TEXT
            )
        """)

        conn.commit()
        logger.info(f"Vector store initialized at {self.db_path}")

    def store_chunk(self, chunk: Chunk, embedding: List[float]) -> str:
        """Store a chunk with its embedding."""
        conn = self._get_connection()
        cursor = conn.cursor()

        # Generate chunk_id
        chunk_id = f"{chunk.content_id}_{chunk.chunk_index:04d}"

        # Store chunk metadata
        cursor.execute("""
            INSERT OR REPLACE INTO chunks
            (chunk_id, content_id, chunk_index, text, token_count, metadata)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (
            chunk_id,
            chunk.content_id,
            chunk.chunk_index,
            chunk.text,
            chunk.token_count,
            json.dumps(chunk.metadata),
        ))

        # Store embedding (vec0 doesn't support REPLACE, so delete first if exists)
        cursor.execute("DELETE FROM chunk_vectors WHERE chunk_id = ?", (chunk_id,))
        cursor.execute("""
            INSERT INTO chunk_vectors (chunk_id, embedding)
            VALUES (?, ?)
        """, (chunk_id, json.dumps(embedding)))

        conn.commit()
        return chunk_id

    def store_chunks(
        self,
        chunks: List[Chunk],
        embeddings: List[List[float]]
    ) -> List[str]:
        """Store multiple chunks with embeddings."""
        if len(chunks) != len(embeddings):
            raise ValueError("chunks and embeddings must have same length")

        chunk_ids = []
        for chunk, embedding in zip(chunks, embeddings):
            chunk_id = self.store_chunk(chunk, embedding)
            chunk_ids.append(chunk_id)

        logger.info(f"Stored {len(chunks)} chunks")
        return chunk_ids

    def search(
        self,
        query_embedding: List[float],
        limit: int = 20,
        content_ids: Optional[List[str]] = None
    ) -> List[SearchResult]:
        """
        Search for similar chunks.

        Args:
            query_embedding: Query vector
            limit: Maximum results to return
            content_ids: Optional filter to specific content IDs

        Returns:
            List of SearchResult sorted by similarity (highest first)
        """
        conn = self._get_connection()
        cursor = conn.cursor()

        # Build query
        if content_ids:
            placeholders = ','.join(['?' for _ in content_ids])
            query = f"""
                SELECT
                    v.chunk_id,
                    c.content_id,
                    c.chunk_index,
                    c.text,
                    c.metadata,
                    vec_distance_cosine(v.embedding, ?) as distance
                FROM chunk_vectors v
                JOIN chunks c ON v.chunk_id = c.chunk_id
                WHERE c.content_id IN ({placeholders})
                ORDER BY distance ASC
                LIMIT ?
            """
            params = [json.dumps(query_embedding)] + content_ids + [limit]
        else:
            query = """
                SELECT
                    v.chunk_id,
                    c.content_id,
                    c.chunk_index,
                    c.text,
                    c.metadata,
                    vec_distance_cosine(v.embedding, ?) as distance
                FROM chunk_vectors v
                JOIN chunks c ON v.chunk_id = c.chunk_id
                ORDER BY distance ASC
                LIMIT ?
            """
            params = [json.dumps(query_embedding), limit]

        cursor.execute(query, params)
        rows = cursor.fetchall()

        results = []
        for row in rows:
            chunk_id, content_id, chunk_index, text, metadata_str, distance = row
            # Convert distance to similarity (1 - distance for cosine)
            similarity = 1 - distance
            results.append(SearchResult(
                chunk_id=chunk_id,
                content_id=content_id,
                chunk_index=chunk_index,
                text=text,
                score=similarity,
                metadata=json.loads(metadata_str) if metadata_str else {},
            ))

        return results

    def get_chunks_for_content(self, content_id: str) -> List[Tuple[str, str]]:
        """Get all chunks for a content item."""
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT chunk_id, text FROM chunks
            WHERE content_id = ?
            ORDER BY chunk_index
        """, (content_id,))
        return cursor.fetchall()

    def delete_content(self, content_id: str) -> int:
        """Delete all chunks for a content item."""
        conn = self._get_connection()
        cursor = conn.cursor()

        # Get chunk IDs first
        cursor.execute(
            "SELECT chunk_id FROM chunks WHERE content_id = ?",
            (content_id,)
        )
        chunk_ids = [row[0] for row in cursor.fetchall()]

        if not chunk_ids:
            return 0

        # Delete from both tables
        placeholders = ','.join(['?' for _ in chunk_ids])
        cursor.execute(f"DELETE FROM chunk_vectors WHERE chunk_id IN ({placeholders})", chunk_ids)
        cursor.execute(f"DELETE FROM chunks WHERE chunk_id IN ({placeholders})", chunk_ids)

        conn.commit()
        logger.info(f"Deleted {len(chunk_ids)} chunks for content {content_id}")
        return len(chunk_ids)

    def record_spend(self, tokens: int, cost_usd: float) -> None:
        """Record embedding API spend for the current month."""
        conn = self._get_connection()
        month = datetime.now().strftime('%Y-%m')
        now = datetime.now().isoformat()
        conn.execute("""
            INSERT INTO monthly_spend (month, tokens_used, cost_usd, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(month) DO UPDATE SET
                tokens_used = tokens_used + excluded.tokens_used,
                cost_usd = cost_usd + excluded.cost_usd,
                updated_at = excluded.updated_at
        """, (month, tokens, cost_usd, now))
        conn.commit()

    def get_monthly_spend(self, month: str = None) -> dict:
        """Get spend for a month (defaults to current month)."""
        if month is None:
            month = datetime.now().strftime('%Y-%m')
        conn = self._get_connection()
        row = conn.execute(
            "SELECT tokens_used, cost_usd, budget_usd FROM monthly_spend WHERE month = ?",
            (month,)
        ).fetchone()
        if row:
            return {"tokens_used": row[0], "cost_usd": row[1], "budget_usd": row[2]}
        return {"tokens_used": 0, "cost_usd": 0.0, "budget_usd": 1.00}

    def get_stats(self) -> dict:
        """Get vector store statistics."""
        conn = self._get_connection()
        cursor = conn.cursor()

        cursor.execute("SELECT COUNT(*) FROM chunks")
        total_chunks = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(DISTINCT content_id) FROM chunks")
        total_content = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM enrichments")
        total_enriched = cursor.fetchone()[0]

        return {
            "total_chunks": total_chunks,
            "total_content": total_content,
            "total_enriched": total_enriched,
            "db_path": str(self.db_path),
            "dimensions": self.dimensions,
        }

    def close(self):
        """Close database connection."""
        if self._conn:
            self._conn.close()
            self._conn = None
