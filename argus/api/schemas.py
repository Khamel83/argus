"""
Pydantic request/response schemas for the HTTP API.
"""

from typing import List, Optional

from pydantic import BaseModel, Field


class SearchRequest(BaseModel):
    query: str = Field(..., min_length=1, description="Search query string")
    mode: str = Field("discovery", description="Search mode: recovery, discovery, grounding, research")
    max_results: int = Field(10, ge=1, le=50, description="Maximum results to return")
    providers: Optional[List[str]] = Field(None, description="Override provider routing order")


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


class RecoverUrlRequest(BaseModel):
    url: str = Field(..., min_length=1, description="URL to recover")
    title: Optional[str] = Field(None, description="Optional title hint for better results")
    domain: Optional[str] = Field(None, description="Optional domain hint")


class ExpandRequest(BaseModel):
    query: str = Field(..., min_length=1, description="Query to expand with related links")
    context: Optional[str] = Field(None, description="Optional context for expansion")


class ProviderTestRequest(BaseModel):
    provider: str = Field(..., description="Provider name to test")
    query: str = Field("argus", description="Test query")


class ErrorResponse(BaseModel):
    error: str
    details: Optional[dict] = None
