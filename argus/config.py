"""
Argus configuration — loaded from environment variables.

All settings use ARGUS_ prefix. Missing keys degrade gracefully.
Falls back to the oneshot secrets vault when env vars are unset.
"""

import os
import subprocess
from dataclasses import dataclass, field
from typing import Optional


@dataclass(frozen=True)
class SearXNGConfig:
    enabled: bool = True
    base_url: str = "http://127.0.0.1:8080"
    timeout_seconds: int = 12


@dataclass(frozen=True)
class ProviderConfig:
    enabled: bool = False
    api_key: str = ""
    monthly_budget_usd: float = 0.0
    timeout_seconds: int = 15


@dataclass(frozen=True)
class ArgusConfig:
    """All Argus settings, loaded from environment."""

    # Core
    env: str = "development"
    log_level: str = "INFO"

    # Database
    db_url: str = "postgresql+psycopg://postgres:postgres@localhost:5432/argus"

    # Broker behavior
    cache_ttl_hours: int = 168
    disable_provider_after_failures: int = 5
    provider_cooldown_minutes: int = 60
    default_max_results: int = 10

    # Providers
    searxng: SearXNGConfig = field(default_factory=SearXNGConfig)
    brave: ProviderConfig = field(default_factory=ProviderConfig)
    serper: ProviderConfig = field(default_factory=ProviderConfig)
    tavily: ProviderConfig = field(default_factory=ProviderConfig)
    exa: ProviderConfig = field(default_factory=ProviderConfig)
    searchapi: ProviderConfig = field(default_factory=ProviderConfig)
    you: ProviderConfig = field(default_factory=ProviderConfig)

    # Service
    host: str = "127.0.0.1"
    port: int = 8000

    # Optional
    allow_mcp: bool = False
    allow_web_ui: bool = False
    log_full_results: bool = False
    log_provider_payloads: bool = False


def _secrets_get(vault_key: str) -> str:
    """Try to fetch a key from the oneshot secrets vault."""
    try:
        result = subprocess.run(
            ["secrets", "get", vault_key],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    except Exception:
        pass
    return ""


def _env(key: str, default: str = "") -> str:
    val = os.environ.get(key, "")
    if val:
        return val
    # Fallback: try secrets vault with the env key name
    return _secrets_get(key) or default


def _env_bool(key: str, default: bool = False) -> bool:
    val = os.environ.get(key, "").lower()
    if val in ("1", "true", "yes"):
        return True
    if val in ("0", "false", "no"):
        return False
    return default


def _env_int(key: str, default: int = 0) -> int:
    val = os.environ.get(key)
    if val is None:
        return default
    try:
        return int(val)
    except ValueError:
        return default


def _env_float(key: str, default: float = 0.0) -> float:
    val = os.environ.get(key)
    if val is None:
        return default
    try:
        return float(val)
    except ValueError:
        return default


def _provider_config(prefix: str, enabled_default: bool = False, budget_default: float = 0.0, timeout_default: int = 15) -> ProviderConfig:
    return ProviderConfig(
        enabled=_env_bool(f"ARGUS_{prefix}_ENABLED", enabled_default),
        api_key=_env(f"ARGUS_{prefix}_API_KEY") or _secrets_get(f"{prefix}_API_KEY"),
        monthly_budget_usd=_env_float(f"ARGUS_{prefix}_MONTHLY_BUDGET_USD", budget_default),
        timeout_seconds=_env_int(f"ARGUS_{prefix}_TIMEOUT_SECONDS", timeout_default),
    )


def load_config() -> ArgusConfig:
    """Load configuration from environment variables."""
    return ArgusConfig(
        env=_env("ARGUS_ENV", "development"),
        log_level=_env("ARGUS_LOG_LEVEL", "INFO"),
        db_url=_env("ARGUS_DB_URL", "postgresql+psycopg://postgres:postgres@localhost:5432/argus"),
        cache_ttl_hours=_env_int("ARGUS_CACHE_TTL_HOURS", 168),
        disable_provider_after_failures=_env_int("ARGUS_DISABLE_PROVIDER_AFTER_FAILURES", 5),
        provider_cooldown_minutes=_env_int("ARGUS_PROVIDER_COOLDOWN_MINUTES", 60),
        default_max_results=_env_int("ARGUS_DEFAULT_MAX_RESULTS", 10),
        searxng=SearXNGConfig(
            enabled=_env_bool("ARGUS_SEARXNG_ENABLED", True),
            base_url=_env("ARGUS_SEARXNG_BASE_URL", "http://127.0.0.1:8080"),
            timeout_seconds=_env_int("ARGUS_SEARXNG_TIMEOUT_SECONDS", 12),
        ),
        brave=_provider_config("BRAVE", enabled_default=True, budget_default=5.0),
        serper=_provider_config("SERPER", enabled_default=True),
        tavily=_provider_config("TAVILY", enabled_default=True, timeout_default=20),
        exa=_provider_config("EXA", enabled_default=True, timeout_default=20),
        searchapi=_provider_config("SEARCHAPI", enabled_default=False),
        you=_provider_config("YOU", enabled_default=False),
        host=_env("ARGUS_HOST", "127.0.0.1"),
        port=_env_int("ARGUS_PORT", 8000),
        allow_mcp=_env_bool("ARGUS_ALLOW_MCP"),
        allow_web_ui=_env_bool("ARGUS_ALLOW_WEB_UI"),
        log_full_results=_env_bool("ARGUS_LOG_FULL_RESULTS"),
        log_provider_payloads=_env_bool("ARGUS_LOG_PROVIDER_PAYLOADS"),
    )


_config: Optional[ArgusConfig] = None


def get_config() -> ArgusConfig:
    """Get or create the global configuration singleton."""
    global _config
    if _config is None:
        _config = load_config()
    return _config
