# Voy — Reference Notes

**Repo**: LeoMartinDev/voy (TypeScript/Bun, MIT)

## What it is
Self-hosted metasearch engine built on SearXNG with AI summaries, auth, and UI. Single provider (SearXNG only) — a thin facade.

## Patterns to borrow for Argus

### API
- `GET /api/health` → `{"status": "ok"}` — minimal health endpoint used by Docker HEALTHCHECK
- `GET /api/search?q=...` with structured validation errors: `{"error": "...", "details": {"q": ["Query is required"]}}`
- `GET /api/suggest` for autocomplete (returns OpenSearch format)
- Request correlation via `x-request-id` header on every response

### Ops
- Env validation at startup (fails fast with clear messages) — use Pydantic BaseSettings
- Docker HEALTHCHECK hits `/api/health`: `wget --spider http://0.0.0.0:$PORT/api/health`
- Auto-migrate on container start (entrypoint runs migrations before `exec "$@"`)
- Non-root container user (`su-exec nodejs`)
- Multi-stage Docker build (base → builder → runner)
- Structured JSON logging with sensitive field redaction

### Provider interface
- `SearchEngine` port with `search()` and `suggest()` methods
- Factory constructor: `makeSearXngSearchEngine({logger})`
- Zod schemas for parsing SearXNG responses

### Graceful degradation
- Suggest endpoint returns empty array with 200 on failure, not 500

## Patterns NOT to borrow
- TanStack file-based routing (full-stack React — irrelevant for Argus)
- Session auth / user accounts / setup wizard (consumer app feature)
- SearXNG as sole provider (Argus's whole point is multi-provider broker)
- LRU byte-size cache for raw SearXNG responses (Argus caches normalized objects differently)
- Clean Architecture 3-layer separation (Argus is simpler — flat modules)
- Image/file result categories (metasearch UI feature)
- Valkey dependency (only needed for SearXNG)

## Applies to
- **API** — endpoint shape, error format, request correlation
- **Ops** — Docker, healthcheck, auto-migrate, env validation
- **providers** — factory constructor pattern
