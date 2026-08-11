"""Pure validation and canonicalization for acceptance-v3 research targets.

The request contract is deliberately independent from providers, persistence,
or network clients.  Values validated here are eventually projected into a
public plan, so malformed URLs, credentials, local paths, and ambiguous source
ownership fail before a workflow operation can begin.
"""

from __future__ import annotations

import base64
import hashlib
import ipaddress
import json
import re
from collections.abc import Mapping, Sequence
from enum import Enum
from typing import Any, Literal
from urllib.parse import SplitResult, unquote, urlsplit

from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field, field_validator, model_validator
from tld import parse_tld


ClaimClass = Literal[
    "capabilities",
    "pricing_eligibility",
    "privacy_data_handling",
    "protected_execution",
    "provenance_governance",
]

CLAIM_CLASSES = frozenset(
    {
        "capabilities",
        "pricing_eligibility",
        "privacy_data_handling",
        "protected_execution",
        "provenance_governance",
    }
)

MAX_PUBLIC_URL_LENGTH = 2_048

_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_CREDENTIAL_MARKER = re.compile(
    r"(?ix)"
    r"(?:"
    r"\bauthorization\b\s*(?:[:=]\s*|\s+)(?:bearer|basic)\b"
    r"|\bbearer\s+[a-z0-9._~+/=-]+(?=$|[\s,;])"
    # A bare ``Basic capabilities`` sentence is ordinary documentation.  The
    # decoder below recognizes padded and unpadded Basic tokens; this branch
    # keeps padded, base64-like tokens fail-closed even when decoding is
    # inconclusive.  The explicit Authorization-header branch above rejects
    # header forms regardless of whether a value is present.
    r"|\bbasic\s+[a-z0-9+/]{2,}={1,2}(?=$|[^a-z0-9+/=])"
    r"|\b(?:x[-_ ]?api[-_ ]?key|api[-_ ]?key)\b\s*(?::|=)"
    r")"
)
_BASIC_CREDENTIAL_TOKEN = re.compile(
    r"(?i)\bbasic\s+([a-z0-9+/]+={0,2})(?=$|[^a-z0-9+/=])"
)
_PRIVATE_KEY_MARKER = re.compile(
    r"(?i)-----begin[\w -]*private key[\w -]*-----"
    r"|\bssh-(?:rsa|dss|ed[0-9]+|ecdsa|xmss|sk-[a-z0-9-]+)\b"
    r"|\becdsa-sha2-[a-z0-9-]+\b"
)
_LOCAL_PATH_MARKER = re.compile(
    r"(?ix)"
    r"(?:"
    r"(?:^|[\s:=\(\[\{\"'])~[a-z0-9._-]*[\\/]"
    r"|(?:^|[\s=\(\[\{\"'])//[^\s/\\]+[\\/][^\s/\\]+"
    r"|(?:^|[\s:=\(\[\{\"'])/(?:"
    r"applications|users|volumes|dev|workspace(?:s)?|tmp|private|var|etc|home|root|opt|srv|"
    r"library|system|bin|sbin|usr|proc|run|mnt|media|data"
    r")(?:[\\/]|$)"
    r"|(?:^|[\s:=\(\[\{\"'])[a-z]:[\\/]"
    r"|(?:^|[\s:=\(\[\{\"'])\\\\"
    r"|(?:^|[\s:=\(\[\{\"'])\.\.?[\\/]"
    r")"
)
_FILE_URI_MARKER = re.compile(r"(?i)\bfile:\s*//")
_PERCENT_CONTROL_MARKER = re.compile(r"(?i)%(?:0[0-9a-f]|1[0-9a-f]|7f)")
_BACKSLASH_CONTROL_MARKER = re.compile(
    r"(?i)\\(?:[0nrt]|x(?:0[0-9a-f]|1[0-9a-f]|7f)|u(?:000[0-9a-f]|001[0-9a-f]|007f))"
)
_DNS_LABEL = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")

# RFC/IANA special-use names are not public acquisition targets even when a
# PSL implementation happens to return a registrable-looking result for them.
_SPECIAL_USE_SUFFIXES = frozenset(
    {
        "alt",
        "corp",
        "example",
        "home.arpa",
        "internal",
        "invalid",
        "lan",
        "local",
        "localhost",
        "localdomain",
        "onion",
        "private",
        "test",
    }
)


def _raise(label: str, reason: str) -> ValueError:
    return ValueError(f"{label} {reason}")


def _decoded_variants(value: str) -> tuple[str, ...]:
    """Return the raw value and a small bounded set of percent-decoded forms."""

    variants = [value]
    current = value
    for _ in range(3):
        decoded = unquote(current)
        if decoded == current:
            break
        variants.append(decoded)
        current = decoded
    return tuple(variants)


