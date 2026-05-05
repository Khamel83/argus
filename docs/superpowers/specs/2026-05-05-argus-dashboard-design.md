# Argus Simple Dashboard Design

**Date:** 2026-05-05
**Status:** Approved
**Deciders:** khamel83, Gemini CLI

## Context
Argus is a powerful search broker but currently lacks a visual interface. The user needs a "siple" Google-like homepage for searching, with result persistence (7 days) and the ability to toggle search engines on/off. The deployment must align with the homelab standard: Cloudflare Tunnel + Cloudflare Access for exposure and SSO.

## Requirements
- **Google-like UI:** Clean, centered search bar on a dark theme.
- **Live Results:** Results should appear without a full page reload (using HTMX).
- **Search Engine Toggles:** A slide-out settings drawer to enable/disable specific providers.
- **Persistence:** Leverage Argus's built-in session system to ensure searches are cached for 7 days.
- **Homelab Standards:** 
  - Subpath routing: `khamel.com/argus`.
  - Auth: Cloudflare Access (Zero Trust) at the edge.
  - Proxy: `funnel-proxy` (Nginx) internal routing.

## Architecture

### Frontend
- **Technology:** HTMX + Tailwind CSS (via CDN) + Jinja2 Templates.
- **Pattern:** SPA-like behavior using HTML fragments swapped by HTMX.
- **State:** Provider settings and `session_id` stored in browser `localStorage`.

### Backend (FastAPI)
- **UI Router:** A new `routes_ui.py` to serve the main dashboard and result fragments.
- **Search Integration:** Wraps the existing `SearchBroker` logic.
- **Base Path:** Support for `/argus` mounting to match homelab routing.

### Exposure & Routing
- **Nginx (`funnel-proxy`):**
  ```nginx
  location /argus/ {
      set $backend "argus:8000";
      proxy_pass http://$backend/;
      proxy_set_header Host $host;
      proxy_set_header X-Real-IP $remote_addr;
      proxy_set_header X-Forwarded-Proto https;
  }
  ```
- **Cloudflare Access:** Policy protecting `khamel.com/argus*`.

## User Interface Design
- **Home:** Large "ARGUS" logo, search input.
- **Settings:** Sidebar with a list of checkboxes for providers.
- **Results:** Vertical list of cards: Title (link), Snippet, Provider badge.

## Security
- Public exposure via Cloudflare Tunnel.
- Identity verification via Cloudflare Access (Google SSO).
- Internal API still supports `X-API-Key` for agent-to-agent communication.
