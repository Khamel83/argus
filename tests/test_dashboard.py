"""Tests for the /dashboard web UI routes."""

from unittest.mock import MagicMock

import pytest


def _build_app(admin_key: str = "admin-secret"):
    import os
    os.environ["ARGUS_ADMIN_API_KEY"] = admin_key

    from argus.api.main import create_app
    from argus.broker.budgets import BudgetTracker
    from argus.broker.health import HealthTracker
    from argus.broker.cache import SearchCache
    from argus.models import ProviderName

    mock_broker = MagicMock()
    bt = BudgetTracker()
    bt.set_budget(ProviderName.BRAVE, 2000)
    bt.set_budget(ProviderName.SERPER, 2500)
    mock_broker.budget_tracker = bt
    mock_broker.health_tracker = HealthTracker()
    mock_broker.cache = SearchCache()

    return create_app(broker=mock_broker)


def test_login_page_renders():
    from fastapi.testclient import TestClient

    client = TestClient(_build_app())
    resp = client.get("/dashboard/login")
    assert resp.status_code == 200
    assert "Argus Dashboard" in resp.text
    assert "admin_key" in resp.text


def test_dashboard_redirects_when_no_cookie():
    from fastapi.testclient import TestClient

    client = TestClient(_build_app(), follow_redirects=False)
    resp = client.get("/dashboard")
    assert resp.status_code == 303
    assert resp.headers["location"] == "/dashboard/login"


def test_login_rejects_wrong_key():
    from fastapi.testclient import TestClient

    client = TestClient(_build_app("the-real-key"), follow_redirects=False)
    resp = client.post("/dashboard/login", data={"admin_key": "wrong"})
    assert resp.status_code == 401
    assert "Invalid" in resp.text


def test_login_sets_cookie_and_redirects():
    from fastapi.testclient import TestClient

    client = TestClient(_build_app("the-real-key"), follow_redirects=False)
    resp = client.post("/dashboard/login", data={"admin_key": "the-real-key"})
    assert resp.status_code == 303
    assert resp.headers["location"] == "/dashboard"
    assert "argus_dash=the-real-key" in resp.headers["set-cookie"]


def test_dashboard_renders_when_authenticated():
    from fastapi.testclient import TestClient

    client = TestClient(_build_app("the-real-key"))
    resp = client.get("/dashboard", cookies={"argus_dash": "the-real-key"})
    assert resp.status_code == 200
    assert "Provider budgets" in resp.text
    assert "Queries per day" in resp.text
    assert "Usage by machine" in resp.text
    assert "brave" in resp.text  # one of the budgets we set


def test_budget_fragment_requires_auth():
    from fastapi.testclient import TestClient

    client = TestClient(_build_app("the-real-key"))
    resp = client.get("/dashboard/fragments/budget")
    assert resp.status_code == 401


def test_budget_fragment_returns_html_when_authed():
    from fastapi.testclient import TestClient

    client = TestClient(_build_app("the-real-key"))
    resp = client.get(
        "/dashboard/fragments/budget",
        cookies={"argus_dash": "the-real-key"},
    )
    assert resp.status_code == 200
    assert 'id="budget-section"' in resp.text
    assert 'hx-trigger="every 60s"' in resp.text


def test_logout_clears_cookie():
    from fastapi.testclient import TestClient

    client = TestClient(_build_app("the-real-key"), follow_redirects=False)
    resp = client.get("/dashboard/logout", cookies={"argus_dash": "the-real-key"})
    assert resp.status_code == 303
    assert resp.headers["location"] == "/dashboard/login"
    assert 'argus_dash=""' in resp.headers["set-cookie"]


def test_usage_aggregations_return_empty_on_missing_db():
    """Aggregators must not raise when the SQLite DB doesn't exist or is non-SQLite."""
    import os

    os.environ["ARGUS_DB_URL"] = "sqlite:////tmp/this-does-not-exist-argus-xyz.db"
    from argus.config import reset_config

    reset_config()

    from argus.api import usage as u

    # The DB file doesn't exist — sqlite3 will create an empty file but the
    # tables won't be there, so each query falls through to the error branch
    # and returns [].
    assert u.get_daily_query_counts() == []
    assert u.get_machine_summary() == []
    assert u.get_provider_activity() == []


def test_chart_data_shape_with_no_rows():
    from argus.api.routes_dashboard import _build_chart_data

    result = _build_chart_data([])
    assert result == {"labels": [], "datasets": []}


def test_chart_data_stacks_machines():
    from argus.api.routes_dashboard import _build_chart_data

    rows = [
        {"day": "2026-05-19", "machine": "alpha", "count": 5},
        {"day": "2026-05-19", "machine": "beta", "count": 3},
        {"day": "2026-05-20", "machine": "alpha", "count": 7},
    ]
    out = _build_chart_data(rows)
    assert out["labels"] == ["2026-05-19", "2026-05-20"]
    assert len(out["datasets"]) == 2
    labels = {d["label"]: d["data"] for d in out["datasets"]}
    assert labels["alpha"] == [5, 7]
    assert labels["beta"] == [3, 0]


def test_open_access_when_no_admin_key_configured():
    import os

    os.environ.pop("ARGUS_ADMIN_API_KEY", None)
    os.environ.pop("ARGUS_API_KEY", None)

    from fastapi.testclient import TestClient

    # Build app with no admin key — dashboard should be accessible without login.
    from argus.api.main import create_app
    from argus.broker.budgets import BudgetTracker
    from argus.broker.health import HealthTracker
    from argus.broker.cache import SearchCache

    mock_broker = MagicMock()
    mock_broker.budget_tracker = BudgetTracker()
    mock_broker.health_tracker = HealthTracker()
    mock_broker.cache = SearchCache()

    client = TestClient(create_app(broker=mock_broker))
    resp = client.get("/dashboard")
    assert resp.status_code == 200
    assert "Provider budgets" in resp.text
