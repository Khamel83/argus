This file is a merged representation of a subset of the codebase, containing specifically included files and files not matching ignore patterns, combined into a single document by Repomix.
The content has been processed where comments have been removed, empty lines have been removed, content has been formatted for parsing in markdown style, content has been compressed (code blocks are separated by ⋮---- delimiter), security check has been disabled.

# File Summary

## Purpose
This file contains a packed representation of a subset of the repository's contents that is considered the most important context.
It is designed to be easily consumable by AI systems for analysis, code review,
or other automated processes.

## File Format
The content is organized as follows:
1. This summary section
2. Repository information
3. Directory structure
4. Repository files (if enabled)
5. Multiple file entries, each consisting of:
  a. A header with the file path (## File: path/to/file)
  b. The full contents of the file in a code block

## Usage Guidelines
- This file should be treated as read-only. Any changes should be made to the
  original repository files, not this packed version.
- When processing this file, use the file path to distinguish
  between different files in the repository.
- Be aware that this file may contain sensitive information. Handle it with
  the same level of security as you would the original repository.

## Notes
- Some files may have been excluded based on .gitignore rules and Repomix's configuration
- Binary files are not included in this packed representation. Please refer to the Repository Structure section for a complete list of file paths, including binary files
- Only files matching these patterns are included: **/*
- Files matching these patterns are excluded: .janitor/, secrets/, *.encrypted, *.env, *.env.tmp, *.key, *.age, eval/traces/, archive/, .cache/, .agent/, .beads/, .oneshot/, dispatch/, .claude/skills/the-audit/SOURCE_DOCS/, .opencode/, .claude/memory/, .claude/plans/, .claude/tasks/, .claude/delegation-log.jsonl
- Files matching patterns in .gitignore are excluded
- Files matching default ignore patterns are excluded
- Code comments have been removed from supported file types
- Empty lines have been removed from all files
- Content has been formatted for parsing in markdown style
- Content has been compressed - code blocks are separated by ⋮---- delimiter
- Security check has been disabled - content may contain sensitive information
- Files are sorted by Git change count (files with more changes are at the bottom)

# Directory Structure
```
.github/
  ISSUE_TEMPLATE/
    bug_report.md
    feature_request.md
  social-preview/
    social-card.png
  workflows/
    ci.yml
    docker-publish.yml
    publish.yml
  pull_request_template.md
  PULL_REQUEST_TEMPLATE.md
argus/
  api/
    __init__.py
    main.py
    rate_limit.py
    routes_admin.py
    routes_extract.py
    routes_health.py
    routes_search.py
    schemas.py
  broker/
    __init__.py
    balance_check.py
    budget_persistence.py
    budgets.py
    cache.py
    dedupe.py
    execution.py
    health.py
    pipeline.py
    policies.py
    ranking.py
    router.py
    session_flow.py
  cli/
    __init__.py
    main.py
  extraction/
    __init__.py
    archive_extractor.py
    auth_extractor.py
    cache.py
    cookies.py
    crawl4ai_extractor.py
    extractor.py
    firecrawl_extractor.py
    models.py
    playwright_extractor.py
    quality_gate.py
    rate_limit.py
    soft_404.py
    ssrf.py
    valyu_extractor.py
    wayback_extractor.py
    you_extractor.py
  mcp/
    __init__.py
    resources.py
    server.py
    tools.py
  persistence/
    __init__.py
    db.py
    models.py
  providers/
    __init__.py
    base.py
    brave.py
    duckduckgo.py
    exa.py
    github.py
    linkup.py
    parallel.py
    searchapi.py
    searxng.py
    serper.py
    tavily.py
    valyu_answer.py
    valyu.py
    you.py
  sessions/
    __init__.py
    models.py
    persistence.py
    refinement.py
    store.py
  __init__.py
  config.py
  logging.py
  models.py
docs/
  research/
    additional-providers-extractors/
      research.md
    mcp-search-competitors/
      gemini-research.md
      research.md
    competitive-analysis.md
    competitive-backlog.md
    mcp-search-landscape.md
  go-to-market.md
  providers.md
  PUBLICITY-CHECKLIST.md
tests/
  __init__.py
  test_api.py
  test_broker.py
  test_config.py
  test_extraction.py
  test_providers.py
  test_quality_gate.py
  test_sessions.py
.dockerignore
.env.example
.gitignore
CHANGELOG.md
CLAUDE.md
CODE_OF_CONDUCT.md
CONTRIBUTING.md
docker-compose.yml
Dockerfile
LICENSE
llms-full.txt
llms.txt
pyproject.toml
README.md
SECURITY.md
server.json
```

# Files

## File: .github/ISSUE_TEMPLATE/bug_report.md
````markdown
---
name: Bug report
about: Something isn't working
title: "[Bug] "
labels: bug
assignees: ''
---

**Describe the bug**
A clear description of what's happening.

**To reproduce**
Steps to reproduce:
1. 
2. 

**Expected behavior**
What you expected to happen.

**Environment**
- Python version:
- Install method (pip/Docker/local):
- Providers configured:
- Argus version:
````

## File: .github/ISSUE_TEMPLATE/feature_request.md
````markdown
---
name: Feature request
about: Suggest an idea
title: "[Feature] "
labels: enhancement
assignees: ''
---

**Problem**
What problem does this solve?

**Proposed solution**
Describe your idea.

**Alternatives considered**
Any other approaches you thought about?
````

## File: .github/PULL_REQUEST_TEMPLATE.md
````markdown
## Summary
Brief description of what this PR does and why.

## Changes
-

## Testing
- [ ] Tests pass: `pytest`
- [ ] Tested manually with:

## Checklist
- [ ] Code follows existing patterns
- [ ] New features have tests
````

## File: argus/api/__init__.py
````python

````

## File: argus/api/rate_limit.py
````python
logger = get_logger("api.rate_limit")
class RateLimiter
⋮----
now = time.time()
cutoff = now - self._window
window = self._requests[client_ip][path]
⋮----
retry_after = int(window[0] + self._window - now) + 1
⋮----
remaining = self._max_requests - len(window)
````

## File: argus/broker/__init__.py
````python
__all__ = ["SearchBroker", "create_broker"]
````

## File: argus/broker/cache.py
````python
class SearchCache
⋮----
def __init__(self, ttl_hours: int = 168)
def _key(self, query: str, mode: SearchMode) -> str
⋮----
normalized = query.strip().lower()
raw = f"{normalized}:{mode.value}"
⋮----
def get(self, query: str, mode: SearchMode) -> Optional[SearchResponse]
⋮----
key = self._key(query, mode)
⋮----
def put(self, query: str, mode: SearchMode, response: SearchResponse) -> None
def clear(self) -> None
def size(self) -> int
````

## File: argus/cli/__init__.py
````python

````

## File: argus/extraction/cache.py
````python
class ExtractionCache
⋮----
def __init__(self, ttl_hours: int = 168)
⋮----
@staticmethod
    def _key(url: str) -> str
⋮----
normalized = url.strip().rstrip("/")
⋮----
normalized = "https://" + normalized
⋮----
def get(self, url: str) -> Optional[ExtractedContent]
⋮----
key = self._key(url)
⋮----
def put(self, url: str, content: ExtractedContent) -> None
def clear(self) -> None
def size(self) -> int
````

## File: argus/extraction/rate_limit.py
````python
logger = get_logger("extraction.rate_limit")
class DomainRateLimiter
⋮----
def __init__(self, max_requests: int = 10, window_seconds: int = 60)
⋮----
@staticmethod
    def _extract_domain(url: str) -> str
def is_allowed(self, url: str) -> tuple[bool, int]
⋮----
"""Check if extracting from this URL's domain is allowed.
        Returns (allowed, retry_after_seconds).
        """
domain = self._extract_domain(url)
⋮----
now = time.time()
cutoff = now - self._window
# Prune old timestamps
window = self._requests[domain]
⋮----
retry_after = int(window[0] + self._window - now) + 1
⋮----
remaining = self._max_requests - len(window)
⋮----
def clear(self) -> None
````

## File: argus/mcp/__init__.py
````python
__all__ = ["serve_mcp"]
````

## File: argus/mcp/resources.py
````python
def provider_status_resource(broker: SearchBroker) -> str
⋮----
providers = {}
⋮----
def provider_budgets_resource(broker: SearchBroker) -> str
⋮----
budgets = {}
⋮----
def routing_policies_resource(broker: SearchBroker) -> str
⋮----
policies = {}
````

## File: argus/providers/__init__.py
````python
__all__ = ["BaseProvider"]
````

## File: argus/providers/base.py
````python
class BaseProvider(ABC)
⋮----
@property
@abstractmethod
    def name(self) -> ProviderName
⋮----
@abstractmethod
    def is_available(self) -> bool
⋮----
@abstractmethod
    def status(self) -> ProviderStatus
⋮----
@abstractmethod
    async def search(self, query: SearchQuery) -> tuple[List[SearchResult], ProviderTrace]
````

## File: argus/providers/searchapi.py
````python
class SearchApiProvider(BaseProvider)
⋮----
def __init__(self, config: ProviderConfig)
⋮----
@property
    def name(self) -> ProviderName
def is_available(self) -> bool
def status(self) -> ProviderStatus
async def search(self, query: SearchQuery) -> Tuple[List[SearchResult], ProviderTrace]
````

## File: argus/sessions/__init__.py
````python
__all__ = ["SessionStore", "Session", "QueryRecord"]
````

## File: argus/logging.py
````python
def setup_logging(level: Optional[str] = None) -> logging.Logger
⋮----
config_level = (level or "INFO").upper()
fmt = "%(asctime)s %(levelname)-5s [%(name)s] %(message)s"
datefmt = "%Y-%m-%d %H:%M:%S"
handler = logging.StreamHandler(sys.stdout)
⋮----
root = logging.getLogger("argus")
⋮----
def get_logger(name: str) -> logging.Logger
````

## File: tests/__init__.py
````python

````

## File: tests/test_config.py
````python
class TestConfig
⋮----
def test_load_config_defaults(self)
⋮----
cfg = load_config()
⋮----
def test_load_config_from_env(self, monkeypatch)
def test_load_config_uses_secret_fallbacks(self)
⋮----
class StubSecrets(SecretsResolver)
⋮----
def get(self, key: str) -> str
cfg = load_config(environ={}, secrets_resolver=StubSecrets())
⋮----
def test_get_config_singleton(self)
⋮----
c1 = get_config()
c2 = get_config()
⋮----
def test_force_reload_rebuilds_singleton(self, monkeypatch)
⋮----
c2 = get_config(force_reload=True)
⋮----
class TestModels
⋮----
def test_search_mode(self)
def test_provider_name(self)
def test_provider_status(self)
def test_search_result(self)
⋮----
r = SearchResult(url="https://example.com", title="Example", snippet="A test page")
⋮----
def test_search_query(self)
⋮----
q = SearchQuery(query="test", mode=SearchMode.GROUNDING, max_results=5)
⋮----
def test_provider_trace(self)
⋮----
t = ProviderTrace(provider=ProviderName.BRAVE, status="success", results_count=10)
⋮----
def test_search_response(self)
⋮----
resp = SearchResponse(query="test", mode=SearchMode.DISCOVERY, results=[])
⋮----
class TestLogging
⋮----
def test_setup_logging(self)
⋮----
logger = setup_logging("DEBUG")
⋮----
child = get_logger("broker")
⋮----
def test_get_logger(self)
⋮----
log = get_logger("test")
````

## File: tests/test_sessions.py
````python
class TestSessionModels
⋮----
def test_query_record_defaults(self)
⋮----
record = QueryRecord(query="test query")
⋮----
def test_session_defaults(self)
⋮----
session = Session(id="abc123")
⋮----
def test_session_extracts_urls_from_all_queries(self)
⋮----
session = Session(id="abc")
⋮----
class TestSessionStore
⋮----
def test_create_session(self)
⋮----
store = SessionStore(persist=False)
session = store.create_session()
⋮----
def test_create_session_with_id(self)
⋮----
session = store.create_session(session_id="myid")
⋮----
def test_create_session_returns_existing(self)
⋮----
s1 = store.create_session(session_id="same")
s2 = store.create_session(session_id="same")
⋮----
def test_get_session(self)
⋮----
session = store.get_session("findme")
⋮----
def test_get_session_missing(self)
def test_add_query(self)
⋮----
session = store.create_session(session_id="s1")
⋮----
def test_add_query_missing_session(self)
⋮----
result = store.add_query("missing", query="test")
⋮----
def test_add_extracted_url(self)
def test_list_sessions(self)
class TestRefinement
⋮----
def test_no_session_returns_query(self)
def test_empty_session_returns_query(self)
⋮----
session = Session(id="s1")
⋮----
def test_single_prior_query_follow_up(self)
⋮----
result = refine_query("fastapi", session)
⋮----
def test_long_query_not_modified(self)
⋮----
result = refine_query("what is the best python web framework for building apis", session)
⋮----
def test_no_prior_queries_unchanged(self)
⋮----
result = refine_query("only query so far", session)
⋮----
def test_skip_short_prior_queries(self)
⋮----
result = refine_query("await", session)
⋮----
class TestSessionPersistence
⋮----
def test_persist_and_load_session(self)
⋮----
db_path = f.name
⋮----
store1 = SessionStore(persist=True, db_path=db_path)
session = store1.create_session(session_id="persist-test")
⋮----
store2 = SessionStore(persist=True, db_path=db_path)
loaded = store2.get_session("persist-test")
⋮----
def test_persist_survives_restart(self)
⋮----
loaded = store2.get_session("multi-q")
⋮----
def test_persist_false_no_persistence(self)
⋮----
store1 = SessionStore(persist=False, db_path=db_path)
⋮----
store2 = SessionStore(persist=False, db_path=db_path)
⋮----
def test_persistence_list_sessions(self)
⋮----
sessions = store2.list_sessions()
⋮----
ids = {s.id for s in sessions}
⋮----
def test_create_session_uses_exists_check_not_full_scan(self)
⋮----
loaded = store2.create_session(session_id="existing")
````

## File: LICENSE
````
MIT License

Copyright (c) 2026 Khamel

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
````

## File: argus/api/routes_admin.py
````python
router = APIRouter()
def get_broker(request: Request) -> SearchBroker
⋮----
@router.post("/test-provider")
async def test_provider(req: ProviderTestRequest, broker: SearchBroker = Depends(get_broker))
⋮----
pname = ProviderName(req.provider)
⋮----
provider = broker._providers.get(pname)
⋮----
query = SearchQuery(query=req.query, mode=SearchMode.DISCOVERY, max_results=3)
````

## File: argus/api/routes_health.py
````python
router = APIRouter()
def get_broker(request: Request) -> SearchBroker
⋮----
@router.get("/health")
async def health(broker: SearchBroker = Depends(get_broker))
⋮----
all_providers = {}
⋮----
status = broker.get_provider_status(pname)
⋮----
healthy = any(
⋮----
@router.get("/health/detail")
async def health_detail(broker: SearchBroker = Depends(get_broker))
⋮----
providers = {}
⋮----
health_all = broker.health_tracker.get_all_status()
⋮----
@router.get("/budgets")
async def budgets(broker: SearchBroker = Depends(get_broker))
⋮----
budget_info = {}
⋮----
token_balances = {}
store = broker.budget_tracker._store
⋮----
token_balances = store.get_all_token_balances()
````

## File: argus/api/routes_search.py
````python
router = APIRouter()
def get_broker(request: Request) -> SearchBroker
def _to_response(resp) -> SearchResponse
⋮----
@router.post("/search", response_model=SearchResponse)
async def search(req: SearchRequest, broker: SearchBroker = Depends(get_broker))
⋮----
query = SearchQuery(
⋮----
response = _to_response(resp)
⋮----
resp = await broker.search(query)
⋮----
@router.post("/recover-url", response_model=SearchResponse)
async def recover_url(req: RecoverUrlRequest, broker: SearchBroker = Depends(get_broker))
⋮----
query_parts = [req.url]
⋮----
search_query = SearchQuery(
resp = await broker.search(search_query)
⋮----
@router.post("/expand", response_model=SearchResponse)
async def expand(req: ExpandRequest, broker: SearchBroker = Depends(get_broker))
⋮----
query_text = req.query
⋮----
query_text = f"{req.query} {req.context}"
````

## File: argus/broker/balance_check.py
````python
logger = get_logger("broker.balance_check")
⋮----
@dataclass
class ProviderBalance
⋮----
provider: ProviderName
remaining: Optional[float] = None
limit: Optional[float] = None
used: Optional[float] = None
unit: str = "queries"
source: str = ""  # e.g. "api", "headers"
raw: Optional[dict] = None
error: Optional[str] = None
async def check_tavily(api_key: str) -> ProviderBalance
⋮----
resp = await client.get(
⋮----
data = resp.json()
key_info = data.get("key", {})
account_info = data.get("account", {})
usage = key_info.get("usage", 0)
limit = key_info.get("limit", 0)
remaining = max(0, limit - usage)
⋮----
async def check_serper(api_key: str) -> ProviderBalance
⋮----
resp = await client.post(
⋮----
credits = data.get("credits", None)
⋮----
async def check_brave(api_key: str) -> ProviderBalance
⋮----
info = {}
⋮----
val = resp.headers.get(hdr)
⋮----
remaining = None
⋮----
remaining = float(info["X-RateLimit-Remaining"])
⋮----
async def check_parallel(api_key: str) -> ProviderBalance
⋮----
remaining = float(info["X-RateLimit-Remaining-Requests"])
⋮----
async def check_linkup(api_key: str) -> ProviderBalance
⋮----
remaining = float(info["X-Credits-Remaining"])
⋮----
_CHECKERS = {
async def check_all_balances(api_keys: dict[ProviderName, str]) -> list[ProviderBalance]
⋮----
results = []
⋮----
key = api_keys.get(provider)
⋮----
balance = await checker(key)
````

## File: argus/broker/dedupe.py
````python
def normalize_url(url: str) -> str
⋮----
parsed = urlparse(url)
scheme = parsed.scheme.lower()
netloc = parsed.netloc.lower()
⋮----
netloc = netloc[4:]
path = parsed.path.rstrip("/") or "/"
query = parsed.query
⋮----
params = sorted(query.split("&"))
query = "&".join(params)
⋮----
params = [p for p in query.split("&") if not p.startswith(("utm_", "ref=", "fbclid", "gclid"))]
query = "&".join(params) if params else ""
normalized = f"{scheme}://{netloc}{path}"
⋮----
def extract_domain(url: str) -> str
⋮----
"""Extract the domain from a URL."""
⋮----
def dedupe_results(results: list[SearchResult]) -> list[SearchResult]
⋮----
"""Deduplicate results by normalized URL.
    Keeps the first occurrence (highest RRF score should already be first).
    """
seen_urls = set()
deduped = []
⋮----
normalized = normalize_url(result.url)
````

## File: argus/broker/health.py
````python
@dataclass
class ProviderHealth
⋮----
provider: ProviderName
consecutive_failures: int = 0
last_success: Optional[float] = None
last_failure: Optional[float] = None
disabled_until: Optional[float] = None
def record_success(self) -> None
def record_failure(self) -> None
def is_in_cooldown(self) -> bool
def apply_cooldown(self, minutes: int) -> None
class HealthTracker
⋮----
def __init__(self, failure_threshold: int = 5, cooldown_minutes: int = 60)
def _get(self, provider: ProviderName) -> ProviderHealth
def record_success(self, provider: ProviderName) -> None
def record_failure(self, provider: ProviderName) -> None
⋮----
health = self._get(provider)
⋮----
def get_status(self, provider: ProviderName) -> Optional[ProviderStatus]
def get_health(self, provider: ProviderName) -> ProviderHealth
def get_all_status(self) -> dict[ProviderName, dict]
⋮----
result = {}
⋮----
status = self.get_status(provider)
````

## File: argus/broker/pipeline.py
````python
logger = get_logger("broker.pipeline")
class SearchResultPipeline
⋮----
def get_cached(self, query: SearchQuery, run_id: str) -> SearchResponse | None
⋮----
cached = self._cache.get(query.query, query.mode)
⋮----
def build_response(self, query: SearchQuery, provider_results: dict, traces: list, budget_warnings: list | None = None) -> SearchResponse
⋮----
merged = reciprocal_rank_fusion(provider_results)
final_results = dedupe_results(merged)[: query.max_results]
response = SearchResponse(
````

## File: argus/broker/ranking.py
````python
RRF_K = 60
⋮----
scores: dict[str, float] = {}
seen: dict[str, SearchResult] = {}
⋮----
rrf_score = 1.0 / (k + rank + 1)
url = result.url
⋮----
sorted_urls = sorted(scores.keys(), key=lambda u: scores[u], reverse=True)
merged = []
⋮----
result = seen[url]
````

## File: argus/extraction/__init__.py
````python
__all__ = [
````

## File: argus/extraction/archive_extractor.py
````python
logger = get_logger("extraction.archive_is")
ARCHIVE_DOMAINS = ["archive.ph", "archive.is", "archive.today"]
ARCHIVE_SUBMIT_URL = "https://archive.ph/submit"
ARCHIVE_NEWEST_URL = "https://archive.ph/newest/"
_min_interval = 5.0
_last_request_time = 0.0
_lock = None
def _get_lock()
⋮----
_lock = asyncio.Lock()
⋮----
async def _rate_limit()
⋮----
now = time.monotonic()
wait = _min_interval - (now - _last_request_time)
⋮----
_last_request_time = time.monotonic()
async def _search_existing(url: str) -> Optional[str]
⋮----
resp = await client.get(f"{ARCHIVE_NEWEST_URL}{url}")
# archive.ph redirects to the archive page if one exists
# If the URL is the same as what we requested, no archive exists
⋮----
final_url = str(resp.url)
# If we were redirected to an archive page (contains /<id>/)
⋮----
async def _submit_and_fetch(url: str) -> Optional[str]
⋮----
resp = await client.post(
⋮----
# Check response text for archive ID
match = re.search(r'archive\.(ph|is|today)/(\w+)/', resp.text)
⋮----
domain = match.group(1)
archive_id = match.group(2)
⋮----
async def _extract_archive(url: str) -> ExtractedContent
⋮----
archive_url = await _search_existing(url)
⋮----
archive_url = await _submit_and_fetch(url)
⋮----
resp = await client.get(archive_url, headers={"User-Agent": "Argus/1.0"})
⋮----
html = resp.text
⋮----
loop = asyncio.get_event_loop()
downloaded = await loop.run_in_executor(
⋮----
downloaded = html
extracted = await loop.run_in_executor(
⋮----
text = extracted["text"]
⋮----
async def extract_archive_is(url: str) -> ExtractedContent
⋮----
"""Public entry point for Archive.is extraction."""
````

## File: argus/extraction/crawl4ai_extractor.py
````python
logger = get_logger("extraction.crawl4ai")
async def extract_crawl4ai(url: str) -> ExtractedContent
⋮----
result = await crawler.arun(url)
⋮----
text = result.markdown.strip()
````

## File: argus/extraction/firecrawl_extractor.py
````python
logger = get_logger("extraction.firecrawl")
FIRECRAWL_API_URL = "https://api.firecrawl.dev/v1/scrape"
TIMEOUT = int(os.getenv("ARGUS_EXTRACTION_TIMEOUT_SECONDS", "30"))
async def extract_firecrawl(url: str) -> ExtractedContent
⋮----
api_key = os.getenv("ARGUS_FIRECRAWL_API_KEY", "")
⋮----
headers = {
body = {"url": url}
⋮----
resp = await client.post(FIRECRAWL_API_URL, json=body, headers=headers)
⋮----
data = resp.json()
⋮----
result = data.get("data", {})
markdown = result.get("markdown", "")
⋮----
metadata = result.get("metadata", {})
title = metadata.get("title", "") or result.get("title", "")
````

## File: argus/extraction/playwright_extractor.py
````python
logger = get_logger("extraction.playwright")
_browser = None
_playwright_instance = None
_PLAYWRIGHT_AVAILABLE = None
def _check_playwright()
⋮----
_PLAYWRIGHT_AVAILABLE = True
⋮----
_PLAYWRIGHT_AVAILABLE = False
⋮----
async def _get_browser()
⋮----
_playwright_instance = await async_playwright().start()
_browser = await _playwright_instance.chromium.launch(
⋮----
async def _extract_playwright(url: str, timeout_ms: int = 15000) -> ExtractedContent
⋮----
browser = await _get_browser()
⋮----
page = None
⋮----
page = await browser.new_page()
⋮----
text = await page.evaluate("""() => {
title = await page.title()
⋮----
text = text.strip()
⋮----
async def extract_playwright(url: str) -> ExtractedContent
⋮----
"""Public entry point for Playwright extraction."""
⋮----
async def close_browser()
⋮----
"""Close the shared browser instance (call on shutdown)."""
````

## File: argus/extraction/valyu_extractor.py
````python
logger = get_logger("extraction.valyu")
VALYU_CONTENTS_URL = "https://api.valyu.ai/v1/contents"
TIMEOUT = int(os.getenv("ARGUS_EXTRACTION_TIMEOUT_SECONDS", "15"))
async def extract_valyu_contents(url: str) -> ExtractedContent
⋮----
config = get_config()
⋮----
headers = {
body = {
⋮----
resp = await client.post(VALYU_CONTENTS_URL, json=body, headers=headers)
⋮----
data = resp.json()
⋮----
results = data.get("results", [])
⋮----
page = results[0]
⋮----
text = page.get("content", "")
````

## File: argus/extraction/wayback_extractor.py
````python
logger = get_logger("extraction.wayback")
AVAILABILITY_URL = "https://archive.org/wayback/available"
WAYBACK_CONTENT_PREFIX = "https://web.archive.org/web"
_min_interval = 10.0
_last_request_time = 0.0
_lock = None
def _get_lock()
⋮----
_lock = asyncio.Lock()
⋮----
async def _rate_limit()
⋮----
now = time.monotonic()
wait = _min_interval - (now - _last_request_time)
⋮----
_last_request_time = time.monotonic()
async def _check_availability(url: str) -> Optional[str]
⋮----
resp = await client.get(AVAILABILITY_URL, params={"url": url})
⋮----
data = resp.json()
⋮----
snapshot = data["archived_snapshots"].get("closest")
⋮----
async def _fetch_archived(wayback_url: str) -> str
⋮----
resp = await client.get(wayback_url, headers={"User-Agent": "Argus/1.0"})
⋮----
async def _extract_wayback(url: str) -> ExtractedContent
⋮----
wayback_url = await _check_availability(url)
⋮----
html = await _fetch_archived(wayback_url)
⋮----
loop = asyncio.get_event_loop()
downloaded = await loop.run_in_executor(
⋮----
downloaded = html
extracted = await loop.run_in_executor(
⋮----
text = extracted["text"]
⋮----
async def extract_wayback(url: str) -> ExtractedContent
⋮----
"""Public entry point for Wayback extraction."""
````

## File: argus/extraction/you_extractor.py
````python
logger = get_logger("extraction.you")
YOU_CONTENTS_URL = "https://ydc-index.io/v1/contents"
TIMEOUT = int(os.getenv("ARGUS_EXTRACTION_TIMEOUT_SECONDS", "15"))
async def extract_you_contents(url: str) -> ExtractedContent
⋮----
config = get_config()
⋮----
headers = {
body = {
⋮----
resp = await client.post(YOU_CONTENTS_URL, json=body, headers=headers)
⋮----
data = resp.json()
⋮----
page = data[0]
markdown = page.get("markdown", "")
⋮----
title = page.get("title", "")
text = markdown.strip()
````

## File: argus/persistence/__init__.py
````python
__all__ = ["get_session", "get_session_factory", "init_db", "persist_search"]
````

## File: argus/persistence/models.py
````python
class Base(DeclarativeBase)
class SearchQueryRow(Base)
⋮----
__tablename__ = "search_queries"
id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
query_text: Mapped[str] = mapped_column(Text, nullable=False)
mode: Mapped[str] = mapped_column(String(50), nullable=False)
max_results: Mapped[int] = mapped_column(Integer, default=10)
created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
runs: Mapped[list["SearchRunRow"]] = relationship(back_populates="query")
class SearchRunRow(Base)
⋮----
__tablename__ = "search_runs"
⋮----
query_id: Mapped[int] = mapped_column(ForeignKey("search_queries.id"), nullable=False)
search_run_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
status: Mapped[str] = mapped_column(String(50), nullable=False, default="started")
total_results: Mapped[int] = mapped_column(Integer, default=0)
cached: Mapped[bool] = mapped_column(Boolean, default=False)
⋮----
finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
query: Mapped["SearchQueryRow"] = relationship(back_populates="runs")
results: Mapped[list["SearchResultRow"]] = relationship(back_populates="run")
traces: Mapped[list["ProviderUsageRow"]] = relationship(back_populates="run")
class SearchResultRow(Base)
⋮----
__tablename__ = "search_results"
⋮----
run_id: Mapped[int] = mapped_column(ForeignKey("search_runs.id"), nullable=False)
url: Mapped[str] = mapped_column(Text, nullable=False)
title: Mapped[str] = mapped_column(Text, default="")
snippet: Mapped[str] = mapped_column(Text, default="")
domain: Mapped[str] = mapped_column(String(255), default="")
provider: Mapped[str] = mapped_column(String(50), default="")
score: Mapped[float] = mapped_column(Float, default=0.0)
final_rank: Mapped[int] = mapped_column(Integer, default=0)
metadata_json: Mapped[str | None] = mapped_column(Text, nullable=True)
⋮----
run: Mapped["SearchRunRow"] = relationship(back_populates="results")
class ProviderUsageRow(Base)
⋮----
__tablename__ = "provider_usage"
⋮----
provider: Mapped[str] = mapped_column(String(50), nullable=False)
status: Mapped[str] = mapped_column(String(50), nullable=False)
results_count: Mapped[int] = mapped_column(Integer, default=0)
latency_ms: Mapped[int] = mapped_column(Integer, default=0)
error: Mapped[str | None] = mapped_column(Text, nullable=True)
budget_remaining: Mapped[float | None] = mapped_column(Float, nullable=True)
⋮----
run: Mapped["SearchRunRow"] = relationship(back_populates="traces")
class SearchEvidenceRow(Base)
⋮----
__tablename__ = "search_evidence"
⋮----
source_provider: Mapped[str] = mapped_column(String(50), default="")
⋮----
evidence_type: Mapped[str] = mapped_column(String(50), default="search")
````

## File: argus/providers/duckduckgo.py
````python
logger = get_logger("providers.duckduckgo")
class DuckDuckGoProvider(BaseProvider)
⋮----
def __init__(self)
def _check_available(self) -> bool
⋮----
@property
    def name(self) -> ProviderName
def is_available(self) -> bool
def status(self) -> ProviderStatus
async def search(self, query: SearchQuery) -> Tuple[List[SearchResult], ProviderTrace]
⋮----
start = time.monotonic()
⋮----
ddgs = DDGS()
raw_results = list(ddgs.text(query.query, max_results=query.max_results))
results = self._normalize(raw_results)
latency_ms = int((time.monotonic() - start) * 1000)
trace = ProviderTrace(
⋮----
def _normalize(self, raw_results: list) -> List[SearchResult]
⋮----
results = []
⋮----
url = item.get("href", "")
⋮----
@staticmethod
    def _extract_domain(url: str) -> str
````

## File: argus/providers/exa.py
````python
logger = get_logger("providers.exa")
EXA_API_BASE = "https://api.exa.ai/search"
class ExaProvider(BaseProvider)
⋮----
def __init__(self, config: ProviderConfig)
⋮----
@property
    def name(self) -> ProviderName
def is_available(self) -> bool
def status(self) -> ProviderStatus
async def search(self, query: SearchQuery) -> Tuple[List[SearchResult], ProviderTrace]
⋮----
start = time.monotonic()
headers = {
payload = {
⋮----
resp = await client.post(EXA_API_BASE, json=payload, headers=headers)
⋮----
data = resp.json()
raw_results = data.get("results", [])
results = self._normalize(raw_results)
latency_ms = int((time.monotonic() - start) * 1000)
trace = ProviderTrace(
⋮----
def _normalize(self, raw_results: list) -> List[SearchResult]
⋮----
results = []
⋮----
url = item.get("url") or ""
⋮----
@staticmethod
    def _extract_domain(url: str) -> str
````

## File: argus/providers/github.py
````python
logger = get_logger("providers.github")
GITHUB_API_BASE = "https://api.github.com/search/repositories"
GITHUB_CODE_BASE = "https://api.github.com/search/code"
class GitHubProvider(BaseProvider)
⋮----
def __init__(self, config: ProviderConfig)
⋮----
@property
    def name(self) -> ProviderName
def is_available(self) -> bool
def status(self) -> ProviderStatus
async def search(self, query: SearchQuery) -> Tuple[List[SearchResult], ProviderTrace]
⋮----
start = time.monotonic()
headers = {
⋮----
params = {
⋮----
resp = await client.get(GITHUB_API_BASE, params=params, headers=headers)
⋮----
latency_ms = int((time.monotonic() - start) * 1000)
trace = ProviderTrace(
⋮----
data = resp.json()
items = data.get("items", [])
results = self._normalize(items)
⋮----
credit_info = {}
⋮----
def _normalize(self, items: list) -> List[SearchResult]
⋮----
results = []
⋮----
url = item.get("html_url") or ""
````

## File: argus/providers/searxng.py
````python
logger = get_logger("providers.searxng")
class SearXNGProvider(BaseProvider)
⋮----
def __init__(self, config: SearXNGConfig)
⋮----
@property
    def name(self) -> ProviderName
def is_available(self) -> bool
def status(self) -> ProviderStatus
async def search(self, query: SearchQuery) -> Tuple[List[SearchResult], ProviderTrace]
⋮----
start = time.monotonic()
url = f"{self._config.base_url.rstrip('/')}/search"
params = {
headers = {"Accept": "application/json"}
⋮----
resp = await client.get(url, params=params, headers=headers)
⋮----
data = resp.json()
results = self._normalize(data.get("results", []))
latency_ms = int((time.monotonic() - start) * 1000)
trace = ProviderTrace(
⋮----
def _normalize(self, raw_results: list) -> List[SearchResult]
⋮----
results = []
⋮----
url = item.get("url") or ""
⋮----
@staticmethod
    def _extract_domain(url: str) -> str
````

## File: argus/providers/tavily.py
````python
logger = get_logger("providers.tavily")
TAVILY_API_BASE = "https://api.tavily.com/search"
class TavilyProvider(BaseProvider)
⋮----
def __init__(self, config: ProviderConfig)
⋮----
@property
    def name(self) -> ProviderName
def is_available(self) -> bool
def status(self) -> ProviderStatus
async def search(self, query: SearchQuery) -> Tuple[List[SearchResult], ProviderTrace]
⋮----
start = time.monotonic()
headers = {
payload = {
⋮----
resp = await client.post(TAVILY_API_BASE, json=payload, headers=headers)
⋮----
data = resp.json()
raw_results = data.get("results", [])
results = self._normalize(raw_results)
latency_ms = int((time.monotonic() - start) * 1000)
trace = ProviderTrace(
⋮----
def _normalize(self, raw_results: list) -> List[SearchResult]
⋮----
results = []
⋮----
url = item.get("url") or ""
⋮----
@staticmethod
    def _extract_domain(url: str) -> str
````

## File: argus/providers/valyu_answer.py
````python
logger = get_logger("providers.valyu_answer")
VALYU_ANSWER_URL = "https://api.valyu.ai/v1/answer"
DEFAULT_TIMEOUT = 30
⋮----
@dataclass
class ValyuAnswerResult
⋮----
answer: str = ""
sources: list = field(default_factory=list)  # search result citations
cost_usd: float = 0.0
ai_usage: dict = field(default_factory=dict)
tx_id: str = ""
error: Optional[str] = None
⋮----
config = get_config()
⋮----
headers = {
payload: dict = {
⋮----
start = time.monotonic()
answer_chunks = []
sources = []
⋮----
data_str = line[6:]
⋮----
data = json.loads(data_str)
⋮----
delta = data["choices"][0].get("delta", {})
content = delta.get("content", "")
⋮----
# metadata event
⋮----
cost_info = data.get("cost", {})
⋮----
latency_ms = int((time.monotonic() - start) * 1000)
⋮----
body = e.response.json()
error_msg = body.get("error", str(e))
⋮----
error_msg = str(e)
````

## File: argus/providers/valyu.py
````python
logger = get_logger("providers.valyu")
VALYU_API_BASE = "https://api.valyu.ai/v1/search"
class ValyuProvider(BaseProvider)
⋮----
def __init__(self, config: ProviderConfig)
⋮----
@property
    def name(self) -> ProviderName
def is_available(self) -> bool
def status(self) -> ProviderStatus
async def search(self, query: SearchQuery) -> Tuple[List[SearchResult], ProviderTrace]
⋮----
start = time.monotonic()
headers = {
payload = {
⋮----
resp = await client.post(VALYU_API_BASE, json=payload, headers=headers)
⋮----
data = resp.json()
⋮----
error_msg = data.get("error", "unknown error")
latency_ms = int((time.monotonic() - start) * 1000)
trace = ProviderTrace(
⋮----
raw_results = data.get("results", [])
results = self._normalize(raw_results)
⋮----
def _normalize(self, raw_results: list) -> List[SearchResult]
⋮----
results = []
⋮----
url = item.get("url") or ""
⋮----
@staticmethod
    def _extract_domain(url: str) -> str
````

## File: argus/providers/you.py
````python
logger = get_logger("providers.you")
YOU_API_BASE = "https://api.you.com/v1/search"
class YouProvider(BaseProvider)
⋮----
def __init__(self, config: ProviderConfig)
⋮----
@property
    def name(self) -> ProviderName
def is_available(self) -> bool
def status(self) -> ProviderStatus
async def search(self, query: SearchQuery) -> Tuple[List[SearchResult], ProviderTrace]
⋮----
start = time.monotonic()
⋮----
headers = {
params = {
⋮----
resp = await client.get(YOU_API_BASE, params=params, headers=headers)
⋮----
data = resp.json()
web_results = data.get("results", {}).get("web", [])
results = self._normalize(web_results)
latency_ms = int((time.monotonic() - start) * 1000)
trace = ProviderTrace(
⋮----
def _normalize(self, raw_results: list) -> List[SearchResult]
⋮----
results = []
⋮----
url = item.get("url") or ""
⋮----
# You.com returns snippets as a list; join the first one
snippets = item.get("snippets", [])
snippet = snippets[0] if snippets else item.get("description", "")
⋮----
@staticmethod
    def _extract_domain(url: str) -> str
````

## File: argus/sessions/models.py
````python
@dataclass
class QueryRecord
⋮----
query: str
mode: str = "discovery"
timestamp: datetime = field(default_factory=lambda: datetime.now(tz=None))
results_count: int = 0
extracted_urls: List[str] = field(default_factory=list)
⋮----
@dataclass
class Session
⋮----
id: str
created_at: datetime = field(default_factory=lambda: datetime.now(tz=None))
queries: List[QueryRecord] = field(default_factory=list)
⋮----
@property
    def extracted_urls(self) -> List[str]
⋮----
urls = []
````

## File: argus/__init__.py
````python
__version__ = "1.0.0"
````

## File: docs/research/additional-providers-extractors/research.md
````markdown
# Additional Search Providers & Content Extractors for Argus

**Research date:** 2026-04-09
**Status:** Active providers already in Argus: SearXNG, Brave, Serper, Tavily, Exa (active); SearchAPI, You.com (stubs)
**Active extractors already in Argus:** trafilatura (local), Jina Reader (API), Playwright (JS rendering), auth_extractor (cookie-based), archive_extractor, wayback_extractor

---

## Executive Summary

The highest-impact additions to Argus fall into two tiers. **Tier 1 (add soon):** Google Programmable Search Engine as a free/cheap foundational provider ($5/1K queries, 100 free/day), Perplexity Sonar API for answer-synthesis mode ($5-12/1K + tokens, useful when the caller LLM is weak), and Crawl4AI as a self-hosted extraction fallback (free, open-source, 50K+ GitHub stars, LLM-aware chunking). **Tier 2 (add when needed):** SerpAPI for multi-engine SERP access ($75+/mo, enterprise-grade), Firecrawl for integrated search+extraction ($83/100K credits), Kagi API for high-quality ad-free results ($10/mo plan with API access), and Diffbot for structured knowledge-graph extraction (10K free calls/mo). **Skip:** DuckDuckGo (no official API), Bing Search API (retired August 2025), Readwise Reader (consumer product, no API), ScrapingBee (HTML-only, expensive for what Argus already does with Playwright).

---

## Search Providers

### Google Programmable Search Engine (Custom Search JSON API)

- **URL:** https://developers.google.com/custom-search
- **Type:** Search provider
- **Pricing:** 100 queries/day free, $5 per 1,000 queries beyond that
- **API:** REST JSON API, official Google client libraries
- **Unique value:** Google's index is the largest in the world. Argus already has Serper (Google SERP wrapper), but having Google's official API as a direct integration provides a free floor of 3,000 searches/month without any API key cost. Serper's pricing is $0.30-1.00/1K, so Google CSE at $5/1K is more expensive at volume but the 100/day free tier is unmatched for light usage. Also supports "Custom Search Engines" scoped to specific sites/domains.
- **Self-hostable:** No (Google cloud service)
- **Recommendation:** **Add.** The free 100 queries/day is a no-brainer as a fallback tier. Implementation is simple REST. Gives Argus a direct Google integration that doesn't depend on a third-party SERP scraper.

### Perplexity Sonar API

- **URL:** https://docs.perplexity.ai/docs/getting-started/pricing
- **Type:** Search + answer synthesis (returns grounded answers, not just links)
- **Pricing:** $5/1K search requests + $1/M input tokens, $1/M output tokens (Sonar base). Sonar Pro: $3/M input, $15/M output. Pro Search (multi-step): additional costs per step.
- **API:** REST API, OpenAI-compatible SDK format
- **Unique value:** Unlike every other provider Argus has, Perplexity returns a *fully synthesized answer* with inline citations, not raw search results. This is useful for a new "grounding" mode where the broker returns a ready-made answer instead of URLs. Average latency ~11 seconds (much slower than raw search). Also offers an Agent API for multi-step research.
- **Self-hostable:** No
- **Recommendation:** **Maybe.** Adds a fundamentally new capability (answer synthesis) but only makes sense if Argus callers sometimes lack their own LLM. If all callers have their own synthesis LLM (which is the typical Argus use case), Perplexity's value is marginal. Could be useful as a premium mode option.

### SerpAPI

- **URL:** https://serpapi.com
- **Type:** Search provider (multi-engine SERP scraper)
- **Pricing:** $75/month for 5,000 searches, scaling to $275/month for 30,000. 250 free/month. Enterprise pricing available.
- **API:** REST JSON API, Python/Node/Ruby/Java clients, LangChain integration
- **Unique value:** Access to 40+ search engines (Google, Bing, YouTube, Amazon, Yelp, Google Maps, etc.) through a single API. 99.9% uptime SLA. Enterprise-grade reliability with up to 100 req/s. Argus already has Serper (Google-only) and Brave (independent index). SerpAPI would add multi-engine breadth (YouTube, Amazon, image search, news-specific, etc.).
- **Self-hostable:** No
- **Recommendation:** **Maybe.** Expensive compared to alternatives (10-50x more than Serper per query), but the multi-engine coverage is unique. Worth adding if users need non-Google search engines (YouTube, Amazon, maps) programmatically. Low priority since Serper covers the main Google use case.

### Kagi Search API

- **URL:** https://help.kagi.com/kagi/api/search.html
- **Type:** Search provider
- **Pricing:** Requires a Kagi subscription ($5/month Starter for 300 searches, $10/month Professional for unlimited + API access). API is included with Professional and Ultimate plans.
- **API:** REST JSON API (documented in Kagi's help center)
- **Unique value:** Kagi has its own index and is widely regarded as producing higher-quality results than Google with zero ads. Supports "Lenses" for focused search (news, academic, technical). API returns standard search result objects (title, url, snippet). The quality advantage is real but the per-search cost at low volumes is high relative to Google CSE or Serper.
- **Self-hostable:** No
- **Recommendation:** **Maybe.** Quality is excellent but the $10/month minimum and the fact that API access requires a paid subscription makes this a niche addition. Could be added as a premium provider for users who already have Kagi subscriptions. Low priority.

### Valyu Search API

- **URL:** https://www.valyu.ai
- **Type:** Search + content extraction (both search and contents/answer APIs)
- **Pricing:** Search API $0.003/result (web), $0.01+/result (proprietary sources like financial data). Contents API $0.001/successful extraction. DeepResearch API $0.10-15/task. 16,000 free requests.
- **API:** REST API, Python SDK, search + content extraction in one platform
- **Unique value:** Benchmarked #1 across 5 domains (FreshQA, SimpleQA, finance, economics, medical) in independent testing against Google, Exa, and Parallel. Has access to proprietary/financial data sources. Combines search and content extraction. The per-result pricing is very competitive at $0.003 for web results.
- **Self-hostable:** No
- **Recommendation:** **Maybe.** Competitive pricing and strong benchmark results make this interesting, but it's a newer provider with less ecosystem integration than Tavily/Exa. The combined search+extraction model mirrors what Argus already does internally. Worth watching.

### You.com Search API

- **URL:** https://you.com/api
- **Type:** Search provider (already exists as stub in Argus)
- **Pricing:** $6.25/1K calls for 1-50 results, $8.00/1K for 51-100 results. $100 in free credits.
- **API:** REST JSON API, OpenAI/Databricks/AWS Marketplace integrations
- **Unique value:** 10B+ page index with 4x better freshness scores than competitors. Citation-backed results. Vertical indexes for News, Healthcare, Legal. SOC 2 Type 2 compliant, zero data retention. Already stubbed in Argus -- completing the implementation would fill the You.com gap.
- **Self-hostable:** No
- **Recommendation:** **Add (complete existing stub).** The stub already exists. You.com offers competitive pricing and unique vertical indexes. Completing this provider is low effort since the interface pattern already exists.

### Firecrawl Search API

- **URL:** https://www.firecrawl.dev
- **Type:** Search + extraction (both in one platform)
- **Pricing:** $83/month for 100K credits (annual). 500 free one-time credits. Search: 2 credits per 10 results. Scrape: 1 credit per page.
- **API:** REST API, Python/Node SDKs, LangChain/LlamaIndex/MCP integrations
- **Unique value:** Only platform that combines search, full content extraction, autonomous agent endpoint, and browser sandbox in one API. Goes from search query to LLM-ready markdown in a single call. 70K+ GitHub stars (open-source core). This is a direct competitor to the search+extraction pipeline that Argus already builds internally.
- **Self-hostable:** Partially (open-source core available, cloud API for managed)
- **Recommendation:** **Maybe.** Feature-rich but the integrated search+extraction model overlaps with Argus's own value proposition. Adding it as a provider would mean Argus can delegate to Firecrawl's pipeline when the caller wants one-call convenience. The autonomous /agent endpoint is genuinely unique. Higher priority than SerpAPI but still depends on whether users need this "all-in-one" mode.

### DuckDuckGo

- **URL:** https://duckduckgo.com
- **Type:** Search provider
- **Pricing:** No official public API. Unofficial scraping via ddgs (Python package) or duckduckgo_search.
- **API:** No official API. Unofficial: `duckduckgo_search` Python package (scrapes DDG HTML), `ddgs` library.
- **Unique value:** Privacy-focused, no tracking. However, no official API means any integration would rely on scraping DDG's HTML, which is fragile and against terms. DDG's search index is also smaller than Google's, making results less comprehensive.
- **Self-hostable:** N/A (no API to self-host)
- **Recommendation:** **Skip.** No official API. Unofficial scraping packages break frequently and violate terms. Argus already has SearXNG which can use DuckDuckGo as a backend engine if users want DDG results.

### Bing Web Search API

- **URL:** https://www.microsoft.com/en-us/bing/apis/bing-web-search-api
- **Type:** Search provider
- **Pricing:** **Retired August 2025.** Microsoft replaced it with "Azure AI Agents - Grounding with Bing Search" at $14-35 per grounded query.
- **API:** Replaced by Azure AI Search grounding
- **Unique value:** None anymore -- the API is dead.
- **Self-hostable:** No
- **Recommendation:** **Skip.** Retired. Replaced by Azure AI Agents grounding at much higher pricing ($14-35/query). Not viable for Argus's pricing model.

---

## Content Extractors

### Crawl4AI

- **URL:** https://github.com/unclecode/crawl4ai
- **Type:** Content extraction (self-hosted)
- **Pricing:** Free and open-source (Apache 2.0). Compute costs only.
- **API:** Python async API, Docker deployment available
- **Unique value:** 50K+ GitHub stars. Purpose-built for RAG and LLM content pipelines. LLM-aware chunking strategies (splits content intelligently for context windows). Async-first architecture for high-throughput concurrent crawling. Configurable noise removal. Unlike trafilatura (which Argus already uses), Crawl4AI has built-in JavaScript rendering, handles SPAs, and includes LLM extraction strategies that work with OpenAI/other providers. Can be self-hosted for zero per-page API costs -- ideal for high-volume or privacy-sensitive workloads where Jina Reader's per-request costs add up.
- **Self-hostable:** Yes (Docker or bare-metal)
- **Recommendation:** **Add.** This is the strongest candidate for a new extractor. Free, self-hostable, purpose-built for exactly Argus's use case (LLM-ready content). Would complement trafilatura as a local extraction option and provide a self-hosted alternative to Jina Reader for users who want zero API costs. LLM-aware chunking is a feature Argus doesn't currently have.

### Firecrawl Extract

- **URL:** https://www.firecrawl.dev
- **Type:** Content extraction (single page and full-site crawl)
- **Pricing:** 1 credit per page. $83/month for 100K credits (annual). 500 free one-time credits.
- **API:** REST API, Python/Node SDKs
- **Unique value:** Best-in-class Markdown output quality (67% fewer tokens than raw HTML). Handles JavaScript-rendered pages, SPAs, and complex sites. Full-site recursive crawling with sitemap support. LLM-powered structured extraction with natural language schema definitions. Sub-1-second response times. This goes beyond what trafilatura can do (better JS rendering) and beyond Jina Reader (structured extraction, crawling).
- **Self-hostable:** Partially (open-source core, but managed API is the primary offering)
- **Recommendation:** **Maybe.** High quality but paid. Argus already has Playwright for JS rendering and Jina Reader for markdown conversion. The value add is structured extraction with schema support and the recursive crawling capability. Worth adding as a premium extraction option for users who want best-quality output and don't mind paying.

### Diffbot Extract API

- **URL:** https://docs.diffbot.com
- **Type:** Content extraction + knowledge graph
- **Pricing:** 10,000 API calls/month free (no credit card). Plus: $299/month. Professional: $999/month.
- **API:** REST API, LangChain integration, OpenAI-compatible LLM API (diffy.chat)
- **Unique value:** AI that automatically classifies web pages into structured entity types (articles, products, people, discussions, events) and returns machine-readable JSON. No prompts required -- Diffbot understands what kind of page it's looking at using computer vision and NLP. Also offers a Knowledge Graph API with 50B+ facts (246M organizations, 1.6B articles, 3M products). The auto-classification is genuinely unique -- no other extractor in Argus's stack can automatically determine that a page is a product page vs an article vs a person profile and extract the appropriate fields.
- **Self-hostable:** No
- **Recommendation:** **Maybe.** The free tier (10K calls/mo) is generous enough to make this worth implementing. The auto-classification capability adds something Argus doesn't have. Enterprise pricing makes it impractical at scale, but for light usage the free tier is excellent. Would be most valuable for users doing knowledge-graph construction or structured data extraction.

### Spider (spider.cloud)

- **URL:** https://spider.cloud
- **Type:** Content extraction + web crawling
- **Pricing:** Free: 200 credits. Basic: $19/month (20K credits). Standard: $49/month (100K credits).
- **API:** REST API, OpenAI-compatible format
- **Unique value:** Extremely fast crawling architecture with clean Markdown output. One of the cheapest per-page options for bulk extraction. Full-site crawling with sitemap support. OpenAI-compatible API format makes integration easy. Fastest page-to-Markdown conversion available per benchmarks.
- **Self-hostable:** No
- **Recommendation:** **Skip.** Overlaps with what Argus already has (Jina Reader for markdown, Playwright for JS rendering). No structured extraction capability. Speed advantage is marginal for Argus's use case since extraction is typically not latency-critical.

### ScrapingBee

- **URL:** https://www.scrapingbee.com
- **Type:** Content extraction (HTML rendering, proxy rotation, CAPTCHA solving)
- **Pricing:** $49/month (150K credits). Free: 1,000 calls.
- **API:** REST API
- **Unique value:** Handles anti-bot measures that simpler extractors can't bypass. Automatic proxy rotation and CAPTCHA solving. JavaScript rendering with Chromium. Returns rendered HTML (not clean Markdown -- you parse it yourself). The anti-bot capability is the main differentiator.
- **Self-hostable:** No
- **Recommendation:** **Skip.** Argus already has Playwright for JS rendering and auth_extractor for cookie-based access. ScrapingBee returns raw HTML (not Markdown), so Argus would need to run trafilatura on the output anyway. The anti-bot/proxy rotation is useful but expensive for what it adds over existing Playwright + trafilatura pipeline. Jina Reader is cheaper and returns clean Markdown directly.

### Olostep

- **URL:** https://www.olostep.com
- **Type:** Content extraction + web crawling + search
- **Pricing:** Custom pricing. Includes free tier with 100 credits.
- **API:** REST API, Python/Node SDKs
- **Unique value:** AI-native API designed for AI agents. Endpoints for /scrapes, /crawls, /searches, /answers, /agents, /parsers, /files, /schedules. Can automate research workflows with natural language prompts. Batch processing with structured extraction. The "agent" endpoint allows no-code automation of multi-step research workflows.
- **Self-hostable:** No
- **Recommendation:** **Skip.** Overlaps heavily with Argus's own value proposition (Argus IS a search broker for AI agents). Adding Olostep as a provider would mean paying for capabilities Argus already implements. The agent/research workflow features are interesting but represent a different product category.

### ScrapeGraphAI

- **URL:** https://scrapegraphai.com
- **Type:** Content extraction + structured data extraction
- **Pricing:** Free: 100 credits. Starter: $19/month (5,000 credits). Growth: $85/month. Pro: $425/month.
- **API:** REST API, Python SDK, LangChain/LangGraph native tools
- **Unique value:** Uses LLMs to extract specific, typed data fields from web pages using natural language prompts. Pydantic schema validation for guaranteed structure. Auto-adapts to website changes (semantic extraction survives redesigns). Markdownify endpoint for clean Markdown (Jina Reader replacement). SmartScraper for structured JSON extraction.
- **Self-hostable:** Partially (open-source Python library available, cloud API for managed)
- **Recommendation:** **Maybe.** The structured extraction with Pydantic schema validation is unique and would add a new capability to Argus (schema-validated extraction). However, it requires an LLM API key to function (additional cost and dependency). Better suited as a user-level tool than a core Argus extractor.

### Readwise Reader

- **URL:** https://readwise.io/read
- **Type:** Content reading/highlighting (consumer product)
- **Pricing:** $7.99/month (consumer subscription)
- **API:** Unofficial/community API only. No official developer API.
- **Unique value:** Excellent read-later service with highlighting and annotation. But it's a consumer product, not a developer API. No programmatic extraction endpoint.
- **Self-hostable:** No
- **Recommendation:** **Skip.** Consumer product with no official API. Not relevant to Argus's architecture.

### Mozilla Readability (via readability-lxml / @mozilla/readability)

- **URL:** https://github.com/mozilla/readability (JavaScript), https://github.com/buriy/python-readability (Python)
- **Type:** Content extraction (library, not API)
- **Pricing:** Free and open-source
- **API:** Python library (readability-lxml), JavaScript library (@mozilla/readability)
- **Unique value:** The algorithm behind Firefox Reader View. Extracts main content from web pages by scoring text density, semantic HTML tags, link ratio penalties, etc. Argus already uses trafilatura which includes readability-inspired heuristics. Adding a dedicated readability pass could improve extraction quality for pages that trafilatura handles poorly.
- **Self-hostable:** Yes (Python package)
- **Recommendation:** **Skip (already covered).** Trafilatura already incorporates readability-style heuristics. Adding a separate readability pass would add marginal improvement at the cost of additional complexity. If extraction quality on specific sites is poor, the better fix is to improve trafilatura's configuration or add Crawl4AI as an alternative local extractor.

---

## Tier List / Priority Matrix

### Tier 1: Add Soon (high impact, low-to-medium effort)

| Provider | Type | Impact | Effort | Why |
|----------|------|--------|--------|-----|
| **Google CSE** | Search | High | Low | 100 free queries/day, Google's index, simple REST API |
| **You.com** (complete stub) | Search | Medium | Low | Stub already exists, $100 free credits, vertical indexes |
| **Crawl4AI** | Extraction | High | Medium | Free, self-hosted, LLM-aware chunking, 50K+ stars |

### Tier 2: Add When Needed (medium impact, medium effort)

| Provider | Type | Impact | Effort | Why |
|----------|------|--------|--------|-----|
| **Perplexity Sonar** | Search+Answer | High | Medium | Adds answer-synthesis mode, but expensive and slow |
| **Firecrawl** | Search+Extraction | High | Medium | Best integrated search+extraction, but overlaps with Argus's core |
| **Diffbot** | Extraction | Medium | Medium | Auto-classification is unique, 10K free calls/mo |
| **Kagi** | Search | Medium | Low | High-quality results, but $10/month minimum subscription |
| **Valyu** | Search+Extraction | Medium | Medium | #1 on benchmarks, competitive pricing, but newer/less proven |

### Tier 3: Skip (low impact or covered by existing tools)

| Provider | Type | Why Skip |
|----------|------|----------|
| DuckDuckGo | Search | No official API, scraping violates terms |
| Bing Web Search | Search | Retired August 2025 |
| SerpAPI | Search | 10-50x more expensive than Serper, only adds multi-engine breadth |
| ScrapingBee | Extraction | HTML-only, expensive, Playwright+trafilatura covers same ground |
| Spider | Extraction | No structured extraction, overlaps with Jina Reader |
| Olostep | Search+Extraction | Overlaps with Argus's own value proposition |
| ScrapeGraphAI | Extraction | Requires LLM API key, better as a user tool than core extractor |
| Readwise Reader | Extraction | Consumer product, no official API |
| Mozilla Readability | Extraction | Already covered by trafilatura |

---

## Sources

### Search providers
- https://o-mega.ai/articles/top-10-ai-search-apis-for-agents-2026 -- Comprehensive pricing/performance comparison of 10 AI search APIs
- https://www.firecrawl.dev/blog/best-web-search-apis -- Firecrawl's comparison of web search APIs for AI
- https://crustdata.com/blog/best-websearch-apis -- 7 best web search APIs for real-time data & AI apps
- https://brightdata.com/blog/web-data/best-research-apis -- Best research APIs in 2026 comparison
- https://composio.dev/content/9-top-ai-search-engine-tools -- 9 top AI search engine tools in 2026
- https://proxies.sx/blog/cheapest-serp-api-comparison-2026 -- Cheapest SERP API pricing comparison
- https://docs.perplexity.ai/docs/getting-started/pricing -- Perplexity API pricing documentation
- https://developers.google.com/custom-search/docs/paid_element -- Google Custom Search pricing
- https://kagi.com/pricing -- Kagi search pricing
- https://help.kagi.com/kagi/api/search.html -- Kagi Search API documentation
- https://serpapi.com/pricing -- SerpAPI pricing
- https://www.valyu.ai/pricing -- Valyu pricing
- https://scrape.do/blog/google-serp-api/ -- Best SERP APIs in 2026 comparison
- https://brave.com/blog/most-powerful-search-api-for-ai/ -- Brave LLM Context API announcement
- https://www.valyu.ai/blogs/benchmarking-search-apis-for-ai-agents -- Valyu benchmarking study

### Content extractors
- https://scrapegraphai.com/blog/jina-alternatives -- 7 best Jina Reader alternatives for AI web scraping in 2026
- https://scrapegraphai.com/blog/firecrawl-alternatives -- 7 best Firecrawl alternatives for AI web scraping in 2026
- https://github.com/unclecode/crawl4ai -- Crawl4AI open-source web crawler for RAG
- https://docs.diffbot.com/reference/introduction-to-diffbot-apis -- Diffbot API documentation
- https://docs.diffbot.com/reference/extract-introduction -- Diffbot Extract API
- https://www.olostep.com/ -- Olostep web data API
- https://www.firecrawl.dev/blog/best-open-source-web-crawler -- Best open-source web crawlers in 2026
- https://prospeo.io/s/firecrawl-alternatives -- Firecrawl alternatives tested & compared
- https://www.digitalapplied.com/blog/ai-web-scraping-tools-firecrawl-guide-2025 -- AI web scraping tools comparison

### Benchmarking and Reddit discussions
- https://www.reddit.com/r/AI_Agents/comments/1rc3nps/ -- Cheapest real-time web search AI API discussion
- https://www.reddit.com/r/LocalLLaMA/comments/1jw4yvq/ -- Best scraper tool discussion (Firecrawl vs alternatives)
- https://medium.com/@unicodeveloper/search-apis-for-ai-agents-we-tested-5-domains-heres-the-gap -- Search API benchmarking across 5,000+ queries
````

## File: docs/research/mcp-search-competitors/gemini-research.md
````markdown
# MCP Web Search Competitive Landscape

**Research date:** 2026-04-09
**Method:** Gemini CLI (3 parallel queries), combined and structured
**Status:** Raw research output -- needs verification against live pricing pages

---

## 1. Executive Summary

The MCP search ecosystem has exploded to **900+ public repositories** on GitHub, but is overwhelmingly dominated by **single-provider wrappers** (95%+). These are MCP servers that connect to exactly one search API (e.g., just Brave, just Tavily), creating vendor lock-in and a "fragmentation tax" where users must configure and manage multiple separate servers.

A small but growing category of **search aggregators/brokers** is emerging. Only two notable multi-provider tools exist: **mcp-omnisearch** (~290 stars, 7 providers) and **one-search-mcp** (~100 stars, 9 providers). Neither implements the broker intelligence that Argus provides (automatic fallback, health tracking, budget enforcement, mode-based routing).

**Argus's position:** The only MCP search server that acts as a true **search broker** -- abstracting multiple APIs behind a single interface with automatic failover, cost-aware routing (free-first tier), cross-provider RRF ranking, health tracking, budget enforcement, and mode-based routing. This is a defensible moat.

**Key pain points in the market:**
- API key fatigue (users tired of signing up for 5+ services)
- Brittle search (hard failures when one API is down or rate-limited)
- Token waste (noisy results consuming context windows)
- Demand for local-first search (no external API keys)
- Demand for synthesized answers (not just links)

---

## 2. Direct MCP Search Servers (Native MCP Tools for Search)

### 2.1 Official Provider MCP Servers

| Server | Provider | GitHub Stars | Free Tier | Paid Pricing | Primary Strength |
|--------|----------|-------------|-----------|-------------|-----------------|
| **tavily-mcp** | Tavily | N/A | 1,000 searches/mo (recurring) | $27/mo (Pro) | AI-optimized RAG; clean Markdown output |
| **exa-mcp-server** | Exa (Metaphor) | N/A | 1,000 searches/mo (recurring) | $50/mo (Starter) | Neural/semantic search; code & academic focus |
| **brave-search-mcp** | Brave | ~860 | $5 credit/mo (~1,000 reqs, recurring) | $5/1k reqs | Independent index; privacy-first; LLM Context endpoint |
| **firecrawl-mcp-server** | Firecrawl | ~6,000 | 500 credits/mo (recurring) | $19/mo (Hobby) | Best-in-class scraping & crawling; URL-to-Markdown |
| **jina-mcp** | Jina AI | N/A | Free (unauthenticated Reader); 10M tokens (one-time Search) | $0.10/1M tokens | Reader (URL-to-Markdown) + Search + Fact-Checker |
| **search-api-mcp** | SearchAPI.io | N/A | 100 searches/mo (recurring) | $40/mo (Dev) | Multi-engine: Google, Bing, YouTube, Maps, Shopping |
| **serper-mcp** | Serper.dev | N/A | 2,500 queries (one-time) | $0.001/search | Cheapest Google Search proxy |
| **Linkup** | Linkup | N/A | EUR 5 credit/mo (~1,000 reqs, recurring) | Varies | Deep vs. standard search modes |

### 2.2 Community / Open-Source MCP Servers

| Server | Provider(s) | Notes |
|--------|------------|-------|
| **duckduckgo-mcp-server** | DuckDuckGo | Free, no API key; uses `ddgs` library |
| **kindly-web-search-mcp-server** | Serper, Tavily, SearXNG | Multi-provider but no ranking/budget logic |
| **web-search-mcp (mrkrsl)** | SearXNG (local) | Locally hosted SearXNG wrapper for local LLMs |
| **perplexity-mcp** | Perplexity | Community wrapper for Perplexity API |
| **g-search-mcp** | Google | Parallel Google search with multiple keywords |
| **mcp-web-search-tool** | Brave (default) | Pluggable, supports multiple providers |
| **free-search-aggregator** | Multiple | Unified aggregator for OpenClaw (similar concept to Argus) |
| **searxng-mcp-server** | SearXNG | Connects agents to self-hosted SearXNG instance |
| **mcp-omnisearch** | 7 providers (Tavily, Brave, Kagi, Exa, etc.) | ~290 stars; unified interface but user must pick provider |
| **one-search-mcp** | 9 providers (incl. Chinese engines) | ~100 stars; includes local browser search via Playwright |

### 2.3 Big Tech Grounding Services (No Free Tier)

| Service | Pricing | Notes |
|---------|---------|-------|
| **Google Vertex AI Grounding** | $35/1k queries | Retired cheap/free search tiers |
| **Azure Bing Search (Grounding)** | $35/1k queries | Standalone Bing API retired Aug 2025 |
| **Perplexity Sonar API** | No free tier | Pro ($20/mo) users get $5/mo API credits |

---

## 3. Free/Freemium Search APIs for AI Agents

### 3.1 Actually Free (Recurring Monthly Credits)

| API | Free Tier | Rate Limit | MCP Server? | Best For |
|-----|-----------|-----------|-------------|----------|
| **Tavily** | 1,000 searches/mo | 1 req/sec | Yes (official) | RAG-optimized agent workflows |
| **Exa** | 1,000 searches/mo | 10 req/min | Yes (official) | Neural/semantic discovery |
| **Brave Search** | ~1,000 reqs/mo ($5 credit) | 1 QPS | Yes (official) | Independent index, privacy |
| **Linkup** | ~1,000 reqs/mo (EUR 5 credit) | Varies | Yes (community) | Deep search |
| **SearchAPI.io** | 100 searches/mo | Low | Yes (official) | Multi-engine SERP |
| **Firecrawl** | 500 credits/mo | 10 RPM | Yes (official) | URL-to-Markdown scraping |
| **DuckDuckGo** | Unlimited (scraping) | Variable | Yes (community) | Free, no key needed |
| **Jina Reader** | Free (unauthenticated) | 20 RPM | Yes (official) | URL-to-Markdown |
| **SearXNG** | Unlimited (self-hosted) | Self-limited | Yes (community) | Privacy, metasearch 70+ engines |

### 3.2 Freemium (Generous One-Time Credits)

| API | One-Time Credits | Post-Exhaustion | MCP Server? |
|-----|-----------------|----------------|-------------|
| **You.com** | $100 credit | Paid plans available | Yes (official) |
| **Serper.dev** | 2,500 queries | $1/1k queries | Yes (community) |
| **Jina Search API** | 10M tokens | Paid plans | Yes (official) |

### 3.3 Paid-Only (No Free Tier)

| API | Pricing | Notes |
|-----|---------|-------|
| **Google Vertex AI Grounding** | $35/1k queries | Formerly Google Custom Search |
| **Azure Bing Search** | $35/1k queries | Standalone API retired Aug 2025 |
| **Perplexity Sonar** | API pricing only | Pro subscribers get $5/mo credit |
| **Kagi** | $5-10/mo | High-quality curated results |

---

## 4. Search Aggregators/Brokers (Multi-Provider)

This is the category where Argus competes. The field is sparse.

### 4.1 mcp-omnisearch

- **Stars:** ~290
- **Providers:** 7 (Tavily, Brave, Kagi, Exa, etc.)
- **Approach:** Unified MCP interface with multiple providers
- **Limitation:** User must manually select which provider to use per request. No automatic routing, no fallback, no ranking.
- **GitHub:** Search "mcp-omnisearch"

### 4.2 one-search-mcp

- **Stars:** ~100
- **Providers:** 9 (includes Chinese engines like Baidu, local browser search via Playwright)
- **Approach:** Multiple providers through one MCP server
- **Limitation:** Includes local browser search (no API key needed) but lacks ranking, health tracking, and budget management.
- **Unique feature:** Can search without any API keys using Playwright browser automation

### 4.3 kindly-web-search-mcp-server

- **Stars:** Low
- **Providers:** Serper, Tavily, SearXNG
- **Approach:** Multi-provider MCP wrapper
- **Limitation:** No ranking, no budget logic, no fallback

### 4.4 free-search-aggregator

- **Stars:** Low
- **Approach:** Unified aggregator built for OpenClaw
- **Limitation:** Similar concept to Argus but simpler implementation

### 4.5 Argus (This Project)

- **Providers:** 9+ (SearXNG, Brave, Serper, Tavily, Exa, SearchAPI, You.com, Jina, plus extraction)
- **Approach:** True search broker with intelligence layer
- **Unique capabilities:**
  - Automatic fallback (provider fails -> next in chain)
  - Health tracking (success rates, cooldown for failing providers)
  - Budget enforcement (cost tracking, spending limits)
  - Mode-based routing (discovery vs. grounding vs. research vs. recovery)
  - Cross-provider RRF ranking
  - Cost-aware tiering (free SearXNG first, paid APIs only when needed)
  - Early stop (cancels subsequent calls if first provider returns sufficient results)
  - Multi-turn session support with query refinement
  - Content extraction (trafilatura -> Jina fallback)
  - URL recovery for dead/moved links
  - Token balance auto-decrement (Jina)

---

## 5. Extraction/Content Tools

These are not search engines but are closely related -- they turn URLs into LLM-consumable text.

| Tool | Type | Free Tier | Pricing | MCP Server? |
|------|------|-----------|---------|-------------|
| **Firecrawl** | Scraping + crawling + search | 500 credits/mo | $19/mo (Hobby) | Yes (official) |
| **Jina Reader** | URL-to-Markdown | Free (unauthenticated) | $0.10/1M tokens | Yes (official) |
| **Jina Search** | Semantic web search | 10M tokens (one-time) | Paid plans | Yes (official) |
| **Tavily** | Search + extract | 1,000/mo | $27/mo | Yes (official) |
| **Bright Data MCP** | Enterprise scraping | Trial | Enterprise pricing | Yes (commercial) |
| **Argus Extractor** | trafilatura + Jina fallback | Depends on Jina balance | Self-managed | Built-in |

**Note:** Firecrawl (~6,000 GitHub stars) is the market leader in this category but is primarily a scraping engine, not a search engine. Jina Reader is the most developer-friendly (free, no auth, just prepend `r.jina.ai/` to any URL).

---

## 6. Gaps and Opportunities in the Market

### 6.1 Problems People Are Complaining About

1. **API Key Fatigue:** Developers must sign up for 5+ services to get reliable search coverage. Strong wish for "unified billing" or a broker that manages keys.

2. **Brittle Search:** Most MCP tools fail hard if an API is down or rate-limited. High demand for "resilience-by-default" (automatic fallbacks).

3. **Token Waste:** Search results are too "noisy," consuming context windows. Users want automatic quality filtering and summarization.

4. **Local-First Search:** Growing demand for web search without any external API keys (driving interest in SearXNG and browser-automation-based search).

5. **Synthesized Answers:** Users want MCP servers that provide synthesized answers directly, not just lists of links.

### 6.2 Where Argus Has Defensible Advantages

| Feature | Single-Provider MCP | Other Aggregators | Argus |
|---------|-------------------|-------------------|-------|
| Multiple providers | No | Yes | Yes |
| Automatic fallback | N/A | No | **Yes** |
| Health tracking | No | No | **Yes** |
| Budget enforcement | No | No | **Yes** |
| Mode-based routing | No | No | **Yes** |
| Cross-provider ranking (RRF) | N/A | No | **Yes** |
| Cost-aware tiering | N/A | No | **Yes** |
| Multi-turn sessions | Varies | No | **Yes** |
| Content extraction | Varies | No | **Yes** |
| URL recovery | No | No | **Yes** |
| CLI + HTTP + MCP + Python | Varies | Varies | **Yes** |

### 6.3 Opportunities

1. **Synthesized answers:** Add an LLM-powered "summarize and answer" mode that returns a synthesized response instead of raw search results.

2. **Unified billing:** Hosted version where Argus manages API keys and charges users per search (removing the key fatigue problem).

3. **Quality gate:** Automatic filtering of low-quality results before they hit the LLM context window.

4. **More extraction providers:** Add Firecrawl as an extraction fallback (currently only trafilatura + Jina).

5. **Browser-based search:** Add Playwright-based local search like one-search-mcp does (search without any API keys).

6. **Observability dashboard:** Web UI showing provider health, costs, search volume, and quality metrics over time.

---

## 7. Sources

### Gemini CLI Queries

1. **Query 1:** "Research the competitive landscape for MCP web search servers..." (competitive matrix, deep dives on individual competitors)
2. **Query 2:** "Find all free or freemium web search APIs that AI agents and LLMs can use in 2025-2026..." (pricing, free tiers, rate limits)
3. **Query 3:** "What is the current state of the MCP ecosystem for search and retrieval tools?..." (ecosystem state, aggregators, complaints)

### Data Sources Referenced by Gemini

- GitHub repository metadata (stars, descriptions)
- Provider pricing pages (Tavily, Exa, Brave, Firecrawl, Jina, Serper, SearchAPI)
- Community discussions (Reddit, GitHub issues, AI agent forums)
- Google/Azure pricing updates (Bing API retirement Aug 2025, Vertex AI Grounding pricing)
- MCP server registries and the modelcontextprotocol GitHub org

### Verification Notes

- Pricing data should be verified against live provider pages (Gemini's training data may be stale)
- GitHub star counts are approximate as of April 2026
- The Bing Web Search API retirement date (Aug 2025) and Vertex AI Grounding pricing ($35/1k) need primary source verification
- MCP server availability should be verified against the official MCP servers list and Smithery/Compass registries

---

*Generated by Gemini CLI research via Claude Code agent. Last updated 2026-04-09.*
````

## File: docs/research/mcp-search-competitors/research.md
````markdown
# MCP Search Competitive Landscape Research

**Date**: 2026-04-09
**Research method**: 6 search queries via Argus (research mode), 5 content extractions
**Focus**: Free/unlimited web search for LLMs via MCP

---

## Executive Summary

The MCP search space is crowded and growing fast. The competitive landscape breaks into five categories:

1. **Single-provider MCP wrappers** -- thin MCP servers that wrap one search API (Brave, Tavily, SearXNG, Exa). These are the most common. They do exactly one thing and require you to manage API keys yourself. Examples: `brave/brave-search-mcp-server` (864 stars), `ihor-sokoliuk/mcp-searxng`, `apappascs/tavily-search-mcp-server`.

2. **Multi-provider MCP aggregators** -- the closest competitors to Argus. These expose multiple search providers through a single MCP interface. Key players: **mcp-omnisearch** (291 stars, 7 providers), **one-search-mcp** (102 stars, 9 providers). Neither has budget enforcement, automatic fallback, or health tracking. Both require the user to supply API keys for every provider.

3. **Search API providers with MCP support** -- commercial search APIs that publish their own MCP servers. Tavily, Brave, Exa, Firecrawl, Linkup, Jina all have official or community MCP servers. These are not brokers -- they sell their own API and wrap it in MCP.

4. **Extraction/content tools** -- Firecrawl, Jina Reader, Crawl4AI. These focus on turning URLs into clean text/Markdown. Not search-first, but often bundled with search in agent workflows.

5. **Enterprise platforms** -- Perplexity, Google Cloud, AWS Bedrock. Enterprise-grade with enterprise pricing. Not relevant to the free/self-hosted tier.

**Argus's unique position**: Argus is the only self-hosted search broker that combines multi-provider routing with automatic fallback, budget enforcement, health tracking, content extraction, and session management in a single package. The closest competitors (mcp-omnisearch, one-search-mcp) are MCP servers that forward requests to whatever providers you have keys for -- they don't optimize routing, enforce budgets, or provide health-aware failover. No other project in this space offers all of these together.

---

## Competitor Analysis

### 1. mcp-omnisearch

- **URL**: https://github.com/spences10/mcp-omnisearch
- **What it does**: MCP server providing unified access to 7 search providers (Tavily, Brave, Kagi, Exa, GitHub, Linkup, Firecrawl) plus AI-powered answers and content extraction. 4 consolidated tools: `web_search`, `ai_search`, `github_search`, `web_extract`.
- **Stars**: 291 | **Forks**: 38 | **Language**: TypeScript
- **Pricing**: Free/open-source (MIT). Requires API keys for each provider you want to use.
- **MCP support**: Native (is an MCP server).
- **Key differentiators**:
  - 7 providers in one MCP interface
  - AI-powered answer tools (Kagi FastGPT, Exa Answer, Linkup)
  - GitHub search integration
  - Content extraction via Firecrawl, Tavily, Kagi
  - Docker deployment support
- **How it compares to Argus**:
  - **No automatic fallback**: User picks the provider per-request. If it fails, they must retry manually.
  - **No budget enforcement**: No per-provider budget tracking or cost limits.
  - **No health tracking**: No cooldown for failing providers, no health dashboards.
  - **No routing policy**: No mode-based routing (discovery/research/grounding).
  - **No content extraction layer**: Relies on Firecrawl/Tavily for extraction rather than having its own trafilatura+Jina hybrid.
  - **No sessions**: No multi-turn query refinement.
  - **Simpler setup**: Single `npx` command, no Python dependencies.
  - **Closer to Argus than anything else in the space**, but Argus adds intelligence layer (routing, health, budgets, extraction, sessions) that mcp-omnisearch lacks.

### 2. one-search-mcp

- **URL**: https://github.com/yokingma/one-search-mcp
- **What it does**: MCP server with web search, scrape, crawl, and content prep. Supports 9 providers: SearXNG, DuckDuckGo, Bing, Tavily, Google, Zhipu, Exa, Bocha, local browser search.
- **Stars**: 102 | **Forks**: 18 | **Language**: TypeScript
- **Pricing**: Free/open-source (MIT). Local browser search requires no API keys. Other providers require keys.
- **MCP support**: Native (is an MCP server).
- **Key differentiators**:
  - Local browser search (DuckDuckGo, Bing, Baidu, Sogou, Google) via agent-browser -- no API keys needed
  - Chinese search engines (Zhipu, Bocha, Baidu, Sogou)
  - Built-in scraping via agent-browser (removed Firecrawl dependency in v1.1.0)
  - Docker image includes Chromium
- **How it compares to Argus**:
  - **No automatic fallback or routing**: User picks one provider via env var at startup.
  - **No budget enforcement or health tracking**.
  - **Browser-based scraping**: Uses Playwright/agent-browser for scraping, which is heavier than Argus's trafilatura approach.
  - **Local search is a real differentiator**: Can work with zero API keys, which Argus cannot do without SearXNG.
  - **No extraction-only endpoint**: Extraction is bundled into the search flow, not a separate tool.
  - **No multi-turn sessions**.

### 3. Brave Search MCP Server (Official)

- **URL**: https://github.com/brave/brave-search-mcp-server
- **What it does**: Official Brave Search MCP server. Web search, local business search, image search, video search, news search, AI summarization.
- **Stars**: 864 | **Forks**: 144 | **Language**: TypeScript
- **Pricing**: Free tier: 2,000 queries/month. Paid: starts at $5/1,000 queries (Data for AI plan) or $3/1,000 queries (Search API plan).
- **MCP support**: Native (is an MCP server). Published on npm as `@brave/brave-search-mcp-server`.
- **Key differentiators**:
  - Official from Brave (864 stars -- most starred MCP search server)
  - Privacy-first independent web index (100M+ users)
  - AI-powered summarization built in
  - Multi-modal search (web, local, image, video, news)
  - Well-documented, Docker support, Claude Desktop integration
- **How it compares to Argus**: Single provider only. Argus wraps Brave as one of 5+ providers. Brave is excellent for what it does, but has no multi-provider routing, no extraction, no sessions, no health tracking.

### 4. Tavily

- **URL**: https://tavily.com
- **What it does**: AI-native search API with built-in extraction, crawling, and research. The dominant player in the "search for AI agents" space. 1M+ developers, 100M+ monthly requests, $25M Series A (Aug 2025).
- **Pricing**:
  - Free: 1,000 credits/month (no credit card)
  - Project: $30/mo for 4,000 credits ($0.0075/credit)
  - Bootstrap: $100/mo for 15,000 credits
  - Startup: $220/mo for 38,000 credits
  - Growth: $500/mo for 100,000 credits ($0.005/credit)
  - Pay-as-you-go: $0.008/credit
  - Search costs: 1 credit (basic) or 2 credits (advanced) per request
  - Extract: 1 credit per 5 URLs (basic) or 2 credits per 5 URLs (advanced)
- **MCP support**: Official Tavily MCP server. Listed on Databricks MCP Marketplace. Partnerships with IBM WatsonX, JetBrains, AWS.
- **Key differentiators**:
  - Fastest on market (180ms p50)
  - Built specifically for AI agents (not repurposed SERP API)
  - Integrated extraction and crawling
  - `/research` endpoint for multi-step agent research
  - SOC 2 certified, enterprise security
  - LangChain, LlamaIndex native integrations
- **How it compares to Argus**: Tavily is a search provider, not a broker. Argus uses Tavily as one of its providers. Tavily is far more polished and scalable, but costs money. Argus provides a free alternative when paired with SearXNG, and adds multi-provider resilience.

### 5. Exa

- **URL**: https://exa.ai
- **What it does**: AI-native semantic search API with embeddings-based retrieval. Neural search mode surfaces results by meaning, not keywords. People/company/code search.
- **Pricing**: Free tier available. Paid plans from $0.001/request.
- **MCP support**: Community MCP servers exist (e.g., `theishangoswami/exa-mcp-server`). No official MCP server found.
- **Key differentiators**:
  - Semantic/neural search (meaning-based, not keyword-based)
  - Dedicated people, company, and code search
  - Query-dependent highlights (50-75% fewer tokens to LLM)
  - Used by Notion, AWS, HubSpot, Monday.com
  - 1B+ web pages indexed
- **How it compares to Argus**: Complementary, not competitive. Exa is a specialized search provider. Argus can wrap Exa as a provider.

### 6. Firecrawl

- **URL**: https://firecrawl.dev
- **What it does**: Web scraping + search + extraction platform for AI. SearchScraper endpoint searches and extracts in one API call. Autonomous `/agent` endpoint. Browser sandbox.
- **Pricing**: 500 free credits (one-time). $16/mo for 3K credits. $83/mo (annual) for 100K credits.
- **MCP support**: Official MCP server. Listed on MCP directories.
- **Key differentiators**:
  - Search + scrape + crawl + extract in one API
  - LLM-ready Markdown/JSON output
  - Browser sandbox and browser automation
  - Autonomous agent endpoint
  - 500K+ developers
- **How it compares to Argus**: Firecrawl is extraction-first with search added on. Argus is search-first with extraction added on. Overlap in extraction capability, but different primary focus. Argus's extraction (trafilatura + Jina) is lighter weight and free, while Firecrawl is more feature-rich but paid.

### 7. Jina AI Reader

- **URL**: https://jina.ai
- **What it does**: URL-to-clean-Markdown converter. Reader API takes any URL and returns clean text optimized for LLM consumption. Also offers search, embeddings, and ranking APIs.
- **Pricing**: Token-based billing. Free tier available. Cost varies with content length (harder to forecast at scale vs. per-page billing).
- **MCP support**: Listed on mcp.so as "MCP server that integrates with Jina AI Search Foundation APIs."
- **Key differentiators**:
  - Excellent at single-page conversion to clean Markdown
  - Token-based pricing (good for many small pages, bad for few large pages)
  - Additional APIs for search, embeddings, ranking
  - Zero monthly commitment on free tier
- **How it compares to Argus**: Jina is Argus's extraction fallback. Argus uses trafilatura (free, local) first, then falls back to Jina. This is a supplier relationship, not competition.

### 8. SearXNG (via MCP adapters)

- **URL**: https://github.com/searxng/searxng
- **What it does**: Privacy-focused metasearch engine. Aggregates results from 70+ search engines (Google, Bing, DuckDuckGo, etc.). Self-hosted.
- **Pricing**: Completely free and open-source. Self-hosted.
- **MCP support**: Multiple community MCP servers: `ihor-sokoliuk/mcp-searxng`, `SecretiveShell/searxng-search`, and bundled in `one-search-mcp` and `mcp-omnisearch`.
- **Key differentiators**:
  - Free, unlimited, no API keys
  - Aggregates 70+ engines
  - Self-hosted (full control, privacy)
  - No vendor lock-in
- **How it compares to Argus**: SearXNG is Argus's primary free provider and the foundation of its "free tier." Argus adds routing intelligence, fallback to paid providers when SearXNG fails, result ranking, and extraction on top of SearXNG.

### 9. Serper.dev

- **URL**: https://serper.dev
- **What it does**: Fast Google SERP API. Returns structured search results in 1-2 seconds. All Google verticals (Search, Images, News, Maps, Places, Videos, Shopping, Scholar, Patents, Autocomplete).
- **Pricing**: Free: 2,500 queries (one-time, no credit card). Paid: $50/250K queries ($0.0002/query) -- extremely cheap.
- **MCP support**: Community MCP servers on mcp.so and mcpservers.org.
- **Key differentiators**:
  - Cheapest per-query Google SERP API
  - Fast (1-2 second response)
  - All Google verticals
  - Structured JSON output
- **How it compares to Argus**: Serper is another provider that Argus wraps. Extremely cheap, which makes it a good budget-conscious option in the Argus provider chain.

### 10. SerpApi

- **URL**: https://serpapi.com
- **What it does**: Google Search API with support for multiple engines. Global IPs, browser cluster, CAPTCHA solving. Advanced location controls.
- **Pricing**: Free: 100 searches/month. Paid from $50/mo.
- **MCP support**: Community MCP servers available.
- **Key differentiators**:
  - CAPTCHA solving included
  - Multiple search engines (Google, Bing, Yahoo, etc.)
  - Advanced location/geo targeting
  - Enterprise infrastructure
- **How it compares to Argus**: Another provider Argus could wrap. More enterprise-oriented than Serper.

### 11. Linkup

- **URL**: https://linkup.so
- **What it does**: Web Search API that works as both SERP API and Web Search API. Built-in LLM connectors (LangChain, LlamaIndex, MCP).
- **Pricing**: Free plan available. Paid plans with pay-as-you-go.
- **MCP support**: Built-in MCP support (native).
- **Key differentiators**:
  - Dual SERP + Web Search API
  - Native MCP support
  - LLM connectors out of the box
- **How it compares to Argus**: Linkup is a single search provider with MCP. Not a broker. Could be wrapped by Argus as a provider.

### 12. Crawleo

- **URL**: https://crawleo.dev
- **What it does**: Combined search + crawl API for AI/RAG pipelines. Claims 5x lower cost than Tavily at scale.
- **Pricing**: 500 free credits/mo. Search + Crawl API with MCP included.
- **MCP support**: Native MCP server included.
- **Key differentiators**:
  - Combined search + crawl in single API
  - Device targeting and geo/language control
  - Claims $100 vs $500 for 100K searches (vs Tavily)
  - Zero data retention
- **How it compares to Argus**: Single-provider API with search+crawl. Not a broker. Could be wrapped by Argus.

---

## Market Categories

### Category 1: Direct MCP Search Servers (Single-Provider Wrappers)

| Project | Provider | Stars | Free Tier | Extraction |
|---------|----------|-------|-----------|------------|
| brave/brave-search-mcp-server | Brave | 864 | 2,000/mo | No (API only) |
| apappascs/tavily-search-mcp-server | Tavily | Low | Via Tavily | No |
| ihor-sokoliuk/mcp-searxng | SearXNG | Low | Unlimited (self-hosted) | No |
| theishangoswami/exa-mcp-server | Exa | Low | Via Exa | No |

### Category 2: Multi-Provider MCP Aggregators (Direct Competitors)

| Project | Providers | Stars | Auto-Fallback | Budget Tracking | Extraction | Sessions |
|---------|-----------|-------|--------------|-----------------|------------|----------|
| **Argus** | 7 (SearXNG, Brave, Tavily, Exa, Serper, SearchAPI, You.com) | N/A | Yes (tier-based) | Yes (per-provider) | Yes (trafilatura+Jina) | Yes (SQLite) |
| mcp-omnisearch | 7 (Tavily, Brave, Kagi, Exa, GitHub, Linkup, Firecrawl) | 291 | No | No | Via Firecrawl/Tavily | No |
| one-search-mcp | 9 (SearXNG, DDG, Bing, Tavily, Google, Zhipu, Exa, Bocha, local) | 102 | No | No | Via agent-browser | No |

### Category 3: Search API Providers with Free Tiers

| Provider | Free Tier | Paid Entry | Search Quality | MCP Support | Best For |
|----------|-----------|------------|----------------|-------------|----------|
| SearXNG | Unlimited (self-hosted) | N/A | Metasearch (70+ engines) | Via adapters | Free, unlimited, privacy |
| Brave | 2,000/mo | $3/1K queries | Independent index, privacy | Official MCP server | Privacy-first search |
| Serper | 2,500 one-time | $50/250K | Google SERP | Community MCP | Cheap Google results |
| Tavily | 1,000/mo | $30/4K credits | AI-optimized, fastest | Official MCP | AI agent search (premium) |
| Exa | Free tier | ~$0.001/req | Semantic/neural | Community MCP | Meaning-based search |
| SerpApi | 100/mo | $50/mo | Google + multi-engine | Community MCP | Enterprise SERP |
| Linkup | Free plan | Pay-as-you-go | SERP + Web Search | Native MCP | Dual-mode search |
| Firecrawl | 500 one-time | $16/mo | AI search + scrape | Official MCP | Search + extraction combo |
| Jina | Free tier | Token-based | N/A (extraction) | Community MCP | URL-to-Markdown |
| Crawleo | 500/mo | Custom | Search + crawl | Native MCP | Budget search + crawl |

### Category 4: Extraction/Content Tools

| Tool | Free Tier | Focus | MCP Support |
|------|-----------|-------|-------------|
| Firecrawl | 500 credits | Scrape + search + extract | Official |
| Jina Reader | Free tier | URL-to-Markdown | Community |
| Crawl4AI | Free (OSS) | AI scraping | Community |
| ScrapeGraphAI | Free (OSS) | Graph-based scraping | Community |
| Tavily Extract | Via credits | Content extraction | Official (bundled) |

### Category 5: Enterprise/Search Platforms

| Platform | Relevance | Notes |
|----------|-----------|-------|
| Perplexity | Low for free tier | $5/1K requests, sub-400ms |
| Google Cloud | Enterprise | Managed MCP servers for Google services |
| AWS Bedrock | Enterprise | Via marketplace integrations |

---

## Argus Competitive Position

### What Makes Argus Unique

1. **Intelligent routing with modes**: Discovery, recovery, grounding, research -- each mode defines a different provider chain. No other MCP search tool does this. mcp-omnisearch and one-search-mcp require the user to pick a provider per-request.

2. **Automatic fallback with health tracking**: If SearXNG fails, Argus falls back to Brave, then Tavily, etc. Failed providers enter cooldown. No competitor does this automatically.

3. **Budget enforcement**: Per-provider budget tracking with automatic disabling when limits are reached. Token balance tracking with auto-decrement for services like Jina. No competitor has this.

4. **Built-in extraction layer**: trafilatura (free, local, fast) with Jina fallback. Not dependent on any single extraction provider. One-search-mcp uses browser-based extraction, mcp-omnisearch delegates to Firecrawl.

5. **Multi-turn sessions**: SQLite-backed session store with query refinement from prior context. No competitor has sessions.

6. **Multiple interfaces**: HTTP API, CLI, MCP server, Python import. Most competitors are MCP-only or HTTP-only.

7. **Search modes for different use cases**: `discovery` for broad exploration, `grounding` for fact-checking, `recovery` for dead URLs, `research` for broad exploratory. This is unique in the space.

8. **URL recovery**: Dedicated endpoint for recovering dead/moved URLs. No competitor offers this.

9. **Expand links**: Discover related pages from a URL. Unique to Argus.

### Where Argus Is Stronger

- **Cost optimization**: Routes cheap providers first (SearXNG is always free, Serper is $0.0002/query), saving expensive providers for when they're needed
- **Resilience**: Health tracking + automatic fallback means searches almost never fail
- **Budget control**: Prevents runaway costs on paid providers
- **Self-hosted**: No data leaves your infrastructure (SearXNG + trafilatura path)
- **Comprehensiveness**: Search + extract + recover + expand in one package

### Where Competitors Are Stronger

- **Ease of setup**: `npx one-search-mcp` or `npx @brave/brave-search-mcp-server` is simpler than `pip install argus-search[mcp]` + configuring providers
- **Local search without backend**: one-search-mcp can do browser-based search with zero infrastructure. Argus needs SearXNG for free search.
- **Brand recognition**: Brave (864 stars), Tavily (1M+ developers), Firecrawl (500K+ developers) have huge communities. Argus is unknown.
- **AI-powered answers**: mcp-omnisearch offers Kagi FastGPT and Exa Answer for AI-synthesized responses. Argus returns raw results.
- **GitHub integration**: mcp-omnisearch has GitHub search. Argus does not.
- **Documentation and polish**: Tavily, Brave, Firecrawl have professional documentation, SDKs, and enterprise support. Argus has project docs.
- **Chinese search engines**: one-search-mcp supports Zhipu, Bocha, Baidu, Sogou. Argus does not.
- **MCP Marketplace presence**: Tavily is on Databricks MCP Marketplace. Brave is on npm. Argus is on PyPI but not in MCP marketplaces.

---

## Sources

- [KDnuggets - 7 Free Web Search APIs for AI Agents](https://www.kdnuggets.com/7-free-web-search-apis-for-ai-agents)
- [FastMCP - Best Free MCP Servers in 2026](https://fastmcp.me/blog/best-free-mcp-servers)
- [O-mega.ai - Top 10 AI Search APIs for Agents 2026](https://o-mega.ai/articles/top-10-ai-search-apis-for-agents-2026)
- [Firecrawl - Best Web Search APIs for AI Applications in 2026](https://www.firecrawl.dev/blog/best-web-search-apis)
- [Bright Data - Best SERP and Web Search APIs of 2026](https://brightdata.com/blog/web-data/best-serp-apis)
- [ScrapingBee - 9 Best Web Search APIs for AI Agents](https://www.scrapingbee.com/blog/best-ai-search-api/)
- [Linkup - Best SERP APIs & Web Search APIs in 2025](https://www.linkup.so/blog/best-serp-apis-web-search)
- [Crawleo vs Firecrawl vs Tavily](https://www.crawleo.dev/compare-search)
- [GitHub - brave/brave-search-mcp-server](https://github.com/brave/brave-search-mcp-server)
- [GitHub - spences10/mcp-omnisearch](https://github.com/spences10/mcp-omnisearch)
- [GitHub - yokingma/one-search-mcp](https://github.com/yokingma/one-search-mcp)
- [GitHub - apappascs/tavily-search-mcp-server](https://github.com/apappascs/tavily-search-mcp-server)
- [GitHub - ihor-sokoliuk/mcp-searxng](https://github.com/ihor-sokoliuk/mcp-searxng)
- [GitHub - wong2/awesome-mcp-servers](https://github.com/wong2/awesome-mcp-servers)
- [Tavily Credits & Pricing](https://docs.tavily.com/documentation/api-credits)
- [Tavily Pricing Page](https://www.tavily.com/pricing)
- [Brave Search API Comparison](https://brave.com/search/api/guides/what-sets-brave-search-api-apart/)
- [Exa vs Tavily Comparison](https://exa.ai/versus/tavily)
- [Firecrawl vs Jina AI](https://www.firecrawl.dev/alternatives/firecrawl-vs-jina-ai)
- [Jina AI vs Firecrawl (Apify)](https://blog.apify.com/jina-ai-vs-firecrawl/)
- [Composio - 9 Top AI Search Engine Tools](https://composio.dev/content/9-top-ai-search-engine-tools)
- [Crustdata - 7 Best Web Search APIs](https://crustdata.com/blog/best-websearch-apis)
- [AIMultiple - Agentic Search in 2026](https://aimultiple.com/agentic-search)
- [mcp.so - MCP Server Directory](https://mcp.so/)
- [MCP Registry (Official)](https://registry.modelcontextprotocol.io/)
- [Reddit r/mcp - Free internet search providers](https://www.reddit.com/r/mcp/comments/1mwkfy1/what_internet_search_providers_are_you_using_that/)
- [Skywork AI - OneSearch MCP Deep Dive](https://skywork.ai/skypage/en/one-search-mcp-server-ai-agents/1977613317349371904)
````

## File: docs/research/competitive-analysis.md
````markdown
# Argus Competitive Analysis & Go-to-Market Research
> Generated: 2026-03-31
> Source: Gemini CLI deep research

## Executive Summary

Argus occupies a specific, high-growth niche for 2026: **Search Infrastructure for Autonomous Agents**. There is no "LiteLLM for Search APIs" that provides a unified, budget-aware, health-monitored gateway. Argus fills the role of **Search Infrastructure Middleware** -- the missing layer between AI agents and the fragmented search API market.

The project is well-timed for the "Agentic Infrastructure" wave. The code is professional, the niche is specific, and the "Cost-Savings + Reliability" angle is an easy sell. The verdict: **pursue publicly**.

---

## 1. Competitive Landscape

| Tool | Approach | Target Audience | Pricing | Pros/Cons vs. Argus |
| :--- | :--- | :--- | :--- | :--- |
| **SearXNG** | Self-hosted Metasearch | Privacy users / Local AI | Free (OSS) | **Pro:** 70+ engines, mature project. **Con:** High maintenance, hard to enforce API-specific budgets, no RRF ranking, not designed as a programmatic API for agents. |
| **Perplexica** | AI Search UI (Perplexity Clone) | End-users | Free (OSS) | **Pro:** Beautiful UI, built-in LLM integration. **Con:** Focused on UX, not a backend broker for other agents. No multi-provider fallback at the API level. |
| **Kagi** | Premium Search Engine | Power users | Paid ($5-10/mo) | **Pro:** Excellent quality, built-in AI summarization. **Con:** Proprietary, not a broker, no API for agents, no fallback logic. |
| **LiteLLM** | LLM Proxy | AI Developers | Free (OSS) | **Pro:** Great at LLM fallback, similar architectural pattern. **Con:** Search is a secondary "tool," not a first-class citizen. No budget enforcement per search provider. |
| **Tavily** | Semantic Search API | AI Developers | Paid (SaaS) | **Pro:** High quality, purpose-built for AI agents. **Con:** Vendor lock-in, no automatic fallback to cheaper engines, single point of failure. |
| **Exa** | Neural Search API | AI Developers | Paid (SaaS) | **Pro:** Semantic understanding, great for research. **Con:** Expensive ($5-10/1k requests), no fallback, vendor lock-in. |
| **duckduckgo-search (DDGS)** | Lightweight Library | Scrapers / Hobbyists | Free (OSS) | **Pro:** Simple, no API key needed. **Con:** Single provider, no health/budget logic, rate-limited, low quality for agent use. |
| **searpy** | Search Library | Scrapers | Free (OSS) | **Pro:** Simple wrapper. **Con:** Single provider, no fallback, no ranking, minimal features. |
| **google-search-python** | Search Library | Scrapers | Free (OSS) | **Pro:** Google results directly. **Con:** Single provider, scraping risks, no fallback or budget logic. |

**The Gap:** No existing tool provides unified search API brokering with automatic fallback, RRF ranking, budget enforcement, and health tracking. The closest analog is LiteLLM for LLMs, but nothing equivalent exists for search.

---

## 2. Unique Value Proposition

Argus is genuinely different because it treats **Search as a Reliability Problem**, not just a data problem.

### Cost-Reliability Arbitrage (Strongest Hook)
Users can set Serper ($1/1k) as the primary and Exa/Tavily ($5-10/1k) as the "High-Quality Fallback." This saves ~80% on costs while maintaining 100% uptime. Most agent frameworks (LangChain, CrewAI) will accidentally drain your wallet if an agent loops -- Argus's budget enforcement at the *broker* level is a critical safety feature that SaaS providers don't offer (they want you to spend).

### MCP-Native Integration
In 2026, agents don't "import libraries"; they "connect to servers." Having a built-in MCP server makes Argus a one-click install for Claude, Copilot, and custom agentic loops. This is a genuine differentiator -- most search tools expect you to write Python code, not connect an MCP client.

### Budget Enforcement as Governance
Budget enforcement at the broker level prevents cost runaway from agent loops, a real production concern. SaaS search providers have no incentive to help you spend less. Argus puts the developer in control.

### RRF vs. Raw Ranking
Most aggregators just concatenate results. RRF is a sophisticated way to ensure that if *both* Brave and SearXNG find a link, it's boosted, leading to much higher signal-to-noise for LLM context. This is technically novel in the search aggregation space.

### Health Tracking
Real-time health monitoring of providers enables automatic degradation rather than hard failures. For production systems, this is the difference between "search got a bad result" and "the entire agent pipeline crashed."

**What makes someone choose Argus over one provider directly?** When a single API failure (429 or 500) would kill the product. Argus is the "Search Insurance" layer.

---

## 3. Market Positioning

### The Real Use Case: Production-grade AI Agents

| Persona | Need Level | Why Argus |
|---------|-----------|-----------|
| AI/LLM developers building agents | **Need-to-have** | Fallback prevents agent failure; RRF improves LLM context quality; MCP is native protocol |
| Teams wanting search redundancy | **Need-to-have** | Multi-provider fallback with health tracking; budget enforcement per team/project |
| Privacy-conscious users | Nice-to-have | SearXNG as default floor keeps queries off proprietary networks |
| Cost-conscious users | **Need-to-have** | Cheapest-first routing with fallback to premium only when needed |

### The Target Persona
**"Reliability Engineer for AI"** -- someone building a system that cannot fail, needs to track costs by department (budgets), and needs to ensure the LLM gets the most relevant context (RRF).

### Nice-to-have vs. Need-to-have
For a hobbyist, Argus is a nice-to-have. For a startup spending $500+/mo on Search APIs, the budget enforcement and fallback logic make it a **need-to-have**. The project should target the latter.

---

## 4. Similar Open-Source Projects

### Direct Competitors / Peers

| Project | Description | Gap Argus Fills |
|---------|-------------|-----------------|
| **Swirl Search** | Enterprise search aggregator (Jira, Slack, etc.) | Too heavy for lightweight AI agents; focused on internal enterprise search, not web search APIs |
| **gitmcp** | MCP server for GitHub search | Single-scope; no multi-provider fallback or ranking |
| **ddgs-metasearch** | DuckDuckGo metasearch | Single provider, no health/budget logic; hobbyist-grade |
| **AgentGov** | Growing project for LLM governance | Argus could be positioned as the "Search Extension" for governance-focused stacks |
| **Browser-use** | Browser automation for agents | Different scope (full browser vs. API), but overlapping audience |

### Peer Benchmarks
- ddgs-metasearch and similar tools: 2k-5k GitHub stars
- Argus can surpass them by being more "enterprise-ready" (health tracking, professional logging, budget enforcement)

### What Gaps Exist
1. No open-source search broker with RRF ranking
2. No MCP-native search server with multi-provider support
3. No search tool with built-in budget governance
4. No search aggregation tool designed specifically for AI agent pipelines (not human UI)

---

## 5. Publicization Strategy

Since you have no audience, you must **leverage existing registries and "Vibe Coding" trends** rather than trying to build an audience from scratch.

### Phase 1: The "Agent-Ready" Foundation (Week 1)

**Actions:**
- Add an `llms.txt` and `llms-full.txt` -- this is the 2026 standard for allowing other AI agents to "read" your repo
- Submit Argus to the **Official MCP Server Registry** and `mcp-get` -- this is where 2026 traffic is
- Ensure the README has clear install-and-use-in-30-seconds instructions
- Add a "One-Click Deploy to Railway" button to lower friction to zero

### Phase 2: The "Hacker News" Hook (Week 2)

**Do NOT post:** "I made a search broker."

**DO post:** *"How I cut my agent's search costs by 70% using a Python broker with RRF ranking."*

Focus on the **arbitrage** (Serper + Exa fallback). HN loves cost-optimization and technical "cleverness" like RRF. Write one high-quality technical blog post about RRF for Search Aggregation and let it live as evergreen content.

### Phase 3: The "Developer Aggregator" (Ongoing)

**Reddit:**
- Post in `/r/LocalLLaMA` and `/r/ClaudeAI`
- Position Argus as the way to give local models "infinite knowledge" without the privacy leaks of a single SaaS
- Title examples: "I built a search broker so my local LLM can use 5 search APIs with automatic fallback"

**GitHub:**
- Add topics/tags: `mcp-server`, `search-api`, `ai-agents`, `llm-tools`, `search-broker`
- Target GitHub Trends via one-click deploy options

### What NOT to Waste Time On

| Don't Do | Why |
|----------|-----|
| **Build a UI** | You are a backend tool. A UI is a distraction. |
| **X/Twitter** | Without a following, you'll scream into a void. |
| **"Weekly Updates"** | Write one high-quality technical post, not weekly noise. |
| **Build a website** | README + GitHub Pages is enough. |
| **Chase press coverage** | They won't cover a 0-star repo. Let users come to you. |

### Realistic Expectations for a Solo Developer

- **Month 1:** 50-200 stars (if HN post lands)
- **Month 3:** 500-1,000 stars (if MCP registry drives traffic)
- **Month 6:** 1,000-3,000 stars (if Reddit posts resonate)
- **Key metric:** Not stars, but active users via MCP registry installs
- **Realistic ceiling:** 5,000-10,000 stars within a year if the agent infrastructure wave continues

### The One Thing That Matters
Make it trivially easy for an AI agent to use Argus via MCP. That's the distribution channel. If Claude, Copilot, and local LLM frameworks can discover and connect to Argus in 30 seconds, you win.

---

## Resources & Links

### Competitors Referenced
- SearXNG: https://github.com/searxng/searxng
- Perplexica: https://github.com/ItzCraKzy/Perplexica
- LiteLLM: https://github.com/BerriAI/litellm
- duckduckgo-search: https://github.com/deedy5/duckduckgo-search
- Swirl Search: https://github.com/swirlai/swirl-search

### Distribution Channels
- MCP Server Registry: https://modelcontextprotocol.io/registry
- mcp-get: https://github.com/punkpeye/mcp-get
- llms.txt standard: https://llmstxt.org/

### Key Subreddits
- r/LocalLLaMA
- r/ClaudeAI
- r/ChatGPTCoding

### Recommended Post Angles
1. "How I cut my agent's search costs by 70% with RRF ranking"
2. "A search broker that treats search as a reliability problem"
3. "The missing middleware between AI agents and search APIs"
````

## File: docs/research/mcp-search-landscape.md
````markdown
# MCP Search Tool Landscape Research

**Date**: 2026-03-31
**Purpose**: Understand the competitive landscape for Argus -- a multi-provider search broker with MCP, HTTP, CLI, and Python interfaces.

---

## 1. MCP Search Servers -- What Exists

The `mcp-server` topic on GitHub has **903 public repositories** matching "search" (as of March 2026). Web search is one of the hottest MCP categories. Here are the key players:

### Single-Provider MCP Search Servers (Most Common Pattern)

| Repo | Stars | Providers | Language | Notes |
|------|-------|-----------|----------|-------|
| [firecrawl/firecrawl-mcp-server](https://github.com/firecrawl/firecrawl-mcp-server) | 5,921 | Firecrawl only | JavaScript | Official Firecrawl. Scraping + search combined. Most starred MCP search server. |
| [exa-labs/exa-mcp-server](https://github.com/exa-labs/exa-mcp-server) | 4,127 | Exa only | TypeScript | Official Exa. Hosted MCP endpoint. Very polished README with Claude Skills integration. |
| [nickclyde/duckduckgo-mcp-server](https://github.com/nickclyde/duckduckgo-mcp-server) | 938 | DuckDuckGo only | Python | Simple, free. No API key needed. |
| [mrkrsl/web-search-mcp](https://github.com/mrkrsl/web-search-mcp) | 695 | Local SearXNG | TypeScript | Locally hosted, for local LLMs. |
| [jsonallen/perplexity-mcp](https://github.com/jsonallen/perplexity-mcp) | 288 | Perplexity only | Python | Wraps Perplexity API. |
| [yoshiko-pg/o3-search-mcp](https://github.com/yoshiko-pg/o3-search-mcp) | 286 | OpenAI o3 only | JavaScript | Single-purpose. |

### Multi-Provider MCP Search Servers (Argus's Category)

| Repo | Stars | Providers | Language | Notes |
|------|-------|-----------|----------|-------|
| [Shelpuk-AI-Technology-Consulting/kindly-web-search-mcp-server](https://github.com/Shelpuk-AI-Technology-Consulting/kindly-web-search-mcp-server) | 254 | Serper, Tavily, SearXNG | Python | Closest to Argus. Python-based. Content retrieval focus. No ranking, no budget, no fallback logic. |
| [199-biotechnologies/search-cli](https://github.com/199-biotechnologies/search-cli) | 9 | Brave, Serper, Exa, Jina, Firecrawl, Perplexity, xAI | Rust | CLI only, no MCP server. Multi-provider but no broker logic. |
| [VulcanusALex/free-search-aggregator](https://github.com/VulcanusALex/free-search-aggregator) | 0 | Brave, Tavily, DuckDuckGo, Serper, SearchAPI | Python | "Unified web search with automatic multi-provider failover for OpenClaw". Nearly identical concept to Argus but 0 stars. |

**Key insight**: There is no well-starred MCP search server that does multi-provider aggregation with fallback, ranking, and budget management. The category exists but is almost empty. The closest is `kindly-web-search-mcp-server` at 254 stars, which is a thin wrapper with no broker logic.

---

## 2. Search Aggregation as a Concept -- Does It Have Traction?

### The Infrastructure Players (Non-MCP)

| Project | Stars | Role | Notes |
|---------|-------|------|-------|
| [searxng/searxng](https://github.com/searxng/searxng) | 27,510 | Metasearch engine | The reference for multi-provider search aggregation. Python, self-hosted, privacy-focused. |
| [deedy5/ddgs](https://github.com/deedy5/ddgs) | 2,382 | Metasearch library | Python library aggregating results from diverse web search services. |
| [ItzCrazyKns/Perplexica](https://github.com/ItzCrazyKns/Perplexica) | ~20,000+ | AI-powered answering engine | Open-source Perplexity alternative. Uses SearXNG internally. Bundles its own SearXNG instance. |

### Demand Signals

1. **SearXNG at 27.5k stars** proves there is strong demand for multi-provider search aggregation, primarily from the privacy/self-hosting community.
2. **Perplexica at ~20k+ stars** shows demand for AI-powered search that combines multiple providers.
3. **903 MCP search repos** on GitHub shows the MCP search category is exploding -- but almost all are single-provider wrappers.
4. **The "kindly" MCP server (254 stars)** and the `free-search-aggregator` (0 stars) show that multi-provider MCP search has been attempted but hasn't gained traction yet.
5. **Exa's MCP server README** devotes enormous effort to integration guides (Cursor, VS Code, Claude Code, Codex, Windsurf, Zed, Gemini CLI, v0, Warp, Kiro, Roo Code) -- showing how hungry the market is for search MCP integration.

---

## 3. Developer Communities That Care About This

### Community 1: MCP/AI Agent Builders
- Building tools for Claude Code, Cursor, Codex, Windsurf, Copilot
- Need search for grounding, fact-checking, web browsing
- Currently: configure 3-5 separate MCP servers, one per provider
- Pain: no single search MCP that works across providers

### Community 2: Self-Hosted / Privacy-Conscious
- SearXNG ecosystem (27.5k stars, Matrix channel, active development)
- Want to avoid vendor lock-in on search
- Currently: run SearXNG, maybe add Tavily API
- Pain: SearXNG is a web app, not an API/SDK/MCP server

### Community 3: AI Application Developers
- Building RAG pipelines, research agents, autonomous tools
- Need reliable, ranked search results
- Currently: hardcode one search provider, add fallback manually
- Pain: no off-the-shelf search broker with ranking and budget management

### Community 4: Local LLM / Homelab Users
- Running Ollama, LM Studio, local LLMs
- Need web search to ground local models
- Currently: use Perplexica (self-hosted) or duckduckgo-mcp-server
- Pain: free options are limited; paid APIs require per-provider setup

---

## 4. Evidence of Demand for Multi-Provider Search

### Explicit Demand
- Multiple 0-star repos attempting multi-provider search aggregation exist (free-search-aggregator, search-aggregation-service) -- shows people are building this for themselves
- The kindly-web-search-mcp-server explicitly lists "Supports Serper, Tavily, and SearXNG" as a key feature
- Perplexica's README mentions "Support for Tavily and Exa coming soon" -- even a 20k-star project sees multi-provider as a roadmap item

### Implicit Demand
- Firecrawl (5.9k stars), Exa (4.1k stars), DuckDuckGo MCP (938 stars) -- users are setting up multiple single-provider MCP servers to get coverage
- ddgs library (2.4k stars) -- developers want a unified interface to multiple search backends
- SearXNG's architecture (aggregating 100+ engines) proves the value of not relying on a single provider

### What's Missing
- **No MCP search server does all of**: multi-provider routing, automatic fallback, result ranking/deduplication, budget enforcement, health tracking
- **No search aggregation library** handles provider degradation gracefully (Argus's core value prop)
- **No tool** bridges the gap between free/self-hosted search (SearXNG) and paid APIs (Brave, Serper, Tavily, Exa) with intelligent routing

---

## 5. Competitive Positioning for Argus

### Argus vs. Single-Provider MCP Servers
Argus doesn't compete with Firecrawl or Exa MCP directly -- it uses them as providers. Argus sits above them as a broker.

### Argus vs. Kindly Web Search MCP
Closest competitor. Argus differentiates with:
- 5 providers vs 3
- Automatic fallback and degradation (not just failover)
- RRF result ranking and deduplication
- Budget enforcement per provider
- Health tracking
- Multiple search modes (discovery, recovery, grounding, research)
- Python SDK + HTTP API + CLI + MCP (not just MCP)

### Argus vs. SearXNG
SearXNG is a metasearch engine for humans. Argus is a search broker for AI agents. Different layer. Argus can use SearXNG as a provider.

### Argus vs. Perplexica
Perplexica is an end-user application (UI + AI answering). Argus is infrastructure (API + broker). Complementary, not competing.

### Argus's Unique Position
**The only search broker that treats search like an infrastructure problem**: multiple providers, automatic failover, budget control, health monitoring, ranking -- all behind one endpoint. Purpose-built for AI agents and developer tools.

---

## 6. Go-to-Market Observations

Note: Web search tools were rate-limited during research; the following is based on GitHub activity patterns and project signals observed.

### What Works for Dev Tools
1. **MCP Registry integration** -- GitHub now has an MCP Registry (visible in their nav). This is a distribution channel.
2. **"Install in Cursor/VsCode/Claude Code" README** -- Exa's server shows this pattern works. Each integration guide is a discovery path.
3. **Stars from tutorials/blog posts** -- The high-star MCP search servers got traction from being featured in "best MCP servers" lists.
4. **Python + MIT license** -- The sweet spot for developer adoption. Most search MCP servers use this combo.

### Risks
1. **The space is new and crowded at the bottom** -- 903 MCP search repos, most with <50 stars. Noise-to-signal ratio is terrible.
2. **Provider companies may ship their own aggregators** -- Exa, Tavily, Brave could theoretically bundle multi-provider support.
3. **SearXNG could add MCP** -- Would instantly make Argus's SearXNG integration redundant (though Argus still adds broker logic).
4. **Claude/OpenAI may build this in** -- Native web search in AI tools could reduce demand for external search MCP servers.

---

## 7. Key Metrics Summary

| Metric | Value |
|--------|-------|
| MCP search repos on GitHub | 903 |
| Highest-star MCP search server | Firecrawl (5,921) |
| Multi-provider MCP search servers | ~3 (all <300 stars) |
| SearXNG stars | 27,510 |
| ddgs metasearch library stars | 2,382 |
| Perplexica estimated stars | ~20,000+ |
| MCP search servers that do ranking | 0 |
| MCP search servers with budget enforcement | 0 |
| MCP search servers with health tracking | 0 |

---

## Sources

- [GitHub MCP server topic (search)](https://github.com/topics/mcp-server?q=search&sort=stars)
- [exa-labs/exa-mcp-server](https://github.com/exa-labs/exa-mcp-server)
- [firecrawl/firecrawl-mcp-server](https://github.com/firecrawl/firecrawl-mcp-server)
- [searxng/searxng](https://github.com/searxng/searxng)
- [ItzCrazyKns/Perplexica](https://github.com/ItzCrazyKns/Perplexica)
- [deedy5/ddgs](https://github.com/deedy5/ddgs)
- [nickclyde/duckduckgo-mcp-server](https://github.com/nickclyde/duckduckgo-mcp-server)
- [Shelpuk-AI-Technology-Consulting/kindly-web-search-mcp-server](https://github.com/Shelpuk-AI-Technology-Consulting/kindly-web-search-mcp-server)
- [Model Context Protocol docs](https://modelcontextprotocol.io/introduction)
````

## File: tests/test_api.py
````python
class TestSchemas
⋮----
def test_search_request_valid(self)
⋮----
req = SearchRequest(query="test", mode="discovery", max_results=10)
⋮----
def test_search_request_invalid_mode(self)
def test_search_request_min_length(self)
def test_search_result_schema(self)
⋮----
r = SearchResultSchema(url="https://example.com", title="Test", snippet="A page")
⋮----
def test_recover_url_request(self)
⋮----
req = RecoverUrlRequest(url="https://example.com")
⋮----
def test_expand_request(self)
⋮----
req = ExpandRequest(query="python", context="web framework")
⋮----
def test_test_provider_request(self)
⋮----
req = ProviderTestRequest(provider="searxng")
⋮----
class TestSearchEndpoint
⋮----
@pytest.mark.asyncio
    async def test_search_returns_results(self)
⋮----
mock_broker = MagicMock()
cached_resp = SearchResponse(
⋮----
client = TestClient(create_app(broker=mock_broker))
resp = client.post("/api/search", json={"query": "test", "mode": "discovery"})
⋮----
data = resp.json()
⋮----
@pytest.mark.asyncio
    async def test_search_invalid_mode_returns_400(self)
⋮----
client = TestClient(create_app())
resp = client.post("/api/search", json={"query": "test", "mode": "invalid_mode"})
⋮----
@pytest.mark.asyncio
    async def test_recover_url_endpoint(self)
⋮----
resp = client.post("/api/recover-url", json={"url": "https://dead.com", "title": "Page Title"})
⋮----
@pytest.mark.asyncio
    async def test_expand_endpoint(self)
⋮----
resp = client.post("/api/expand", json={"query": "python", "context": "web framework"})
⋮----
def test_create_app_uses_lazy_singleton_broker_factory(self)
⋮----
broker = MagicMock()
broker_factory = MagicMock(return_value=broker)
app = create_app(broker_factory=broker_factory)
⋮----
class TestHealthEndpoint
⋮----
@pytest.mark.asyncio
    async def test_health_endpoint(self)
⋮----
resp = client.get("/api/health")
⋮----
class TestRequestCorrelation
⋮----
@pytest.mark.asyncio
    async def test_x_request_id_header(self)
⋮----
resp = client.get("/api/health", headers={"x-request-id": "test-123"})
⋮----
@pytest.mark.asyncio
    async def test_auto_generated_request_id(self)
class TestRateLimitComposition
⋮----
def test_rate_limit_headers_and_enforcement(self)
⋮----
limiter = RateLimiter(max_requests=1, window_seconds=60, exempt_paths=[], api_key="")
client = TestClient(create_app(broker=mock_broker, rate_limiter=limiter))
first = client.post("/api/search", json={"query": "test", "mode": "discovery"})
second = client.post("/api/search", json={"query": "test", "mode": "discovery"})
````

## File: tests/test_quality_gate.py
````python
class TestQualityGate
⋮----
def setup_method(self)
def test_q1_short_content_fails(self)
⋮----
text = " ".join(["word"] * 50)
result = self.gate.evaluate(text, "https://example.com/article")
⋮----
def test_q2_preview_patterns_detected(self)
⋮----
text = (
⋮----
def test_q3_single_pattern_passes(self)
⋮----
result = self.gate.evaluate(text, "https://example.com/blog/article")
⋮----
def test_q4_high_risk_short_fails(self)
⋮----
text = " ".join(["word"] * 200)
result = self.gate.evaluate(text, "https://www.nytimes.com/article")
⋮----
def test_q5_high_risk_long_passes(self)
⋮----
text = " ".join(["word"] * 2000)
⋮----
def test_q6_archive_grace(self)
⋮----
text = " ".join(["word"] * 60)
result = self.gate.evaluate(
⋮----
def test_q7_transcript_threshold(self)
⋮----
text = " ".join(["word"] * 300)
result = self.gate.evaluate(text, "https://example.com/podcast", content_type="transcript")
⋮----
def test_q8_soft_404_rejected(self)
⋮----
text = "Sorry, we couldn't find that page. It may have been moved or deleted."
result = self.gate.evaluate(text, "https://example.com/missing")
⋮----
def test_q9_note_exempt(self)
⋮----
text = " ".join(["word"] * 5)
result = self.gate.evaluate(text, "https://example.com/note/abc", content_type="note")
⋮----
def test_q10_normal_article_passes(self)
⋮----
text = " ".join(["word"] * 500)
⋮----
def test_quick_check_fast_reject(self)
⋮----
text = " ".join(["word"] * 20)
⋮----
def test_quick_check_fast_accept(self)
class TestSoft404
⋮----
def test_real_soft_404(self)
⋮----
text = "Page not found. Sorry, we couldn't find that page."
⋮----
def test_short_text_is_soft_404(self)
def test_good_content_not_soft_404(self)
⋮----
"""Normal article content is not a soft 404."""
text = " ".join(["word"] * 200) + " This is a real article about technology."
⋮----
def test_soft_404_check_tuple(self)
def test_expired_content_detected(self)
⋮----
text = "This content is no longer available. " + " ".join(["word"] * 50)
⋮----
class TestSSRF
⋮----
def test_s1_private_ip(self)
def test_s2_loopback(self)
def test_s3_internal_hostname(self)
def test_s4_link_local(self)
def test_s5_valid_https(self)
def test_s6_valid_http(self)
⋮----
"""S6: Valid HTTP passes."""
⋮----
def test_non_http_blocked(self)
def test_no_hostname_blocked(self)
````

## File: CODE_OF_CONDUCT.md
````markdown
# Contributor Covenant Code of Conduct

## Our Pledge

We as members, contributors, and leaders pledge to make participation in our
community a harassment-free experience for everyone, regardless of age, body
size, visible or invisible disability, ethnicity, sex characteristics, gender
identity and expression, level of experience, education, socio-economic status,
nationality, personal appearance, race, caste, color, religion, or sexual
identity and orientation.

We pledge to act and interact in ways that contribute to an open, welcoming,
diverse, inclusive, and healthy community.

## Our Standards

Examples of behavior that contributes to a positive environment for our
community include:

* Demonstrating empathy and kindness toward other people
* Being respectful of differing opinions, viewpoints, and experiences
* Giving and gracefully accepting constructive feedback
* Accepting responsibility and apologizing to those affected by our mistakes,
  and learning from the experience
* Focusing on what is best not just for us as individuals, but for the overall
  community

Examples of unacceptable behavior include:

* The use of sexualized language or imagery, and sexual attention or advances of
  any kind
* Trolling, insulting or derogatory comments, and personal or political attacks
* Public or private harassment
* Publishing others' private information, such as a physical or email address,
  without their explicit permission
* Other conduct which could reasonably be considered inappropriate in a
  professional setting

## Enforcement Responsibilities

Community leaders are responsible for clarifying and enforcing our standards of
acceptable behavior and will take appropriate and fair corrective action in
response to any behavior that they deem inappropriate, threatening, offensive,
or harmful.

Community leaders have the right and responsibility to remove, edit, or reject
comments, commits, code, wiki edits, issues, and other contributions that are
not aligned to this Code of Conduct, and will communicate reasons for moderation
decisions when appropriate.

## Scope

This Code of Conduct applies within all community spaces, and also applies when
an individual is officially representing the community in public spaces.
Examples of representing our community include using an official e-mail address,
posting via an official social media account, or acting as an appointed
representative at an online or offline event.

## Enforcement

Instances of abusive, harassing, or otherwise unacceptable behavior may be
reported to the community leaders responsible for enforcement at
[INSERT CONTACT METHOD]. All complaints will be reviewed and investigated
promptly and fairly.

All community leaders are obligated to respect the privacy and security of the
reporter of any incident.

## Enforcement Guidelines

Community leaders will follow these Community Impact Guidelines in determining
the consequences for any action they deem in violation of this Code of Conduct:

### 1. Correction

**Community Impact**: Use of inappropriate language or other behavior deemed
unprofessional or unwelcome in the community.

**Consequence**: A private, written warning from community leaders, providing
clarity around the nature of the violation and an explanation of why the
behavior was inappropriate. A public apology may be requested.

### 2. Warning

**Community Impact**: A violation through a single incident or series of
actions.

**Consequence**: A warning with consequences for continued behavior. No
interaction with the people involved, including unsolicited interaction with
those enforcing the Code of Conduct, for a specified period of time. This
includes avoiding interactions in community spaces as well as external channels
like social media. Violating these terms may lead to a temporary or permanent
ban.

### 3. Temporary Ban

**Community Impact**: A serious violation of community standards, including
sustained inappropriate behavior.

**Consequence**: A temporary ban from any sort of interaction or public
communication with the community for a specified period of time. No public or
private interaction with the people involved, including unsolicited interaction
with those enforcing the Code of Conduct, is allowed during this period.
Violating these terms may lead to a permanent ban.

### 4. Permanent Ban

**Community Impact**: Demonstrating a pattern of violation of community
standards, including sustained inappropriate behavior, harassment of an
individual, or aggression toward or disparagement of classes of individuals.

**Consequence**: A permanent ban from any sort of public interaction within the
community.

## Attribution

This Code of Conduct is adapted from the [Contributor Covenant][homepage],
version 2.1, available at
[https://www.contributor-covenant.org/version/2/1/code_of_conduct.html][v2.1].

Community Impact Guidelines were inspired by
[Mozilla's code of conduct enforcement ladder][Mozilla CoC].

For answers to common questions about this code of conduct, see the FAQ at
[https://www.contributor-covenant.org/faq][FAQ]. Translations are available
at [https://www.contributor-covenant.org/translations][translations].

[homepage]: https://www.contributor-covenant.org
[v2.1]: https://www.contributor-covenant.org/version/2/1/code_of_conduct.html
[Mozilla CoC]: https://github.com/mozilla/diversity
[FAQ]: https://www.contributor-covenant.org/faq
[translations]: https://www.contributor-covenant.org/translations
````

## File: CONTRIBUTING.md
````markdown
# Contributing to Argus

Thanks for taking a look. Here's how to get started.

## Quick Setup

```bash
git clone https://github.com/Khamel83/argus.git && cd argus
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev,mcp]"
cp .env.example .env  # configure at least one provider key
pytest
```

## Development

- `pytest tests/` to run the test suite
- All config via env vars — see `.env.example` for what's available
- Provider adapters live in `argus/providers/` and implement `BaseProvider`

## Pull Requests

1. Fork the repo
2. Create a feature branch: `git checkout -b feature/my-feature`
3. Make your changes, make sure tests pass: `pytest`
4. Push and open a PR

One change per PR makes review easier. If it's two logically separate things, it's probably two PRs.

## Adding a Search Provider

1. Create `argus/providers/yourprovider.py` implementing `BaseProvider`
2. Add a `ProviderName` enum entry in `argus/models.py`
3. Wire it into `create_broker()` in `argus/broker/router.py`
4. Add config entries in `argus/config.py` and `.env.example`
5. Add tests in `tests/test_providers.py`
6. Add to routing policies in `argus/broker/policies.py` and budget tiers in `argus/broker/budgets.py`

The DuckDuckGo provider is a good reference — it's simple and doesn't need an API key.
````

## File: SECURITY.md
````markdown
# Security Policy

## Supported Versions

Only the latest release is actively maintained. Check [PyPI](https://pypi.org/project/argus-search/) for the current version.

## Reporting a Vulnerability

If you find a security vulnerability, please open a [GitHub issue](https://github.com/Khamel83/argus/issues/new?template=bug_report.md) with the `security` label.

## What Argus Handles

- **SSRF protection**: All URL extraction blocks private/internal IP ranges (10.x, 172.16-31.x, 192.168.x, 127.x, ::1)
- **Domain rate limiting**: 10 requests/minute per domain to prevent abuse
- **No user data storage**: Search queries and sessions are stored locally by the user — nothing is sent to Argus servers
- **API keys**: Keys are read from environment variables only — never logged, transmitted, or stored outside the user's config

## Dependencies

Argus relies on `httpx` for outbound HTTP requests. Keep dependencies updated:

```bash
pip install --upgrade argus-search
```

Dependabot is enabled on this repository for automated dependency tracking.
````

## File: server.json
````json
{
  "$schema": "https://static.modelcontextprotocol.io/schemas/2025-12-11/server.schema.json",
  "name": "io.github.Khamel83/argus",
  "title": "Argus Search",
  "description": "Multi-provider web search broker for AI agents with RRF ranking and budget-aware routing.",
  "repository": {
    "url": "https://github.com/Khamel83/argus",
    "source": "github"
  },
  "version": "1.3.3",
  "packages": [
    {
      "registryType": "pypi",
      "registryBaseUrl": "https://pypi.org",
      "identifier": "argus-search",
      "version": "1.3.3",
      "runtimeHint": "uvx",
      "transport": {
        "type": "stdio"
      },
      "environmentVariables": [
        {
          "name": "ARGUS_BRAVE_API_KEY",
          "description": "Brave Search API key (2,000 free queries/month)",
          "isRequired": false,
          "isSecret": true
        },
        {
          "name": "ARGUS_TAVILY_API_KEY",
          "description": "Tavily API key (1,000 free queries/month)",
          "isRequired": false,
          "isSecret": true
        },
        {
          "name": "ARGUS_EXA_API_KEY",
          "description": "Exa API key (1,000 free queries/month)",
          "isRequired": false,
          "isSecret": true
        },
        {
          "name": "ARGUS_SERPER_API_KEY",
          "description": "Serper API key (2,500 one-time free queries)",
          "isRequired": false,
          "isSecret": true
        },
        {
          "name": "ARGUS_LINKUP_API_KEY",
          "description": "Linkup API key (1,000 free queries/month)",
          "isRequired": false,
          "isSecret": true
        },
        {
          "name": "ARGUS_SEARXNG_BASE_URL",
          "description": "SearXNG instance URL (self-hosted, free unlimited search)",
          "isRequired": false,
          "isSecret": false
        }
      ]
    }
  ]
}
````

## File: .github/workflows/docker-publish.yml
````yaml
name: Build and Push to GHCR
on:
  push:
    branches: [main]
    tags: ['v*']
env:
  REGISTRY: ghcr.io
  IMAGE_NAME: ${{ github.repository }}
jobs:
  build-and-push:
    runs-on: ubuntu-latest
    permissions:
      contents: read
      packages: write
    steps:
      - name: Checkout
        uses: actions/checkout@v4
      - name: Set up Docker Buildx
        uses: docker/setup-buildx-action@v3
      - name: Login to GHCR
        uses: docker/login-action@v3
        with:
          registry: ${{ env.REGISTRY }}
          username: ${{ github.actor }}
          password: ${{ secrets.GITHUB_TOKEN }}
      - name: Extract metadata
        id: meta
        uses: docker/metadata-action@v5
        with:
          images: ${{ env.REGISTRY }}/${{ env.IMAGE_NAME }}
          tags: |
            type=raw,value=latest,enable={{is_default_branch}}
            type=semver,pattern={{version}}
            type=sha,prefix=
      - name: Build and push
        uses: docker/build-push-action@v5
        with:
          context: .
          push: true
          tags: ${{ steps.meta.outputs.tags }}
          labels: ${{ steps.meta.outputs.labels }}
          cache-from: type=gha
          cache-to: type=gha,mode=max
````

## File: .github/pull_request_template.md
````markdown
## Summary
Brief description of what this PR does and why.

## Changes
-

## Testing
- [ ] Tests pass: `pytest`
- [ ] Tested manually with:

## Checklist
- [ ] Code follows existing patterns
- [ ] New features have tests
````

## File: argus/broker/session_flow.py
````python
logger = get_logger("broker.session_flow")
class SessionSearchService
⋮----
def __init__(self, session_store=None)
⋮----
session = None
effective_session_id = session_id
⋮----
session = self._session_store.get_session(effective_session_id)
⋮----
session = self._session_store.create_session(effective_session_id)
effective_session_id = session.id
refined_text = refine_query(query.query, session)
effective_query = query
⋮----
effective_query = SearchQuery(
response = await search_fn(effective_query)
````

## File: argus/extraction/quality_gate.py
````python
logger = get_logger("quality_gate")
class GateResult(Enum)
⋮----
PASS = "pass"
FAIL = "fail"
⋮----
@dataclass
class QualityGateEvaluation
⋮----
decision: GateResult
reason: str
checks_passed: List[str] = field(default_factory=list)
checks_failed: List[str] = field(default_factory=list)
word_count: int = 0
metadata: dict = field(default_factory=dict)
⋮----
@property
    def passed(self) -> bool
THRESHOLDS = {
PREVIEW_PATTERNS = [
# Hard paywall domains
HIGH_RISK_DOMAINS = {
# Archive sources that get lower thresholds
ARCHIVE_DOMAINS = {
_PREVIEW_REGEXES = [re.compile(p, re.IGNORECASE) for p in PREVIEW_PATTERNS]
class QualityGate
⋮----
"""Content quality gate checked between extraction steps."""
def __init__(self)
⋮----
"""
        Evaluate content against quality gate.
        Args:
            content: Extracted text content
            source_url: Original URL
            content_type: article, transcript, video, etc.
            extractor: Which extractor produced this (wayback, archive_is, etc.)
        Returns:
            QualityGateEvaluation with decision and reason
        """
checks_passed = []
checks_failed = []
metadata = {}
# Notes always pass
⋮----
words = content.split()
word_count = len(words)
⋮----
min_words = THRESHOLDS.get(content_type, 100)
parsed = urlparse(source_url)
domain = parsed.netloc.replace('www.', '').lower()
⋮----
is_archive = (
⋮----
is_high_risk = any(hrd in domain for hrd in self.high_risk_domains)
⋮----
# CHECK 2: Preview/truncation patterns
check_region = content[:1000] + content[-1000:] if len(content) > 2000 else content
check_region_lower = check_region.lower()
preview_matches = []
⋮----
def quick_check(self, content: str, content_type: str = "article") -> bool
⋮----
word_count = len(content.split())
⋮----
check_region = content[:500].lower()
````

## File: argus/extraction/soft_404.py
````python
SOFT_404_PATTERNS = [
_COMPILED_PATTERNS = [re.compile(p, re.IGNORECASE) for p in SOFT_404_PATTERNS]
def is_soft_404(text: str) -> bool
⋮----
"""
    Detect if extracted text is a soft 404 page.
    Args:
        text: Extracted text content (not raw HTML)
    Returns:
        True if this appears to be a soft 404
    """
⋮----
# Check first 5000 chars where error messages appear
check_text = text[:5000].lower()
matches = 0
⋮----
# Require at least 1 match — single strong pattern is enough for text content
# (HTML version was more conservative because navigation text has false positives)
⋮----
def soft_404_check(content: str, url: str = "") -> tuple[bool, str]
⋮----
"""
    Quality-gate-compatible soft 404 check.
    Returns:
        (is_soft_404, reason) tuple — if is_soft_404 is True, reject content
    """
````

## File: argus/extraction/ssrf.py
````python
def is_safe_url(url: str) -> tuple[bool, str]
⋮----
parsed = urlparse(url)
⋮----
hostname = parsed.hostname
⋮----
internal_patterns = [
hostname_lower = hostname.lower()
⋮----
# Resolve and check IP addresses
⋮----
resolved = socket.getaddrinfo(
⋮----
ip_str = sockaddr[0]
⋮----
ip = ipaddress.ip_address(ip_str)
⋮----
# DNS resolution failed — let the request fail naturally
````

## File: argus/mcp/tools.py
````python
def _serialize_response(resp) -> str
⋮----
results = []
⋮----
traces = []
⋮----
search_mode = SearchMode(mode)
q = SearchQuery(query=query, mode=search_mode, max_results=max_results)
⋮----
result = json.loads(_serialize_response(resp))
⋮----
resp = await broker.search(q)
⋮----
query_parts = [url]
⋮----
q = SearchQuery(query=" ".join(query_parts), mode=SearchMode.RECOVERY, max_results=10)
⋮----
archive_result = await _try_archive_ph(url)
⋮----
json_resp = json.loads(_serialize_response(resp))
⋮----
async def _try_archive_ph(url: str) -> Optional[dict]
⋮----
archive_url = f"https://archive.ph/newest/{quote_plus(url)}"
⋮----
resp = await client.get(archive_url, headers={
⋮----
html = resp.text
⋮----
loop = __import__("asyncio").get_event_loop()
extracted = await loop.run_in_executor(None, trafilatura.bare_extraction, html)
⋮----
query_text = f"{query} {context}" if context else query
q = SearchQuery(query=query_text, mode=SearchMode.DISCOVERY, max_results=15)
⋮----
def search_health(broker: SearchBroker) -> str
⋮----
"""Get health status of all search providers.
    Returns provider availability, health state, and any active cooldowns.
    """
⋮----
providers = {}
⋮----
def search_budgets(broker: SearchBroker) -> str
⋮----
budgets = {}
⋮----
pname = ProviderName(provider)
⋮----
prov = broker._providers.get(pname)
⋮----
q = SearchQuery(query=query, mode=SearchMode.DISCOVERY, max_results=3)
⋮----
async def valyu_answer(query: str, fast_mode: bool = False) -> str
⋮----
result = await _answer(query, fast_mode=fast_mode)
⋮----
async def extract_content(url: str, domain: str = None) -> str
⋮----
result = await extract_url(url, domain=domain)
⋮----
def cookie_health() -> str
⋮----
summary = get_health_summary()
````

## File: argus/providers/brave.py
````python
logger = get_logger("providers.brave")
BRAVE_API_BASE = "https://api.search.brave.com/res/v1/web/search"
class BraveProvider(BaseProvider)
⋮----
def __init__(self, config: ProviderConfig)
⋮----
@property
    def name(self) -> ProviderName
def is_available(self) -> bool
def status(self) -> ProviderStatus
async def search(self, query: SearchQuery) -> Tuple[List[SearchResult], ProviderTrace]
⋮----
start = time.monotonic()
headers = {
params = {"q": query.query, "count": query.max_results}
⋮----
resp = await client.get(BRAVE_API_BASE, params=params, headers=headers)
⋮----
data = resp.json()
web_results = data.get("web", {}).get("results", [])
results = self._normalize(web_results)
latency_ms = int((time.monotonic() - start) * 1000)
credit_info = {}
⋮----
trace = ProviderTrace(
⋮----
def _normalize(self, raw_results: list) -> List[SearchResult]
⋮----
results = []
⋮----
url = item.get("url") or ""
⋮----
@staticmethod
    def _extract_domain(url: str) -> str
````

## File: argus/providers/linkup.py
````python
logger = get_logger("providers.linkup")
LINKUP_API_BASE = "https://api.linkup.so/v1/search"
class LinkupProvider(BaseProvider)
⋮----
def __init__(self, config: ProviderConfig)
⋮----
@property
    def name(self) -> ProviderName
def is_available(self) -> bool
def status(self) -> ProviderStatus
async def search(self, query: SearchQuery) -> Tuple[List[SearchResult], ProviderTrace]
⋮----
start = time.monotonic()
headers = {
body = {
⋮----
resp = await client.post(LINKUP_API_BASE, json=body, headers=headers)
⋮----
data = resp.json()
raw_results = data.get("results", [])
results = self._normalize(raw_results)
latency_ms = int((time.monotonic() - start) * 1000)
credit_info = {}
⋮----
trace = ProviderTrace(
⋮----
def _normalize(self, raw_results: list) -> List[SearchResult]
⋮----
results = []
⋮----
url = item.get("url") or ""
⋮----
@staticmethod
    def _extract_domain(url: str) -> str
````

## File: argus/providers/parallel.py
````python
logger = get_logger("providers.parallel")
PARALLEL_API_BASE = "https://api.parallel.ai/v1beta/search"
class ParallelProvider(BaseProvider)
⋮----
def __init__(self, config: ProviderConfig)
⋮----
@property
    def name(self) -> ProviderName
def is_available(self) -> bool
def status(self) -> ProviderStatus
async def search(self, query: SearchQuery) -> Tuple[List[SearchResult], ProviderTrace]
⋮----
start = time.monotonic()
headers = {
body = {
⋮----
resp = await client.post(PARALLEL_API_BASE, json=body, headers=headers)
⋮----
data = resp.json()
raw_results = data.get("results", [])
results = self._normalize(raw_results)
latency_ms = int((time.monotonic() - start) * 1000)
credit_info = {}
⋮----
trace = ProviderTrace(
⋮----
def _normalize(self, raw_results: list) -> List[SearchResult]
⋮----
results = []
⋮----
url = item.get("url") or ""
⋮----
@staticmethod
    def _extract_domain(url: str) -> str
````

## File: argus/providers/serper.py
````python
logger = get_logger("providers.serper")
SERPER_API_BASE = "https://google.serper.dev/search"
class SerperProvider(BaseProvider)
⋮----
def __init__(self, config: ProviderConfig)
⋮----
@property
    def name(self) -> ProviderName
def is_available(self) -> bool
def status(self) -> ProviderStatus
async def search(self, query: SearchQuery) -> Tuple[List[SearchResult], ProviderTrace]
⋮----
start = time.monotonic()
headers = {
payload = {
⋮----
resp = await client.post(SERPER_API_BASE, json=payload, headers=headers)
⋮----
data = resp.json()
organic = data.get("organic", [])
results = self._normalize(organic)
latency_ms = int((time.monotonic() - start) * 1000)
credit_info = {}
⋮----
trace = ProviderTrace(
⋮----
def _normalize(self, raw_results: list) -> List[SearchResult]
⋮----
results = []
⋮----
url = item.get("link") or ""
⋮----
@staticmethod
    def _extract_domain(url: str) -> str
````

## File: argus/sessions/refinement.py
````python
logger = get_logger("sessions.refinement")
MAX_CONTEXT_QUERIES = 3
def refine_query(current_query: str, session: Optional[Session]) -> str
⋮----
prior = session.queries[:-1]
⋮----
recent = prior[-MAX_CONTEXT_QUERIES:]
context_terms: List[str] = []
⋮----
words = current_query.split()
is_follow_up = (
⋮----
last_context = context_terms[-1]
````

## File: docs/go-to-market.md
````markdown
# Go-to-Market Plan

## Timeline

### Week 1: Foundation
- [x] llms.txt + llms-full.txt
- [x] GitHub topics (mcp-server, search-api, ai-agents, llm-tools, search-broker, python, fastapi, web-search)
- [x] pyproject.toml metadata (keywords, classifiers, URLs)
- [x] README badges + MCP install section (Claude Code, Cursor, VS Code)
- [x] CONTRIBUTING.md + .github/ templates
- [ ] Submit to MCP Server Registry (https://modelcontextprotocol.io/registry)
- [ ] Submit to mcp-get (https://github.com/punkpeye/mcp-get)

### Week 2: Launch
- [ ] Hacker News post — see angle below
- [ ] One technical blog post on RRF for search aggregation (evergreen content)
- [ ] Submit to Python Weekly, PyCoder's Weekly

### Week 3+: Sustain
- [ ] Reddit: r/LocalLLaMA, r/ClaudeAI, r/ChatGPTCoding
- [ ] Monitor GitHub Issues for feature requests and bugs
- [ ] Respond to every issue within 48 hours

## HN Post Angle

**Title:** "How I cut my agent's search costs by 70% using a Python broker with RRF ranking"

**Hook:** The cost-reliability arbitrage story — use Serper ($1/1k) as primary with Exa/Tavily ($5-10/1k) as fallback. RRF ranking merges results so you get better quality than any single provider.

**Technical depth:** Brief explanation of Reciprocal Rank Fusion (k=60) and how it boosts results that appear across multiple providers.

**Don't say:** "I made a tool." Say: "Here's a technique" and the tool is the implementation.

## Reddit Angles

**r/LocalLLaMA:** "I built a search broker so my local LLM can use 5 search APIs with automatic fallback — no vendor lock-in, budget enforcement included"

**r/ClaudeAI:** "Argus: MCP search server with 5 providers, RRF ranking, and budget tracking — one config block and Claude Code gets reliable web search"

## Key Metrics

| Timeline | Target |
|----------|--------|
| Month 1 | 50-200 stars |
| Month 3 | 500-1,000 stars |
| Month 6 | 1,000-3,000 stars |
| Real success metric | Active MCP registry installs |

## What NOT to Do

| Don't | Why |
|-------|-----|
| Build a UI | Backend tool. A UI is a distraction. |
| X/Twitter | No following = screaming into a void. |
| Weekly updates | One good post > weekly noise. |
| Build a website | README + GitHub is enough. |
| Chase press coverage | They won't cover a 0-star repo. |
| Chase Product Hunt | Dead for dev tools in 2026. |

## Competitive Position

Argus is the only search broker that: multi-provider routing + RRF ranking + budget enforcement + health tracking + MCP native. The closest competitor (`multi-search-api`) has no MCP, no HTTP API, no ranking, no budgets. The MCP search category has 903 repos on GitHub but **zero** do all of this.

## Distribution Channels

1. **MCP Server Registry** — primary discovery path for AI agent builders
2. **mcp-get** — CLI-based MCP server discovery
3. **llms.txt** — AI agents can "read" the repo automatically
4. **GitHub topics** — drives Explore and search discovery
5. **HN + Reddit** — initial user acquisition
````

## File: docs/providers.md
````markdown
# Provider Setup

Each provider needs an API key set in `.env`. Unset keys are silently skipped.

## SearXNG (free, self-hosted)

No API key needed. Runs locally in Docker.

```bash
docker run -d --name searxng -p 8080:8080 searxng/searxng:latest
```

Verify it returns JSON:

```bash
curl 'http://localhost:8080/search?q=test&format=json'
```

### Tuning

Edit SearXNG settings inside the container:

```bash
docker exec -it searxng sh -c "vi /etc/searxng/settings.yml"
docker restart searxng
```

Key settings:

```yaml
search:
  formats:
    - json        # Required for Argus
  safe_search: 0  # 0=off, 1=moderate, 2=strict

server:
  bind_address: "127.0.0.1"
  limiter: false  # Set to true in production
```

### Docker Networking

If SearXNG and Argus are on the same Docker network, use the container hostname:

```
ARGUS_SEARXNG_BASE_URL=http://searxng:8080
```

The included `docker-compose.yml` handles this automatically.

## Brave Search

Free tier: 2,000 queries/month.

1. Go to [brave.com/search/api](https://brave.com/search/api/)
2. Sign up → get API key
3. Set in `.env`:

```
ARGUS_BRAVE_API_KEY=BSA...
```

## Serper

Free tier: 2,500 queries/month.

1. Go to [serper.dev](https://serper.dev)
2. Sign up → copy API key from dashboard
3. Set in `.env`:

```
ARGUS_SERPER_API_KEY=abc...
```

## Tavily

Free tier: 1,000 queries/month.

1. Go to [app.tavily.com](https://app.tavily.com/sign-up)
2. Sign up → copy API key
3. Set in `.env`:

```
ARGUS_TAVILY_API_KEY=tvly-...
```

## Exa

Free tier: 1,000 queries/month.

1. Go to [dashboard.exa.ai](https://dashboard.exa.ai/signup)
2. Sign up → copy API key
3. Set in `.env`:

```
ARGUS_EXA_API_KEY=...
```

## Budgets

Set monthly spend limits per provider in `.env`:

```
ARGUS_BRAVE_MONTHLY_BUDGET_USD=5
ARGUS_SERPER_MONTHLY_BUDGET_USD=0   # 0 = unlimited
```

When a provider hits its budget, it's automatically skipped until next month.
````

## File: tests/test_providers.py
````python
def _make_mock_response(data)
⋮----
mock_resp = MagicMock()
⋮----
def _mock_httpx(mock_get_or_post, response_data)
⋮----
mock_client = AsyncMock()
⋮----
class TestSearXNGProvider
⋮----
def test_is_available_when_enabled(self)
⋮----
p = SearXNGProvider(SearXNGConfig(enabled=True, base_url="http://localhost:8080"))
⋮----
def test_not_available_when_disabled(self)
⋮----
p = SearXNGProvider(SearXNGConfig(enabled=False))
⋮----
def test_name(self)
⋮----
p = SearXNGProvider(SearXNGConfig())
⋮----
@pytest.mark.asyncio
    async def test_search_normalizes_results(self)
⋮----
mock_response = {
⋮----
query = SearchQuery(query="test")
⋮----
@pytest.mark.asyncio
    async def test_search_returns_error_trace_on_failure(self)
class TestBraveProvider
⋮----
def test_is_available_with_key(self)
⋮----
p = BraveProvider(ProviderConfig(enabled=True, api_key="test-key"))
⋮----
def test_not_available_without_key(self)
⋮----
p = BraveProvider(ProviderConfig(enabled=True, api_key=""))
⋮----
def test_status_missing_key(self)
⋮----
@pytest.mark.asyncio
    async def test_search_normalizes_web_results(self)
⋮----
p = BraveProvider(ProviderConfig(enabled=True, api_key="key"))
⋮----
query = SearchQuery(query="brave")
⋮----
class TestSerperProvider
⋮----
p = SerperProvider(ProviderConfig(enabled=True, api_key="key"))
⋮----
@pytest.mark.asyncio
    async def test_search_normalizes_organic(self)
⋮----
query = SearchQuery(query="google")
⋮----
class TestTavilyProvider
⋮----
p = TavilyProvider(ProviderConfig(enabled=True, api_key="key"))
⋮----
query = SearchQuery(query="tavily")
⋮----
class TestExaProvider
⋮----
p = ExaProvider(ProviderConfig(enabled=True, api_key="key"))
⋮----
query = SearchQuery(query="exa")
⋮----
class TestStubs
⋮----
def test_searchapi_not_available(self)
⋮----
p = SearchApiProvider(ProviderConfig())
⋮----
def test_you_not_available(self)
⋮----
p = YouProvider(ProviderConfig())
⋮----
def test_valyu_not_available(self)
⋮----
p = ValyuProvider(ProviderConfig())
⋮----
@pytest.mark.asyncio
    async def test_searchapi_returns_empty(self)
⋮----
@pytest.mark.asyncio
    async def test_you_returns_empty(self)
⋮----
@pytest.mark.asyncio
    async def test_valyu_returns_empty_when_disabled(self)
class TestValyuProvider
⋮----
p = ValyuProvider(ProviderConfig(enabled=True, api_key="val_test_key"))
⋮----
p = ValyuProvider(ProviderConfig(enabled=True, api_key=""))
⋮----
def test_status_disabled(self)
⋮----
p = ValyuProvider(ProviderConfig(enabled=False))
⋮----
query = SearchQuery(query="test query")
⋮----
@pytest.mark.asyncio
    async def test_search_handles_api_error(self)
⋮----
@pytest.mark.asyncio
    async def test_search_handles_connection_error(self)
class TestGitHubProvider
⋮----
p = GitHubProvider(ProviderConfig(enabled=True))
⋮----
p = GitHubProvider(ProviderConfig(enabled=False))
⋮----
query = SearchQuery(query="argus search broker")
⋮----
@pytest.mark.asyncio
    async def test_search_handles_rate_limit(self)
⋮----
class TestProviderContracts
⋮----
def test_implements_base_provider_contract(self, provider_name, factory)
⋮----
provider = factory()
````

## File: .dockerignore
````
# VCS
.git
.gitignore

# Python
__pycache__/
*.py[cod]
*.egg-info/
dist/
build/
.eggs/
*.egg

# Virtual environments
.venv/
venv/

# Environment (injected at runtime, not baked in)
.env
.env.bak
.env.local

# Testing
tests/
.pytest_cache/
.coverage
htmlcov/

# Docs
docs/
*.md
!README.md
LICENSE

# IDE
.vscode/
.idea/
*.swp
*.swo
*~

# OS
.DS_Store
Thumbs.db

# Runtime artifacts
argus_budgets.db/
1shot/
docker-compose.yml
Dockerfile
````

## File: .github/workflows/ci.yml
````yaml
name: CI
on:
  push:
    branches: [main]
  pull_request:
    branches: [main]
jobs:
  test:
    runs-on: ubuntu-latest
    strategy:
      matrix:
        python-version: ["3.11", "3.12", "3.13"]
    steps:
      - uses: actions/checkout@v4
      - name: Set up Python ${{ matrix.python-version }}
        uses: actions/setup-python@v5
        with:
          python-version: ${{ matrix.python-version }}
      - name: Install dependencies
        run: |
          python -m pip install --upgrade pip
          pip install -e ".[dev]"
      - name: Run tests
        run: python -m pytest tests/ -v --tb=short
  freshness:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Check llms.txt freshness
        run: |
          README_TS=$(git log -1 --format=%ct -- README.md)
          LLMS_TS=$(git log -1 --format=%ct -- llms.txt)
          LLMSFULL_TS=$(git log -1 --format=%ct -- llms-full.txt)
          if [ "$LLMS_TS" -lt "$README_TS" ] || [ "$LLMSFULL_TS" -lt "$README_TS" ]; then
            echo "::warning::llms.txt or llms-full.txt is older than README.md — update them before release"
          fi
      - name: Check version sync
        run: |
          PYPI_VER=$(python3 -c "import tomllib; d=tomllib.load(open('pyproject.toml','rb')); print(d['project']['version'])")
          SJSON_VER=$(python3 -c "import json; d=json.load(open('server.json')); print(d['version'])")
          SJSON_PKG_VER=$(python3 -c "import json; d=json.load(open('server.json')); print(d['packages'][0]['version'])")
          if [ "$PYPI_VER" != "$SJSON_VER" ]; then
            echo "::error::pyproject.toml ($PYPI_VER) and server.json top-level ($SJSON_VER) version mismatch"
            exit 1
          fi
          if [ "$PYPI_VER" != "$SJSON_PKG_VER" ]; then
            echo "::error::pyproject.toml ($PYPI_VER) and server.json packages[0] ($SJSON_PKG_VER) version mismatch"
            exit 1
          fi
          echo "Versions in sync: $PYPI_VER"
````

## File: .github/workflows/publish.yml
````yaml
name: Publish
on:
  release:
    types: [created]
permissions:
  contents: write
  id-token: write
jobs:
  pypi:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - name: Install build tools
        run: pip install build twine
      - name: Build
        run: python -m build
      - name: Publish to PyPI
        env:
          TWINE_USERNAME: __token__
          TWINE_PASSWORD: ${{ secrets.PYPI_API_TOKEN }}
        run: twine upload dist/*
  mcp-registry:
    needs: pypi
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Install mcp-publisher
        run: |
          curl -L "https://github.com/modelcontextprotocol/registry/releases/latest/download/mcp-publisher_$(uname -s | tr '[:upper:]' '[:lower:]')_$(uname -m | sed 's/x86_64/amd64/;s/aarch64/arm64/').tar.gz" | tar xz mcp-publisher
          sudo mv mcp-publisher /usr/local/bin/
      - name: Login to MCP Registry
        run: mcp-publisher login github-oidc
      - name: Publish to MCP Registry
        run: mcp-publisher publish
````

## File: argus/api/schemas.py
````python
_VALID_MODES: Set[str] = {"recovery", "discovery", "grounding", "research"}
_VALID_PROVIDERS: Set[str] = {"searxng", "brave", "serper", "tavily", "exa", "searchapi", "you"}
class SearchRequest(BaseModel)
⋮----
query: str = Field(..., min_length=1, max_length=500, description="Search query string")
mode: str = Field("discovery", description="Search mode: recovery, discovery, grounding, research")
max_results: int = Field(10, ge=1, le=50, description="Maximum results to return")
providers: Optional[List[str]] = Field(None, description="Override provider routing order")
session_id: Optional[str] = Field(None, description="Session ID for multi-turn context")
⋮----
@field_validator("query")
@classmethod
    def sanitize_query(cls, v: str) -> str
⋮----
cleaned = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', '', v)
cleaned = re.sub(r'\s{3,}', ' ', cleaned)
⋮----
@field_validator("mode")
@classmethod
    def validate_mode(cls, v: str) -> str
⋮----
@field_validator("providers")
@classmethod
    def validate_providers(cls, v: Optional[List[str]]) -> Optional[List[str]]
⋮----
invalid = [p for p in v if p.lower() not in _VALID_PROVIDERS]
⋮----
class SearchResultSchema(BaseModel)
⋮----
url: str
title: str
snippet: str
domain: str = ""
provider: Optional[str] = None
score: float = 0.0
class ProviderTraceSchema(BaseModel)
⋮----
provider: str
status: str
results_count: int = 0
latency_ms: int = 0
error: Optional[str] = None
budget_remaining: Optional[float] = None
class SearchResponse(BaseModel)
⋮----
query: str
mode: str
results: List[SearchResultSchema] = []
traces: List[ProviderTraceSchema] = []
total_results: int = 0
cached: bool = False
search_run_id: Optional[str] = None
session_id: Optional[str] = None
class RecoverUrlRequest(BaseModel)
⋮----
url: str = Field(..., min_length=1, max_length=2048, description="URL to recover")
title: Optional[str] = Field(None, description="Optional title hint for better results")
domain: Optional[str] = Field(None, description="Optional domain hint")
class ExpandRequest(BaseModel)
⋮----
query: str = Field(..., min_length=1, max_length=500, description="Query to expand with related links")
context: Optional[str] = Field(None, description="Optional context for expansion")
class ProviderTestRequest(BaseModel)
⋮----
provider: str = Field(..., description="Provider name to test")
query: str = Field("argus", description="Test query")
class ExtractRequest(BaseModel)
⋮----
url: str = Field(..., min_length=1, max_length=2048, description="URL to extract content from")
domain: Optional[str] = Field(None, description="Domain hint for authenticated extraction (e.g. nytimes.com)")
⋮----
@field_validator("url")
@classmethod
    def validate_url(cls, v: str) -> str
⋮----
parsed = urlparse(v)
hostname = parsed.hostname or ""
⋮----
class ExtractResponse(BaseModel)
⋮----
title: str = ""
text: str = ""
author: str = ""
date: Optional[str] = None
word_count: int = 0
extractor: Optional[str] = None
⋮----
quality_passed: Optional[bool] = None
quality_reason: Optional[str] = None
extractors_tried: Optional[list[str]] = None
class ErrorResponse(BaseModel)
⋮----
error: str
details: Optional[dict] = None
````

## File: argus/sessions/persistence.py
````python
logger = get_logger("sessions.persistence")
DEFAULT_DB_PATH = "argus_budgets.db"
_SCHEMA = """
class SessionPersistence
⋮----
def __init__(self, db_path: Optional[str] = None)
def _get_conn(self) -> sqlite3.Connection
def save_session(self, session_id: str, created_at: float) -> None
⋮----
conn = self._get_conn()
⋮----
ts = timestamp or time.time()
cursor = conn.execute(
⋮----
row = conn.execute(
⋮----
def load_session(self, session_id: str) -> Optional[dict]
⋮----
queries = []
⋮----
q_idx = len(queries)
extracted_urls = [
⋮----
def session_exists(self, session_id: str) -> bool
def list_session_ids(self) -> list[str]
⋮----
rows = conn.execute("SELECT id FROM sessions ORDER BY created_at DESC").fetchall()
⋮----
def list_sessions(self) -> list[dict]
⋮----
rows = conn.execute("SELECT id, created_at FROM sessions ORDER BY created_at DESC").fetchall()
⋮----
def close(self) -> None
````

## File: argus/sessions/store.py
````python
logger = get_logger("sessions")
class SessionStore
⋮----
def __init__(self, persist: bool = True, db_path: Optional[str] = None)
def _load_session(self, session_id: str) -> Optional[Session]
⋮----
data = self._db.load_session(session_id)
⋮----
session = Session(
⋮----
def create_session(self, session_id: Optional[str] = None) -> Session
⋮----
sid = session_id or str(uuid.uuid4())[:8]
⋮----
loaded = self._load_session(sid)
⋮----
session = Session(id=sid)
⋮----
def get_session(self, session_id: str) -> Optional[Session]
⋮----
session = self._sessions.get(session_id)
⋮----
record = QueryRecord(
⋮----
def add_extracted_url(self, session_id: str, query_index: int, url: str) -> None
def list_sessions(self) -> list[Session]
````

## File: docs/research/competitive-backlog.md
````markdown
# Competitive Improvement Backlog

Tracked from competitive research (April 2026).
Items from `docs/research/mcp-search-competitors/research.md` "Where Competitors Are Stronger" section.

## Completed

- [x] **AI-powered answers** — Valyu Answer MCP tool added (`valyu_answer`). Returns synthesized answers with citations via SSE. Replaces the Perplexity Sonar gap.
- [x] **More extraction providers** — Valyu Contents ($0.001/URL) and Firecrawl (1 credit/page) added to 9-step extraction chain.
- [x] **More search providers** — Valyu Search added as tier 3 provider across all modes.
- [x] **GitHub integration** — GitHub provider added (tier 0, free, 30 req/min with token). Searches repositories. In discovery and research modes.
- [x] **Ease of setup** — README zero-config section strengthened. One-liner install + search. pipx instructions added. MCP setup section clarified.

## Open (from competitive research)

### High Impact
- [ ] **MCP Marketplace presence** — Three registries, all need browser action or public deployment:
  - **Smithery** (smithery.ai/new): Needs public HTTPS URL serving MCP (Streamable HTTP). Argus supports SSE transport — would need deployment to a public host first (Vercel, Railway, etc.). Smithery auto-scans the URL for tools/metadata.
  - **mcp.so** (mcp.so/submit): Web form — needs Name + URL. Browser submission. No GitHub issue path found.
  - **mcpservers.org** (mcpservers.org/submit): Web form — free listing or $39 premium for priority review. This is the awesome-mcp-servers repo (no PRs accepted).
  - **Official MCP Registry** (registry.modelcontextprotocol.io): TypeScript SDK only — no Python support yet.
  - **Blocker**: All three need either (a) Argus deployed to a public HTTPS URL, or (b) manual browser submission. Neither is a code change.

### Medium Impact
- [ ] **Local search without backend** — DuckDuckGo works with zero infra, but research notes "Argus needs SearXNG for free search" is a perception issue. README now leads with zero-config. Consider adding a "Quick Start" badge or section at the very top.
- [ ] **Documentation polish** — Competitors have professional docs, SDKs, enterprise support. Argus has project docs. Consider readthedocs or similar.

### Low Priority / Future
- [ ] **Chinese search engines** — one-search-mcp supports Zhipu, Bocha, Baidu, Sogou. Niche requirement.
- [ ] **Brand recognition** — Marketing effort, not code. Blog posts, Reddit presence, etc.

## Not Actionable (Aspirational)

These were identified by the explore agent but NOT in the original research docs:
- Observability dashboard (separate project)
- Knowledge graph API
- Structured extraction with schemas
- SOC 2 / enterprise compliance
- Kubernetes/edge deployment
````

## File: CHANGELOG.md
````markdown
# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.3.3] - 2026-04-14

### Added
- MCP Registry publishing — live at [modelcontextprotocol.io](https://registry.modelcontextprotocol.io/servers/io.github.Khamel83/argus) as `io.github.Khamel83/argus`
- `server.json` for MCP Registry metadata and verification
- GitHub Actions publish workflow (PyPI + MCP Registry on release)
- `mcp-name` verification tag in README for PyPI-based ownership

### Changed
- MCP badge links to registry listing
- README MCP quickstart includes registry-based install option
- CLAUDE.md documents version sync convention and MCP Registry interface

## [1.3.2] - 2026-04-13

### Added
- GitHub search provider (free, tier 0) — 10 req/min unauthenticated, 30/min with token
- Valyu provider — search, contents extraction, and AI-synthesized answers with citations
- Firecrawl extractor — content extraction (1 credit/page)
- Pace-aware routing — always queries free providers, paces paid ones based on remaining budget
- Proactive balance checking — header parsing + Tavily usage API for live balance tracking

### Changed
- Documentation quality pass — features, JSON examples, modes, FAQ
- README and CLAUDE.md updated for new providers and ease-of-setup positioning

## [1.3.1] - 2026-04-09

### Added
- DuckDuckGo search provider — zero-config free search, no API key, unlimited
- `ddgs` package as core dependency

### Fixed
- Duplicate SearXNG entry in RESEARCH mode preferences causing double-dispatch

### Changed
- Documentation rewritten with free-first positioning and two deployment tiers
- Hardware requirements table (Raspberry Pi, Mac Mini, laptop, cloud VM)
- All credit claims corrected to standard signup amounts (not promo deals)

## [1.3.0] - 2026-04-08

### Added
- 3 new search providers: Linkup, Parallel AI, You.com
- 2 new extractors: You.com Contents API, Crawl4AI (local JS rendering)
- Tier-based credit routing: Tier 0 (free) → Tier 1 (monthly) → Tier 3 (one-time)
- Budget enforcement with per-provider query-count tracking on 30-day rolling window
- `argus mcp init` command for MCP client configuration

### Changed
- Provider routing now sorts by credit tier first, mode preference second
- Override provider lists are also tier-sorted
- DuckDuckGo added to all 4 search mode preference lists

## [1.2.1] - 2026-03-28

### Changed
- Renamed PyPI package from `argus` to `argus-search`
- Updated install commands across all documentation

### Fixed
- MCP server sync with mcp 1.26.0 API changes

## [1.2.0] - 2026-03-25

### Added
- Content extraction with quality gates — trafilatura, Playwright, Jina, Wayback, archive.is
- `argus extract` CLI command for URL content extraction
- `argus cookies import/health` commands for authenticated extraction
- Cookie-based authenticated extraction for paywall domains
- Quality gate system between extraction steps (paywall detection, soft 404s, minimum quality)
- Docker multi-stage build with GHCR auto-publish on push/tag

### Changed
- Extract response now includes `quality_passed`, `quality_reason`, `extractors_tried`

### Fixed
- MCP server version kwarg removal and async fixes
- Docker builder stage source copy

## [1.1.0] - 2026-03-20

### Added
- Single retry for unhandled provider exceptions
- Documented and configurable cost estimates

### Changed
- Lazy extractor initialization (no SQLite trigger at import time)
- Deduplicated `_extract_domain` across providers
- UTC-aware timestamps throughout

### Removed
- Docker files (replaced with direct install approach)

## [1.0.0] - 2026-03-15

### Added
- 7 search providers: SearXNG, Brave, Serper, Tavily, Exa, You.com, SearchAPI
- Tier-based routing policies (RECOVERY, DISCOVERY, GROUNDING, RESEARCH modes)
- Reciprocal Rank Fusion (RRF) result ranking
- URL deduplication with normalization (www, trailing slash, tracking params, case)
- In-memory search cache with configurable TTL
- Health tracker with failure threshold and cooldown
- Budget tracker with 30-day rolling window
- Multi-turn sessions with TTL, max turns, and context limits
- Authenticated extraction for paywall domains via Playwright
- HTTP API (FastAPI) with OpenAPI docs
- CLI (Click) with search, health, budgets, extract commands
- MCP server for LLM integration
- PostgreSQL persistence layer
- PyPI publishing pipeline

## [1.0] - 2026-03-12

### Added
- Session TTL, max turns, max context chars, and delete endpoint
- Runtime provider disable/enable/reset-health admin endpoints
- Provenance fields on SearchResult model
- Degraded-state test suite (14 tests)
````

## File: argus/api/routes_extract.py
````python
router = APIRouter()
⋮----
@router.post("/extract", response_model=ExtractResponse)
async def extract(req: ExtractRequest)
⋮----
result = await extract_url(req.url, domain=req.domain)
⋮----
@router.get("/cookies/health")
async def cookie_health()
````

## File: argus/broker/execution.py
````python
logger = get_logger("broker.execution")
_COST_ESTIMATES = {
_TIER_0_PROVIDERS = {p for p, t in PROVIDER_TIERS.items() if t == 0}
⋮----
@dataclass
class ProviderExecutionOutcome
⋮----
traces: List[ProviderTrace]
provider_results: Dict[str, List[SearchResult]]
live_providers_used: int
budget_pace_warnings: List[str] = field(default_factory=list)
class ProviderExecutor
⋮----
def _should_query_paid(self, provider: ProviderName, tier: int) -> tuple[bool, str]
⋮----
traces: List[ProviderTrace] = []
provider_results: Dict[str, List[SearchResult]] = {}
live_providers_used = 0
pace_warnings: List[str] = []
ordered = [p for p in provider_order if p != ProviderName.CACHE]
total_results_so_far = 0
⋮----
provider = self._providers.get(pname)
⋮----
health_status = self._health.get_status(pname)
⋮----
tier = self._budgets.get_provider_tier(pname)
# Tier 0: always query (free, unlimited)
# Tier 1/3: check budget pace before spending credits
⋮----
remaining = self._budgets.get_remaining_budget(pname) or 0
used_today = self._budgets.used_today(pname)
pace = self._budgets.daily_pace(pname)
warning = (
⋮----
cost = _COST_ESTIMATES.get(provider_name, 0.0)
````

## File: argus/extraction/cookies.py
````python
logger = get_logger("extraction.cookies")
COOKIE_DIR = Path(os.getenv("ARGUS_COOKIE_DIR", "~/.config/argus/cookies")).expanduser()
HEALTH_FILE = COOKIE_DIR / "health.json"
AUTH_RATE_LIMIT_SECONDS = int(os.getenv("ARGUS_AUTH_RATE_LIMIT", "10"))
PAYWALL_DOMAINS = {
_last_auth_request: dict[str, float] = {}
def _load_health() -> dict
def _save_health(health: dict) -> None
def get_cookie_path(domain: str) -> Optional[Path]
⋮----
path = COOKIE_DIR / f"{domain}.json"
⋮----
parts = domain.split(".")
⋮----
parent = ".".join(parts[-2:])
parent_path = COOKIE_DIR / f"{parent}.json"
⋮----
def needs_auth(url: str) -> bool
⋮----
hostname = urlparse(url).hostname or ""
# Check exact match and parent domain match
⋮----
parts = hostname.split(".")
⋮----
def can_authenticate(domain: str) -> bool
⋮----
"""Check if we have cookies and aren't rate-limited for this domain."""
⋮----
health = _load_health()
status = health.get(domain, {}).get("status", "healthy")
⋮----
now = time.monotonic()
last = _last_auth_request.get(domain, 0)
⋮----
def record_auth_request(domain: str, success: bool, status_code: int = 0) -> None
⋮----
entry = health.setdefault(domain, {
⋮----
def get_health_summary() -> dict
⋮----
now = datetime.now(timezone.utc)
summary = {}
⋮----
cookie_path = get_cookie_path(domain)
last_used = data.get("last_used")
days_since = None
⋮----
last_dt = datetime.fromisoformat(last_used)
days_since = (now - last_dt).days
⋮----
def load_editthiscookie_json(path: Path) -> list[dict]
⋮----
raw_cookies = json.load(f)
⋮----
raw_cookies = raw_cookies.get("cookies", [raw_cookies])
sanitized = []
⋮----
c = {
⋮----
ss = cookie["sameSite"]
⋮----
exp = cookie["expirationDate"]
````

## File: argus/persistence/db.py
````python
logger = get_logger("persistence.db")
_engine = None
_session_factory = None
def init_db(db_url: Optional[str] = None)
⋮----
db_url = get_config().db_url
_engine = create_engine(db_url, pool_pre_ping=True)
_session_factory = sessionmaker(bind=_engine)
⋮----
def get_engine()
def get_session_factory()
⋮----
@contextmanager
def get_session() -> Generator[Session, None, None]
⋮----
factory = get_session_factory()
session = factory()
⋮----
def persist_search(query_text: str, mode: str, response: SearchResponse) -> Optional[str]
⋮----
run_id = response.search_run_id or uuid.uuid4().hex[:16]
⋮----
q_row = SearchQueryRow(query_text=query_text, mode=mode, max_results=response.total_results)
⋮----
run_row = SearchRunRow(
⋮----
result_row = SearchResultRow(
⋮----
# Persist traces
⋮----
usage_row = ProviderUsageRow(
⋮----
class SearchPersistenceGateway
⋮----
def record_completed_search(self, query: SearchQuery, response: SearchResponse) -> Optional[str]
````

## File: docs/PUBLICITY-CHECKLIST.md
````markdown
# Argus Publicity — Step-by-Step Instructions

## Auto-Update Logic (what stays current vs what needs manual work)

| Platform | Auto-updates? | What updates automatically |
|----------|---------------|----------------------|
| PyPI | Only on new tag | Version, description — but only when you push a `v*` tag |
| Glama | Daily GitHub sync | README, server info, score badge, tools list |
| Smithery | Scans GitHub | README metadata, repo info |
| PulseMCP | Scans GitHub | README metadata, repo info |
| mcp.so | Scans GitHub | README metadata, repo info |
| awesome lists (all) | **Never** | Static entry — one-liner you submitted. To update, submit a new PR |
| modelcontextprotocol/servers | **Never** | Static entry. Submit new PR to update |

**Bottom line:** PyPI, Glama, Smithery, PulseMCP, and mcp.so all pull from GitHub automatically. Your README badges, description, and version update on their own. The awesome lists are static — the one-liner you submit today is what stays forever (unless you send a new PR to update it).

---

## Task 1: PR to modelcontextprotocol/servers (Anthropic official)

**Highest impact listing.** ~5 min.

1. Go to https://github.com/modelcontextprotocol/servers
2. Click **Fork** (top-right)
3. On your fork, click README.md → click the pencil icon to edit
4. Search for `🌎 Community Servers` section
5. Add this line in alphabetical order among the other entries:

```
- **[Argus](https://github.com/Khamel83/argus)** - Multi-provider search broker. Routes queries across SearXNG, Brave, Serper, Tavily, and Exa with automatic fallback, RRF ranking, content extraction, and budget enforcement.
```

6. At the bottom, commit message: `Add Argus search broker`
7. Click **Create pull request**
8. Title: `Add Argus multi-provider search broker`
9. Body:

```
Argus is an open-source search broker that routes queries across SearXNG, Brave, Serper, Tavily, and Exa with automatic fallback, RRF ranking, content extraction, and budget enforcement. SQLite only, no external dependencies.

Tools: search_web, extract_content, recover_url, expand_links, search_health, search_budgets, test_provider

Repo: https://github.com/Khamel83/argus
PyPI: https://pypi.org/project/argus-search/
```

10. Click **Create pull request** — done, wait for review.

---

## Task 2: Submit to wong2/awesome-mcp-servers

**Does NOT accept PRs.** Use their website instead. ~2 min.

1. Go to https://mcpservers.org/submit
2. Fill in the form:
   - **Name:** `Argus`
   - **GitHub URL:** `https://github.com/Khamel83/argus`
   - **Description:** `Multi-provider search broker for AI agents. Routes across SearXNG, Brave, Serper, Tavily, and Exa with automatic fallback, RRF ranking, content extraction, and budget enforcement.`
   - **Category:** Search
3. Submit — done.

---

## Task 3: PR to awesome-python (~200k stars)

~5 min.

1. Go to https://github.com/vinta/awesome-python
2. Click **Fork**
3. On your fork, edit README.md
4. Search for `__HTTP & Scraping__` (under Web Development)
5. Add this line in alphabetical order:

```
- [Argus](https://github.com/Khamel83/argus) - Multi-provider search broker that routes queries across SearXNG, Brave, Serper, Tavily, and Exa with automatic fallback and RRF ranking.
```

6. Commit: `Add Argus search broker`
7. Open PR — title: `Add Argus search broker`
8. Body: `Open-source search broker with 5 provider adapters, content extraction, and budget enforcement. PyPI: https://pypi.org/project/argus-search/`
9. Submit — done.

---

## Task 4: PR to awesome-fastapi

~5 min.

1. Go to https://github.com/mjhea0/awesome-fastapi
2. Click **Fork**
3. Edit README.md
4. Search for `### Utils` (under Third-Party Extensions)
5. Add this line:

```
- [Argus](https://github.com/Khamel83/argus) - Multi-provider search broker with content extraction, built as a FastAPI service.
```

6. Commit: `Add Argus search broker`
7. Open PR — title: `Add Argus search broker`
8. Submit — done.

---

## Task 5: Submit to Smithery

~2 min.

1. Go to https://smithery.ai
2. Sign in with GitHub
3. Click **Add Server** or **Submit**
4. Point it at `https://github.com/Khamel83/argus`
5. Fill in description (same as Task 2)
6. Submit — done.

---

## Task 6: Submit to PulseMCP

~2 min.

1. Go to https://pulsemcp.com
2. Look for "Submit" or "Add Server"
3. Enter:
   - Name: `Argus`
   - URL: `https://github.com/Khamel83/argus`
   - Description: same as Task 2
4. Submit — done.

---

## Task 7: Submit to mcp.so

~2 min.

1. Go to https://mcp.so
2. Look for "Submit" or "Add Server"
3. Enter repo URL: `https://github.com/Khamel83/argus`
4. Description: same as Task 2
5. Submit — done.

---

## Status (2026-04-14)

| Task | Platform | Status | Link |
|------|----------|--------|------|
| 0 | MCP Registry (official) | **Live** — `io.github.Khamel83/argus` | [registry](https://registry.modelcontextprotocol.io/servers/io.github.Khamel83/argus) |
| 1 | modelcontextprotocol/servers | PR open, review pending | [#3833](https://github.com/modelcontextprotocol/servers/pull/3833) |
| 2 | wong2/awesome-mcp-servers | Submitted (website form) | mcpservers.org |
| 2b | punkpeye/awesome-mcp-servers | PR open | [#4036](https://github.com/punkpeye/awesome-mcp-servers/pull/4036) |
| 3 | vinta/awesome-python | PR open, review pending | [#3026](https://github.com/vinta/awesome-python/pull/3026) |
| 4 | mjhea0/awesome-fastapi | PR open | [#274](https://github.com/mjhea0/awesome-fastapi/pull/274) |
| 5 | Smithery | Skipped — needs live HTTPS endpoint | N/A |
| 6 | PulseMCP | Auto-ingests from official registry | Pulses when #3833 merges |
| 7 | mcp.so | Live | [mcp.so/server/argus-search](https://mcp.so/server/argus-search/Khamel83) |

---

## Order of operations

Do them in this order for maximum impact:
1. modelcontextprotocol/servers (biggest audience)
2. wong2/awesome-mcp-servers (large community list, use website not PR)
3. awesome-python (200k+ stars, huge reach)
4. awesome-fastapi (FastAPI-specific audience)
5-7. Smithery, PulseMCP, mcp.so (form submissions, can be done anytime)

---

## Future updates: what you need to do vs what's automatic

**Automatic (just push code to GitHub, no action needed):**
- PyPI version — only when you tag a new release (`git tag v1.0.1 && git push origin v1.0.1`)
- Glama score/tools — resyncs daily
- Smithery/PulseMCP/mcp.so — pull from GitHub on their schedule

**Manual (submit a new PR if you want to update the one-liner):**
- modelcontextprotocol/servers
- awesome-python
- awesome-fastapi

**Manual (submit updated info via their website form):**
- wong2/awesome-mcp-servers → https://mcpservers.org/submit
- Smithery → update via their dashboard
- PulseMCP → update via their dashboard
- mcp.so → update via their dashboard
````

## File: tests/test_extraction.py
````python
class TestExtractionModels
⋮----
def test_extracted_content_defaults(self)
⋮----
content = ExtractedContent(url="https://example.com")
⋮----
def test_extracted_content_full(self)
⋮----
content = ExtractedContent(
⋮----
class TestTrafilaturaExtractor
⋮----
@pytest.fixture(autouse=True)
    def _skip_without_trafilatura(self)
⋮----
@pytest.mark.asyncio
    async def test_trafilatura_success(self)
⋮----
result = await _extract_trafilatura("https://example.com")
⋮----
@pytest.mark.asyncio
    async def test_trafilatura_fetch_fails(self)
⋮----
@pytest.mark.asyncio
    async def test_trafilatura_no_content(self)
class TestJinaExtractor
⋮----
@pytest.mark.asyncio
    async def test_jina_success(self)
⋮----
mock_response = MagicMock()
⋮----
mock_client = AsyncMock()
⋮----
result = await _extract_jina("https://example.com")
⋮----
@pytest.mark.asyncio
    async def test_jina_too_short(self)
def _good_text(n: int = 150) -> str
_BAD_RESULT = ExtractedContent(url="https://example.com", error="failed")
_CHAIN_EXTRACTORS = [
⋮----
@pytest.fixture
def mock_chain()
⋮----
patches = []
mocks = {}
⋮----
p = patch(f"{module_path}.{func_name}", new_callable=AsyncMock, return_value=_BAD_RESULT)
m = p.start()
⋮----
class TestExtractUrl
⋮----
@pytest.mark.asyncio
    async def test_trafilatura_primary_no_fallback(self, mock_chain)
⋮----
good_result = ExtractedContent(
⋮----
result = await extract_url("https://example.com")
⋮----
@pytest.mark.asyncio
    async def test_falls_back_to_jina(self, mock_chain)
⋮----
@pytest.mark.asyncio
    async def test_all_extractors_fail(self, mock_chain)
⋮----
@pytest.mark.asyncio
    async def test_ssrf_blocks_private_ip(self)
⋮----
result = await extract_url("http://192.168.1.1/admin")
⋮----
@pytest.mark.asyncio
    async def test_quality_gate_rejects_short_content(self, mock_chain)
⋮----
short_result = ExtractedContent(
⋮----
@pytest.mark.asyncio
    async def test_extractors_tried_tracked(self, mock_chain)
class TestExtractionCache
⋮----
def test_put_and_get(self)
⋮----
cache = ExtractionCache(ttl_hours=1)
⋮----
result = cache.get("https://example.com")
⋮----
def test_cache_miss(self)
⋮----
cache = ExtractionCache()
⋮----
def test_cache_normalizes_url(self)
⋮----
content = ExtractedContent(url="https://example.com", text="hi", word_count=1)
⋮----
def test_cache_ttl_expires(self)
⋮----
cache = ExtractionCache(ttl_hours=0)
⋮----
def test_cache_skips_errors(self)
⋮----
content = ExtractedContent(url="https://example.com", error="failed")
⋮----
def test_cache_clear(self)
def test_cache_strips_trailing_slash(self)
class TestDomainRateLimiter
⋮----
def test_allows_within_limit(self)
⋮----
limiter = DomainRateLimiter(max_requests=3, window_seconds=60)
⋮----
def test_blocks_over_limit(self)
⋮----
limiter = DomainRateLimiter(max_requests=2, window_seconds=60)
⋮----
def test_separate_domains_independent(self)
⋮----
limiter = DomainRateLimiter(max_requests=1, window_seconds=60)
⋮----
def test_window_expires(self)
⋮----
limiter = DomainRateLimiter(max_requests=1, window_seconds=0)
⋮----
def test_invalid_url_allowed(self)
def test_clear(self)
class TestExtractUrlWithCache
⋮----
@pytest.mark.asyncio
    async def test_cached_result_returned(self)
⋮----
result = await extract_url("https://cached.example.com")
⋮----
@pytest.mark.asyncio
    async def test_domain_rate_limit_blocks(self)
⋮----
result = await extract_url("https://limited.example.com/other")
````

## File: .gitignore
````
# Python
__pycache__/
*.py[cod]
*.egg-info/
dist/
build/
.eggs/
*.egg

# Virtual environments
.venv/
venv/

# Environment
.env
.env.bak
.env.local
!.env.example

# Testing
.pytest_cache/
.coverage
htmlcov/

# IDE
.vscode/
.idea/
*.swp
*.swo
*~

# OS
.DS_Store
Thumbs.db

# Budget persistence
argus_budgets.db
argus.db
argus.db-shm
argus.db-wal

# AI tooling (local config, not project code)
.claude/
.janitor/
.mcp.json
.opencode/
CLAUDE.local.md

# Local operator/session artifacts
1shot/
docs/sessions/
.mcpregistry_*
````

## File: docker-compose.yml
````yaml
services:
  argus:
    build: .
    image: argus:latest
    ports:
      - "${ARGUS_PORT:-8000}:8000"
    env_file:
      - .env
    environment:
      - ARGUS_HOST=0.0.0.0
      - ARGUS_PORT=8000
      - ARGUS_BUDGET_DB_PATH=/app/argus_budgets.db
    volumes:
      - argus-data:/app
    restart: unless-stopped
    healthcheck:
      test: ["CMD", "python", "-c", "import urllib.request; urllib.request.urlopen('http://localhost:8000/api/health')"]
      interval: 30s
      timeout: 5s
      retries: 3
      start_period: 10s
volumes:
  argus-data:
````

## File: llms-full.txt
````
# Argus

> Argus is a multi-provider web search broker for AI agents. It routes queries across 11 providers with tier-based credit routing, Reciprocal Rank Fusion ranking, content extraction, and budget enforcement. Works immediately with DuckDuckGo — no API keys needed. Add API keys for 5,000+ free monthly queries. Connect via HTTP, CLI, MCP server, or Python import.

## What It Does

> You pass Argus a search query. It fans out to multiple providers (prioritizing free ones), collects results, ranks them via Reciprocal Rank Fusion (k=60), deduplicates by URL, and returns a clean list. If a provider is down or over budget, it skips it automatically. Your code never touches a provider API directly.
> Output is designed for LLM consumption — title, snippet, URL, domain, provider, and relevance score for each result.

## Installation

> pip install argus-search works immediately with DuckDuckGo on any machine with Python 3.11+
> pip install "argus-search[mcp]" adds MCP server support
> pip install "argus-search[mcp,crawl4ai]" adds local JS rendering (needs playwright + crawl4ai)
> Docker: docker compose up -d for SearXNG + Argus

## Providers

> Tier 0 (free, unlimited): SearXNG (self-hosted), DuckDuckGo (no key), GitHub (free, rate-limited)
> Tier 1 (monthly recurring): Brave (2k/mo), Tavily (1k/mo), Exa (1k/mo), Linkup (1k/mo)
> Tier 3 (one-time credits): Serper (2.5k), Parallel AI (4k), You.com ($20), Valyu ($10)
> Routing: always queries free providers first, then monthly, then one-time. Exhausted providers skipped.

## Content Extraction

> 9-step fallback chain with quality gates: trafilatura → Crawl4AI → Playwright → Jina Reader → Valyu Contents → Firecrawl → You.com Contents → Wayback Machine → archive.is
> First 3 are local (need hosting). Last 6 are external APIs (work anywhere).
> extract_content for working URLs. recover_url for dead/moved URLs (queries archives and runs extraction loop).
> SSRF protection blocks private IPs. Domain rate limiting at 10 req/min/domain. Cookie auth for paywalls.

## Multi-Turn Sessions

> Pass session_id to any search call. Broker stores queries and extracted URLs in SQLite. Follow-up searches are context-enriched from prior conversation. Persists across restarts.

## Integration

### HTTP API

> argus serve on port 8000. POST /api/search {"query": "...", "mode": "discovery", "max_results": 10, "session_id": "..."}
> POST /api/extract {"url": "..."}. POST /api/recover-url {"url": "...", "title": "..."}
> GET /api/health/detail, GET /api/budgets. OpenAPI at /docs.

### CLI

> argus search -q "query" --mode discovery --session my-session --json
> argus extract -u "https://example.com" -d nytimes.com
> argus recover-url -u "https://dead.link" -t "Title"
> argus health, argus budgets, argus set-balance -s jina -b 9833638, argus test-provider -p brave

### MCP

> argus mcp serve (stdio) or argus mcp serve --transport sse --port 8001
> Tools: search_web, extract_content, recover_url, expand_links, search_health, search_budgets, test_provider, valyu_answer, cookie_health
> Resources: argus://providers/status, argus://providers/budgets, argus://policies/current
> Config: {"mcpServers": {"argus": {"command": "argus", "args": ["mcp", "serve"]}}}
> MCP Registry: io.github.Khamel83/argus

### Python

> from argus.broker.router import create_broker; from argus.models import SearchQuery, SearchMode
> from argus.extraction import extract_url
> broker = create_broker()
> response = await broker.search(SearchQuery(query="...", mode=SearchMode.DISCOVERY, max_results=10))
> for r in response.results: print(f"{r.title}: {r.url} (score: {r.score:.3f})")
> content = await extract_url("https://example.com")

## Search Modes

> discovery: related pages and canonical sources. Free providers always lead.
> recovery: find moved or dead URLs via archive sources.
> grounding: few live sources for fact-checking.
> research: broad exploratory retrieval.

## Configuration

> All config via ARGUS_ prefix env vars. Key variables: ARGUS_SEARXNG_BASE_URL, provider API keys (ARGUS_BRAVE_API_KEY etc.), ARGUS_CACHE_TTL_HOURS (168), per-provider budgets (ARGUS_BRAVE_MONTHLY_BUDGET_USD etc.), ARGUS_CRAWL4AI_ENABLED, ARGUS_EXTRACTION_TIMEOUT_SECONDS.
> See .env.example for full list. Missing keys degrade gracefully — providers are skipped, not errors.

## Architecture

> Caller (CLI/HTTP/MCP/Python) → SearchBroker → routing policy (tier-sorted, mode-specific) → provider executor (budget check → health check → search → early stop) → cache → dedupe → RRF ranking → response
> Provider adapters in argus/providers/. Broker in argus/broker/. Extractor in argus/extraction/. API in argus/api/. CLI in argus/cli/. MCP in argus/mcp/. Sessions in argus/sessions/.

## Documentation

> README.md — product page with quickstart, providers, integration, configuration
> docs/providers.md — provider setup and configuration details
> CONTRIBUTING.md — contribution guide and adding providers
> CLAUDE.md — developer guide and architecture overview
> CHANGELOG.md — release history
````

## File: llms.txt
````
# Argus

> Multi-provider web search broker for AI agents. Routes across 11 providers (SearXNG, DuckDuckGo, GitHub, Brave, Serper, Tavily, Exa, Linkup, Parallel AI, You.com, Valyu) with tier-based routing, RRF ranking, content extraction, and budget enforcement. Works immediately with DuckDuckGo (no API keys). 5,000+ free monthly queries with API keys.

## Installation

> pip install argus-search && argus search -q "query"
> pip install "argus-search[mcp]" for MCP server support
> pip install "argus-search[mcp,crawl4ai]" for local JS rendering extraction
> Zero config — DuckDuckGo handles search with no keys, no containers, no accounts

## MCP Server

> argus mcp serve (stdio) or argus mcp serve --transport sse --port 8001 (HTTP)
> Tools: search_web, extract_content, recover_url, expand_links, search_health, search_budgets, test_provider, valyu_answer, cookie_health
> Resources: argus://providers/status, argus://providers/budgets, argus://policies/current
> Claude Code config: {"mcpServers": {"argus": {"command": "argus", "args": ["mcp", "serve"]}}}
> Also on MCP Registry: io.github.Khamel83/argus

## HTTP API

> argus serve starts on port 8000. OpenAPI docs at /docs.
> POST /api/search {"query": "...", "mode": "discovery", "max_results": 10}
> POST /api/extract {"url": "..."}
> POST /api/recover-url {"url": "...", "title": "..."}
> POST /api/expand {"query": "..."}
> GET /api/health/detail, GET /api/budgets

## CLI

> argus search -q "query" --mode discovery --session my-session
> argus extract -u "https://example.com" -d nytimes.com
> argus recover-url -u "https://dead.link" -t "Page Title"
> argus health, argus budgets, argus test-provider -p brave
> argus set-balance -s jina -b 9833638

## Search Modes

> discovery: related pages, canonical sources. Recovery: find moved/dead URLs. Grounding: few sources for fact-checking. Research: broad exploratory retrieval.
> Routing: Tier 0 (free: SearXNG, DuckDuckGo, GitHub) → Tier 1 (monthly: Brave 2k, Tavily 1k, Exa 1k, Linkup 1k) → Tier 3 (one-time: Serper 2.5k, Parallel 4k, You.com $20, Valyu $10)
> Budget-exhausted providers are skipped automatically

## Content Extraction

> 9-step fallback chain: trafilatura → Crawl4AI → Playwright → Jina → Valyu Contents → Firecrawl → You.com Contents → Wayback → archive.is
> Quality gates between every step. SSRF protection. Domain rate limiting (10 req/min). Cookie auth for paywalls.

## Multi-Turn Sessions

> Pass session_id to search for conversational refinement. Prior queries enrich follow-up searches. SQLite-backed, persists across restarts.

## Python SDK

> from argus.broker.router import create_broker; from argus.models import SearchQuery, SearchMode
> broker = create_broker()
> response = await broker.search(SearchQuery(query="...", mode=SearchMode.DISCOVERY, max_results=10))
> content = await extract_url("https://example.com")

## Documentation

> README.md — full docs with quickstart, providers, integration, configuration
> docs/providers.md — provider setup and configuration
> CONTRIBUTING.md — contribution guide and adding providers
> CLAUDE.md — developer guide and architecture overview
````

## File: argus/broker/budget_persistence.py
````python
logger = get_logger("broker.budget_persistence")
DEFAULT_DB_PATH = "argus_budgets.db"
_SCHEMA = """
class BudgetStore
⋮----
def __init__(self, db_path: Optional[str] = None)
def _get_conn(self) -> sqlite3.Connection
def record_usage(self, provider: str, cost_usd: float = 0.0) -> None
⋮----
conn = self._get_conn()
⋮----
def get_monthly_usage(self, provider: str) -> float
⋮----
cutoff = time.time() - (30 * 24 * 3600)
⋮----
row = conn.execute(
⋮----
def get_usage_count(self, provider: str) -> int
def close(self) -> None
def set_token_balance(self, service: str, balance: float) -> None
def get_token_balance(self, service: str) -> Optional[float]
def get_all_token_balances(self) -> dict
⋮----
rows = conn.execute("SELECT service, balance, updated_at FROM token_balances").fetchall()
````

## File: argus/broker/budgets.py
````python
logger = get_logger("broker.budgets")
PROVIDER_TIERS: dict[ProviderName, int] = {
class BudgetTracker
⋮----
def __init__(self, persist_path: Optional[str] = None)
def _load_from_store(self) -> None
⋮----
cutoff = time.time() - (30 * 24 * 3600)
⋮----
conn = self._store._get_conn()
rows = conn.execute(
⋮----
pname = PN(provider_str)
⋮----
def set_budget(self, provider: ProviderName, monthly_budget: float) -> None
def record_usage(self, provider: ProviderName, cost: float = 1.0) -> None
def get_monthly_usage(self, provider: ProviderName) -> float
⋮----
entries = [c for t, c in self._usage[provider] if t >= cutoff]
⋮----
def get_remaining_budget(self, provider: ProviderName) -> Optional[float]
⋮----
budget = self._budgets.get(provider)
⋮----
def is_budget_exhausted(self, provider: ProviderName) -> bool
⋮----
remaining = self.get_remaining_budget(provider)
⋮----
def used_today(self, provider: ProviderName) -> int
⋮----
cutoff = time.time() - (24 * 3600)
⋮----
def daily_pace(self, provider: ProviderName) -> float
def is_over_pace(self, provider: ProviderName) -> bool
def get_usage_count(self, provider: ProviderName) -> int
def get_provider_tier(self, provider: ProviderName) -> int
def check_status(self, provider: ProviderName) -> Optional[ProviderStatus]
def close(self) -> None
````

## File: argus/broker/policies.py
````python
MODE_PROVIDER_PREFERENCES: dict[SearchMode, list[ProviderName]] = {
def get_provider_order(mode: SearchMode) -> list[ProviderName]
⋮----
preferences = MODE_PROVIDER_PREFERENCES.get(
tier_sorted = sorted(preferences, key=lambda p: PROVIDER_TIERS.get(p, 99))
⋮----
tier_sorted = sorted(override_providers, key=lambda p: PROVIDER_TIERS.get(p, 99))
````

## File: argus/extraction/auth_extractor.py
````python
logger = get_logger("extraction.auth")
AUTH_TIMEOUT_MS = 15_000
_browser = None
_contexts: dict[str, object] = {}
class ExtractorName(str)
⋮----
AUTH = "auth_playwright"
AUTH_EXTRACTOR = ExtractorName("auth_playwright")
async def _get_browser()
⋮----
pw = await async_playwright().start()
_browser = await pw.chromium.launch(headless=True)
⋮----
async def _get_context(domain: str)
⋮----
browser = await _get_browser()
⋮----
cookie_path = get_cookie_path(domain)
⋮----
cookies = load_editthiscookie_json(cookie_path)
⋮----
context = await browser.new_context(
⋮----
async def extract_authenticated(url: str, domain: str) -> Optional[ExtractedContent]
⋮----
context = await _get_context(domain)
⋮----
status_code = 0
⋮----
page = await context.new_page()
⋮----
response = await page.goto(
status_code = response.status if response else 0
⋮----
html = await page.content()
⋮----
loop = asyncio.get_event_loop()
extracted = await loop.run_in_executor(None, _extract_from_html, html)
⋮----
# Also grab the title from the page
title = await page.title()
word_count = len(extracted.split())
⋮----
def _extract_from_html(html: str) -> str
⋮----
result = trafilatura.bare_extraction(html)
````

## File: argus/extraction/models.py
````python
class ExtractorName(str, Enum)
⋮----
TRAFILATURA = "trafilatura"
JINA = "jina"
PLAYWRIGHT = "playwright"
WAYBACK = "wayback"
ARCHIVE_IS = "archive_is"
AUTH = "auth"
CRAWL4AI = "crawl4ai"
YOU_CONTENTS = "you_contents"
VALYU_CONTENTS = "valyu_contents"
FIRECRAWL = "firecrawl"
⋮----
@dataclass
class ExtractedContent
⋮----
url: str
title: str = ""
text: str = ""
author: str = ""
date: Optional[str] = None
word_count: int = 0
extracted_at: datetime = field(default_factory=lambda: datetime.now(tz=None))
extractor: Optional[ExtractorName] = None
error: Optional[str] = None
quality_passed: bool = True
quality_reason: Optional[str] = None
extractors_tried: list = field(default_factory=list)
````

## File: Dockerfile
````dockerfile
FROM python:3.12-slim AS builder

WORKDIR /app

# Install build deps (needed for psycopg2-binary, lxml, etc.)
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc libxml2-dev libxslt1-dev && \
    rm -rf /var/lib/apt/lists/*

COPY pyproject.toml ./
COPY argus/ ./argus/
RUN pip install --no-cache-dir --prefix=/install ".[mcp]"

# --- Final image ---
FROM python:3.12-slim

WORKDIR /app

# Runtime deps for lxml
RUN apt-get update && apt-get install -y --no-install-recommends \
    libxml2 libxslt1.1 && \
    rm -rf /var/lib/apt/lists/*

# Copy installed packages from builder
COPY --from=builder /install /usr/local

# Copy application code
COPY argus/ ./argus/

# Create non-root user
RUN useradd -m -s /bin/sh argus && chown -R argus:argus /app
USER argus

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/api/health')" || exit 1

CMD ["uvicorn", "argus.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
````

## File: tests/test_broker.py
````python
@dataclass
class StubProvider
⋮----
name: ProviderName
results: list[SearchResult] | None = None
trace: ProviderTrace | None = None
available: bool = True
raise_error: Exception | None = None
def __post_init__(self)
def is_available(self) -> bool
def status(self) -> ProviderStatus
async def search(self, query: SearchQuery)
class TestPolicies
⋮----
def test_recovery_order(self)
⋮----
order = get_provider_order(SearchMode.RECOVERY)
⋮----
def test_discovery_order(self)
⋮----
order = get_provider_order(SearchMode.DISCOVERY)
⋮----
def test_grounding_order(self)
⋮----
order = get_provider_order(SearchMode.GROUNDING)
⋮----
def test_research_order(self)
⋮----
order = get_provider_order(SearchMode.RESEARCH)
⋮----
def test_tier_sorting_free_first(self)
⋮----
order = get_provider_order(mode)
⋮----
def test_tier_sorting_monthly_before_onetime(self)
⋮----
"""Tier 1 (monthly) should always come before tier 3 (one-time)."""
⋮----
searxng_idx = order.index(ProviderName.SEARXNG)
# Find first tier-1 and first tier-3 provider
⋮----
first_monthly = None
first_onetime = None
⋮----
tier = PROVIDER_TIERS.get(p, 99)
⋮----
first_monthly = p
⋮----
first_onetime = p
⋮----
def test_override_providers_sorted_by_tier(self)
⋮----
"""Override provider lists should also be tier-sorted."""
⋮----
# Serper (tier 3) before Brave (tier 1) in override -> should reorder
override = [ProviderName.SERPER, ProviderName.BRAVE]
result = resolve_routing(SearchMode.DISCOVERY, override)
⋮----
def test_no_override_uses_policy(self)
⋮----
result = resolve_routing(SearchMode.DISCOVERY, None)
⋮----
# --- Ranking ---
class TestRanking
⋮----
def test_rrf_basic(self)
⋮----
provider_results = {
merged = reciprocal_rank_fusion(provider_results)
# https://a.com/1 appears in both providers, should rank higher
⋮----
def test_rrf_single_provider(self)
⋮----
results = [
merged = reciprocal_rank_fusion({"p": results})
⋮----
def test_rrf_empty(self)
⋮----
merged = reciprocal_rank_fusion({})
⋮----
class TestDedupe
⋮----
def test_dedupes_same_url(self)
⋮----
deduped = dedupe_results(results)
⋮----
def test_dedupes_www_prefix(self)
def test_dedupes_trailing_slash(self)
def test_keeps_distinct_urls(self)
# --- URL Normalization ---
class TestUrlNormalization
⋮----
def test_normalizes_case(self)
⋮----
result = normalize_url("https://EXAMPLE.com")
⋮----
def test_strips_tracking_params(self)
⋮----
url = "https://example.com/page?utm_source=fb&ref=abc&id=1"
normalized = normalize_url(url)
⋮----
def test_strips_www(self)
⋮----
result = normalize_url("https://www.example.com")
⋮----
def test_sorts_query_params(self)
⋮----
url = normalize_url("https://example.com?b=2&a=1")
⋮----
class TestCache
⋮----
def test_put_and_get(self)
⋮----
cache = SearchCache(ttl_hours=1)
resp = SearchResponse(query="test", mode=SearchMode.DISCOVERY, results=[])
⋮----
def test_cache_miss(self)
⋮----
cache = SearchCache()
⋮----
def test_different_modes_separate(self)
⋮----
r1 = SearchResponse(query="test", mode=SearchMode.DISCOVERY, results=[])
r2 = SearchResponse(query="test", mode=SearchMode.GROUNDING, results=[])
⋮----
def test_case_insensitive(self)
def test_clear(self)
class TestHealth
⋮----
def test_initial_state(self)
⋮----
h = HealthTracker()
status = h.get_status(ProviderName.BRAVE)
⋮----
def test_success_resets_failures(self)
⋮----
h = HealthTracker(failure_threshold=2)
⋮----
health = h.get_health(ProviderName.BRAVE)
⋮----
def test_degraded_after_threshold(self)
⋮----
h = HealthTracker(failure_threshold=3)
⋮----
def test_degraded_when_cooldown_expires(self)
⋮----
h = HealthTracker(failure_threshold=2, cooldown_minutes=60)
⋮----
def test_cooldown_applied_after_threshold(self)
def test_all_status(self)
⋮----
all_status = h.get_all_status()
⋮----
class TestBudgets
⋮----
def test_no_budget_unlimited(self)
⋮----
b = BudgetTracker()
⋮----
def test_set_budget_and_track(self)
def test_budget_exhausted(self)
def test_usage_count(self)
def test_check_status(self)
def test_daily_pace_calculation(self)
def test_over_pace_detection(self)
def test_unlimited_provider_never_over_pace(self)
class TestRouter
⋮----
def test_create_broker(self)
⋮----
broker = create_broker()
⋮----
@pytest.mark.asyncio
    async def test_free_providers_always_queried(self, monkeypatch)
⋮----
searxng = StubProvider(
ddg = StubProvider(
paid = StubProvider(
broker = SearchBroker(
response = await broker.search(
⋮----
@pytest.mark.asyncio
    async def test_paid_provider_skipped_when_over_pace(self, monkeypatch)
⋮----
free = StubProvider(
⋮----
budget = BudgetTracker()
⋮----
@pytest.mark.asyncio
    async def test_paid_provider_used_when_under_pace(self, monkeypatch)
⋮----
@pytest.mark.asyncio
    async def test_one_time_credits_conserved_when_over_pace(self, monkeypatch)
⋮----
onetime = StubProvider(
⋮----
@pytest.mark.asyncio
    async def test_search_skips_budget_exhausted_provider(self, monkeypatch)
⋮----
backup = StubProvider(
exhausted_budget = BudgetTracker()
⋮----
@pytest.mark.asyncio
    async def test_search_handles_provider_exception_and_continues(self, monkeypatch)
⋮----
failing = StubProvider(
⋮----
@pytest.mark.asyncio
    async def test_persistence_failure_is_non_fatal(self, monkeypatch)
⋮----
primary = StubProvider(
broker = SearchBroker(providers={ProviderName.SEARXNG: primary})
````

## File: argus/api/main.py
````python
logger = get_logger("api")
⋮----
@asynccontextmanager
async def lifespan(app: FastAPI)
⋮----
broker = app.state.get_broker()
⋮----
def _build_rate_limiter() -> RateLimiter
⋮----
current = broker
factory = broker_factory or create_broker
def get_broker() -> SearchBroker
⋮----
current = factory()
⋮----
app = FastAPI(
⋮----
@app.middleware("http")
    async def rate_limit_middleware(request: Request, call_next)
⋮----
client_ip = request.client.host if request.client else "unknown"
api_key_header = request.headers.get("x-api-key")
⋮----
response = await call_next(request)
⋮----
@app.middleware("http")
    async def add_request_id(request: Request, call_next)
⋮----
request_id = request.headers.get("x-request-id", uuid.uuid4().hex[:16])
⋮----
app = create_app()
````

## File: argus/mcp/server.py
````python
logger = get_logger("mcp.server")
def serve_mcp(transport: str = "stdio", host: str = "127.0.0.1", port: int = 8001)
⋮----
broker = create_broker()
mcp = FastMCP("argus", host=host, port=port)
⋮----
@mcp.tool()
    async def search_web(query: str, mode: str = "discovery", max_results: int = 10, session_id: str = None) -> str
⋮----
@mcp.tool()
    async def recover_url(url: str, title: str = None, domain: str = None) -> str
⋮----
@mcp.tool()
    async def expand_links(query: str, context: str = None) -> str
⋮----
@mcp.tool()
    def search_health() -> str
⋮----
@mcp.tool()
    def search_budgets() -> str
⋮----
@mcp.tool()
    async def test_provider(provider: str, query: str = "argus") -> str
⋮----
@mcp.tool()
    async def extract_content(url: str, domain: str = None) -> str
⋮----
@mcp.tool()
    async def valyu_answer(query: str, fast_mode: bool = False) -> str
⋮----
@mcp.tool()
    def cookie_health() -> str
⋮----
@mcp.resource("argus://providers/status")
    def provider_status() -> str
⋮----
@mcp.resource("argus://providers/budgets")
    def provider_budgets() -> str
⋮----
@mcp.resource("argus://policies/current")
    def routing_policies() -> str
````

## File: argus/config.py
````python
@dataclass(frozen=True)
class SearXNGConfig
⋮----
enabled: bool = True
base_url: str = "http://127.0.0.1:8080"
timeout_seconds: int = 12
⋮----
@dataclass(frozen=True)
class ProviderConfig
⋮----
enabled: bool = False
api_key: str = ""
monthly_budget_usd: float = 0.0
timeout_seconds: int = 15
⋮----
@dataclass(frozen=True)
class ArgusConfig
⋮----
"""All Argus settings, loaded from environment."""
env: str = "development"
log_level: str = "INFO"
db_url: str = ""
cache_ttl_hours: int = 168
disable_provider_after_failures: int = 5
provider_cooldown_minutes: int = 60
default_max_results: int = 10
searxng: SearXNGConfig = field(default_factory=SearXNGConfig)
brave: ProviderConfig = field(default_factory=ProviderConfig)
serper: ProviderConfig = field(default_factory=ProviderConfig)
tavily: ProviderConfig = field(default_factory=ProviderConfig)
exa: ProviderConfig = field(default_factory=ProviderConfig)
searchapi: ProviderConfig = field(default_factory=ProviderConfig)
you: ProviderConfig = field(default_factory=ProviderConfig)
parallel: ProviderConfig = field(default_factory=ProviderConfig)
linkup: ProviderConfig = field(default_factory=ProviderConfig)
valyu: ProviderConfig = field(default_factory=ProviderConfig)
github: ProviderConfig = field(default_factory=ProviderConfig)
host: str = "127.0.0.1"
port: int = 8000
allow_mcp: bool = False
allow_web_ui: bool = False
log_full_results: bool = False
log_provider_payloads: bool = False
class SecretsResolver
⋮----
def get(self, key: str) -> str
class SubprocessSecretsResolver(SecretsResolver)
⋮----
"""Fetch optional secrets from an external `secrets get` helper."""
⋮----
result = subprocess.run(
⋮----
class EnvironmentConfigLoader
⋮----
value = self._environ.get(key, "")
⋮----
secret = self._secrets.get(secret_key)
⋮----
def get_bool(self, key: str, default: bool = False) -> bool
⋮----
value = self._environ.get(key, "").lower()
⋮----
def get_int(self, key: str, default: int = 0) -> int
⋮----
value = self._environ.get(key)
⋮----
def get_float(self, key: str, default: float = 0.0) -> float
⋮----
def load(self) -> ArgusConfig
⋮----
_config: Optional[ArgusConfig] = None
def get_config(*, force_reload: bool = False) -> ArgusConfig
⋮----
_config = load_config()
⋮----
def reset_config() -> None
⋮----
_config = None
````

## File: argus/broker/router.py
````python
logger = get_logger("broker.router")
class SearchBroker
⋮----
budget_map = {
⋮----
@property
    def cache(self) -> SearchCache
⋮----
@property
    def health_tracker(self) -> HealthTracker
⋮----
@property
    def budget_tracker(self) -> BudgetTracker
async def search(self, query: SearchQuery) -> SearchResponse
⋮----
cache_run_id = os.urandom(8).hex()
cached = self._pipeline.get_cached(query, cache_run_id)
⋮----
provider_order = resolve_routing(query.mode, query.providers)
outcome = await self._executor.execute(query, provider_order)
response = self._pipeline.build_response(
⋮----
def get_provider_status(self, provider: ProviderName) -> dict
⋮----
provider_obj = self._providers.get(provider)
base_status = provider_obj.status() if provider_obj else "unknown"
health_status = self._health.get_status(provider)
budget_status = self._budgets.check_status(provider)
effective = base_status
⋮----
effective = health_status.value
⋮----
effective = budget_status.value
⋮----
def create_broker() -> SearchBroker
⋮----
config = get_config()
providers: dict[ProviderName, BaseProvider] = {
⋮----
session_store = SessionStore()
````

## File: argus/models.py
````python
class SearchMode(str, Enum)
⋮----
RECOVERY = "recovery"
DISCOVERY = "discovery"
GROUNDING = "grounding"
RESEARCH = "research"
class ProviderName(str, Enum)
⋮----
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
GITHUB = "github"
CACHE = "cache"
class ProviderStatus(str, Enum)
⋮----
ENABLED = "enabled"
DISABLED_BY_CONFIG = "disabled_by_config"
UNAVAILABLE_MISSING_KEY = "unavailable_missing_key"
TEMPORARILY_DISABLED = "temporarily_disabled_after_failures"
BUDGET_EXHAUSTED = "budget_exhausted"
DEGRADED = "degraded"
HEALTHY = "healthy"
⋮----
@dataclass
class SearchQuery
⋮----
query: str
mode: SearchMode = SearchMode.DISCOVERY
max_results: int = 10
providers: Optional[List[ProviderName]] = None
⋮----
@dataclass
class SearchResult
⋮----
url: str
title: str
snippet: str
domain: str = ""
provider: Optional[ProviderName] = None
score: float = 0.0
raw_rank: int = 0
metadata: Dict[str, Any] = field(default_factory=dict)
⋮----
@dataclass
class ProviderTrace
⋮----
"""Metadata about a single provider call within a search run."""
provider: ProviderName
status: str  # "success", "error", "skipped"
results_count: int = 0
latency_ms: int = 0
error: Optional[str] = None
budget_remaining: Optional[float] = None
credit_info: Optional[dict] = None
⋮----
@dataclass
class SearchResponse
⋮----
mode: SearchMode
results: List[SearchResult]
traces: List[ProviderTrace] = field(default_factory=list)
total_results: int = 0
cached: bool = False
search_run_id: Optional[str] = None
created_at: datetime = field(default_factory=lambda: datetime.now(tz=None))
budget_warnings: List[str] = field(default_factory=list)
````

## File: .env.example
````
# Argus core
ARGUS_ENV=development
ARGUS_LOG_LEVEL=INFO

# Database
ARGUS_DB_URL=postgresql+psycopg2://postgres:postgres@localhost:5432/argus

# Broker behavior
ARGUS_CACHE_TTL_HOURS=168
ARGUS_DISABLE_PROVIDER_AFTER_FAILURES=5
ARGUS_PROVIDER_COOLDOWN_MINUTES=60
ARGUS_DEFAULT_MAX_RESULTS=10

# SearXNG
ARGUS_SEARXNG_ENABLED=true
ARGUS_SEARXNG_BASE_URL=http://127.0.0.1:8080
ARGUS_SEARXNG_TIMEOUT_SECONDS=12

# Brave
ARGUS_BRAVE_ENABLED=true
ARGUS_BRAVE_API_KEY=
ARGUS_BRAVE_MONTHLY_BUDGET_USD=5
ARGUS_BRAVE_TIMEOUT_SECONDS=15

# Serper
ARGUS_SERPER_ENABLED=true
ARGUS_SERPER_API_KEY=
ARGUS_SERPER_MONTHLY_BUDGET_USD=0
ARGUS_SERPER_TIMEOUT_SECONDS=15

# Tavily
ARGUS_TAVILY_ENABLED=true
ARGUS_TAVILY_API_KEY=
ARGUS_TAVILY_MONTHLY_BUDGET_USD=0
ARGUS_TAVILY_TIMEOUT_SECONDS=20

# Exa
ARGUS_EXA_ENABLED=true
ARGUS_EXA_API_KEY=
ARGUS_EXA_MONTHLY_BUDGET_USD=0
ARGUS_EXA_TIMEOUT_SECONDS=20

# Optional providers
ARGUS_SEARCHAPI_ENABLED=false
ARGUS_SEARCHAPI_API_KEY=
ARGUS_SEARCHAPI_MONTHLY_BUDGET_USD=0

ARGUS_YOU_ENABLED=false
ARGUS_YOU_API_KEY=
ARGUS_YOU_MONTHLY_BUDGET_USD=0
# You.com Contents API for extraction ($1/1k pages, uses same key)
# ARGUS_YOU_CONTENTS_ENABLED=true

ARGUS_PARALLEL_ENABLED=false
ARGUS_PARALLEL_API_KEY=
ARGUS_PARALLEL_MONTHLY_BUDGET_USD=0
ARGUS_PARALLEL_TIMEOUT_SECONDS=15

ARGUS_LINKUP_ENABLED=false
ARGUS_LINKUP_API_KEY=
ARGUS_LINKUP_MONTHLY_BUDGET_USD=0
ARGUS_LINKUP_TIMEOUT_SECONDS=15

ARGUS_VALYU_ENABLED=false
ARGUS_VALYU_API_KEY=
ARGUS_VALYU_MONTHLY_BUDGET_USD=0
ARGUS_VALYU_TIMEOUT_SECONDS=15

# GitHub (free, 10 req/min unauthenticated, 30/min with token)
ARGUS_GITHUB_ENABLED=true
ARGUS_GITHUB_API_KEY=
ARGUS_GITHUB_TIMEOUT_SECONDS=15

# Crawl4AI (self-hosted extractor, no API key needed)
# ARGUS_CRAWL4AI_ENABLED=true

# Firecrawl (external extractor, 1 credit/page)
# ARGUS_FIRECRAWL_API_KEY=

# API / service
ARGUS_HOST=0.0.0.0
ARGUS_PORT=8000

# Optional interfaces
ARGUS_ALLOW_MCP=false
ARGUS_ALLOW_WEB_UI=false

# Logging / debug
ARGUS_LOG_FULL_RESULTS=false
ARGUS_LOG_PROVIDER_PAYLOADS=false

# Budget persistence (SQLite file path — needed for token balance tracking)
ARGUS_BUDGET_DB_PATH=argus_budgets.db

# API rate limiting (requests per window, per client IP)
ARGUS_RATE_LIMIT=60
ARGUS_RATE_LIMIT_WINDOW=60

# API key — set to bypass rate limiting (e.g. for internal services)
# ARGUS_API_KEY=

# Content extraction
ARGUS_EXTRACTION_TIMEOUT_SECONDS=10
# Extraction cache TTL (hours) — avoids re-extracting the same URL
ARGUS_EXTRACTION_CACHE_TTL_HOURS=168
# Domain rate limiting (max requests per domain per window)
ARGUS_EXTRACTION_DOMAIN_RATE_LIMIT=10
ARGUS_EXTRACTION_DOMAIN_WINDOW_SECONDS=60
# Jina Reader API key (optional — Jina works without a key but rate-limited)
# ARGUS_JINA_API_KEY=
````

## File: argus/cli/main.py
````python
logger = get_logger("cli")
def _run(coro)
⋮----
loop = asyncio.get_running_loop()
⋮----
loop = None
⋮----
@click.group()
@click.version_option(package_name="argus")
def cli()
⋮----
@cli.command()
@click.option("--query", "-q", required=True, help="Search query")
@click.option("--mode", "-m", default="discovery", type=click.Choice(["recovery", "discovery", "grounding", "research"]))
@click.option("--max-results", "-n", default=10, help="Max results")
@click.option("--providers", "-p", multiple=False, help="Override providers (comma-separated)")
@click.option("--session", "-s", default=None, help="Session ID for multi-turn context")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
def search(query, mode, max_results, providers, as_json, session)
⋮----
broker = create_broker()
q = SearchQuery(query=query, mode=SearchMode(mode), max_results=max_results)
⋮----
session_id = sid
⋮----
resp = _run(broker.search(q))
session_id = None
⋮----
data = {
⋮----
provider = f" [{r.provider.value}]" if r.provider else ""
⋮----
@cli.command()
@click.option("--url", "-u", required=True, help="URL to extract content from")
@click.option("--domain", "-d", help="Domain hint for authenticated extraction (e.g. nytimes.com)")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
def extract(url, as_json, domain)
⋮----
result = _run(extract_url(url, domain=domain))
⋮----
@cli.command(name="recover-url")
@click.option("--url", "-u", required=True, help="URL to recover")
@click.option("--title", "-t", help="Optional title hint")
@click.option("--domain", "-d", help="Optional domain hint")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
def recover_url(url, title, domain, as_json)
⋮----
query_parts = [url]
⋮----
q = SearchQuery(query=" ".join(query_parts), mode=SearchMode.RECOVERY, max_results=10)
⋮----
@cli.command()
def health()
⋮----
"""Show provider health status."""
⋮----
status = broker.get_provider_status(pname)
effective = status["effective_status"]
⋮----
all_health = broker.health_tracker.get_all_status()
⋮----
@cli.command()
def budgets()
⋮----
"""Show provider budget status."""
⋮----
remaining = broker.budget_tracker.get_remaining_budget(pname)
usage = broker.budget_tracker.get_monthly_usage(pname)
count = broker.budget_tracker.get_usage_count(pname)
exhausted = broker.budget_tracker.is_budget_exhausted(pname)
budget_str = f"${remaining:.4f}" if remaining is not None else "unlimited"
status = "EXHAUSTED" if exhausted else "ok"
⋮----
# Token balances
store = broker.budget_tracker._store
⋮----
balances = store.get_all_token_balances()
⋮----
@cli.command("check-balances")
def check_balances()
⋮----
api_keys = {}
⋮----
cfg = provider._config if hasattr(provider, "_config") else None
⋮----
balances = asyncio.get_event_loop().run_until_complete(check_all_balances(api_keys))
⋮----
limit_str = f"/{b.limit:.0f}" if b.limit else ""
⋮----
@cli.command()
@click.option("--service", "-s", required=True, help="Service name (e.g. jina)")
@click.option("--balance", "-b", required=True, type=float, help="Current token balance")
def set_balance(service, balance)
⋮----
@cli.command()
@click.option("--provider", "-p", required=True, help="Provider name")
@click.option("--query", "-q", default="argus", help="Test query")
def test_provider(provider, query)
⋮----
pname = ProviderName(provider)
⋮----
prov = broker._providers.get(pname)
⋮----
q = SearchQuery(query=query, mode=SearchMode.DISCOVERY, max_results=3)
⋮----
@cli.command()
@click.option("--host", "-h", default="0.0.0.0", help="Bind host")
@click.option("--port", "-p", default=8000, help="Bind port")
@click.option("--reload", is_flag=True, help="Auto-reload on code changes")
def serve(host, port, reload)
⋮----
@cli.group()
def mcp()
⋮----
@mcp.command(name="serve")
@click.option("--transport", "-t", default="stdio", type=click.Choice(["stdio", "sse"]))
@click.option("--host", "-h", default="0.0.0.0", help="Host for SSE transport")
@click.option("--port", "-p", default=8001, help="Port for SSE transport")
def mcp_serve(transport, host, port)
⋮----
@mcp.command(name="init")
@click.option("--global", "global_", is_flag=True, help="Add to ~/.claude.json (all projects)")
def mcp_init(global_)
⋮----
argus_bin = str(Path(sys.argv[0]).resolve())
entry = {
⋮----
config_path = Path.home() / ".claude.json"
scope = "global (~/.claude.json)"
⋮----
config_path = Path(".mcp.json")
scope = "project (.mcp.json)"
⋮----
data = json.loads(config_path.read_text()) if config_path.stat().st_size else {}
⋮----
data = {}
servers = data.setdefault("mcpServers", {})
⋮----
action = "Updated"
⋮----
action = "Added"
⋮----
@cli.group()
def cookies()
⋮----
"""Manage browser cookies for authenticated extraction."""
⋮----
@cookies.command(name="import")
@click.option("--domain", "-d", default=None, help="Domain (e.g. nytimes.com). Inferred from cookies if omitted.")
@click.option("--file", "-f", "filepath", default=None, type=click.Path(exists=True), help="EditThisCookie JSON file. If omitted, imports all from inbox.")
def cookies_import(domain, filepath)
⋮----
cookie_dir = COOKIE_DIR
inbox_dir = cookie_dir / "inbox"
⋮----
files = [Path(filepath)]
⋮----
files = sorted(inbox_dir.glob("*.json"))
⋮----
imported = 0
⋮----
# Load raw to infer domain
⋮----
raw = json.loads(f.read_text())
⋮----
# Get cookies list (handle both array and wrapped object)
⋮----
cookie_list = raw.get("cookies", [raw])
⋮----
cookie_list = raw
inferred = domain
⋮----
domains_seen = set()
⋮----
d = c.get("domain", "")
# Strip leading dots, skip empty
d = d.lstrip(".")
⋮----
# Get base domain (last 2 parts)
parts = d.split(".")
⋮----
base = ".".join(parts[-2:])
⋮----
inferred = Counter(domains_seen).most_common(1)[0][0]
dest = cookie_dir / f"{inferred}.json"
loaded = load_editthiscookie_json(f)
⋮----
health = _load_health()
⋮----
@cookies.command(name="health")
def cookies_health()
⋮----
summary = get_health_summary()
⋮----
status_emoji = "OK" if info["status"] == "healthy" else "STALE"
age = f"{info['days_since_used']}d ago" if info['days_since_used'] is not None else "never"
warning = " [REFRESH NEEDED]" if info.get("stale_warning") else ""
⋮----
# Show what cookies are available on disk
⋮----
files = sorted(f.stem for f in COOKIE_DIR.glob("*.json") if f.stem != "health")
````

## File: argus/extraction/extractor.py
````python
logger = get_logger("extraction")
DEFAULT_TIMEOUT = int(os.getenv("ARGUS_EXTRACTION_TIMEOUT_SECONDS", "10"))
JINA_READER_URL = "https://r.jina.ai/"
JINA_API_KEY = os.getenv("ARGUS_JINA_API_KEY", "")
# Shared cache — lives for the process lifetime
_cache = ExtractionCache(
_domain_limiter = DomainRateLimiter(
_quality_gate = QualityGate()
_jina_call_count = 0
_jina_accumulated_tokens = 0
_JINA_SYNC_INTERVAL = 10
_TOKENS_PER_WORD = 1.3
def _run_quality_gate(content: str, url: str, extractor_name: str) -> tuple[bool, str]
⋮----
evaluation = _quality_gate.evaluate(content, url, extractor=extractor_name)
⋮----
async def _extract_trafilatura(url: str) -> ExtractedContent
⋮----
"""Extract content using trafilatura (local, no API call)."""
⋮----
loop = asyncio.get_event_loop()
downloaded = await loop.run_in_executor(None, trafilatura.fetch_url, url)
⋮----
extracted = await loop.run_in_executor(None, trafilatura.bare_extraction, downloaded)
⋮----
text = extracted["text"]
⋮----
async def _extract_jina(url: str) -> ExtractedContent
⋮----
headers = {"Accept": "text/plain"}
⋮----
reader_url = f"{JINA_READER_URL}{url}"
⋮----
resp = await client.get(reader_url, headers=headers, follow_redirects=True)
⋮----
text = resp.text.strip()
⋮----
lines = text.split("\n", 1)
title = lines[0].lstrip("# ").strip() if lines else ""
body = lines[1].strip() if len(lines) > 1 else text
⋮----
def get_extraction_cache() -> ExtractionCache
⋮----
"""Return the shared extraction cache instance."""
⋮----
async def extract_url(url: str, domain: str = None) -> ExtractedContent
⋮----
"""Extract clean content from a URL using the integrated fallback chain.
    Chain:
      SSRF → cache → rate limit → auth → QG → trafilatura → QG →
      crawl4ai → QG → playwright → QG → jina → QG →
      valyu_contents → QG → firecrawl → QG → you_contents → QG →
      wayback → QG → archive.is → QG → return best result (even if all quality gates failed)
    Results are cached in memory — same URL within TTL returns cached result.
    """
# SSRF check
⋮----
# Cache check
cached = _cache.get(url)
⋮----
extractors_tried = []
best_result = None
def track_attempt(name: str, result: ExtractedContent)
⋮----
best_result = result
⋮----
result = await extract_authenticated(url, domain)
⋮----
result = await _extract_trafilatura(url)
⋮----
result = await extract_crawl4ai(url)
⋮----
result = await extract_playwright(url)
⋮----
result = await _extract_jina(url)
⋮----
result = await extract_valyu_contents(url)
⋮----
result = await extract_firecrawl(url)
⋮----
result = await extract_you_contents(url)
⋮----
result = await extract_wayback(url)
⋮----
result = await extract_archive_is(url)
⋮----
result = ExtractedContent(
⋮----
def _track_jina_usage(word_count: int) -> None
⋮----
estimated_tokens = int(word_count * _TOKENS_PER_WORD)
⋮----
store = BudgetStore()
current = store.get_token_balance("jina")
⋮----
new_balance = current - _jina_accumulated_tokens
````

## File: pyproject.toml
````toml
[build-system]
requires = ["setuptools>=68.0", "wheel"]
build-backend = "setuptools.build_meta"

[project]
name = "argus-search"
version = "1.3.3"
description = "One endpoint, every free search API. Search broker for AI agents with automatic fallback, RRF ranking, and budget enforcement. The LiteLLM of web search."
readme = "README.md"
license = {text = "MIT"}
requires-python = ">=3.11"
keywords = ["mcp", "mcp-server", "search", "search-api", "search-broker", "content-extraction", "ai-agents", "llm-tools", "web-search", "fastapi", "brave-search", "serper", "tavily", "exa", "searxng", "trafilatura", "jina", "you-com", "linkup", "parallel-ai", "crawl4ai"]
classifiers = [
    "Development Status :: 4 - Beta",
    "Intended Audience :: Developers",
    "License :: OSI Approved :: MIT License",
    "Programming Language :: Python :: 3",
    "Programming Language :: Python :: 3.11",
    "Programming Language :: Python :: 3.12",
    "Programming Language :: Python :: 3.13",
    "Topic :: Internet :: WWW/HTTP",
    "Topic :: Software Development :: Libraries :: Python Modules",
]
dependencies = [
    "fastapi>=0.115.0",
    "uvicorn[standard]>=0.30.0",
    "pydantic>=2.5.0",
    "sqlalchemy>=2.0.0",
    "psycopg2-binary>=2.9.0",
    "httpx>=0.27.0",
    "click>=8.1.0",
    "pyyaml>=6.0",
    "trafilatura>=1.6.0",
    "ddgs>=9.0",
    "playwright>=1.40.0",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.0.0",
    "pytest-asyncio>=0.23.0",
    "httpx>=0.27.0",
]
mcp = [
    "mcp>=1.0.0",
]
crawl4ai = [
    "crawl4ai>=0.4",
]

[project.scripts]
argus = "argus.cli.main:cli"

[project.urls]
Homepage = "https://github.com/Khamel83/argus"
Repository = "https://github.com/Khamel83/argus"
Documentation = "https://github.com/Khamel83/argus#readme"
Issues = "https://github.com/Khamel83/argus/issues"

[tool.setuptools.packages.find]
include = ["argus*"]

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
````

## File: CLAUDE.md
````markdown
# Project Instructions

## Overview

Search broker that puts every free search API in one place with intelligent credit-aware routing. Provider adapters: SearXNG and DuckDuckGo (free, unlimited, no API keys), GitHub (free, code search), Brave, Tavily, Exa, Linkup (monthly free tiers), Serper, Parallel AI, You.com, Valyu, SearchAPI. Tier-based routing: free providers first, monthly recurring next, one-time credits last. Budget enforcement skips exhausted providers automatically. 9-step content extraction fallback chain. Multi-turn sessions (SQLite). Connect via HTTP, CLI, MCP, or Python import.

## Two Deployment Tiers

### Tier 1: No server (API keys only)
- `pip install argus-search` — works immediately with DuckDuckGo
- Add API keys for 5,000+ more free monthly queries
- Extraction via external APIs only (Jina, Valyu Contents, Firecrawl, You.com Contents, Wayback)
- Runs on any machine with Python 3.11+ (laptop, Mac Mini, Pi, cloud VM)
- No Docker, no database server, no API keys required to start

### Tier 2: Full install on hardware you already have
- Raspberry Pi 3 (1GB): SearXNG + all search providers. Fits alongside Pi-hole (SearXNG ~512MB, Pi-hole ~100MB).
- Raspberry Pi 4 (4GB): Everything — SearXNG, all providers, Crawl4AI local JS extraction.
- Mac Mini M1+ (8GB): Full stack with headroom for other services.
- Any old laptop (4GB+): Full stack via Docker.
- Free cloud VM (1GB): SearXNG + search. No Crawl4AI (use external APIs for extraction).
- `docker compose up -d` for one-command setup

## Key Commands

```bash
# Setup
cp .env.example .env                    # configure providers and DB
pip install "argus-search[mcp]"         # install from PyPI (with MCP support)
pip install "argus-search[mcp,crawl4ai]" # with Crawl4AI extractor
# or from source: pip install -e ".[mcp]"

# Zero-config search (no API keys needed)
argus search -q "python web frameworks"  # uses DuckDuckGo automatically

# Run
argus serve                   # HTTP API on :8000
argus mcp serve               # MCP server (stdio)
argus mcp serve --transport sse --port 8001

# Search
argus search -q "query" --mode discovery
argus search -q "follow up" --session abc123   # multi-turn context

# Content Extraction
argus extract -u "https://example.com/article"

# Admin
argus health                  # provider status
argus budgets                 # budget status + token balances
argus set-balance -s jina -b 9833638   # set token balance for a service
argus test-provider -p brave

# Test
pytest tests/
```

## Architecture

```
Caller (CLI/HTTP/MCP/Python)
  → SearchBroker
    → routing policy (tier-sorted, mode-specific within tiers)
      → provider executor (budget check → health check → search → early stop)
    → result pipeline (cache → dedupe → RRF ranking → response)
  → SessionStore (optional, per-request)
    → query refinement from prior context
  → Extractor (on demand)
    → trafilatura → crawl4ai → playwright → jina →
      valyu_contents → firecrawl → you_contents → wayback → archive.is
```

| Module | Responsibility |
|--------|---------------|
| `argus/broker/` | Tier-based routing, ranking, dedup, caching, health, budgets |
| `argus/providers/` | Provider adapters (one per search API) |
| `argus/extraction/` | 9-step URL extraction fallback chain with quality gates |
| `argus/sessions/` | Multi-turn session store and query refinement |
| `argus/api/` | FastAPI HTTP endpoints |
| `argus/cli/` | Click CLI commands |
| `argus/mcp/` | MCP server for LLM integration |
| `argus/persistence/` | PostgreSQL query/result storage |

## Provider Tiers

| Tier | Providers | Credits |
|------|-----------|---------|
| 0 (free) | SearXNG, DuckDuckGo, GitHub | Unlimited, no API keys (GitHub rate-limited) |
| 1 (monthly) | Brave (2k/mo), Tavily (1k/mo), Exa (1k/mo), Linkup (1k/mo) | Recurring monthly |
| 3 (one-time) | Serper (2.5k), Parallel (4k), You.com ($20), SearchAPI, Valyu ($10) | Don't come back |

Routing sorts by tier first (free → monthly → one-time), then preserves mode-specific ordering within each tier. Budget enforcement skips exhausted providers automatically.

## Interfaces

| Interface | How to use |
|-----------|-----------|
| HTTP API | `POST /api/search`, `POST /api/extract`, `POST /api/recover-url`, `POST /api/expand` — OpenAPI at `/docs` |
| CLI | `argus search`, `argus extract`, `argus recover-url`, `argus health`, `argus budgets`, `argus set-balance` |
| MCP | `argus mcp serve` — tools: `search_web`, `extract_content`, `recover_url`, `expand_links`, `search_health`, `search_budgets`, `test_provider`, `valyu_answer` |
| MCP Registry | [registry.modelcontextprotocol.io](https://registry.modelcontextprotocol.io/servers/io.github.Khamel83/argus) — `io.github.Khamel83/argus` |
| Python | `from argus.broker.router import create_broker`, `from argus.extraction import extract_url`, `from argus.providers.valyu_answer import valyu_answer` |

## Search Modes

Each mode defines which providers are best suited for that query type. Routing sorts by credit tier first, then preserves mode-specific ordering within each tier. Budget-exhausted providers are skipped.

| Mode | Use case | Actual runtime order |
|------|----------|---------------------|
| `discovery` | Related pages, canonical sources | SearXNG → DuckDuckGo → GitHub → Brave → Exa → Tavily → Linkup → Serper → Parallel → You → Valyu |
| `recovery` | Dead/moved URL | SearXNG → DuckDuckGo → Brave → Tavily → Exa → Linkup → Serper → Parallel → You → Valyu |
| `grounding` | Few sources for fact-checking | SearXNG → DuckDuckGo → Brave → Linkup → Serper → Parallel → You → Valyu |
| `research` | Broad exploratory | SearXNG → DuckDuckGo → GitHub → Tavily → Exa → Brave → Linkup → Serper → Parallel → You → Valyu |

Free providers (SearXNG, DuckDuckGo) always lead. Within-tier ordering reflects provider strengths per query type.

## Content Extraction

9-step fallback chain with quality gates between every step:

```
trafilatura (local, fast) → Crawl4AI (local, JS rendering) →
Playwright (local, headless browser) → Jina Reader (external API) →
Valyu Contents ($0.001/URL) → Firecrawl (1 credit/page) →
You.com Contents ($1/1k pages) → Wayback Machine → archive.is
```

First 3 extractors need local hosting. Last 6 are external APIs that work anywhere. SSRF protection blocks private IPs. Results cached in memory (168h TTL). Domain rate limiting (10 req/min/domain). Authenticated extraction via cookies for paywall domains (NYT, Bloomberg, etc.).

## Multi-Turn Sessions

Pass `session_id` to search to enable conversational refinement. The broker remembers prior queries and uses them to context-enrich follow-up searches. Sessions persist to SQLite across restarts.

## Configuration

All config via env vars (see `.env.example`). Missing API keys degrade gracefully — providers are skipped, not errors. Budget values are query counts (not USD): 0 = unlimited, set to enforce credit tracking.

## Conventions

- Provider adapters must never leak provider-specific shapes outside `argus/providers/`
- All search results are `SearchResult`: url, title, snippet, domain, provider, score
- Extracted content is `ExtractedContent`: url, title, text, author, date, word_count
- Routes prefixed with `/api`
- Free/self-hosted-first: SearXNG and DuckDuckGo are the fallback floor
- Token balances persist in SQLite alongside budget tracking
- Budget env var is named `MONTHLY_BUDGET_USD` but values are query counts (legacy naming)
- Version bumps must update `pyproject.toml` AND `server.json` (top-level + packages[0])
- `README.md` must retain `<!-- mcp-name: io.github.Khamel83/argus -->` (PyPI verification for MCP Registry)
````

## File: README.md
````markdown
# Argus

<!-- mcp-name: io.github.Khamel83/argus -->

[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-brightgreen)](https://www.python.org/downloads/)
[![PyPI Version](https://img.shields.io/pypi/v/argus-search)](https://pypi.org/project/argus-search/)
[![PyPI Downloads](https://img.shields.io/pepy/dt/argus-search)](https://pepy.tech/projects/argus-search)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)
[![CI](https://github.com/Khamel83/argus/actions/workflows/ci.yml/badge.svg)](https://github.com/Khamel83/argus/actions/workflows/ci.yml)
[![MCP Registry](https://img.shields.io/badge/MCP-Registry-blue)](https://registry.modelcontextprotocol.io/servers/io.github.Khamel83/argus)
[![Docker](https://img.shields.io/badge/ghcr.io-khamel83%2Fargus-blue)](https://github.com/Khamel83/argus/pkgs/container/argus)

Multi-provider web search broker for AI agents. Routes across SearXNG, DuckDuckGo, GitHub, Brave, Tavily, Exa, and more — using RRF fusion, content extraction, and budget-aware routing so you don't waste your free search credits.

**Features at a glance:**

- **Multi-provider search** — 11 providers, one API, free-first tier routing
- **5,000+ free queries/month** — automatic budget tracking, exhausted providers skipped
- **Content extraction** — 9-step fallback chain with quality gates (local + external)
- **Multi-turn sessions** — pass `session_id` for conversational search refinement
- **4 search modes** — discovery, research, recovery, grounding
- **Dead URL recovery** — first-class `/recover-url` endpoint with archive fallbacks
- **4 integration paths** — HTTP API, CLI, MCP server, Python SDK

_Built for AI agent builders, RAG infra, and ops teams who don't want to hand-wire search APIs._

## Contents

- [Quickstart](#quickstart)
- [Providers](#providers)
- [HTTP API](#http-api)
- [Integration](#integration)
  - [CLI](#cli)
  - [MCP](#mcp)
  - [Python](#python)
- [Content Extraction](#content-extraction)
- [Architecture](#architecture)
- [Configuration](#configuration)
- [FAQ](#faq)

## Quickstart

### Mode 1: Local CLI (zero config)

```bash
pip install argus-search && argus search -q "python web frameworks"
```

That's it. DuckDuckGo handles the search — no accounts, no keys, no containers. You get unlimited free search from your laptop right now. Add API keys whenever you want more providers, or don't.

```bash
argus extract -u "https://example.com/article"       # extract clean text from any URL
```

Works on any machine with Python 3.11+ — laptop, Mac Mini, Raspberry Pi, cloud VM. Nothing to host.

**For MCP (Claude Code, Cursor, VS Code):**

```bash
pipx install argus-search[mcp] && argus mcp serve
```

Then add to your MCP config:

```json
{"mcpServers": {"argus": {"command": "argus", "args": ["mcp", "serve"]}}}
```

Or install from the [MCP Registry](https://registry.modelcontextprotocol.io/servers/io.github.Khamel83/argus):

```json
{
  "mcpServers": {
    "argus": {
      "registryType": "pypi",
      "identifier": "argus-search",
      "runtimeHint": "uvx"
    }
  }
}
```

One command to install, one JSON block to connect. No server to run, no keys to configure.

### Mode 2: Full Stack Server

Got a Raspberry Pi running Pi-hole? A Mac Mini on your desk? An old laptop? That's enough to run the full stack — SearXNG (your own private search engine) plus local JS-rendering content extraction.

```bash
docker compose up -d    # SearXNG + Argus
```

| What you have | What you get |
|--------------|-------------|
| **Any machine with Python 3.11+** | DuckDuckGo + API providers (no server) |
| **Raspberry Pi 4 / old laptop** (4GB+) | Everything — SearXNG, all providers, Crawl4AI |
| **Mac Mini M1+** (8GB+) | Full stack with headroom |
| **Free cloud VM** (1GB) | SearXNG + search providers (skip Crawl4AI) |

SearXNG takes 512MB of RAM and gives you a private Google-style search engine that nobody can rate-limit, block, or charge for. It runs alongside Pi-hole on hardware millions of people already own.

## Providers

| Provider | Credit type | Free capacity | Setup |
|----------|------------|---------------|-------|
| DuckDuckGo | Free (scraped) | Unlimited | None |
| SearXNG | Free (self-hosted) | Unlimited | Docker |
| GitHub | Free (API) | Unlimited | None (token for higher rate limit) |
| Brave Search | Monthly recurring | 2,000 queries/month | [dashboard](https://brave.com/search/api/) |
| Tavily | Monthly recurring | 1,000 queries/month | [signup](https://app.tavily.com/sign-up) |
| Exa | Monthly recurring | 1,000 queries/month | [signup](https://dashboard.exa.ai/signup) |
| Linkup | Monthly recurring | 1,000 queries/month | [signup](https://linkup.so) |
| Serper | One-time signup | 2,500 credits | [signup](https://serper.dev/signup) |
| Parallel AI | One-time signup | 4,000 credits | [signup](https://parallel.ai) |
| You.com | One-time signup | $20 credit | [platform](https://you.com/platform) |
| Valyu | One-time signup | $10 credit | [platform](https://platform.valyu.ai) |

**5,000 free queries/month** from the four recurring providers. Three providers need no API key at all. Routing priority: **Tier 0** (free: SearXNG, DuckDuckGo, GitHub) → **Tier 1** (monthly: Brave, Tavily, Exa, Linkup) → **Tier 2** (one-time: Serper, Parallel, You.com, Valyu, SearchAPI). Budget-exhausted providers are skipped automatically.

## HTTP API

All endpoints prefixed with `/api`. OpenAPI docs at `http://localhost:8000/docs`.

```bash
# Search
curl -X POST http://localhost:8000/api/search \
  -H "Content-Type: application/json" \
  -d '{"query": "python web frameworks", "mode": "discovery", "max_results": 5}'

# Multi-turn search (conversational refinement)
curl -X POST http://localhost:8000/api/search \
  -H "Content-Type: application/json" \
  -d '{"query": "what about async?", "session_id": "my-session"}'

# Extract content from a working URL
curl -X POST http://localhost:8000/api/extract \
  -H "Content-Type: application/json" \
  -d '{"url": "https://example.com/article"}'

# Recover a dead or moved URL
curl -X POST http://localhost:8000/api/recover-url \
  -H "Content-Type: application/json" \
  -d '{"url": "https://example.com/old-page", "title": "Example Article"}'

# Health & budgets
curl http://localhost:8000/api/health/detail
curl http://localhost:8000/api/budgets
```

#### Search modes

| Mode | Use for | Example |
|------|---------|---------|
| `discovery` | Related pages, canonical sources | "Find the official docs for X" |
| `research` | Broad exploratory retrieval | "Latest approaches to Y?" |
| `recovery` | Finding moved/dead content | "This URL is 404" |
| `grounding` | Fact-checking with live sources | "Verify this claim about Z" |

Tier-based routing always applies first. Within each tier, the mode selects provider order.

#### Response format

```json
{
  "query": "python web frameworks",
  "mode": "discovery",
  "results": [
    {"url": "https://fastapi.tiangolo.com", "title": "FastAPI", "snippet": "Modern Python web framework", "score": 0.942}
  ],
  "total_results": 1,
  "cached": false,
  "traces": [
    {"provider": "duckduckgo", "status": "success", "results_count": 5, "latency_ms": 312}
  ]
}
```

Each result includes `url`, `title`, `snippet`, `domain`, `provider`, and `score`. The `traces` array shows which providers were called and their outcomes.

#### Budgets

```json
{
  "budgets": {
    "brave": {"remaining": 1847, "monthly_usage": 153, "usage_count": 153, "exhausted": false},
    "duckduckgo": {"remaining": 0, "monthly_usage": 0, "usage_count": 42, "exhausted": false}
  },
  "token_balances": {"jina": 9833638}
}
```

Each provider tracks usage per calendar month. When a provider hits its budget, Argus skips it and moves to the next tier. Free providers (SearXNG, DuckDuckGo, GitHub) have no limit. Set `ARGUS_*_MONTHLY_BUDGET_USD` to enforce custom limits per provider.

## Integration

### CLI

```bash
argus search -q "python web framework"              # zero-config, uses DuckDuckGo
argus search -q "python web framework" --mode research -n 20
argus search -q "fastapi" --session my-session       # multi-turn context
argus extract -u "https://example.com/article"       # extract clean text
argus extract -u "https://example.com/article" -d nytimes.com  # auth extraction
argus recover-url -u "https://dead.link" -t "Title"
argus health                                         # provider status
argus budgets                                        # budget + token balances
argus set-balance -s jina -b 9833638                 # track token balance
argus test-provider -p brave                         # smoke-test a provider
argus serve                                          # start API server
argus mcp serve                                      # start MCP server
```

All commands support `--json` for structured output.

<details>
<summary>How sessions work</summary>

Pass `session_id` to any search call. Argus stores each query and extracted URL in a SQLite-backed session. Reusing the same `session_id` gives the broker context from prior queries — follow-up searches are automatically refined using earlier conversation context. Sessions persist across restarts. Omit `session_id` for stateless, one-shot searches.

</details>

### MCP

Add to your MCP client config:

```json
{
  "mcpServers": {
    "argus": {
      "command": "argus",
      "args": ["mcp", "serve"]
    }
  }
}
```

Works with **Claude Code**, **Cursor**, **VS Code**, and any MCP-compatible client.

**Option B — Self-hosted server (homelab / always-on machine)**

Run Argus once on a server, connect every client to it over the network. No local install needed on client machines.

On the server (`docker compose up -d` starts both):
```bash
argus mcp serve --transport sse --host 0.0.0.0 --port 8001
```

On each client machine, add to `~/.claude/claude_desktop_config.json` (or equivalent):
```json
{
  "mcpServers": {
    "argus": {
      "url": "http://<your-server>:8271/sse"
    }
  }
}
```

With [Tailscale](https://tailscale.com), `<your-server>` is your machine's Tailscale hostname (e.g. `homelab-ts`). One server, every machine on your network gets search.

Available tools: `search_web`, `extract_content`, `recover_url`, `expand_links`, `search_health`, `search_budgets`, `test_provider`, `cookie_health`, `valyu_answer`

### Python

```python
from argus.broker.router import create_broker
from argus.models import SearchQuery, SearchMode
from argus.extraction import extract_url

broker = create_broker()

response = await broker.search(
    SearchQuery(query="python web frameworks", mode=SearchMode.DISCOVERY, max_results=10)
)
for r in response.results:
    print(f"{r.title}: {r.url} (score: {r.score:.3f})")

content = await extract_url(response.results[0].url)
print(content.title)
print(content.text)
```

## Content Extraction

Argus tries up to nine methods to extract content from any URL: first local (trafilatura, Crawl4AI, Playwright), then external APIs (Jina, Valyu Contents, Firecrawl, You.com, Wayback, archive.is). Each attempt is quality-checked for garbage output. See [docs/providers.md](docs/providers.md) for the full extractor comparison.

**Extract** gets the full text of a working URL. **Recover-URL** finds alternatives when a URL is dead, paywalled, or radically changed by querying archival sources (Wayback, archive.is) and running a question-guided extraction loop.

## Architecture

```
Caller (CLI/HTTP/MCP/Python) → SearchBroker → tier-sorted providers → RRF ranking → response
                                     ↕ SessionStore (optional)
                            Extractor (on demand) → 9-step fallback chain with quality gates
```

| Module | Responsibility |
|--------|---------------|
| `argus/broker/` | Tier-based routing, ranking, dedup, caching, health, budgets |
| `argus/providers/` | Provider adapters (one per search API) |
| `argus/extraction/` | 9-step URL extraction fallback chain with quality gates |
| `argus/sessions/` | Multi-turn session store and query refinement |
| `argus/api/` | FastAPI HTTP endpoints |
| `argus/cli/` | Click CLI commands |
| `argus/mcp/` | MCP server for LLM integration |
| `argus/persistence/` | PostgreSQL query/result storage |

Add new providers or extractors with a single adapter file. See [CONTRIBUTING.md](CONTRIBUTING.md) for the interface.

## Configuration

All config via environment variables. See `.env.example` for the full list. Missing keys degrade gracefully — providers are skipped, not errors.

| Variable | Default | Description |
|----------|---------|-------------|
| `ARGUS_SEARXNG_BASE_URL` | `http://127.0.0.1:8080` | SearXNG endpoint |
| `ARGUS_BRAVE_API_KEY` | — | Brave Search API key |
| `ARGUS_SERPER_API_KEY` | — | Serper API key |
| `ARGUS_TAVILY_API_KEY` | — | Tavily API key |
| `ARGUS_EXA_API_KEY` | — | Exa API key |
| `ARGUS_LINKUP_API_KEY` | — | Linkup API key |
| `ARGUS_PARALLEL_API_KEY` | — | Parallel AI API key |
| `ARGUS_YOU_API_KEY` | — | You.com API key |
| `ARGUS_VALYU_API_KEY` | — | Valyu API key (search, contents, answer) |
| `ARGUS_FIRECRAWL_API_KEY` | — | Firecrawl API key (content extraction) |
| `ARGUS_GITHUB_API_KEY` | — | GitHub token (higher rate limit) |
| `ARGUS_*_MONTHLY_BUDGET_USD` | 0 (unlimited) | Query-count budget per provider |
| `ARGUS_CRAWL4AI_ENABLED` | false | Enable Crawl4AI extraction step |
| `ARGUS_YOU_CONTENTS_ENABLED` | false | Enable You.com Contents API extraction |
| `ARGUS_CACHE_TTL_HOURS` | 168 | Result cache TTL |

## FAQ

**How is this different from calling Tavily/Serper directly?**
Argus calls them for you — plus 9 other providers. You get one ranked, deduplicated result set instead of managing multiple API keys and stitching results together. Free providers are tried first, so you only burn credits when needed.

**Can I run only one provider?**
Yes. Set only the API key for the provider you want. All others are silently skipped. For zero-config, just install and go — DuckDuckGo handles everything with no keys.

**Do I need Docker?**
No. `pip install argus-search` works immediately on any machine with Python 3.11+. Docker is only needed for SearXNG (self-hosted search) or Crawl4AI (local JS rendering).

## License

MIT — see [CHANGELOG.md](CHANGELOG.md) for release history.
````
