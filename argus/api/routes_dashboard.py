"""Web dashboard routes — usage, budget burn, and machine breakdown.

Served at /dashboard. Auth is cookie-based: the user POSTs the admin key
to /dashboard/login, which sets an HttpOnly cookie. All other dashboard
routes verify the cookie against ARGUS_ADMIN_API_KEY.

If no admin key is configured, the dashboard is open (matches the rest
of the project's "trust local" defaults).
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates

from argus.api import usage as usage_queries
from argus.operations.provider_presentation import ProviderPresentationService

router = APIRouter()

_TEMPLATES_DIR = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))

# When Argus is served under a subpath (e.g. /argus/ via nginx), set
# ARGUS_ROOT_PATH=/argus so that template links and redirects resolve correctly.
ROOT_PATH = os.environ.get("ARGUS_ROOT_PATH", "").rstrip("/")
templates.env.globals["root_path"] = ROOT_PATH

COOKIE_NAME = "argus_dash"
COOKIE_MAX_AGE = 86400  # 1 day


def _is_https(request: Request) -> bool:
    """True when the original request arrived over HTTPS.

    Checks X-Forwarded-Proto set by Caddy/Cloudflare and the raw scheme.
    """
    proto = request.headers.get("x-forwarded-proto", "").lower()
    return proto == "https" or request.url.scheme == "https"


def _check_auth(request: Request) -> bool:
    auth = request.app.state.auth_config
    if not auth.has_admin_key():
        return True
    cookie_val = request.cookies.get(COOKIE_NAME, "")
    return auth.matches_admin_token(cookie_val)


def _get_presentation(request: Request) -> ProviderPresentationService:
    return request.app.state.provider_presentation


def _build_chart_data(daily_rows: list[dict]) -> dict:
    """Reshape daily retrieval-operation counts into Chart.js data."""
    if not daily_rows:
        return {"labels": [], "datasets": []}

    days = sorted({r["day"] for r in daily_rows})
    machines = sorted({r["machine"] for r in daily_rows})
    by_machine: dict[str, dict[str, int]] = {m: {} for m in machines}
    for r in daily_rows:
        by_machine[r["machine"]][r["day"]] = r["count"]

    palette = [
        "#60a5fa",
        "#34d399",
        "#fbbf24",
        "#f87171",
        "#a78bfa",
        "#fb7185",
        "#22d3ee",
        "#a3e635",
    ]
    datasets = []
    for i, m in enumerate(machines):
        datasets.append(
            {
                "label": m,
                "data": [by_machine[m].get(d, 0) for d in days],
                "backgroundColor": palette[i % len(palette)],
            }
        )
    return {"labels": days, "datasets": datasets}


@router.get("/dashboard/login", response_class=HTMLResponse)
async def login_page(request: Request):
    return templates.TemplateResponse(
        request,
        "login.html",
        {"error": None},
    )


@router.post("/dashboard/login")
async def login_submit(request: Request, admin_key: str = Form("")):
    auth = request.app.state.auth_config
    if auth.has_admin_key() and not auth.matches_admin_token(admin_key.strip()):
        return templates.TemplateResponse(
            request,
            "login.html",
            {"error": "Invalid admin key."},
            status_code=401,
        )
    response = RedirectResponse(f"{ROOT_PATH}/dashboard", status_code=303)
    response.set_cookie(
        COOKIE_NAME,
        admin_key.strip(),
        max_age=COOKIE_MAX_AGE,
        httponly=True,
        samesite="lax",
        secure=_is_https(request),
    )
    return response


@router.get("/dashboard/logout")
async def logout(request: Request):
    response = RedirectResponse(f"{ROOT_PATH}/dashboard/login", status_code=303)
    response.delete_cookie(COOKIE_NAME)
    return response


@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request):
    if not _check_auth(request):
        return RedirectResponse(f"{ROOT_PATH}/dashboard/login", status_code=303)

    budget_state = _get_presentation(request).budget_state()
    daily = usage_queries.get_daily_query_counts(days=30)
    machines = usage_queries.get_machine_summary(days=30)
    provider_activity = usage_queries.get_provider_activity(days=7)
    caller_activity = usage_queries.get_caller_activity(days=7)
    chart_data = _build_chart_data(daily)

    exhausted = [b for b in budget_state if b["status"] == "exhausted"]
    over_pace = [b for b in budget_state if b["status"] == "over_pace"]

    from argus.config import get_config

    cfg = get_config()

    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "budget_state": budget_state,
            "machines": machines,
            "provider_activity": provider_activity,
            "caller_activity": caller_activity,
            "chart_data_json": json.dumps(chart_data),
            "exhausted": exhausted,
            "over_pace": over_pace,
            "machine_name": cfg.node.machine_name or "(unset)",
            "egress_type": cfg.node.egress_type,
        },
    )


@router.get("/dashboard/fragments/budget", response_class=HTMLResponse)
async def budget_fragment(request: Request):
    if not _check_auth(request):
        return Response(status_code=401)
    budget_state = _get_presentation(request).budget_state()
    exhausted = [b for b in budget_state if b["status"] == "exhausted"]
    over_pace = [b for b in budget_state if b["status"] == "over_pace"]
    return templates.TemplateResponse(
        request,
        "_budget_section.html",
        {
            "budget_state": budget_state,
            "exhausted": exhausted,
            "over_pace": over_pace,
        },
    )
