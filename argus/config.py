"""Argus configuration."""

import os
import subprocess
from dataclasses import dataclass, field
from typing import Mapping, Optional


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

    env: str = "development"
    log_level: str = "INFO"
    db_url: str = ""
    db_path: str = "argus.db"
    cache_ttl_hours: int = 168
    disable_provider_after_failures: int = 5
    provider_cooldown_minutes: int = 60
    default_max_results: int = 10
    searxng: SearXNGConfig = field(default_factory=SearXNGConfig)
    brave: ProviderConfig = field(default_factory=ProviderConfig)
    serper: ProviderConfig = field(default_factory=ProviderConfig)
    tavily: ProviderConfig = field(default_factory=ProviderConfig)
    exa: ProviderConfig = field(default_factory=ProviderConfig)
    host: str = "127.0.0.1"
    port: int = 8000
    allow_mcp: bool = False
    allow_web_ui: bool = False
    log_full_results: bool = False
    log_provider_payloads: bool = False
    session_max_turns: int = 20
    session_ttl_hours: int = 168
    session_max_context_chars: int = 2000


class SecretsResolver:
    def get(self, key: str) -> str:
        return ""


class SubprocessSecretsResolver(SecretsResolver):
    """Fetch optional secrets from an external `secrets get` helper."""

    def get(self, key: str) -> str:
        try:
            result = subprocess.run(
                ["secrets", "get", key],
                capture_output=True,
                text=True,
                timeout=5,
            )
        except Exception:
            return ""
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
        return ""


class EnvironmentConfigLoader:
    def __init__(
        self,
        *,
        environ: Mapping[str, str] | None = None,
        secrets_resolver: SecretsResolver | None = None,
    ):
        self._environ = environ if environ is not None else os.environ
        self._secrets = secrets_resolver or SubprocessSecretsResolver()

    def get_str(
        self,
        key: str,
        default: str = "",
        *,
        secret_keys: tuple[str, ...] = (),
    ) -> str:
        value = self._environ.get(key, "")
        if value:
            return value
        for secret_key in (key, *secret_keys):
            secret = self._secrets.get(secret_key)
            if secret:
                return secret
        return default

    def get_bool(self, key: str, default: bool = False) -> bool:
        value = self._environ.get(key, "").lower()
        if value in ("1", "true", "yes"):
            return True
        if value in ("0", "false", "no"):
            return False
        return default

    def get_int(self, key: str, default: int = 0) -> int:
        value = self._environ.get(key)
        if value is None:
            return default
        try:
            return int(value)
        except ValueError:
            return default

    def get_float(self, key: str, default: float = 0.0) -> float:
        value = self._environ.get(key)
        if value is None:
            return default
        try:
            return float(value)
        except ValueError:
            return default

    def provider_config(
        self,
        prefix: str,
        *,
        enabled_default: bool = False,
        budget_default: float = 0.0,
        timeout_default: int = 15,
    ) -> ProviderConfig:
        return ProviderConfig(
            enabled=self.get_bool(f"ARGUS_{prefix}_ENABLED", enabled_default),
            api_key=self.get_str(
                f"ARGUS_{prefix}_API_KEY",
                secret_keys=(f"{prefix}_API_KEY",),
            ),
            monthly_budget_usd=self.get_float(
                f"ARGUS_{prefix}_MONTHLY_BUDGET_USD",
                budget_default,
            ),
            timeout_seconds=self.get_int(
                f"ARGUS_{prefix}_TIMEOUT_SECONDS",
                timeout_default,
            ),
        )

    def _resolve_db_path(self) -> str:
        """Single unified DB path. Prefers ARGUS_DB_PATH; falls back to parsing ARGUS_DB_URL."""
        explicit = self.get_str("ARGUS_DB_PATH", "")
        if explicit:
            return explicit
        db_url = self.get_str("ARGUS_DB_URL", "")
        if db_url.startswith("sqlite:///"):
            return db_url[len("sqlite:///"):]
        if db_url.startswith("sqlite://"):
            return db_url[len("sqlite://"):]
        return "argus.db"

    def load(self) -> ArgusConfig:
        db_path = self._resolve_db_path()
        return ArgusConfig(
            env=self.get_str("ARGUS_ENV", "development"),
            log_level=self.get_str("ARGUS_LOG_LEVEL", "INFO"),
            db_url=self.get_str(
                "ARGUS_DB_URL",
                f"sqlite:///{db_path}",
                secret_keys=("DB_URL",),
            ),
            db_path=db_path,
            cache_ttl_hours=self.get_int("ARGUS_CACHE_TTL_HOURS", 168),
            disable_provider_after_failures=self.get_int(
                "ARGUS_DISABLE_PROVIDER_AFTER_FAILURES",
                5,
            ),
            provider_cooldown_minutes=self.get_int(
                "ARGUS_PROVIDER_COOLDOWN_MINUTES",
                60,
            ),
            default_max_results=self.get_int("ARGUS_DEFAULT_MAX_RESULTS", 10),
            searxng=SearXNGConfig(
                enabled=self.get_bool("ARGUS_SEARXNG_ENABLED", True),
                base_url=self.get_str(
                    "ARGUS_SEARXNG_BASE_URL",
                    "http://127.0.0.1:8080",
                ),
                timeout_seconds=self.get_int("ARGUS_SEARXNG_TIMEOUT_SECONDS", 12),
            ),
            brave=self.provider_config("BRAVE", enabled_default=True, budget_default=5.0),
            serper=self.provider_config("SERPER", enabled_default=True),
            tavily=self.provider_config("TAVILY", enabled_default=True, timeout_default=20),
            exa=self.provider_config("EXA", enabled_default=True, timeout_default=20),
            host=self.get_str("ARGUS_HOST", "127.0.0.1"),
            port=self.get_int("ARGUS_PORT", 8000),
            allow_mcp=self.get_bool("ARGUS_ALLOW_MCP"),
            allow_web_ui=self.get_bool("ARGUS_ALLOW_WEB_UI"),
            log_full_results=self.get_bool("ARGUS_LOG_FULL_RESULTS"),
            log_provider_payloads=self.get_bool("ARGUS_LOG_PROVIDER_PAYLOADS"),
            session_max_turns=self.get_int("ARGUS_SESSION_MAX_TURNS", 20),
            session_ttl_hours=self.get_int("ARGUS_SESSION_TTL_HOURS", 168),
            session_max_context_chars=self.get_int("ARGUS_SESSION_MAX_CONTEXT_CHARS", 2000),
        )


def load_config(
    *,
    environ: Mapping[str, str] | None = None,
    secrets_resolver: SecretsResolver | None = None,
) -> ArgusConfig:
    """Load configuration from environment variables and optional secret fallback."""
    return EnvironmentConfigLoader(
        environ=environ,
        secrets_resolver=secrets_resolver,
    ).load()


_config: Optional[ArgusConfig] = None


def get_config(*, force_reload: bool = False) -> ArgusConfig:
    """Get or create the global configuration singleton."""
    global _config
    if _config is None or force_reload:
        _config = load_config()
    return _config


def reset_config() -> None:
    global _config
    _config = None