def _contains_encoded_control(value: str) -> bool:
    if _PERCENT_CONTROL_MARKER.search(value) or _BACKSLASH_CONTROL_MARKER.search(value):
        return True
    return any(_CONTROL.search(decoded) for decoded in _decoded_variants(value)[1:])


def _contains_basic_credential(value: str) -> bool:
    """Recognize an unpadded Basic token without flagging prose like ``Basic capabilities``."""

    for match in _BASIC_CREDENTIAL_TOKEN.finditer(value):
        token = match.group(1)
        try:
            padded = token + "=" * (-len(token) % 4)
            decoded = base64.b64decode(padded, validate=True)
        except (ValueError, base64.binascii.Error):
            continue
        if b":" in decoded:
            return True
    return False


def _raw_path_has_dot_segment(path: str) -> bool:
    return any(segment in {".", ".."} for segment in re.split(r"[/\\]", path))


def validate_raw_public_https_url(value: Any, *, label: str = "URL") -> Any:
    """Reject unsafe raw URL spellings before Pydantic canonicalization.

    ``AnyHttpUrl`` normalizes literal and encoded dot segments.  This guard is
    deliberately run in ``mode='before'`` validators and again by the pure
    URL helpers so callers cannot bypass it by handing them a raw string.
    """

    if value is None:
        return value
    if not isinstance(value, (str, AnyHttpUrl)):
        return value
    text = str(value)
    if len(text) > MAX_PUBLIC_URL_LENGTH:
        raise _raise(label, f"must be at most {MAX_PUBLIC_URL_LENGTH} characters")
    if _CONTROL.search(text) or _contains_encoded_control(text):
        raise _raise(label, "contains an ASCII or encoded control character")
    for decoded in _decoded_variants(text):
        try:
            path = urlsplit(decoded).path
        except ValueError as exc:
            raise _raise(label, "is not a valid URL") from exc
        if _raw_path_has_dot_segment(path):
            raise _raise(label, "must not contain dot path segments")
    return value


def validate_public_text(
    value: str,
    *,
    label: str,
    allow_empty: bool = False,
) -> str:
    """Validate a string that is safe to project into the public plan."""

    if not isinstance(value, str):
        raise _raise(label, "must be a string")
    if not allow_empty and not value.strip():
        raise _raise(label, "must not be blank")
    for candidate in _decoded_variants(value):
        if _CONTROL.search(candidate) or _contains_encoded_control(candidate):
            raise _raise(label, "contains an ASCII or encoded control character")
        if _CREDENTIAL_MARKER.search(candidate) or _contains_basic_credential(candidate):
            raise _raise(label, "contains a credential or bearer/API-key marker")
        if _PRIVATE_KEY_MARKER.search(candidate):
            raise _raise(label, "contains private-key material")
        if _FILE_URI_MARKER.search(candidate):
            raise _raise(label, "contains a file URI")
        if _LOCAL_PATH_MARKER.search(candidate):
            raise _raise(label, "contains a local absolute-path marker")
    return value


def _idna_host(host: str, *, label: str) -> str:
    if not host:
        raise _raise(label, "must include a hostname")
    try:
        normalized = host.rstrip(".").lower().encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise _raise(label, "has an invalid IDNA hostname") from exc
    if not normalized or len(normalized) > 253:
        raise _raise(label, "has an invalid hostname length")
    if any(not _DNS_LABEL.fullmatch(part) for part in normalized.split(".")):
        raise _raise(label, "has an invalid hostname")
    try:
        address = ipaddress.ip_address(normalized)
    except ValueError:
        address = None
    if address is not None:
        raise _raise(label, "must not be an IP address")
    if normalized in {"broadcasthost", "ip6-localhost"} or any(
        normalized == suffix or normalized.endswith(f".{suffix}")
        for suffix in _SPECIAL_USE_SUFFIXES
    ):
        raise _raise(label, "must be a public hostname")
    tld_name, domain, _subdomain = parse_tld(
        f"https://{normalized}",
        fail_silently=True,
        search_private=True,
    )
    if not tld_name or not domain or domain == tld_name:
        raise _raise(label, "must not be a public-suffix-only hostname")
    return normalized


