"""
Centralized logging setup for Argus.

Structured enough to be useful, quiet enough to not dump full payloads.
"""

import logging
import sys
from typing import Optional


def setup_logging(level: Optional[str] = None) -> logging.Logger:
    """Set up Argus logging. Call once at startup."""
    config_level = (level or "INFO").upper()

    fmt = "%(asctime)s %(levelname)-5s [%(name)s] %(message)s"
    datefmt = "%Y-%m-%d %H:%M:%S"

    handler = logging.StreamHandler(sys.stdout)
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
