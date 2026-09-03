"""
Centralized logging setup for Argus.

Structured enough to be useful, quiet enough to not dump full payloads.
"""

import logging
import re
import sys
from typing import IO
from typing import Optional


_URL = re.compile(r"https?://[^\s]+", re.IGNORECASE)
_SECRET = re.compile(
    r"(?i)(authorization|cookie|set-cookie|(?:api[_-]?key|credential|password|secret|signature|token))\s*[:=]\s*[^\s,;]+"
)
_CONTROL = re.compile(r"[\r\n\t]+")


def sanitize_causal_detail(value: object, *, maximum: int = 256) -> str:
    """Return bounded causal text with URLs and credential values removed."""

    text = value if isinstance(value, str) else type(value).__name__
    text = _URL.sub("<url>", text)
    text = _SECRET.sub(lambda match: f"{match.group(1)}=<redacted>", text)
    text = _CONTROL.sub(" ", text).strip()
    return text[:maximum] if text else "unspecified failure"


def log_failure(
    logger: logging.Logger,
    *,
    code: str,
    request_id: str,
    detail: object,
    operation_id: str | None = None,
) -> None:
    """Write one correlated, sanitized causal failure record."""

    logger.warning(
        "operation_failure code=%s request_id=%s operation_id=%s cause=%s",
        code,
        request_id,
        operation_id or "none",
        sanitize_causal_detail(detail),
    )


def setup_logging(level: Optional[str] = None, stream: IO[str] | None = None) -> logging.Logger:
    """Set up Argus logging. Call once at startup."""
    config_level = (level or "INFO").upper()

    fmt = "%(asctime)s %(levelname)-5s [%(name)s] %(message)s"
    datefmt = "%Y-%m-%d %H:%M:%S"

    handler = logging.StreamHandler(stream or sys.stderr)
    handler.setFormatter(logging.Formatter(fmt, datefmt=datefmt))

    # Root logger
    root = logging.getLogger("argus")
    root.setLevel(getattr(logging, config_level, logging.INFO))
    root.handlers = [handler]
    root.propagate = False

    # Quiet noisy third-party loggers
    for name in ["httpx", "httpcore", "sqlalchemy.engine"]:
        logging.getLogger(name).setLevel(logging.WARNING)

    return root


def get_logger(name: str) -> logging.Logger:
    """Get a child logger under argus."""
    return logging.getLogger(f"argus.{name}")
