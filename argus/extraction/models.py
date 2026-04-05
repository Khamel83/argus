"""
Extraction domain models.
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional


class ExtractorName(str, Enum):
    """Which extractor produced the result."""
    TRAFILATURA = "trafilatura"
    JINA = "jina"
    PLAYWRIGHT = "playwright"
    WAYBACK = "wayback"
    ARCHIVE_IS = "archive_is"
    AUTH = "auth"


@dataclass
class ExtractedContent:
    """Result of extracting content from a URL."""
    url: str
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
