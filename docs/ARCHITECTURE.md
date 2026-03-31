# Argus Architecture

Argus is a standalone semantic search service extracted from Atlas. Atlas manages content ingestion, enrichment, and storage. Argus handles all search, embeddings, and answer synthesis.

## Relationship to Atlas

```
┌─────────────────────────────────────────────────────────────┐
│                         ATLAS                                │
│                                                              │
│  Ingest → Enrich → Store ──POST /api/index──→  ┌─────────┐ │
│                                                  │  ARGUS  │ │
│  CLI/Web ────GET /api/search──→ ←──────────────── │         │ │
│              POST /api/synthesize                  │ Search  │ │
│                                                     │ Service│ │
│  Health ←───GET /api/health─────────────────────── │         │ │
│                                                      └─────────┘ │
└─────────────────────────────────────────────────────────────┘
```

**Atlas owns:** ingestion, enrichment, content storage, podcast transcripts, URL processing
**Argus owns:** embeddings, vector storage, search, synthesis, annotations

## How Search Works Now (in Atlas)

### 1. Indexing Pipeline

Content flows through Atlas enrichment (ad removal, quality checks) then gets indexed:

```
Content on disk → ContentIndexer discovers it
  → ContentChunker splits into ~512 token chunks with 50 token overlap
  → EmbeddingClient generates 1536-dim vectors via OpenRouter (text-embedding-3-small)
  → VectorStore stores chunks + vectors in SQLite-vec with FTS5 full-text index
```

**Content sources currently indexed:**
- `data/podcasts/{slug}/transcripts/*.md` — Podcast transcripts
- `data/content/article/{year}/{month}/{day}/{id}/content.md` — Articles (prefers clean/ version)
- `data/content/email/{year}/{month}/{day}/{id}/content.md` — Emails
- `data/content/newsletter/{year}/{month}/{day}/{id}/content.md` — Newsletters
- `data/content/note/{year}/{month}/{day}/{id}/content.md` — Notes
- `data/stratechery/{articles,podcasts}/*.md` — Stratechery archive

### 2. Search Flow

```
User query → HybridRetriever
  → Vector search (SQLite-vec cosine similarity) — 70% weight
  → Keyword search (FTS5 BM25) — 30% weight
  → Reciprocal Rank Fusion merges both
  → Source diversity enforcement
  → Optionally: AnswerSynthesizer generates LLM answer from top results
```

### 3. Synthesis (Multi-Source Analysis)

```
Query → Retrieve many results
  → Cluster by source/author
  → Ensure minimum 3 sources
  → LLM analysis in one of 4 modes:
    - compare: How sources agree/disagree
    - timeline: How thinking evolved
    - summarize: Key insights across sources
    - contradict: Find contradictions
```

## What Argus Needs from Atlas

### API: `POST /api/index` — Atlas sends content to Argus

Atlas must POST content to Argus after enrichment is complete:

```json
{
  "content_id": "article-2026-03-31-abc123",
  "title": "The Future of AI Agents",
  "content_type": "article",
  "source_url": "https://example.com/article",
  "text": "Full text content here (ad-free, cleaned)...",
  "metadata": { "author": "...", "published": "..." }
}
```

### API: `POST /api/search` — Atlas asks Argus to search

```json
// Request
{
  "query": "What are the implications of AI for jobs?",
  "limit": 20,
  "synthesize": true,
  "content_types": ["podcast", "article"],
  "sources": ["stratechery", "ezra-klein-show"]
}

// Response
{
  "query": "What are the implications of AI for jobs?",
  "results": [
    {
      "content_id": "podcast-lex-fridman-123",
      "title": "Lex Fridman #412",
      "content_type": "podcast",
      "source_url": "https://...",
      "score": 0.89,
      "snippet": "The key thing about AI agents is..."
    }
  ],
  "total": 15,
  "answer": "Based on 15 sources, AI is expected to..."
}
```

### API: `POST /api/synthesize` — Multi-source analysis

