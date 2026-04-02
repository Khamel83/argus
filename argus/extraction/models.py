"""
Extraction domain models.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Optional


class ExtractorName(str, Enum):
    TRAFILATURA = "trafilatura"
    JINA = "jina"


@dataclass
class ExtractedContent:
    """Result of extracting content from a URL."""
    url: str
    title: str = ""
    text: str = ""
    author: str = ""
    date: Optional[str] = None
    word_count: int = 0
    extracted_at: datetime = field(default_factory=lambda: datetime.now(tz=timezone.utc))
    extractor: Optional[ExtractorName] = None
    error: Optional[str] = None
