"""
Personal annotations for chunks and content.

Allows users to:
- Add notes to specific chunks
- Mark chunks as important (boosts retrieval)
- React to content (agree, disagree, interesting)
- Highlight key passages

Annotations affect retrieval scoring.
"""

import logging
import sqlite3
import uuid
from typing import List, Optional, Dict, Any
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

from .config import get_config, AskConfig

logger = logging.getLogger(__name__)


class AnnotationType(str, Enum):
    NOTE = "note"           # Free-form text note
    REACTION = "reaction"   # agree, disagree, interesting, important
    HIGHLIGHT = "highlight" # Mark text as important
    IMPORTANCE = "importance"  # Numeric weight boost


class Reaction(str, Enum):
    AGREE = "agree"
    DISAGREE = "disagree"
    INTERESTING = "interesting"
    IMPORTANT = "important"
    QUESTION = "question"


@dataclass
class Annotation:
    """A user annotation on a chunk or content."""
    id: str
    target_type: str  # "chunk" or "content"
    target_id: str    # chunk_id or content_id
    annotation_type: AnnotationType
    value: str        # Note text, reaction type, or importance weight
    created_at: datetime = field(default_factory=datetime.now)

    @classmethod
    def from_row(cls, row: tuple) -> "Annotation":
        return cls(
            id=row[0],
            target_type=row[1],
            target_id=row[2],
            annotation_type=AnnotationType(row[3]),
            value=row[4],
            created_at=datetime.fromisoformat(row[5]) if row[5] else datetime.now(),
        )