```json
// Request
{
  "query": "Future of work",
  "mode": "compare",
  "sources": ["ben-thompson", "ezra-klein"],
  "max_sources": 10
}
```

### API: `DELETE /api/index/{content_id}` — Atlas tells Argus to remove content

When Atlas deletes content, it must notify Argus so the vector store stays in sync.

## What Argus Owns

| Component | Technology | Purpose |
|-----------|-----------|---------|
| Embeddings | OpenRouter → text-embedding-3-small | 1536-dim vectors |
| Vector DB | SQLite-vec | Embedding storage + cosine similarity |
| FTS | SQLite FTS5 | Full-text keyword search |
| LLM | OpenRouter → gemini-2.5-flash-lite | Answer synthesis |
| Annotations | SQLite | User notes/reactions/importance |
| Cost tracking | SQLite | Monthly spend on embeddings |

## Config

All config in `config/ask_config.yml`:

- **embeddings**: model, dimensions (1536), batch size (100)
- **llm**: model, max context tokens
- **chunking**: max 512 tokens, 50 overlap
- **retrieval**: vector 70% / keyword 30%, max 20 results
- **synthesis**: min 3 sources, max 15 chunks

## Dependencies

```
# Core
fastapi, uvicorn, pydantic
sqlite-vec  (vector search extension)
tiktoken    (token counting)
requests    (HTTP client for OpenRouter)

# From Atlas (copied)
argus/search/ — all search module files
argus/config/ask_config.yml
```

## Migration Path

1. **Phase 1** (now): Copy search code, create API layer, document contract
2. **Phase 2**: Stand up Argus service, Atlas starts POSTing to `/api/index`
3. **Phase 3**: Atlas search routes to Argus instead of local modules/ask
4. **Phase 4**: Remove modules/ask from Atlas, Atlas becomes a thin search client
5. **Phase 5**: Argus can serve other clients (web UI, mobile, CLI) independently

## Files Copied from Atlas

| Atlas File | Argus Location | Status |
|------------|---------------|--------|
| `modules/ask/chunker.py` | `argus/search/chunker.py` | Needs: remove Atlas paths |
| `modules/ask/embeddings.py` | `argus/search/embeddings.py` | Needs: remove Atlas imports |
| `modules/ask/vector_store.py` | `argus/search/vector_store.py` | Needs: remove Atlas imports |
| `modules/ask/retriever.py` | `argus/search/retriever.py` | Needs: remove Atlas imports |
| `modules/ask/synthesizer.py` | `argus/search/synthesizer.py` | Needs: remove Atlas imports |
| `modules/ask/synthesis.py` | `argus/search/synthesis.py` | Needs: remove Atlas imports |
| `modules/ask/annotations.py` | `argus/search/annotations.py` | Needs: remove Atlas imports |
| `modules/ask/output_formats.py` | `argus/search/output_formats.py` | Needs: remove Atlas imports |
| `modules/ask/intelligence.py` | `argus/search/intelligence.py` | Needs: remove Atlas imports |
| `modules/ask/topic_map.py` | `argus/search/topic_map.py` | Needs: remove Atlas imports |
| `modules/ask/config.py` | `argus/search/config.py` | Needs: update default paths |
| `modules/ask/indexer.py` | `argus/search/indexer.py` | Optional (Atlas indexes via API) |
| `config/ask_config.yml` | `config/ask_config.yml` | Ready as-is |

## TODO

- [ ] Remove Atlas-specific imports from all copied files
- [ ] Update config.py default paths for Argus
- [ ] Add requirements.txt
- [ ] Add OpenRouter API key to secrets
- [ ] Add systemd service file
- [ ] Update Atlas to POST content to Argus after enrichment
- [ ] Update Atlas CLI/API to call Argus search endpoints
- [ ] Import existing Atlas vector DB (or re-index from scratch)
- [ ] Add auth between Atlas and Argus (API key or mTLS)
