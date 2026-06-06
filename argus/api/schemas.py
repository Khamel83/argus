"""
Pydantic request/response schemas for the HTTP API.
"""

import re
from typing import Any, List, Optional, Set

from pydantic import BaseModel, Field, field_validator

from argus.models import ProviderName

_VALID_MODES: Set[str] = {"recovery", "discovery", "grounding", "research"}
_VALID_PROVIDERS: Set[str] = {provider.value for provider in ProviderName if provider != ProviderName.CACHE}


class SearchRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=500, description="Search query string")
    mode: str = Field("discovery", description="Search mode: recovery, discovery, grounding, research")
    max_results: int = Field(10, ge=1, le=50, description="Maximum results to return")
    providers: Optional[List[str]] = Field(None, description="Override provider routing order")
    session_id: Optional[str] = Field(None, description="Session ID for multi-turn context")
    include_attribution: bool = Field(False, description="Include per-provider score attribution")
    free_only: bool = Field(False, description="Only use free (tier-0) providers: SearXNG, DuckDuckGo, Yahoo, GitHub, WolframAlpha")
    caller: str = Field("", description="Caller identifier for attribution (e.g. 'atlas', 'media_rename')")

    @field_validator("query")
    @classmethod
    def sanitize_query(cls, v: str) -> str:
        """Strip control characters and collapse excessive whitespace."""
        cleaned = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', '', v)
        cleaned = re.sub(r'\s{3,}', ' ', cleaned)
        return cleaned.strip()

    @field_validator("mode")
    @classmethod
    def validate_mode(cls, v: str) -> str:
        if v not in _VALID_MODES:
            raise ValueError(
                f"Invalid mode: {v}. Must be one of: {', '.join(sorted(_VALID_MODES))}"
            )
        return v

    @field_validator("providers")
    @classmethod
    def validate_providers(cls, v: Optional[List[str]]) -> Optional[List[str]]:
        if v is None:
            return v
        invalid = [p for p in v if p.lower() not in _VALID_PROVIDERS]
        if invalid:
            raise ValueError(f"Unknown providers: {', '.join(invalid)}")
        return [p.lower() for p in v]


class SearchResultSchema(BaseModel):
    url: str
    title: str
    snippet: str
    domain: str = ""
    provider: Optional[str] = None
    score: float = 0.0
    egress: Optional[str] = None
    machine: Optional[str] = None
    score_attribution: dict[str, float] = Field(default_factory=dict)


class ProviderTraceSchema(BaseModel):
    provider: str
    status: str
    results_count: int = 0
    latency_ms: int = 0
    error: Optional[str] = None
    budget_remaining: Optional[float] = None


class SearchResponse(BaseModel):
    query: str
    mode: str
    results: List[SearchResultSchema] = Field(default_factory=list)
    traces: List[ProviderTraceSchema] = Field(default_factory=list)
    total_results: int = 0
    cached: bool = False
    search_run_id: Optional[str] = None
    session_id: Optional[str] = None


class RecoverUrlRequest(BaseModel):
    url: str = Field(..., min_length=1, max_length=2048, description="URL to recover")
    title: Optional[str] = Field(None, description="Optional title hint for better results")
    domain: Optional[str] = Field(None, description="Optional domain hint")


class ExpandRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=500, description="Query to expand with related links")
    context: Optional[str] = Field(None, description="Optional context for expansion")


class ProviderTestRequest(BaseModel):
    provider: str = Field(..., description="Provider name to test")
    query: str = Field("argus", description="Test query")


class ExtractRequest(BaseModel):
    url: str = Field(..., min_length=1, max_length=2048, description="URL to extract content from")
    domain: Optional[str] = Field(None, description="Domain hint for authenticated extraction (e.g. nytimes.com)")
    mode: str = Field("default", description="Extraction mode: default, archive_ingest")

    @field_validator("url")
    @classmethod
    def validate_url(cls, v: str) -> str:
        if not v.startswith(("http://", "https://")):
            raise ValueError("URL must start with http:// or https://")
        # Block private/internal URLs to prevent SSRF
        from urllib.parse import urlparse
        parsed = urlparse(v)
        hostname = parsed.hostname or ""
        if hostname in ("localhost", "127.0.0.1", "::1") or hostname.startswith(("10.", "172.16.", "192.168.", "169.254.")):
            raise ValueError("Private/internal URLs are not allowed")
        return v

    @field_validator("mode")
    @classmethod
    def validate_mode(cls, v: str) -> str:
        if v not in ("default", "archive_ingest"):
            raise ValueError("Invalid extraction mode")
        return v