class AnnotationStore:
    """SQLite storage for annotations."""

    # Importance boosts for retrieval scoring
    IMPORTANCE_BOOSTS = {
        Reaction.IMPORTANT: 2.0,
        Reaction.AGREE: 1.3,
        Reaction.DISAGREE: 1.2,  # Still relevant even if disagreed
        Reaction.INTERESTING: 1.5,
        Reaction.QUESTION: 1.1,
    }

    def __init__(self, config: Optional[AskConfig] = None):
        self.config = config or get_config()
        self.db_path = self.config.vector_db_path
        self._ensure_schema()

    def _get_connection(self) -> sqlite3.Connection:
        return sqlite3.connect(str(self.db_path))

    def _ensure_schema(self):
        """Create annotations table if not exists."""
        conn = self._get_connection()
        cursor = conn.cursor()

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS annotations (
                id TEXT PRIMARY KEY,
                target_type TEXT NOT NULL,
                target_id TEXT NOT NULL,
                annotation_type TEXT NOT NULL,
                value TEXT NOT NULL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Index for fast lookups
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_annotations_target
            ON annotations(target_type, target_id)
        """)

        # Importance weights table (cached from annotations)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS importance_weights (
                chunk_id TEXT PRIMARY KEY,
                weight REAL DEFAULT 1.0,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)

        conn.commit()
        conn.close()

    def add_note(
        self,
        target_id: str,
        note: str,
        target_type: str = "chunk",
    ) -> Annotation:
        """Add a text note to a chunk or content."""
        return self._add_annotation(
            target_type=target_type,
            target_id=target_id,
            annotation_type=AnnotationType.NOTE,
            value=note,
        )

    def add_reaction(
        self,
        target_id: str,
        reaction: Reaction,
        target_type: str = "chunk",
    ) -> Annotation:
        """Add a reaction to a chunk or content."""
        annotation = self._add_annotation(
            target_type=target_type,
            target_id=target_id,
            annotation_type=AnnotationType.REACTION,
            value=reaction.value,
        )

        # Update importance weight if chunk
        if target_type == "chunk":
            self._update_importance_weight(target_id)

        return annotation

    def set_importance(
        self,
        chunk_id: str,
        weight: float,
    ) -> Annotation:
        """Set custom importance weight for a chunk."""
        annotation = self._add_annotation(
            target_type="chunk",
            target_id=chunk_id,
            annotation_type=AnnotationType.IMPORTANCE,
            value=str(weight),
        )

        self._update_importance_weight(chunk_id)
        return annotation

    def _add_annotation(
        self,
        target_type: str,
        target_id: str,
        annotation_type: AnnotationType,
        value: str,
    ) -> Annotation:
        """Add an annotation to the database."""
        annotation = Annotation(
            id=str(uuid.uuid4())[:8],
            target_type=target_type,
            target_id=target_id,
            annotation_type=annotation_type,
            value=value,
        )

        conn = self._get_connection()
        cursor = conn.cursor()

        cursor.execute(
            """
            INSERT INTO annotations (id, target_type, target_id, annotation_type, value, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                annotation.id,
                annotation.target_type,
                annotation.target_id,
                annotation.annotation_type.value,
                annotation.value,
                annotation.created_at.isoformat(),
            ),
        )

        conn.commit()
        conn.close()

        logger.info(f"Added {annotation_type.value} annotation to {target_type} {target_id}")
        return annotation

    def _update_importance_weight(self, chunk_id: str):
        """Update cached importance weight for a chunk based on its annotations."""
        conn = self._get_connection()
        cursor = conn.cursor()

        # Get all annotations for this chunk
        cursor.execute(
            """
            SELECT annotation_type, value FROM annotations
            WHERE target_type = 'chunk' AND target_id = ?
            """,
            (chunk_id,),
        )

        rows = cursor.fetchall()

        # Calculate weight
        weight = 1.0

        for ann_type, value in rows:
            if ann_type == AnnotationType.IMPORTANCE.value:
                try:
                    weight = max(weight, float(value))
                except ValueError:
                    pass
            elif ann_type == AnnotationType.REACTION.value:
                try:
                    reaction = Reaction(value)
                    boost = self.IMPORTANCE_BOOSTS.get(reaction, 1.0)
                    weight = max(weight, boost)
                except ValueError:
                    pass

        # Upsert weight
        cursor.execute(
            """
            INSERT OR REPLACE INTO importance_weights (chunk_id, weight, updated_at)
            VALUES (?, ?, ?)
            """,
            (chunk_id, weight, datetime.now().isoformat()),
        )

        conn.commit()
        conn.close()

    def get_annotations(
        self,
        target_id: str,
        target_type: str = "chunk",
    ) -> List[Annotation]:
        """Get all annotations for a target."""
        conn = self._get_connection()
        cursor = conn.cursor()

        cursor.execute(
            """
            SELECT * FROM annotations
            WHERE target_type = ? AND target_id = ?
            ORDER BY created_at DESC
            """,
            (target_type, target_id),
        )

        rows = cursor.fetchall()
        conn.close()

        return [Annotation.from_row(row) for row in rows]

    def get_importance_weight(self, chunk_id: str) -> float:
        """Get importance weight for a chunk."""
        conn = self._get_connection()
        cursor = conn.cursor()

        cursor.execute(
            "SELECT weight FROM importance_weights WHERE chunk_id = ?",
            (chunk_id,),
        )

        row = cursor.fetchone()
        conn.close()

        return row[0] if row else 1.0

    def get_importance_weights(self, chunk_ids: List[str]) -> Dict[str, float]:
        """Get importance weights for multiple chunks."""
        if not chunk_ids:
            return {}

        conn = self._get_connection()
        cursor = conn.cursor()

        placeholders = ",".join(["?" for _ in chunk_ids])
        cursor.execute(
            f"SELECT chunk_id, weight FROM importance_weights WHERE chunk_id IN ({placeholders})",
            chunk_ids,
        )

        weights = {row[0]: row[1] for row in cursor.fetchall()}
        conn.close()

        # Default weight of 1.0 for chunks without annotations
        return {cid: weights.get(cid, 1.0) for cid in chunk_ids}

    def list_annotated(
        self,
        annotation_type: Optional[AnnotationType] = None,
        limit: int = 50,
    ) -> List[Annotation]:
        """List annotations, optionally filtered by type."""
        conn = self._get_connection()
        cursor = conn.cursor()

        if annotation_type:
            cursor.execute(
                """
                SELECT * FROM annotations
                WHERE annotation_type = ?
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (annotation_type.value, limit),
            )
        else:
            cursor.execute(
                """
                SELECT * FROM annotations
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (limit,),
            )

        rows = cursor.fetchall()
        conn.close()

        return [Annotation.from_row(row) for row in rows]

    def delete(self, annotation_id: str) -> bool:
        """Delete an annotation."""
        conn = self._get_connection()
        cursor = conn.cursor()

        # Get annotation to update weights after delete
        cursor.execute("SELECT target_type, target_id FROM annotations WHERE id = ?", (annotation_id,))
        row = cursor.fetchone()

        if not row:
            conn.close()
            return False

        target_type, target_id = row

        cursor.execute("DELETE FROM annotations WHERE id = ?", (annotation_id,))
        conn.commit()

        # Update importance weight if chunk
        if target_type == "chunk":
            self._update_importance_weight(target_id)

        conn.close()
        return True

    def stats(self) -> Dict[str, int]:
        """Get annotation statistics."""
        conn = self._get_connection()
        cursor = conn.cursor()

        cursor.execute("""
            SELECT annotation_type, COUNT(*) FROM annotations
            GROUP BY annotation_type
        """)

        stats = {row[0]: row[1] for row in cursor.fetchall()}

        cursor.execute("SELECT COUNT(*) FROM annotations")
        stats["total"] = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM importance_weights WHERE weight > 1.0")
        stats["boosted_chunks"] = cursor.fetchone()[0]

        conn.close()
        return stats


# Convenience functions
_store: Optional[AnnotationStore] = None


def _get_store() -> AnnotationStore:
    global _store
    if _store is None:
        _store = AnnotationStore()
    return _store


def annotate_note(target_id: str, note: str, target_type: str = "chunk") -> Annotation:
    """Add a note annotation."""
    return _get_store().add_note(target_id, note, target_type)


def annotate_reaction(target_id: str, reaction: str, target_type: str = "chunk") -> Annotation:
    """Add a reaction annotation."""
    return _get_store().add_reaction(target_id, Reaction(reaction), target_type)


def set_importance(chunk_id: str, weight: float) -> Annotation:
    """Set chunk importance weight."""
    return _get_store().set_importance(chunk_id, weight)


def get_annotations(target_id: str, target_type: str = "chunk") -> List[Annotation]:
    """Get annotations for a target."""
    return _get_store().get_annotations(target_id, target_type)
