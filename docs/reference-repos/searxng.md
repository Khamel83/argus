# SearXNG — Reference Notes

## What it is
Self-hosted metasearch engine. Argus treats it as an upstream infrastructure dependency, the free local provider floor.

## API Response Format (`GET /search?format=json`)

Top-level:
```json
{
  "query": "string",
  "number_of_results": int,
  "results": [...],
  "answers": [...],
  "corrections": [...],
  "infoboxes": [...],
  "suggestions": [...],
  "unresponsive_engines": [[engine_name, error_message], ...]
}
```

Each result in `results[]`:
```json
{
  "url": "string | null",
  "engine": "string",
  "title": "string",
  "content": "string",
  "engines": ["string"],
  "score": float,
  "publishedDate": "string (ISO 8601) | null",
  "author": "string",
  "thumbnail": "string",
  "img_src": "string",
  "iframe_src": "string",
  "category": "string",
  "priority": "string ('', 'high', 'low')",
  "template": "string"
}
```

Key fields for Argus normalization:
- `url` → `SearchResult.url`
- `title` → `SearchResult.title`
- `content` → `SearchResult.snippet`
- `engine` → `SearchResult.provider` (map to ProviderName.SEARXNG)
- `score` → `SearchResult.score`
- `publishedDate` → `SearchResult.metadata["published_date"]`
- `author` → `SearchResult.metadata["author"]`

## Patterns to borrow
- Local-only deployment stance (127.0.0.1:8080)
- `/search?format=json` as the canonical search endpoint
- `unresponsive_engines` field for health tracking

## Patterns NOT to borrow
- Internal metasearch architecture
- Any fork/reshape of SearXNG itself
- Engine routing logic (SearXNG handles its own engine selection)

## Applies to
- **providers** (SearXNG adapter uses this API)
- **ops** (local-only deployment pattern)
