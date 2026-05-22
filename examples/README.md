# Argus examples

Minimal runnable examples. Every Python example here works with **zero API
keys** — Argus falls back to DuckDuckGo when no other providers are configured.

| File | What it shows |
|------|--------------|
| [`basic_search.py`](basic_search.py) | Run a single search using the Python SDK. |
| [`extract_and_recover.py`](extract_and_recover.py) | Extract an article, fall back to `recover_url` if it's dead. |
| [`research_pack.py`](research_pack.py) | Build a local research pack programmatically. |
| [`mcp_quickstart.md`](mcp_quickstart.md) | Copy-paste MCP setup for the most common clients. |

Run any Python example with:

```bash
uv run python examples/basic_search.py
# or, if you've installed argus-search globally:
python examples/basic_search.py
```
