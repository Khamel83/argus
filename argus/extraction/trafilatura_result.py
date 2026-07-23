"""Normalize Trafilatura's supported extraction result shapes.

Trafilatura 2.x returns a ``Document`` by default while older configurations
and explicit ``as_dict=True`` calls return mappings.  Keep that compatibility
boundary here so extraction callers never depend on either provider-specific
shape.  Only content metadata is admitted; provenance remains Argus-owned.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class NormalizedTrafilaturaResult:
    """Allowlisted content fields produced by Trafilatura."""

    text: str
    title: str = ""
    author: str = ""
    date: str | None = None


def normalize_trafilatura_result(
    raw_result: Any,
) -> NormalizedTrafilaturaResult | None:
    """Return an allowlisted result for a Document or mapping.

    Missing or malformed required content is rejected. Optional metadata is
    accepted only when it already has the expected string shape, avoiding
    implicit conversion of arbitrary objects and keeping provenance fields out
    of the normalization boundary.
    """
    if isinstance(raw_result, Mapping):
        values = raw_result
    else:
        as_dict = getattr(raw_result, "as_dict", None)
        if not callable(as_dict):
            return None
        try:
            values = as_dict()
        except Exception:
            return None
        if not isinstance(values, Mapping):
            return None

    text = values.get("text")
    if not isinstance(text, str) or not text.strip():
        return None

    title = values.get("title")
    author = values.get("author")
    date = values.get("date")

    return NormalizedTrafilaturaResult(
        text=text,
        title=title if isinstance(title, str) else "",
        author=author if isinstance(author, str) else "",
        date=date if isinstance(date, str) else None,
    )
