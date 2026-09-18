# Infrastructure Service: Argus (Search & Extraction Plane)

**Role:** High-quality multi-provider search infrastructure and 12-step content extraction broker.  
**Ownership Model:** Single authoritative primary node on Homelab; all external agents and CLIs are thin callers.

---

## 1. Network Placement & Endpoints

All access is secured within the Tailscale mesh. No public ports or external listeners are exposed.

| Service Surface | Protocol / Format | Tailscale Endpoint | Authentication | Normal Callers |
| :--- | :--- | :--- | :--- | :--- |
| **HTTP Authority API** | HTTPS / REST | `https://homelab.deer-panga.ts.net:8270` | Bearer Token (`ARGUS_AUTHORITY_TOKEN`) | Mac Mini CLI, Maya, G2K, automated scripts |
| **MCP Adapter** | Streamable HTTP / JSON-RPC | `https://homelab.deer-panga.ts.net:8443` | Bearer Token (`ARGUS_API_KEY`) | Interactive coding agents (Claude Code, Antigravity, Codex) |
| **Admin Control** | HTTPS / REST | `https://homelab.deer-panga.ts.net:8270/api/admin/*` | `X-Admin-API-Key` | Operator maintenance, canary probes, doctor checks |

*Note: The MagicDNS FQDN (`homelab.deer-panga.ts.net`) is required for HTTPS TLS certificate validation (Let's Encrypt).*

---

## 2. Caller Integration Contract

Agents, subagents, and development machines must **never** receive provider API keys, database credentials, or direct filesystem access. They connect strictly as thin HTTP/MCP callers.

### Standard Client Environment (e.g. in `~/.zshrc` or container env):
```bash
export ARGUS_NODE_ROLE="caller"
export ARGUS_AUTHORITY_URL="https://homelab.deer-panga.ts.net:8270"
export ARGUS_AUTHORITY_TOKEN="$(<"$HOME/Library/Application Support/Argus/mac-agents-token")"
export ARGUS_API_KEY="$ARGUS_AUTHORITY_TOKEN"
```

### Retrieval CLI Usage:
```bash
# Normal search (uses expiring Tier 1 monthly credits first: Brave, Tavily, Exa, Linkup, Parallel)
argus search -q "query terms" --max-results 5

# Zero-spend dry run / CI testing (strictly uses Tier 0 scrapers; guarantees $0.00 spent)
argus search -q "test query" --free

# 12-step fallback page content extraction
argus extract -u "https://example.com/article"
```

---

## 3. Tier Routing & Accounting Invariants

- **AI-First Tier Routing (`monthly_first`)**: In normal mode, Argus automatically expends renewable monthly credits (Brave 2k/mo, Tavily 1k/mo, Exa 1k/mo, Linkup 1k/mo, Parallel 5k/mo) before defaulting to scrapers.
- **Zero-Spend Flag (`--free` / `free_only=true`)**: Guaranteed zero-spend mode for automated tests, CI, and local dry runs.
- **Accounting Separation**: Search queries are accounted as 1 query = 1 request unit. LLMs (G2K) are token-based. Health probes and readiness telemetry consume 0 credits and 0 tokens.
- **Self-Healing Circuit Breaker**: Upstream 4xx errors (e.g. 400 Bad Request, 401 Auth, 429 Rate Limit) settle with `charge = 0.0` and never trigger permanent database bans. Upstream 5xx errors enter a 10-minute cooldown, followed by automatic half-open probe recovery.

---

## 4. Agent Tooling Boundary (Maya & G2K)

- **G2K (OCI VM `100.126.13.70:4312`)**: External reasoning backbone. Argus retrieval never synchronously blocks on or fails if G2K is slow or unreachable.
- **Maya**: Consumes Argus as a downstream tool via MCP or authenticated HTTP. Argus is not a note store; it is internet retrieval only.

---

## 5. Health & Diagnostic Verification

To verify the live health of the search cluster without spending credits:

```bash
# Verify HTTP contract and capabilities:
curl -s -H "Authorization: Bearer $ARGUS_AUTHORITY_TOKEN" \
  https://homelab.deer-panga.ts.net:8270/api/capabilities | jq .capabilities

# Verify provider readiness matrix:
curl -s -H "Authorization: Bearer $ARGUS_AUTHORITY_TOKEN" \
  https://homelab.deer-panga.ts.net:8270/api/provider-health | jq .

# Full system diagnostic (requires admin token):
ARGUS_AUTHORITY_TOKEN="$ARGUS_ADMIN_API_KEY" argus doctor
```
