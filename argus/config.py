"""Argus configuration."""

import logging
import os
import re
import subprocess
from dataclasses import dataclass, field
from typing import Mapping, Optional

_log = logging.getLogger("argus.config")


@dataclass(frozen=True)
class SearXNGConfig:
    enabled: bool = False
    base_url: str = "http://127.0.0.1:8080"
    residential_base_url: str = ""
    timeout_seconds: int = 12


@dataclass(frozen=True)
class ProviderConfig:
    enabled: bool = False
    api_key: str = ""
    monthly_budget_usd: float = 0.0
    timeout_seconds: int = 15


@dataclass(frozen=True)
class NodeConfig:
    role: str = "primary"  # primary|worker|caller|dev
    egress_type: str = "unknown"  # residential|datacenter|unknown
    machine_name: str = ""
    trusted_tailnet: bool = False


@dataclass(frozen=True)
class ResidentialConfig:
    endpoints: list[str] = field(default_factory=list)
    shared_secret: str = ""
    timeout_seconds: int = 30
    allowed_cidrs: list[str] = field(default_factory=lambda: [
        "127.0.0.1/32", "::1/128", "100.64.0.0/10", "10.0.0.0/8",
        "172.16.0.0/12", "192.168.0.0/16"
    ])
    policy: str = "fallback"  # off|fallback|prefer_on_datacenter|prefer_for_domains|always


@dataclass(frozen=True)
class ArgusConfig:
    """All Argus settings, loaded from environment."""

    env: str = "development"
    log_level: str = "INFO"
    db_url: str = ""
    cache_ttl_hours: int = 168
    disable_provider_after_failures: int = 5
    provider_cooldown_minutes: int = 60
    default_max_results: int = 10
    searxng: SearXNGConfig = field(default_factory=SearXNGConfig)
    brave: ProviderConfig = field(default_factory=ProviderConfig)
    serper: ProviderConfig = field(default_factory=ProviderConfig)
    tavily: ProviderConfig = field(default_factory=ProviderConfig)
    exa: ProviderConfig = field(default_factory=ProviderConfig)
    searchapi: ProviderConfig = field(default_factory=ProviderConfig)
    you: ProviderConfig = field(default_factory=ProviderConfig)
    parallel: ProviderConfig = field(default_factory=ProviderConfig)
    linkup: ProviderConfig = field(default_factory=ProviderConfig)
    valyu: ProviderConfig = field(default_factory=ProviderConfig)
    github: ProviderConfig = field(default_factory=ProviderConfig)
    yahoo: ProviderConfig = field(default_factory=ProviderConfig)
    wolfram: ProviderConfig = field(default_factory=ProviderConfig)
    node: NodeConfig = field(default_factory=NodeConfig)
    residential: ResidentialConfig = field(default_factory=ResidentialConfig)
    host: str = "127.0.0.1"
    port: int = 8000
    allow_mcp: bool = False
    allow_web_ui: bool = False
    log_full_results: bool = False
    log_provider_payloads: bool = False


class SecretsResolver:
    def get(self, key: str) -> str:
        return ""


