"""Suite-wide isolation from developer configuration and persistent state."""

import os
import shutil
import tempfile
from pathlib import Path

import pytest


_RUNTIME_ROOT: Path | None = None
_SAFE_ENV: dict[str, str] = {}
_PROVIDERS = (
    "SEARXNG", "BRAVE", "SERPER", "TAVILY", "EXA", "SEARCHAPI", "YOU",
    "PARALLEL", "LINKUP", "VALYU", "GITHUB", "YAHOO", "WOLFRAM", "JINA",
    "FIRECRAWL",
)


def pytest_configure(config):
    """Set all configuration before test modules can import Argus."""
    global _RUNTIME_ROOT
    _RUNTIME_ROOT = Path(tempfile.mkdtemp(prefix="argus-pytest-"))
    for key in tuple(os.environ):
        if key.startswith("ARGUS_"):
            os.environ.pop(key)
    _SAFE_ENV.update({
        "ARGUS_AUTOLOAD_DOTENV": "false",
        "ARGUS_DISABLE_SECRET_RESOLUTION": "true",
        "ARGUS_DATA_ROOT": str(_RUNTIME_ROOT / "data"),
        "ARGUS_DB_URL": f"sqlite:///{_RUNTIME_ROOT / 'argus.db'}",
        "ARGUS_BUDGET_DB_PATH": str(_RUNTIME_ROOT / "budgets.db"),
        "ARGUS_NODE_ROLE": "dev",
        "ARGUS_EGRESS_TYPE": "unknown",
        "ARGUS_RESIDENTIAL_POLICY": "off",
        "ARGUS_EGRESS_NODES": "",
        "ARGUS_EGRESS_SHARED_SECRET": "",
        "ARGUS_RESIDENTIAL_SHARED_SECRET": "",
        "ARGUS_CRAWL4AI_ENABLED": "false",
        "ARGUS_YOU_CONTENTS_ENABLED": "false",
    })
    for provider in _PROVIDERS:
        _SAFE_ENV[f"ARGUS_{provider}_ENABLED"] = "false"
    os.environ.update(_SAFE_ENV)


def _restore_safe_environment() -> None:
    for key in tuple(os.environ):
        if key.startswith("ARGUS_"):
            os.environ.pop(key)
    os.environ.update(_SAFE_ENV)


def pytest_unconfigure(config):
    if _RUNTIME_ROOT is not None:
        shutil.rmtree(_RUNTIME_ROOT, ignore_errors=True)


@pytest.fixture(autouse=True)
def reset_argus_config_cache():
    from argus.config import reset_config

    _restore_safe_environment()
    reset_config()
    yield
    _restore_safe_environment()
    reset_config()
