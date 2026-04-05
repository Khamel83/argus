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

## Status (2026-04-05)

| Task | Platform | Status | Link |
|------|----------|--------|------|
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