def _split_public_https(value: Any, *, label: str) -> tuple[str, SplitResult, str]:
    """Validate URL security properties and return canonical host metadata."""

    if not isinstance(value, (str, AnyHttpUrl)):
        raise _raise(label, "must be an HTTP(S) URL")
    validate_raw_public_https_url(value, label=label)
    text = str(value)
    if len(text) > MAX_PUBLIC_URL_LENGTH:
        raise _raise(label, f"must be at most {MAX_PUBLIC_URL_LENGTH} characters")
    if _CONTROL.search(text):
        raise _raise(label, "contains an ASCII control character")
    if "*" in text:
        raise _raise(label, "must not contain a wildcard")
    try:
        parsed = urlsplit(text)
    except ValueError as exc:
        raise _raise(label, "is not a valid URL") from exc
    if parsed.scheme.lower() != "https":
        raise _raise(label, "must use HTTPS")
    if not parsed.netloc:
        raise _raise(label, "must include a hostname")
    if parsed.username is not None or parsed.password is not None or "@" in parsed.netloc:
        raise _raise(label, "must not contain credentials")
    if parsed.query or "?" in text.split("#", 1)[0]:
        raise _raise(label, "must not contain a query")
    if parsed.fragment or "#" in text:
        raise _raise(label, "must not contain a fragment")
    try:
        port = parsed.port
    except ValueError as exc:
        raise _raise(label, "has an invalid port") from exc
    host = _idna_host(parsed.hostname or "", label=label)
    if port is None or port == 443:
        normalized_netloc = host
    else:
        normalized_netloc = f"{host}:{port}"
    path = parsed.path or "/"
    if not path.startswith("/"):
        path = "/" + path
    if any(segment in {".", ".."} for segment in path.split("/")):
        raise _raise(label, "must not contain dot path segments")
    if "*" in path:
        raise _raise(label, "must not contain a wildcard")
    canonical = f"https://{normalized_netloc}{path}"
    return host, parsed, canonical


def validate_public_https_url(value: Any, *, label: str = "URL") -> Any:
    """Validate one canonical, credential-free, public HTTPS URL.

    The original Pydantic ``AnyHttpUrl`` instance is returned so model fields
    remain typed URLs; canonical persistence uses ``model_dump(mode="json")``.
    """

    _split_public_https(value, label=label)
    return value


def _canonical_url_text(value: Any, *, label: str = "source prefix") -> str:
    _host, _parsed, canonical = _split_public_https(value, label=label)
    return canonical


def normalize_source_prefix(value: Any) -> str:
    """Return the path-boundary identity for a public HTTPS source prefix."""

    canonical = _canonical_url_text(value)
    parsed = urlsplit(canonical)
    path = parsed.path or "/"
    if path != "/":
        path = path.rstrip("/") or "/"
    # The root path is represented by the host alone in the identity form;
    # ``urlsplit`` still reconstructs it as ``/`` when matching candidates.
    return f"{parsed.scheme.lower()}://{parsed.netloc.lower()}{'' if path == '/' else path}"


def _prefix_path(value: str) -> tuple[str, str, int | None, str]:
    parsed = urlsplit(value)
    return (
        parsed.scheme.lower(),
        (parsed.hostname or "").lower(),
        parsed.port,
        parsed.path or "/",
    )


def prefixes_overlap(left: Any, right: Any) -> bool:
    """Return whether two normalized prefixes can claim the same URL."""

    left_normalized = normalize_source_prefix(left)
    right_normalized = normalize_source_prefix(right)
    left_scheme, left_host, left_port, left_path = _prefix_path(left_normalized)
    right_scheme, right_host, right_port, right_path = _prefix_path(right_normalized)
    if (left_scheme, left_host, left_port) != (right_scheme, right_host, right_port):
        return False
    if left_path == "/" or right_path == "/":
        return True
    return left_path == right_path or left_path.startswith(right_path + "/") or right_path.startswith(left_path + "/")


def validate_prefixes(prefixes: Sequence[Any], *, label: str = "source prefixes") -> Sequence[Any]:
    """Validate uniqueness and path-boundary disjointness for one prefix set."""

    normalized: list[str] = []
    for prefix in prefixes:
        identity = normalize_source_prefix(prefix)
        if identity in normalized:
            raise _raise(label, "contain duplicate or overlapping prefixes")
        if any(prefixes_overlap(identity, prior) for prior in normalized):
            raise _raise(label, "contain ancestor/descendant overlapping prefixes")
        normalized.append(identity)
    return prefixes


# Descriptive alias used by callers that prefer a plan-oriented name.
prefixes_overlap_within_plan = prefixes_overlap


def prefix_matches(prefix: Any, candidate: Any) -> bool:
    """Return whether ``candidate`` is the prefix itself or a descendant."""

    try:
        normalized_prefix = normalize_source_prefix(prefix)
        candidate_text = _canonical_url_text(candidate, label="candidate URL")
        normalized_candidate = normalize_source_prefix(candidate_text)
    except ValueError:
        return False
    prefix_scheme, prefix_host, prefix_port, prefix_path = _prefix_path(normalized_prefix)
    candidate_scheme, candidate_host, candidate_port, candidate_path = _prefix_path(
        normalized_candidate
    )
    if (prefix_scheme, prefix_host, prefix_port) != (
        candidate_scheme,
        candidate_host,
        candidate_port,
    ):
        return False
    if prefix_path == "/":
        return True
    return candidate_path == prefix_path or candidate_path.startswith(prefix_path + "/")


