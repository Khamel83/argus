# Argus Publicity Playbook

Step-by-step instructions for getting Argus listed in directories and registries. No social media account needed. You just need your GitHub account.

Everything below is a one-time task. Do them in order — the first few are the highest impact.

---

## Task 1: PR to modelcontextprotocol/servers (20 min)

This is the official MCP server directory maintained by Anthropic. Getting listed here is the single most valuable thing you can do.

### Steps

1. **Open the repo in your browser:**
   Go to `https://github.com/modelcontextprotocol/servers`

2. **Fork it:**
   Click the "Fork" button in the top-right corner. This creates a copy under your GitHub account.

3. **Find where community servers are listed:**
   Look at the README.md — there's a section listing community servers. It's usually a table or list organized by category.

4. **Edit the README.md in your fork:**
   Click the pencil icon (edit button) on README.md in your fork. Add Argus to the appropriate section (likely "Search" or "Web"). Use this text:

   ```markdown
   - **[Argus](https://github.com/Khamel83/argus)** - Multi-provider search broker. Routes queries across SearXNG, Brave, Serper, Tavily, and Exa with automatic fallback, RRF ranking, content extraction, and budget enforcement.
   ```

   (Adjust the format to match whatever format the other entries use — table row, bullet point, etc.)

5. **Commit the change:**
   At the bottom of the edit page, write a commit message like:
   `Add Argus search broker MCP server`
   Click "Commit changes."

6. **Open a Pull Request:**
   After committing, GitHub will show a banner saying "Compare & pull request." Click it.
   - Title: `Add Argus multi-provider search broker`
   - Description:
     ```
     Argus is a multi-provider search broker for AI agents. One MCP server
     that routes across SearXNG, Brave, Serper, Tavily, and Exa with
     automatic fallback, RRF ranking, content extraction, and budget
     enforcement. SQLite only, no external dependencies.

     Tools: search_web, extract_content, recover_url, expand_links,
     search_health, search_budgets, test_provider

     Repo: https://github.com/Khamel83/argus
     ```
   Click "Create pull request."

7. **Wait.** The maintainers will review it. This can take days to weeks. You don't need to do anything else.

---

## Task 2: PR to awesome-mcp-servers (10 min)

This is the most popular community list of MCP servers on GitHub (thousands of stars).

### Steps

1. Go to `https://github.com/punkpeye/awesome-mcp-servers`

2. Fork it (same as Task 1 — click Fork button).

3. Edit the README.md in your fork. Find the appropriate category (likely "Search" or "Web Search" or similar). Add:

   ```markdown
   - [Argus](https://github.com/Khamel83/argus) - Multi-provider search broker with automatic fallback, RRF ranking, content extraction, and budget enforcement. Routes across SearXNG, Brave, Serper, Tavily, Exa.
   ```

   Match the format of existing entries.

4. Commit with message: `Add Argus search broker`

5. Open a Pull Request:
   - Title: `Add Argus search broker`
   - Description: Same as Task 1 description.

6. **Also do this for the second popular list:**
   Repeat the same process at `https://github.com/wong2/awesome-mcp-servers`

---

## Task 3: Submit to MCP directories (15 min total)

These are websites that list MCP servers. You fill out a form, they list you. No PR needed.

### mcp.so

1. Go to `https://mcp.so`
2. Look for a "Submit" or "Add Server" button
3. Fill in:
   - Name: `Argus`
   - URL: `https://github.com/Khamel83/argus`
   - Description: `Multi-provider search broker for AI agents. Routes across SearXNG, Brave, Serper, Tavily, Exa with fallback, RRF ranking, content extraction, and budget enforcement.`
   - Category: Search / Web
4. Submit

### Smithery

1. Go to `https://smithery.ai`
2. Sign in with your GitHub account
3. Look for "Add Server" or "Register"
4. Point it at your GitHub repo URL: `https://github.com/Khamel83/argus`
5. It may auto-detect the MCP config from your repo. Fill in any missing fields.
6. Submit

### Glama

