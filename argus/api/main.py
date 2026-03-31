"""
Argus API - Search-as-a-service for Atlas.

Atlas sends content to index and queries to search.
Argus owns all embedding, vector storage, and answer synthesis.

Endpoints:
    POST /api/index      - Index content (called by Atlas after ingestion)
    POST /api/search     - Semantic search with optional synthesis
    POST /api/synthesize - Multi-source cross-analysis
    GET  /api/health     - Health check
"""

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from typing import List, Optional

app = FastAPI(
    title="Argus Search Service",
    version="0.1.0",
    description="Semantic search extracted from Atlas. Provides search-as-a-service.",
)


# --- Request/Response Models ---

class IndexRequest(BaseModel):
    """Content to be indexed. Sent by Atlas after ingestion/enrichment."""
    content_id: str
    title: str
    content_type: str  # article, podcast, email, note, newsletter
    source_url: Optional[str] = None
    text: str  # The full text to chunk and embed
    metadata: Optional[dict] = None


class IndexResponse(BaseModel):
    content_id: str
    status: str  # "indexed" | "skipped" | "error"
    chunks_created: int = 0
    message: Optional[str] = None


class SearchResultItem(BaseModel):
    content_id: str
    title: str
    content_type: str
    source_url: Optional[str] = None
    score: float
    snippet: Optional[str] = None
    chunk_id: Optional[str] = None


class SearchRequest(BaseModel):
    query: str
    limit: int = Field(default=20, le=100)
    synthesize: bool = Field(default=False, description="Generate an LLM answer from results")
    content_types: Optional[List[str]] = Field(default=None, description="Filter by type")
    sources: Optional[List[str]] = Field(default=None, description="Filter by source/author")


class SearchResponse(BaseModel):
    query: str
    results: List[SearchResultItem]
    total: int
    answer: Optional[str] = None  # Present when synthesize=true


class SynthesizeRequest(BaseModel):
    query: str
    mode: str = Field(default="compare", description="compare|timeline|summarize|contradict")
    sources: Optional[List[str]] = None
    max_sources: int = Field(default=10)


class SynthesizeResponse(BaseModel):
    query: str
    mode: str
    answer: str
    sources_used: List[str]
    confidence: float


class HealthResponse(BaseModel):
    status: str
    version: str
    vector_db: str
    total_chunks: int = 0


# --- Endpoints ---

@app.get("/api/health", response_model=HealthResponse)
async def health():
    """Health check. Called by Atlas health monitor."""
    from .search.config import get_config
    from .search.vector_store import VectorStore

    config = get_config()
    try:
        store = VectorStore(config.vector_db_path)
        total = store.count_chunks()
        db_status = "ok"
    except Exception:
        total = 0
        db_status = "error"

    return HealthResponse(
        status="ok" if db_status == "ok" else "degraded",
        version="0.1.0",
        vector_db=db_status,
        total_chunks=total,
    )


@app.post("/api/index", response_model=IndexResponse)
async def index_content(req: IndexRequest):
    """Index a piece of content. Called by Atlas after enrichment."""
    from .search.vector_store import VectorStore
    from .search.chunker import chunk_content
    from .search.embeddings import embed_texts
    from .search.config import get_config

    config = get_config()
    store = VectorStore(config.vector_db_path)

    # Check if already indexed
    existing = store.get_chunks_by_content_id(req.content_id)
    if existing:
        return IndexResponse(content_id=req.content_id, status="skipped", chunks_created=0, message="Already indexed")

    # Chunk the content
    chunks = chunk_content(req.text, max_tokens=config.chunking.max_chunk_tokens)

    if not chunks:
        return IndexResponse(content_id=req.content_id, status="skipped", chunks_created=0, message="No text to index")

    # Generate embeddings
    texts = [c.text for c in chunks]
    embeddings = embed_texts(texts)

    # Store
    chunk_ids = store.add_chunks(
        content_id=req.content_id,
        title=req.title,
        content_type=req.content_type,
        source_url=req.source_url,
        chunks=chunks,
        embeddings=embeddings,
    )

    return IndexResponse(content_id=req.content_id, status="indexed", chunks_created=len(chunk_ids))


@app.post("/api/search", response_model=SearchResponse)
async def search(req: SearchRequest):
    """Semantic search. Optional LLM synthesis."""
    from .search.retriever import HybridRetriever
    from .search.config import get_config

    config = get_config()
    retriever = HybridRetriever(config)

    results = retriever.retrieve(
        req.query,
        limit=req.limit,
        content_types=req.content_types,
        sources=req.sources,
    )

    items = [
        SearchResultItem(
            content_id=r.content_id,
            title=r.title,
            content_type=r.content_type,
            source_url=r.source_url,
            score=r.score,
            snippet=r.snippet,
            chunk_id=r.chunk_id,
        )
        for r in results
    ]

    answer = None
    if req.synthesize and results:
        from .search.synthesizer import AnswerSynthesizer
        synthesizer = AnswerSynthesizer(config)
        synthesized = synthesizer.ask(req.query, results)
        answer = synthesized.answer if synthesized else None

    return SearchResponse(
        query=req.query,
        results=items,
        total=len(items),
        answer=answer,
    )


@app.post("/api/synthesize", response_model=SynthesizeResponse)
async def synthesize(req: SynthesizeRequest):
    """Multi-source cross-analysis."""
    from .search.retriever import HybridRetriever
    from .search.synthesis import MultiSourceSynthesizer
    from .search.config import get_config

    config = get_config()
    retriever = HybridRetriever(config)

    results = retriever.retrieve(req.query, limit=50)

    ms = MultiSourceSynthesizer(config)
    synthesis = ms.synthesize(req.query, results, mode=req.mode, max_sources=req.max_sources)

    return SynthesizeResponse(
        query=req.query,
        mode=req.mode,
        answer=synthesis.answer,
        sources_used=synthesis.sources_used,
        confidence=synthesis.confidence,
    )


@app.delete("/api/index/{content_id}")
async def delete_content(content_id: str):
    """Remove all chunks for a content item. Called when Atlas deletes content."""
    from .search.vector_store import VectorStore
    from .search.config import get_config

    config = get_config()
    store = VectorStore(config.vector_db_path)
    deleted = store.delete_chunks_by_content_id(content_id)

    return {"content_id": content_id, "deleted_chunks": deleted}