is_prefix_match = prefix_matches
source_prefix_matches = prefix_matches


class ResearchRequirement(BaseModel):
    """One mandatory, publicly rendered claim class and research query."""

    model_config = ConfigDict(extra="forbid", strict=True)

    claim_class: ClaimClass
    query: str = Field(..., min_length=1, max_length=500)

    @field_validator("query")
    @classmethod
    def validate_query(cls, value: str) -> str:
        return validate_public_text(value, label="requirement query")


class ResearchTarget(BaseModel):
    """One named target with disjoint public source prefixes."""

    model_config = ConfigDict(extra="forbid", strict=True)

    name: str = Field(..., min_length=1, max_length=80)
    source_prefixes: list[AnyHttpUrl] = Field(..., min_length=1, max_length=4)
    requirements: list[ResearchRequirement] = Field(..., min_length=1, max_length=3)

    @field_validator("source_prefixes", mode="before")
    @classmethod
    def validate_raw_source_prefixes(cls, value: Any) -> Any:
        if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
            for index, prefix in enumerate(value):
                validate_raw_public_https_url(prefix, label=f"source prefix {index}")
        return value

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        return validate_public_text(value, label="target name")

    @field_validator("source_prefixes")
    @classmethod
    def validate_source_prefixes(cls, value: list[AnyHttpUrl]) -> list[AnyHttpUrl]:
        validate_prefixes(value)
        return value

    @model_validator(mode="after")
    def validate_claim_classes(self) -> "ResearchTarget":
        classes = [requirement.claim_class for requirement in self.requirements]
        if len(classes) != len(set(classes)):
            raise ValueError("requirements must have unique claim_class values")
        return self


def _model_json(value: BaseModel) -> dict[str, Any]:
    """Dump a request with URL values serialized as JSON strings."""

    return value.model_dump(mode="json")


def _json_value(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return _json_value(_model_json(value))
    if isinstance(value, AnyHttpUrl):
        return str(value)
    if isinstance(value, Enum):
        return _json_value(value.value)
    if isinstance(value, Mapping):
        return {str(key): _json_value(child) for key, child in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(child) for child in value]
    return value


def canonical_request_projection(request: Any) -> dict[str, Any]:
    """Build the exact JSON-mode request projection used for persistence/hash.

    ``official_url`` is the one optional field where absent and explicit null
    carry different contract meaning.  A model that did not receive that field
    omits it; a model that explicitly received ``None`` retains the JSON null.
    All other defaults are included so the effective request is unambiguous.
    """

    fields_set: set[str] | None = None
    if isinstance(request, BaseModel):
        fields_set = set(getattr(request, "model_fields_set", set()))
        payload: Any = _model_json(request)
    else:
        payload = request
    projected = _json_value(payload)
    if not isinstance(projected, dict):
        raise TypeError("request must be a mapping or Pydantic model")
    if fields_set is not None and "official_url" not in fields_set:
        projected.pop("official_url", None)
    return projected


def canonical_request_json(request: Any) -> str:
    """Return compact, sorted, UTF-8-safe canonical request JSON."""

    return json.dumps(
        canonical_request_projection(request),
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def canonical_request_sha256(request: Any) -> str:
    """Hash the exact canonical request JSON with SHA-256."""

    return hashlib.sha256(canonical_request_json(request).encode("utf-8")).hexdigest()


# Compatibility aliases make the pure contract convenient at adapters without
# introducing provider/runtime imports into this module.
canonical_request_hash = canonical_request_sha256
request_sha256 = canonical_request_sha256
canonical_sha256 = canonical_request_sha256
canonical_hash = canonical_request_sha256
canonical_request = canonical_request_projection
canonical_json = canonical_request_json
normalize_prefix = normalize_source_prefix
url_matches_prefix = prefix_matches
prefix_overlaps = prefixes_overlap
validate_source_prefixes = validate_prefixes


__all__ = [
    "CLAIM_CLASSES",
    "ClaimClass",
    "ResearchRequirement",
    "ResearchTarget",
    "canonical_request_hash",
    "canonical_hash",
    "canonical_json",
    "canonical_request",
    "canonical_request_json",
    "canonical_request_projection",
    "canonical_request_sha256",
    "canonical_sha256",
    "is_prefix_match",
    "normalize_source_prefix",
    "normalize_prefix",
    "prefix_matches",
    "prefix_overlaps",
    "prefixes_overlap",
    "request_sha256",
    "source_prefix_matches",
    "validate_prefixes",
    "validate_source_prefixes",
    "validate_public_https_url",
    "validate_raw_public_https_url",
    "validate_public_text",
    "url_matches_prefix",
]
