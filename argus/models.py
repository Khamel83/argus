"""
Argus domain models.

Normalized data structures used across all layers.
Provider-specific shapes must never leak outside adapters.
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
import time
from typing import List, Optional, Dict, Any, Callable

from argus.contracts.outcomes import CanonicalOutcome


class SearchMode(str, Enum):
    RECOVERY = "recovery"
    DISCOVERY = "discovery"
    GROUNDING = "grounding"
    RESEARCH = "research"


class ProviderName(str, Enum):
    SEARXNG = "searxng"
    DUCKDUCKGO = "duckduckgo"
    YAHOO = "yahoo"
    BRAVE = "brave"
    SERPER = "serper"
    TAVILY = "tavily"
    EXA = "exa"
    SEARCHAPI = "searchapi"
    YOU = "you"
    PARALLEL = "parallel"
    LINKUP = "linkup"
    VALYU = "valyu"
    GITHUB = "github"
    WOLFRAM = "wolfram"
    ARCHIVE = "archive_ph"
    CACHE = "cache"


def is_adapter_provider(provider: ProviderName) -> bool:
    """Whether a provider has a configurable executable search adapter."""
    return provider not in {ProviderName.CACHE, ProviderName.ARCHIVE}


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
    free_only: bool = False
    caller: str = ""  # e.g. "media_rename", "atlas", "mcp", "cli", ""
    user_visible: bool = True
    metadata: Dict[str, Any] = field(default_factory=dict)


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
    # Per-provider Shapley attribution for this result's RRF score.
    # Keys are provider names; values sum to self.score.
    # Populated only when attribution is requested.
    score_attribution: Dict[str, float] = field(default_factory=dict)


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
    egress: str = "local"  # "local" | egress node name e.g. "oci-dev"
    http_status: Optional[int] = None


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


@dataclass(frozen=True, slots=True)
class FusionPolicy:
    """Pure local fusion limits and the inherited absolute operation deadline."""

    rrf_k: int = 60
    max_provider_batches: int = 14
    max_observations_per_provider: int = 50
    max_total_observations: int = 700
    operation_deadline: float | None = None
    monotonic: Callable[[], float] = time.monotonic
    activatable: bool = False
    publication_reserve_seconds: float = 0.0


@dataclass(frozen=True, slots=True)
class FilteredObservation:
    provider: ProviderName
    provider_rank: int
    url: str
    document_key: str | None
    eligible: bool
    reason: str


@dataclass(frozen=True, slots=True)
class DuplicateRelation:
    document_key: str
    left_provider: ProviderName
    left_rank: int
    right_provider: ProviderName
    right_rank: int
    proof: str = "equal_conservative_document_key"


@dataclass(frozen=True, slots=True)
class RRFContribution:
    provider: ProviderName
    provider_rank: int
    numerator: int
    denominator: int


@dataclass(frozen=True, slots=True)
class ClusterRankingRecord:
    cluster_sort_key: str
    base_rank: int
    score_numerator: int
    score_denominator: int
    best_provider_rank: int
    contributor_count: int
    smallest_provider: ProviderName
    contributions: tuple[RRFContribution, ...]


@dataclass(frozen=True, slots=True)
class RankedResultCluster:
    cluster_sort_key: str
    observations: tuple[object, ...]
    representative_url: str
    representative_title: str
    representative_snippet: str
    representative_provider: ProviderName
    representative_rank: int
    site_key: str
    contributions: tuple[RRFContribution, ...]
    score_numerator: int
    score_denominator: int
    best_provider_rank: int
    contributor_count: int
    smallest_provider: ProviderName
    base_rank: int
    output_rank: int

    @property
    def score(self) -> float:
        return self.score_numerator / self.score_denominator


@dataclass(frozen=True, slots=True)
class DiversitySelection:
    cluster_sort_key: str
    base_rank: int
    output_rank: int
    site_key: str
    reason: str
    candidate_site_count: int


@dataclass(frozen=True, slots=True)
class DiversityDecision:
    cluster_sort_key: str
    base_rank: int
    output_rank: int | None
    site_key: str
    events: tuple[str, ...]
    selected_site_count_before: int
    selected_site_count_after: int
    site_selected_count_before: int
    site_selected_count_after: int


@dataclass(frozen=True, slots=True)
class SiteDiversityTrace:
    applied: bool
    psl_snapshot: str
    psl_snapshot_sha256: str
    available_sites: int
    required_sites: int
    selections: tuple[DiversitySelection, ...]
    decisions: tuple[DiversityDecision, ...]
    selected_sites: int
    passed: bool
    disposition: str


@dataclass(frozen=True, slots=True)
class EvidenceFloorTrace:
    required_clusters: int
    required_sites: int
    actual_clusters: int
    actual_sites: int
    passed: bool
    eligible_computed_answers: int = 0
    disposition: str = "floor_passed"


@dataclass(frozen=True, slots=True)
class RankingTrace:
    policy_version: str
    rrf_k: int
    base_order: tuple[str, ...]
    exact_scores: tuple[tuple[str, int, int], ...]
    records: tuple[ClusterRankingRecord, ...] = ()


@dataclass(frozen=True, slots=True)
class ComputedAnswerArtifact:
    provider: ProviderName
    provider_rank: int
    url: str
    title: str
    text: str
    egress: str
    machine: str | None
    observed_at: datetime
    eligible_for_grounding: bool
    disposition: str


@dataclass(frozen=True, slots=True)
class FusionOutcome:
    outcome: CanonicalOutcome
    reason: str
    ranked_result_clusters: tuple[RankedResultCluster, ...] = ()
    filtered_observations: tuple[FilteredObservation, ...] = ()
    duplicate_relations: tuple[DuplicateRelation, ...] = ()
    site_diversity_trace: SiteDiversityTrace = field(
        default_factory=lambda: SiteDiversityTrace(
            applied=False,
            psl_snapshot="unverified",
            psl_snapshot_sha256="",
            available_sites=0,
            required_sites=0,
            selections=(),
            decisions=(),
            selected_sites=0,
            passed=True,
            disposition="not_evaluated",
        )
    )
    evidence_floor_trace: EvidenceFloorTrace = field(
        default_factory=lambda: EvidenceFloorTrace(0, 0, 0, 0, True)
    )
    ranking_trace: RankingTrace = field(
        default_factory=lambda: RankingTrace("", 60, (), ())
    )
    completed_phases: tuple[str, ...] = ()
    provider_batches: tuple[object, ...] = ()
    computed_answers: tuple[ComputedAnswerArtifact, ...] = ()
