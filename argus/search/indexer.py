"""
Content indexer for Atlas Ask.

Indexes content from the Atlas storage into the vector store.
Can be run as a one-shot batch job or hooked into the ingest pipeline.

Usage:
    # Index all unindexed content
    python -m modules.ask.indexer --all

    # Index specific content type
    python -m modules.ask.indexer --type podcasts
    python -m modules.ask.indexer --type articles

    # Dry run
    python -m modules.ask.indexer --all --dry-run
"""

import logging
import sqlite3
from pathlib import Path
from typing import List, Optional, Set, Iterator, Tuple
from dataclasses import dataclass
from datetime import datetime
import json

from .config import get_config, AskConfig
from .embeddings import EmbeddingClient
from .chunker import ContentChunker
from .vector_store import VectorStore

# Quality gating imports
from modules.quality.verifier import ContentVerifier, QualityLevel

logger = logging.getLogger(__name__)


@dataclass
class ContentItem:
    """A piece of content to index."""
    content_id: str
    content_type: str  # "podcast", "article", "newsletter", etc.
    title: str
    text: str
    source_path: Path
    metadata: dict


class ContentIndexer:
    """
    Indexes Atlas content into the vector store.

    Discovers content from:
    - data/podcasts/{slug}/transcripts/*.md
    - data/content/article/{date}/{id}/content.md
    - data/content/newsletter/{date}/{id}/content.md
    - data/content/note/{date}/{id}/content.md
    - data/stratechery/{articles,podcasts}/*.md
    """

    def __init__(self, config: Optional[AskConfig] = None, use_clean: bool = True,
                 skip_quality_check: bool = False):
        self.config = config or get_config()
        self.vector_store = VectorStore(self.config)
        self.embedding_client = EmbeddingClient(
            self.config,
            spend_tracker=self.vector_store.record_spend,
        )
        self.chunker = ContentChunker(self.config)
        self.use_clean = use_clean  # Prefer ad-free versions from data/clean/
        self.skip_quality_check = skip_quality_check  # Skip quality gate (costs money for bad content)

        # Quality gate - verifies content before indexing
        self.verifier = None if skip_quality_check else ContentVerifier()

        # Base paths
        self.data_dir = Path("data")
        self.clean_dir = Path("data/clean")  # Ad-free versions
        self.podcasts_dir = self.data_dir / "podcasts"
        self.content_dir = self.data_dir / "content"
        self.stratechery_dir = self.data_dir / "stratechery"

        # Load clean path mapping from enrich database
        self._clean_path_mapping = self._load_clean_path_mapping() if use_clean else {}

    MONTHLY_BUDGET_USD = 1.00
    BUDGET_START_MONTH = "2026-04"  # Grace period: backlog ran in March 2026

    def _check_monthly_budget(self) -> tuple:
        """Returns (can_proceed, spent_usd). Only enforces from BUDGET_START_MONTH onwards."""
        month = datetime.now().strftime('%Y-%m')
        if month < self.BUDGET_START_MONTH:
            return True, 0.0
        spend = self.vector_store.get_monthly_spend(month)
        spent = spend["cost_usd"]
        if spent >= self.MONTHLY_BUDGET_USD:
            logger.warning(
                f"Monthly embedding budget reached: ${spent:.4f} / ${self.MONTHLY_BUDGET_USD:.2f}. "
                f"Resuming next month."
            )
            return False, spent
        if spent >= self.MONTHLY_BUDGET_USD * 0.9:
            logger.warning(f"Approaching monthly budget: ${spent:.4f} / ${self.MONTHLY_BUDGET_USD:.2f}")
        return True, spent

    def get_indexed_content_ids(self) -> Set[str]:
        """Get set of already-indexed content IDs (has chunks)."""
        conn = self.vector_store._get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT DISTINCT content_id FROM chunks")
        return {row[0] for row in cursor.fetchall()}

    def get_fully_indexed_content_ids(self) -> Set[str]:
        """Get content IDs where ALL chunks have vectors."""
        conn = self.vector_store._get_connection()
        cursor = conn.cursor()
        # Find content_ids where every chunk has a corresponding vector
        # Uses chunk_id match (not rowid - those are separate tables)
        cursor.execute("""
            SELECT content_id
            FROM chunks
            WHERE content_id NOT IN (
                SELECT DISTINCT c.content_id
                FROM chunks c
                WHERE c.chunk_id NOT IN (SELECT chunk_id FROM chunk_vectors)
            )
            GROUP BY content_id
        """)
        return {row[0] for row in cursor.fetchall()}

    def get_chunks_without_vectors(self) -> List[Tuple[str, str]]:
        """Find chunks that exist but have no embedding vectors."""
        conn = self.vector_store._get_connection()
        cursor = conn.cursor()
        # Uses chunk_id match (not rowid - those are separate tables)
        cursor.execute("""
            SELECT chunk_id, text
            FROM chunks
            WHERE chunk_id NOT IN (SELECT chunk_id FROM chunk_vectors)
            ORDER BY content_id, chunk_index
        """)
        return cursor.fetchall()

    def _load_clean_path_mapping(self) -> dict[str, str]:
        """Load original->clean path mapping from enrich database."""
        enrich_db = Path("data/enrich/enrich.db")
        if not enrich_db.exists():
            return {}

        mapping = {}
        try:
            conn = sqlite3.connect(enrich_db)
            cursor = conn.execute("SELECT original_path, clean_path FROM cleaning_records")
            for row in cursor.fetchall():
                # Normalize paths for lookup
                orig = str(Path(row[0]))
                clean = str(Path(row[1]))
                mapping[orig] = clean
            conn.close()
        except Exception as e:
            logger.warning(f"Failed to load clean path mapping: {e}")
        return mapping

    def _get_clean_path(self, original_path: Path) -> Path:
        """Get the clean version path for an original file."""
        # First check the enrich database mapping
        orig_str = str(original_path)
        if orig_str in self._clean_path_mapping:
            return Path(self._clean_path_mapping[orig_str])

        # Fallback: direct path mapping for podcasts
        # data/podcasts/{slug}/transcripts/... -> data/clean/podcasts/{slug}/transcripts/...
        try:
            rel_path = original_path.relative_to(self.data_dir)
            return self.clean_dir / rel_path
        except ValueError:
            return original_path

    def _read_content(self, original_path: Path) -> tuple[str, Path, bool]:
        """
        Read content, preferring clean version if available.

        Returns (text, actual_path, is_clean).
        """
        if self.use_clean:
            clean_path = self._get_clean_path(original_path)
            if clean_path.exists():
                return clean_path.read_text(), clean_path, True
        return original_path.read_text(), original_path, False

    def _should_index(self, item: ContentItem) -> tuple[bool, str]:
        """
        Check if content should be indexed based on quality.

        Returns (should_index, reason) tuple.
        """
        if self.skip_quality_check:
            return True, "quality_check_skipped"

        if self.verifier is None:
            return True, "no_verifier"

        # Verify the content
        result = self.verifier.verify_file(item.source_path)

        if result.quality == QualityLevel.GOOD:
            return True, "quality_good"
        elif result.quality == QualityLevel.MARGINAL:
            # Index marginal content but log it
            logger.info(f"Indexing marginal content: {item.content_id} (score: {result.score})")
            return True, f"quality_marginal (score: {result.score})"
        else:
            # Skip bad content - saves embedding API costs
            logger.warning(f"Skipping bad quality content: {item.content_id} - {result.issues}")
            return False, f"quality_bad: {'; '.join(result.issues[:2])}"

    def discover_podcasts(self) -> Iterator[ContentItem]:
        """Discover podcast transcripts."""
        if not self.podcasts_dir.exists():
            return

        for slug_dir in self.podcasts_dir.iterdir():
            if not slug_dir.is_dir():
                continue

            transcripts_dir = slug_dir / "transcripts"
            if not transcripts_dir.exists():
                continue

            for md_file in transcripts_dir.glob("*.md"):
                content_id = f"podcast:{slug_dir.name}:{md_file.stem}"

                try:
                    text, actual_path, is_clean = self._read_content(md_file)
                    if len(text) < 100:
                        continue

                    # Extract title from first line or filename
                    lines = text.strip().split('\n')
                    title = lines[0].lstrip('#').strip() if lines else md_file.stem

                    yield ContentItem(
                        content_id=content_id,
                        content_type="podcast",
                        title=title,
                        text=text,
                        source_path=actual_path,
                        metadata={
                            "slug": slug_dir.name,
                            "filename": md_file.name,
                            "title": title,
                            "is_clean": is_clean,
                        }
                    )
                except Exception as e:
                    logger.warning(f"Failed to read {md_file}: {e}")

    def discover_articles(self) -> Iterator[ContentItem]:
        """Discover article content.

        Structure: data/content/article/{year}/{month}/{day}/{content_id}/content.md
        """
        articles_dir = self.content_dir / "article"
        if not articles_dir.exists():
            return

        # Use glob to find all content.md files at any depth
        for content_file in articles_dir.glob("**/content.md"):
            content_dir = content_file.parent
            content_id_hash = content_dir.name

            # Extract date from path: article/{year}/{month}/{day}/{id}/content.md
            try:
                parts = content_file.relative_to(articles_dir).parts
                if len(parts) >= 4:  # year/month/day/id/content.md
                    date_str = f"{parts[0]}-{parts[1]}-{parts[2]}"
                else:
                    date_str = "unknown"
            except Exception:
                date_str = "unknown"

            content_id = f"article:{date_str}:{content_id_hash}"

            try:
                text, actual_path, is_clean = self._read_content(content_file)
                if len(text) < 100:
                    continue

                # Try to get title from metadata.json
                metadata_file = content_dir / "metadata.json"
                metadata = {}
                title = content_id_hash

                if metadata_file.exists():
                    try:
                        metadata = json.loads(metadata_file.read_text())
                        title = metadata.get("title", title)
                    except (json.JSONDecodeError, IOError) as e:
                        logger.debug(f"Error reading metadata {metadata_file}: {e}")

                yield ContentItem(
                    content_id=content_id,
                    content_type="article",
                    title=title,
                    text=text,
                    source_path=actual_path,
                    metadata={
                        "date": date_str,
                        "title": title,
                        "is_clean": is_clean,
                        **metadata,
                    }
                )
            except Exception as e:
                logger.warning(f"Failed to read {content_file}: {e}")

    def discover_newsletters(self) -> Iterator[ContentItem]:
        """Discover newsletter content.

        Structure: data/content/newsletter/{year}/{month}/{day}/{content_id}/content.md
        """
        newsletters_dir = self.content_dir / "newsletter"
        if not newsletters_dir.exists():
            return

        # Use glob to find all content.md files at any depth
        for content_file in newsletters_dir.glob("**/content.md"):
            content_dir = content_file.parent
            content_id_hash = content_dir.name

            # Extract date from path
            try:
                parts = content_file.relative_to(newsletters_dir).parts
                if len(parts) >= 4:  # year/month/day/id/content.md
                    date_str = f"{parts[0]}-{parts[1]}-{parts[2]}"
                else:
                    date_str = "unknown"
            except Exception:
                date_str = "unknown"

            content_id = f"newsletter:{date_str}:{content_id_hash}"

            try:
                text, actual_path, is_clean = self._read_content(content_file)
                if len(text) < 100:
                    continue

                metadata_file = content_dir / "metadata.json"
                metadata = {}
                title = content_id_hash

                if metadata_file.exists():
                    try:
                        metadata = json.loads(metadata_file.read_text())
                        title = metadata.get("title", title)
                    except (json.JSONDecodeError, IOError) as e:
                        logger.debug(f"Error reading metadata {metadata_file}: {e}")

                yield ContentItem(
                    content_id=content_id,
                    content_type="newsletter",
                    title=title,
                    text=text,
                    source_path=actual_path,
                    metadata={
                        "date": date_str,
                        "title": title,
                        "is_clean": is_clean,
                        **metadata,
                    }
                )
            except Exception as e:
                logger.warning(f"Failed to read {content_file}: {e}")

    def discover_emails(self) -> Iterator[ContentItem]:
        """Discover email/newsletter content ingested via Gmail.

        Structure: data/content/email/{year}/{month}/{day}/{content_id}/content.md
        """
        emails_dir = self.content_dir / "email"
        if not emails_dir.exists():
            return

        for content_file in emails_dir.glob("**/content.md"):
            content_dir = content_file.parent
            content_id_hash = content_dir.name

            try:
                parts = content_file.relative_to(emails_dir).parts
                if len(parts) >= 4:
                    date_str = f"{parts[0]}-{parts[1]}-{parts[2]}"
                else:
                    date_str = "unknown"
            except Exception:
                date_str = "unknown"

            content_id = f"email:{date_str}:{content_id_hash}"

            try:
                text, actual_path, is_clean = self._read_content(content_file)
                if len(text) < 100:
                    continue

                metadata_file = content_dir / "metadata.json"
                metadata = {}
                title = content_id_hash

                if metadata_file.exists():
                    try:
                        metadata = json.loads(metadata_file.read_text())
                        title = metadata.get("title", title)
                    except (json.JSONDecodeError, IOError) as e:
                        logger.debug(f"Error reading metadata {metadata_file}: {e}")

                yield ContentItem(
                    content_id=content_id,
                    content_type="email",
                    title=title,
                    text=text,
                    source_path=actual_path,
                    metadata={
                        "date": date_str,
                        "title": title,
                        "is_clean": is_clean,
                        **metadata,
                    }
                )
            except Exception as e:
                logger.warning(f"Failed to read {content_file}: {e}")

    def discover_notes(self) -> Iterator[ContentItem]:
        """Discover user notes (selections, highlights).

        Structure: data/content/note/{year}/{month}/{day}/{content_id}/content.md
        """
        notes_dir = self.content_dir / "note"
        if not notes_dir.exists():
            return

        # Use glob to find all content.md files at any depth
        for content_file in notes_dir.glob("**/content.md"):
            content_dir = content_file.parent
            content_id_hash = content_dir.name

            # Extract date from path
            try:
                parts = content_file.relative_to(notes_dir).parts
                if len(parts) >= 4:  # year/month/day/id/content.md
                    date_str = f"{parts[0]}-{parts[1]}-{parts[2]}"
                else:
                    date_str = "unknown"
            except Exception:
                date_str = "unknown"

            content_id = f"note:{date_str}:{content_id_hash}"

            try:
                text = content_file.read_text()
                # Notes can be short, lower threshold
                if len(text) < 20:
                    continue

                # Try to get title from metadata.json
                metadata_file = content_dir / "metadata.json"
                metadata = {}
                title = content_id_hash

                if metadata_file.exists():
                    try:
                        metadata = json.loads(metadata_file.read_text())
                        title = metadata.get("title", title)
                    except (json.JSONDecodeError, IOError) as e:
                        logger.debug(f"Error reading metadata {metadata_file}: {e}")

                yield ContentItem(
                    content_id=content_id,
                    content_type="note",
                    title=title,
                    text=text,
                    source_path=content_file,
                    metadata={
                        "date": date_str,
                        "title": title,
                        "is_clean": True,  # Notes are user-curated
                        **metadata,
                    }
                )
            except Exception as e:
                logger.warning(f"Failed to read {content_file}: {e}")

    def discover_stratechery(self) -> Iterator[ContentItem]:
        """Discover Stratechery archive content."""
        if not self.stratechery_dir.exists():
            return

        for subdir in ["articles", "podcasts"]:
            content_dir = self.stratechery_dir / subdir
            if not content_dir.exists():
                continue

            content_type = "stratechery-article" if subdir == "articles" else "stratechery-podcast"

            for md_file in content_dir.glob("*.md"):
                content_id = f"stratechery:{subdir}:{md_file.stem}"

                try:
                    text, actual_path, is_clean = self._read_content(md_file)
                    if len(text) < 100:
                        continue

                    lines = text.strip().split('\n')
                    title = lines[0].lstrip('#').strip() if lines else md_file.stem

                    yield ContentItem(
                        content_id=content_id,
                        content_type=content_type,
                        title=title,
                        text=text,
                        source_path=actual_path,
                        metadata={
                            "type": subdir,
                            "filename": md_file.name,
                            "title": title,
                            "is_clean": is_clean,
                        }
                    )
                except Exception as e:
                    logger.warning(f"Failed to read {md_file}: {e}")

    def discover_all(self, content_types: Optional[List[str]] = None) -> Iterator[ContentItem]:
        """Discover all content, optionally filtered by type."""
        types = content_types or ["podcasts", "articles", "newsletters", "emails", "stratechery", "notes"]

        if "podcasts" in types:
            yield from self.discover_podcasts()
        if "articles" in types:
            yield from self.discover_articles()
        if "newsletters" in types:
            yield from self.discover_newsletters()
        if "emails" in types:
            yield from self.discover_emails()
        if "stratechery" in types:
            yield from self.discover_stratechery()
        if "notes" in types:
            yield from self.discover_notes()

    def is_content_indexed(self, content_id: str) -> bool:
        """Check if content is already indexed (fast check, no embedding call)."""
        conn = self.vector_store._get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT 1 FROM chunks WHERE content_id = ? LIMIT 1", (content_id,))
        return cursor.fetchone() is not None

    def index_content(
        self,
        item: ContentItem,
        force: bool = False,
    ) -> int:
        """
        Index a single content item.

        Returns number of chunks indexed.
        Returns -1 if skipped due to quality issues.
        """
        # Check if already indexed BEFORE any expensive operations
        if not force and self.is_content_indexed(item.content_id):
            logger.debug(f"Already indexed: {item.content_id}")
            return 0

        # QUALITY GATE: Skip content that failed verification
        # This prevents wasting embedding API costs on paywalled/404/short content
        should_index, reason = self._should_index(item)
        if not should_index:
            logger.info(f"Skipped {item.content_id}: {reason}")
            return -1  # Return -1 to indicate quality skip

        # Chunk the content
        chunks = self.chunker.chunk_text(
            item.text,
            content_id=item.content_id,
            metadata={
                "title": item.title,
                "type": item.content_type,
                **item.metadata,
            }
        )

        if not chunks:
            logger.debug(f"No chunks generated for: {item.content_id}")
            return 0

        # Generate embeddings
        chunk_texts = [c.text for c in chunks]
        embeddings = self.embedding_client.embed_chunks(chunk_texts)

        # Store
        self.vector_store.store_chunks(chunks, embeddings)

        logger.debug(f"Indexed {len(chunks)} chunks for: {item.content_id}")
        return len(chunks)

    def resume_failed_embeddings(
        self,
        batch_size: int = 50,
        dry_run: bool = False,
        limit: Optional[int] = None
    ) -> int:
        """
        Embed chunks that exist but have no vectors.

        No re-chunking, no discovery. Just fills in missing vectors.
        Safe to run repeatedly - only processes chunks without vectors.

        Args:
            batch_size: Number of chunks to embed per API call
            dry_run: If True, just count without embedding
            limit: Max total chunks to embed (None = all)
        """
        can_proceed, _ = self._check_monthly_budget()
        if not can_proceed:
            return 0

        missing = self.get_chunks_without_vectors()

        if not missing:
            logger.info("No chunks without vectors found")
            return 0

        total_missing = len(missing)
        if limit:
            missing = missing[:limit]

        logger.info(f"Found {total_missing} chunks without vectors, processing {len(missing)}")

        if dry_run:
            # Show breakdown by content type
            by_type = {}
            for chunk_id, _ in missing:
                ctype = chunk_id.split(':')[0]
                by_type[ctype] = by_type.get(ctype, 0) + 1
            for ctype, count in sorted(by_type.items(), key=lambda x: -x[1]):
                logger.info(f"  {ctype}: {count}")
            return len(missing)

        embedded = 0
        failed_batches = 0
        conn = self.vector_store._get_connection()

        for i in range(0, len(missing), batch_size):
            batch = missing[i:i+batch_size]
            chunk_ids = [c[0] for c in batch]
            texts = [c[1] for c in batch]

            try:
                embeddings = self.embedding_client.embed_chunks(texts)

                # Store vectors only (chunks already exist)
                for chunk_id, embedding in zip(chunk_ids, embeddings):
                    conn.execute("DELETE FROM chunk_vectors WHERE chunk_id = ?", (chunk_id,))
                    conn.execute(
                        "INSERT INTO chunk_vectors (chunk_id, embedding) VALUES (?, ?)",
                        (chunk_id, json.dumps(embedding))
                    )
                conn.commit()

                embedded += len(batch)
                failed_batches = 0  # Reset on success
                logger.info(f"Progress: {embedded}/{len(missing)} chunks embedded")

            except Exception as e:
                logger.error(f"Batch failed at {embedded}: {e}")
                conn.rollback()
                failed_batches += 1
                # Stop after 3 consecutive failures (likely API issue)
                if failed_batches >= 3:
                    logger.error("3 consecutive batch failures, stopping")
                    break

        return embedded

    def index_all(
        self,
        content_types: Optional[List[str]] = None,
        force: bool = False,
        dry_run: bool = False,
        batch_size: int = 50,
    ) -> Tuple[int, int]:
        """
        Index all unindexed content.

        Returns (items_indexed, chunks_indexed).
        Content skipped due to quality issues is logged but not counted as failures.
        """
        can_proceed, spent = self._check_monthly_budget()
        if not can_proceed:
            return 0, 0

        # Pre-load FULLY indexed IDs (all chunks have vectors)
        # This ensures partially-failed content gets retried
        indexed_ids = set() if force else self.get_fully_indexed_content_ids()
        logger.info(f"Found {len(indexed_ids)} fully-indexed content items")

        items_indexed = 0
        chunks_indexed = 0
        skipped = 0
        quality_skipped = 0
        batch = []

        for item in self.discover_all(content_types):
            # Fast check against pre-loaded set
            if item.content_id in indexed_ids:
                skipped += 1
                continue

            if dry_run:
                logger.info(f"Would index: {item.content_id} ({item.title[:50]}...)")
                items_indexed += 1
                continue

            batch.append(item)

            if len(batch) >= batch_size:
                for batch_item in batch:
                    try:
                        chunks = self.index_content(batch_item, force=force)
                        if chunks == -1:
                            # Quality skip
                            quality_skipped += 1
                        elif chunks > 0:
                            items_indexed += 1
                            chunks_indexed += chunks
                    except Exception as e:
                        logger.error(f"Failed to index {batch_item.content_id}: {e}")

                logger.info(f"Progress: {items_indexed} items, {chunks_indexed} chunks, {quality_skipped} quality-skipped")
                batch = []

        # Process remaining batch
        for batch_item in batch:
            try:
                chunks = self.index_content(batch_item, force=force)
                if chunks == -1:
                    # Quality skip
                    quality_skipped += 1
                elif chunks > 0:
                    items_indexed += 1
                    chunks_indexed += chunks
            except Exception as e:
                logger.error(f"Failed to index {batch_item.content_id}: {e}")

        if quality_skipped > 0:
            logger.info(f"Quality gate skipped {quality_skipped} items (saved embedding costs)")

        return items_indexed, chunks_indexed

        return items_indexed, chunks_indexed

    def close(self):
        """Close connections."""
        self.vector_store.close()


