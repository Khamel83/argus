"""
Extraction domain models.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from argus.contracts import CanonicalOutcome
    from argus.extraction.completeness import CompletenessResult
    from argus.extraction.outcomes import (
        ArtifactDisposition,
        ExtractionAcceptanceReceipt,
    )
    from argus.extraction.rejection import ExtractionRejection


class ExtractorName(str, Enum):
    """Which extractor produced the result."""
    RESIDENTIAL = "residential"
    TRAFILATURA = "trafilatura"
    JINA = "jina"
    OBSCURA = "obscura"
    PLAYWRIGHT = "playwright"
    WAYBACK = "wayback"
    ARCHIVE_IS = "archive_is"
    AUTH = "auth"
    CRAWL4AI = "crawl4ai"
    YOU_CONTENTS = "you_contents"
    VALYU_CONTENTS = "valyu_contents"
    FIRECRAWL = "firecrawl"
    YOUTUBE = "youtube"


@dataclass(frozen=True)
class ExtractionAttempt:
    """One bounded, persistence-safe extractor attempt."""

    extractor: str
    status: str
    latency_ms: int
    failure_summary: Optional[str] = None


@dataclass
class ExtractedContent:
    """Result of extracting content from a URL."""
    url: str
    extraction_run_id: Optional[str] = None
    title: str = ""
    text: str = ""
    author: str = ""
    date: Optional[str] = None
    word_count: int = 0
    extracted_at: datetime = field(default_factory=lambda: datetime.now(tz=None))
    extractor: Optional[ExtractorName] = None
    error: Optional[str] = None
    quality_passed: bool = True
    quality_reason: Optional[str] = None
    extractors_tried: list = field(default_factory=list)
    attempts: list[ExtractionAttempt] = field(default_factory=list)
    completeness_result: Optional["CompletenessResult"] = None
    cache_hit: bool = False
    cache_source_extractor: Optional[str] = None
    # Additive S3 projection fields. Legacy callers may ignore these.
    rejection: ExtractionRejection | None = None
    artifact_disposition: ArtifactDisposition | None = None
    acceptance_receipt: ExtractionAcceptanceReceipt | None = None
    accepted_outcome: CanonicalOutcome | None = None

    # Provenance metadata
    source_type: Optional[str] = None  # live|authenticated|residential|wayback|archive|paid_api|search_recovery
    egress: str = "unknown"  # residential|datacenter|local|unknown
    machine: Optional[str] = None
    auth_used: bool = False
    cookies_used: bool = False
    archive_used: bool = False
    cost: float = 0.0
