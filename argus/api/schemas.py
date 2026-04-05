"""
Pydantic request/response schemas for the HTTP API.
"""

import re
from typing import List, Optional, Set

from pydantic import BaseModel, Field, field_validator

_VALID_MODES: Set[str] = {"recovery", "discovery", "grounding", "research"}
_VALID_PROVIDERS: Set[str] = {"searxng", "brave", "serper", "tavily", "exa", "searchapi", "you"}


class SearchRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=500, description="Search query string")
    mode: str = Field("discovery", description="Search mode: recovery, discovery, grounding, research")
    max_results: int = Field(10, ge=1, le=50, description="Maximum results to return")
    providers: Optional[List[str]] = Field(None, description="Override provider routing order")
    session_id: Optional[str] = Field(None, description="Session ID for multi-turn context")

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
    results: List[SearchResultSchema] = []
    traces: List[ProviderTraceSchema] = []
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


class ErrorResponse(BaseModel):
    error: str
    details: Optional[dict] = None