1. Go to `https://glama.ai/mcp/servers`
2. Look for "Submit" or "Add"
3. Enter repo URL: `https://github.com/Khamel83/argus`
4. Fill in description if asked (same as above)
5. Submit

### PulseMCP

1. Go to `https://pulsemcp.com`
2. Look for "Submit" or "Add Server"
3. Same info as above
4. Submit

**Note:** These sites change their UI. If you can't find a submit button, look for a "Contact" or "GitHub Issues" link — some accept submissions via their own GitHub repo issues.

---

## Task 4: PR to awesome-python (10 min)

This is one of the most popular repos on all of GitHub (~200k stars).

### Steps

1. Go to `https://github.com/vinta/awesome-python`
2. Fork it
3. Look for a "Web" or "Search" or "Third-party APIs" section in README.md
4. Add:

   ```markdown
   - [Argus](https://github.com/Khamel83/argus) - Multi-provider search broker that routes across SearXNG, Brave, Serper, Tavily, and Exa with automatic fallback and RRF ranking.
   ```

5. Commit and open PR:
   - Title: `Add Argus search broker`
   - Description: One-liner about what it does.

**Note:** awesome-python has strict criteria. Your project may get rejected if it's too new or niche. That's fine — try anyway, costs nothing.

---

## Task 5: PR to awesome-fastapi (10 min)

1. Go to `https://github.com/mjhea0/awesome-fastapi`
2. Fork it
3. Find "Utilities" or "Tools" section
4. Add:

   ```markdown
   - [Argus](https://github.com/Khamel83/argus) - Multi-provider search broker with content extraction. FastAPI HTTP API with rate limiting and auth.
   ```

5. Commit and open PR with title: `Add Argus search broker`

---

## Task 6: Upstream framework integrations (future, harder)

These are bigger tasks where we write actual code (a small wrapper around Argus's HTTP API) and submit it to agent frameworks. **I can write the code for you in a future session.** You'd then just need to push the branch and open the PR.

Target frameworks:
- **LangChain** (`langchain-community` package) — `ArgusSearchTool`
- **LlamaIndex** (`llama-index-tools` package) — `ArgusSearchToolSpec`
- **CrewAI** (`crewAI-tools`) — `ArgusSearchTool`

Each of these is an HTTP client that calls your `/api/search` endpoint. The code is tiny but each framework has its own contribution process.

**Don't do these yet.** Do Tasks 1-5 first. Come back to this in a future session and I'll write the code and PR descriptions for you.

---

## Checklist

Copy this and check things off as you go:

```
[ ] Task 1: PR to modelcontextprotocol/servers
[ ] Task 2: PR to awesome-mcp-servers (punkpeye)
[ ] Task 2b: PR to awesome-mcp-servers (wong2)
[ ] Task 3a: Submit to mcp.so
[ ] Task 3b: Submit to Smithery
[ ] Task 3c: Submit to Glama
[ ] Task 3d: Submit to PulseMCP
[ ] Task 4: PR to awesome-python
[ ] Task 5: PR to awesome-fastapi
[ ] Task 6: Framework integrations (future session)
```

---

## Copy-Paste Description

Use this everywhere. It's the same core text, just copy-paste it:

**Short (one line):**
> Multi-provider search broker for AI agents with automatic fallback, RRF ranking, content extraction, and budget enforcement.

**Medium (for PR descriptions):**
> Argus is an open-source search broker that routes queries across SearXNG, Brave, Serper, Tavily, and Exa. One endpoint with automatic fallback, Reciprocal Rank Fusion ranking, content extraction, multi-turn sessions, and budget enforcement. Connect via HTTP, CLI, MCP, or Python. SQLite only — zero external dependencies.

**MCP-specific:**
> MCP server providing 7 tools (search_web, extract_content, recover_url, expand_links, search_health, search_budgets, test_provider) and 3 resources. Routes across 5 search providers with automatic fallback and RRF ranking. Install: `pip install 'argus[mcp]'`, run: `argus mcp serve`.
