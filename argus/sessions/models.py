"""
Session domain models.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional


@dataclass
class QueryRecord:
    """A single query within a session."""
    query: str
    mode: str = "discovery"
    timestamp: datetime = field(default_factory=lambda: datetime.now(tz=None))
    results_count: int = 0
    extracted_urls: List[str] = field(default_factory=list)


@dataclass
class Session:
    """A search session that tracks query history and extracted URLs."""
    id: str
    created_at: datetime = field(default_factory=lambda: datetime.now(tz=None))
    queries: List[QueryRecord] = field(default_factory=list)

    @property
    def extracted_urls(self) -> List[str]:
        """All URLs that were extracted during this session."""
        urls = []
        for q in self.queries:
            urls.extend(q.extracted_urls)
        return urls