class ExtractResponse(BaseModel):
    url: str
    title: str = ""
    text: str = ""
    author: str = ""
    date: Optional[str] = None
    word_count: int = 0
    extractor: Optional[str] = None
    error: Optional[str] = None
    quality_passed: Optional[bool] = None
    quality_reason: Optional[str] = None
    extractors_tried: Optional[list[str]] = None
    # Completeness assessment — present when extraction succeeded
    is_complete: Optional[bool] = None
    completeness_confidence: Optional[float] = None
    truncation_type: Optional[str] = None
    completeness_signals: Optional[list[str]] = None
    recommended_action: Optional[str] = None

    # Provenance metadata
    source_type: Optional[str] = None
    egress: Optional[str] = None
    machine: Optional[str] = None
    auth_used: bool = False
    cookies_used: bool = False
    archive_used: bool = False
    cost: float = 0.0


class AssessContentRequest(BaseModel):
    text: str
    url: str = ""


class AssessContentResponse(BaseModel):
    is_complete: bool
    confidence: float
    truncation_type: str
    signals: list[str]
    word_count: int
    recommended_action: str


class ErrorResponse(BaseModel):
    error: str
    details: Optional[dict] = None


class PathsResponse(BaseModel):
    data_root: str
    docs_root: str
    docs_cache_dir: str
    docs_cache_index: str
    research_dir: str
    workflow_runs_dir: str
    snapshots_dir: str
    imports_dir: str
    env_override: Optional[str] = None
    uses_platformdirs: bool = False


class WorkflowArtifactSchema(BaseModel):
    kind: str
    path: str
    description: str = ""


class CitationSchema(BaseModel):
    id: str
    title: str
    url: str
    artifact_path: str
    note: str = ""


class SummarySectionSchema(BaseModel):
    heading: str
    body: str
    citation_ids: List[str] = Field(default_factory=list)


class StoredDocumentSchema(BaseModel):
    id: str
    url: str
    title: str
    artifact_path: str
    word_count: int = 0
    domain: str = ""
    role: str = "source"
    source_type: str = "web"
    extractor: Optional[str] = None
    egress: Optional[str] = None
    machine: Optional[str] = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class WorkflowRunResponse(BaseModel):
    run_id: str
    kind: str
    status: str
    target: str
    created_at: Optional[str] = None
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    status_url: Optional[str] = None
    snapshot_dir: str = ""
    report_path: Optional[str] = None
    manifest_path: Optional[str] = None
    artifacts: List[WorkflowArtifactSchema] = Field(default_factory=list)
    documents: List[StoredDocumentSchema] = Field(default_factory=list)
    citations: List[CitationSchema] = Field(default_factory=list)
    summary_sections: List[SummarySectionSchema] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    error: Optional[str] = None


class RecoverArticleWorkflowRequest(BaseModel):
    url: str = Field(..., min_length=1, max_length=2048)
    title: Optional[str] = None
    domain: Optional[str] = None


class CaptureSiteWorkflowRequest(BaseModel):
    url: str = Field(..., min_length=1, max_length=2048)
    soft_page_limit: int = Field(75, ge=1, le=500)
    hard_page_limit: int = Field(200, ge=1, le=500)


class BuildResearchPackWorkflowRequest(BaseModel):
    """Request schema for building a research pack.

    Attributes:
        topic: The research topic.
        official_url: Optional URL of official documentation.
        max_research_pages: Maximum number of external research pages to capture.
    """
    topic: str = Field(..., min_length=1, max_length=200, description="Research topic name")
    official_url: Optional[str] = Field(None, description="Official documentation URL if known")
    max_research_pages: int = Field(40, ge=1, le=200, description="Maximum external research pages to retrieve")



class SearchAndSummarizeWorkflowRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=1000, description="Search query to research and summarize")
    max_search_results: int = Field(5, ge=1, le=20, description="Number of search results to extract")