def index_single(content_id: str, text: str, title: str, content_type: str, metadata: dict = None):
    """
    Convenience function to index a single piece of content.

    Call this from your ingest pipeline after saving content.

    Example:
        from modules.ask.indexer import index_single

        # After saving a podcast transcript
        index_single(
            content_id=f"podcast:{slug}:{episode_id}",
            text=transcript_text,
            title=episode_title,
            content_type="podcast",
            metadata={"slug": slug}
        )
    """
    indexer = ContentIndexer()
    try:
        item = ContentItem(
            content_id=content_id,
            content_type=content_type,
            title=title,
            text=text,
            source_path=Path("."),  # Not used for direct indexing
            metadata=metadata or {},
        )
        return indexer.index_content(item)
    finally:
        indexer.close()


def main():
    """CLI entry point."""
    import argparse
    import sys
    import os

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s: %(message)s"
    )

    parser = argparse.ArgumentParser(description="Index Atlas content")
    parser.add_argument("--all", action="store_true", help="Index all content types")
    parser.add_argument("--type", dest="content_type", choices=["podcasts", "articles", "newsletters", "emails", "stratechery", "notes"], help="Index specific type")
    parser.add_argument("--resume", action="store_true", help="Only embed chunks that failed previously (no re-chunking)")
    parser.add_argument("--force", action="store_true", help="Re-index existing content")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be indexed")
    parser.add_argument("--batch-size", type=int, default=50, help="Batch size for API calls")
    parser.add_argument("--limit", type=int, default=None, help="Max chunks to process (for testing)")
    parser.add_argument("--use-original", action="store_true", help="Use original files instead of clean (ad-free) versions")
    parser.add_argument("--skip-quality-check", action="store_true", help="Skip quality verification (may waste API costs on bad content)")

    args = parser.parse_args()

    if not args.all and not args.content_type and not args.resume:
        parser.print_help()
        return 1

    if not os.getenv("OPENROUTER_API_KEY"):
        print("OPENROUTER_API_KEY not set.")
        print("Run with: ./scripts/run_with_secrets.sh python -m modules.ask.indexer ...")
        return 1

    use_clean = not args.use_original
    indexer = ContentIndexer(use_clean=use_clean, skip_quality_check=args.skip_quality_check)

    if not args.skip_quality_check:
        print("Quality gate enabled: bad content will be skipped")
    else:
        print("Quality gate disabled: all content will be indexed")

    # Handle --resume mode (fill in missing vectors only)
    if args.resume:
        try:
            embedded = indexer.resume_failed_embeddings(
                batch_size=args.batch_size,
                dry_run=args.dry_run,
                limit=args.limit
            )
            if args.dry_run:
                print(f"\nWould embed {embedded} chunks")
            else:
                print(f"\nEmbedded {embedded} previously-failed chunks")

                stats = indexer.vector_store.get_stats()
                print(f"Vector store: {stats['total_chunks']} total chunks")
        finally:
            indexer.close()
        return 0

    content_types = None
    if args.content_type:
        content_types = [args.content_type]

    if use_clean:
        print("Using clean (ad-free) versions when available")
    try:
        items, chunks = indexer.index_all(
            content_types=content_types,
            force=args.force,
            dry_run=args.dry_run,
            batch_size=args.batch_size,
        )

        if args.dry_run:
            print(f"\nWould index {items} items")
        else:
            print(f"\nIndexed {items} items ({chunks} chunks)")

            stats = indexer.vector_store.get_stats()
            print(f"\nVector store: {stats['total_chunks']} total chunks, {stats['total_content']} content items")

    finally:
        indexer.close()

    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
