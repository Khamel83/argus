"""
Argus domain models.

Normalized data structures used across all layers.
Provider-specific shapes must never leak outside adapters.
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import List, Optional, Dict, Any


class SearchMode(str, Enum):
    RECOVERY = "recovery"
    DISCOVERY = "discovery"
    GROUNDING = "grounding"
    RESEARCH = "research"


class ProviderName(str, Enum):
    SEARXNG = "searxng"
    DUCKDUCKGO = "duckduckgo"
    BRAVE = "brave"
    SERPER = "serper"
    TAVILY = "tavily"
    EXA = "exa"
    SEARCHAPI = "searchapi"
    YOU = "you"
    PARALLEL = "parallel"
    LINKUP = "linkup"
    VALYU = "valyu"
    CACHE = "cache"


class ProviderStatus(str, Enum):
    ENABLED = "enabled"
    DISABLED_BY_CONFIG = "disabled_by_config"
    UNAVAILABLE_MISSING_KEY = "unavailable_missing_key"
    TEMPORARILY_DISABLED = "temporarily_disabled_after_failures"
    BUDGET_EXHAUSTED = "budget_exhausted"
    DEGRADED = "degraded"
    HEALTHY = "healthy"


@dataclass
class SearchQuery:
    """A search request from a caller."""
    query: str
    mode: SearchMode = SearchMode.DISCOVERY
    max_results: int = 10
    providers: Optional[List[ProviderName]] = None  # override routing policy


@dataclass
class SearchResult:
    """A normalized search result. Provider-agnostic."""
    url: str
    title: str
    snippet: str
    domain: str = ""
    provider: Optional[ProviderName] = None
    score: float = 0.0
    raw_rank: int = 0
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ProviderTrace:
    """Metadata about a single provider call within a search run."""
    provider: ProviderName
    status: str  # "success", "error", "skipped"
    results_count: int = 0
    latency_ms: int = 0
    error: Optional[str] = None
    budget_remaining: Optional[float] = None
    credit_info: Optional[dict] = None  # raw credit/rate-limit data from provider


@dataclass
class SearchResponse:
    """The complete response from the broker."""
    query: str
    mode: SearchMode
    results: List[SearchResult]
    traces: List[ProviderTrace] = field(default_factory=list)
    total_results: int = 0
    cached: bool = False
    search_run_id: Optional[str] = None
    created_at: datetime = field(default_factory=lambda: datetime.now(tz=None))
    budget_warnings: List[str] = field(default_factory=list)
