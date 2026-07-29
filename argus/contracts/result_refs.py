"""Pure canonical references for accepted retrieval results."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import asdict, is_dataclass
from enum import Enum
from typing import Any


def canonical_json_value(value: Any) -> Any:
    if is_dataclass(value):
        return canonical_json_value(asdict(value))
    if isinstance(value, Mapping):
        return {
            str(key): canonical_json_value(child)
            for key, child in sorted(value.items(), key=lambda item: str(item[0]))
        }
    if isinstance(value, (list, tuple)):
        return [canonical_json_value(child) for child in value]
    if isinstance(value, Enum):
        return value.value
    return value


def canonical_json(value: Any) -> str:
    return json.dumps(
        canonical_json_value(value),
        sort_keys=True,
        separators=(",", ":"),
    )


def accepted_result_refs(results: Any) -> tuple[str, ...]:
    return tuple(
        "wf-"
        + hashlib.sha256(canonical_json(item).encode()).hexdigest()[:24]
        + f"-{ordinal}"
        for ordinal, item in enumerate(results)
    )