class SubprocessSecretsResolver(SecretsResolver):
    """Batch-load all secrets from the vault once, serve lookups from cache.

    On first access, decrypts all known vault files in a single pass (~0.2s).
    If the ``secrets`` CLI is not on PATH, all lookups return empty immediately
    after the first probe.
    """

    _ENV_LINE_RE = re.compile(r"^([A-Z_][A-Z0-9_]*)=(.*)$", re.MULTILINE)

    def __init__(self, *, vault_names: tuple[str, ...] | None = None):
        self._cache: dict[str, str] = {}
        self._batch_done = False
        self._vault_names = vault_names or (
            "argus", "argus_keys", "research_keys",
            "argus.env.env", "services", "argus_auth",
        )

    def _load_batch(self) -> None:
        if self._batch_done:
            return
        self._batch_done = True
        for name in self._vault_names:
            try:
                result = subprocess.run(
                    ["secrets", "decrypt", name],
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
            except (FileNotFoundError, PermissionError, subprocess.TimeoutExpired, OSError):
                _log.debug("secrets CLI not found — using environment variables only")
                return
            except Exception:
                continue
            if result.returncode == 0:
                keys = self._ENV_LINE_RE.findall(result.stdout)
                for key, value in keys:
                    if key not in self._cache:
                        self._cache[key] = value
                if keys:
                    _log.debug("loaded %d keys from vault: %s", len(keys), name)
        _log.debug("secrets cache: %d keys total", len(self._cache))

    def get(self, key: str) -> str:
        self._load_batch()
        return self._cache.get(key, "")


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

    def load(self) -> ArgusConfig:
        res_endpoints_raw = self.get_str("ARGUS_RESIDENTIAL_ENDPOINTS")
        legacy_res_url = self.get_str("ARGUS_RESIDENTIAL_EXTRACTOR_URL")
        res_endpoints = [u.strip() for u in res_endpoints_raw.split(",") if u.strip()] if res_endpoints_raw else ([legacy_res_url] if legacy_res_url else [])

        res_allowed_cidrs_raw = self.get_str(
            "ARGUS_RESIDENTIAL_ALLOWED_CIDRS",
            "127.0.0.1/32,::1/128,100.64.0.0/10,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16",
        )
        res_allowed_cidrs = [item.strip() for item in res_allowed_cidrs_raw.split(",") if item.strip()]

        return ArgusConfig(
            env=self.get_str("ARGUS_ENV", "development"),
            log_level=self.get_str("ARGUS_LOG_LEVEL", "INFO"),
            db_url=self.get_str(
                "ARGUS_DB_URL",
                "postgresql+psycopg2://postgres:postgres@localhost:5432/argus",
                secret_keys=("DB_URL",),
            ),
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
                enabled=self.get_bool("ARGUS_SEARXNG_ENABLED", False),
                base_url=self.get_str(
                    "ARGUS_SEARXNG_BASE_URL",
                    "http://127.0.0.1:8080",
                ),
                residential_base_url=self.get_str("ARGUS_SEARXNG_RESIDENTIAL_BASE_URL"),
                timeout_seconds=self.get_int("ARGUS_SEARXNG_TIMEOUT_SECONDS", 12),
            ),
            brave=self.provider_config("BRAVE", enabled_default=True, budget_default=2000.0),
            serper=self.provider_config("SERPER", enabled_default=True, budget_default=2500.0),
            tavily=self.provider_config("TAVILY", enabled_default=True, timeout_default=20, budget_default=1000.0),
            exa=self.provider_config("EXA", enabled_default=True, timeout_default=20, budget_default=1000.0),
            searchapi=self.provider_config("SEARCHAPI", enabled_default=True),
            you=self.provider_config("YOU", enabled_default=True, budget_default=20000.0),
            parallel=self.provider_config("PARALLEL", enabled_default=True, budget_default=16000.0),
            linkup=self.provider_config("LINKUP", enabled_default=True, budget_default=1000.0),
            valyu=self.provider_config("VALYU", enabled_default=True, budget_default=10000.0),
            github=self.provider_config("GITHUB", enabled_default=True),
            yahoo=ProviderConfig(
                enabled=self.get_bool("ARGUS_YAHOO_ENABLED", True),
                timeout_seconds=self.get_int("ARGUS_YAHOO_TIMEOUT_SECONDS", 15),
            ),
            wolfram=ProviderConfig(
                enabled=self.get_bool("ARGUS_WOLFRAM_ENABLED", True),
                api_key=self.get_str(
                    "ARGUS_WOLFRAM_API_KEY",
                    secret_keys=("WOLFRAM_APP_ID", "WOLFRAM_API_KEY"),
                ),
                monthly_budget_usd=self.get_float(
                    "ARGUS_WOLFRAM_MONTHLY_BUDGET_USD", 2000.0
                ),
                timeout_seconds=self.get_int("ARGUS_WOLFRAM_TIMEOUT_SECONDS", 15),
            ),
            node=NodeConfig(
                role=self.get_str("ARGUS_NODE_ROLE", "primary"),
                egress_type=self.get_str("ARGUS_EGRESS_TYPE", "unknown"),
                machine_name=self.get_str("ARGUS_MACHINE_NAME", ""),
                trusted_tailnet=self.get_bool("ARGUS_TRUSTED_TAILNET", False),
            ),
            residential=ResidentialConfig(
                endpoints=res_endpoints,
                shared_secret=self.get_str("ARGUS_RESIDENTIAL_SHARED_SECRET"),
                timeout_seconds=self.get_int("ARGUS_RESIDENTIAL_TIMEOUT_SECONDS", 30),
                allowed_cidrs=res_allowed_cidrs,
                policy=self.get_str("ARGUS_RESIDENTIAL_POLICY", "fallback"),
            ),
            host=self.get_str("ARGUS_HOST", "127.0.0.1"),
            port=self.get_int("ARGUS_PORT", 8000),
            allow_mcp=self.get_bool("ARGUS_ALLOW_MCP"),
            allow_web_ui=self.get_bool("ARGUS_ALLOW_WEB_UI"),
            log_full_results=self.get_bool("ARGUS_LOG_FULL_RESULTS"),
            log_provider_payloads=self.get_bool("ARGUS_LOG_PROVIDER_PAYLOADS"),
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
