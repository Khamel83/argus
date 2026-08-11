"""Explicit evidence-rich MCP tools over the HTTP-only execution adapter."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping
from typing import Annotated, Any, Literal

from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.mcpserver.tools.base import Tool
from mcp.types import CallToolResult, TextContent
from pydantic import Field, ValidationError

from argus.contracts import (
    CanonicalOutcome,
    V2Envelope,
    is_success_like,
    mcp_is_error_for,
    validate_v2_envelope,
)

_TEXT_LIMIT = 64 * 1024


V2ToolResult = Annotated[CallToolResult, V2Envelope]


class _BoundedSchemaTool(Tool):
    """Keep pinned-SDK schema errors bounded and free of rejected values."""

    async def run(self, arguments, context=None, convert_result=False):
        try:
            self.fn_metadata.arg_model.model_validate(arguments)
        except ValidationError as exc:
            raise ToolError(
                "Tool arguments did not match the advertised input schema"
            ) from exc
        return await super().run(
            arguments,
            context=context,
            convert_result=convert_result,
        )


def _bounded(text: str) -> str:
    encoded = text.encode("utf-8")
    if len(encoded) <= _TEXT_LIMIT:
        return text
    suffix = "\n\n[Argus MCP text rendering truncated; structuredContent is complete]"
    suffix_bytes = suffix.encode("utf-8")
    prefix = encoded[: _TEXT_LIMIT - len(suffix_bytes)].decode(
        "utf-8",
        errors="ignore",
    )
    return prefix + suffix


def _search_text(result: Mapping[str, Any]) -> str:
    traces = result.get("traces") or []
    providers = [
        str(trace.get("provider"))
        for trace in traces
        if isinstance(trace, Mapping) and trace.get("results_count", 0) > 0
    ]
    provider_text = ", ".join(providers) if providers else "none"
    cached = " (cached)" if result.get("cached") else ""
    lines = [
        f"## Search Results: {result.get('query', '')!r}",
        (
            f"Mode: {result.get('mode', 'discovery')} | "
            f"{result.get('total_results', 0)} results | "
            f"via {provider_text}{cached}"
        ),
        "",
    ]
    warnings = result.get("budget_warnings") or []
    if warnings:
        lines.extend(
            [
                "**Budget warnings:** " + "; ".join(str(item) for item in warnings),
                "",
            ]
        )
    for index, item in enumerate(result.get("results") or [], 1):
        if not isinstance(item, Mapping):
            continue
        lines.append(f"{index}. **{item.get('title') or '(no title)'}**")
        lines.append(f"   URL: {item.get('url', '')}")
        lines.append(f"   Egress: {item.get('egress') or 'unknown'}")
        if item.get("snippet"):
            lines.append(f"   {item['snippet']}")
        lines.append("")
    if result.get("session_id"):
        lines.append(f"_Session ID: {result['session_id']}_")
    return "\n".join(lines)


def _extract_text(result: Mapping[str, Any]) -> str:
    metadata = []
    for label, key in (
        ("Author", "author"),
        ("Date", "date"),
        ("Words", "word_count"),
        ("Extractor", "extractor"),
        ("Egress", "egress"),
        ("Machine", "machine"),
    ):
        if result.get(key):
            metadata.append(f"{label}: {result[key]}")
    lines = [
        f"# {result.get('title') or result.get('url', '')}",
        f"URL: {result.get('url', '')}",
    ]
    if metadata:
        lines.append(" | ".join(metadata))
    lines.extend(["", str(result.get("text") or "")])
    return "\n".join(lines)


def _result(tool_name: str, envelope: Mapping[str, Any]) -> CallToolResult:
    validated = validate_v2_envelope(envelope)
    outcome = CanonicalOutcome(validated.outcome)
    result = validated.result
    if is_success_like(outcome) and result is not None:
        text = (
            _extract_text(result)
            if tool_name == "extract_content_v2"
            else _search_text(result)
        )
        if outcome is not CanonicalOutcome.SUCCESS:
            text = f"[Argus outcome: {outcome.value}]\n{text}"
    else:
        problem = validated.error
        detail = problem.detail if problem is not None else "Operation unavailable"
        lines = [
            f"Argus {tool_name} failure outcome: {outcome.value}",
            f"Request ID: {validated.request_id}\nError: {detail}",
        ]
        if result is not None:
            evidence_text = (
                _extract_text(result)
                if tool_name == "extract_content_v2"
                else _search_text(result)
            )
            lines.extend(["", "Partial evidence:", evidence_text])
        text = "\n".join(lines)
    return CallToolResult(
        content=[TextContent(type="text", text=_bounded(text))],
        structuredContent=dict(envelope),
        isError=mcp_is_error_for(outcome),
    )


def _pinned_tool_manager(mcp):
    from argus.capabilities import CapabilityManifestError

    manager = getattr(mcp, "_tool_manager", None)
    if (
        manager is None
        or not isinstance(getattr(manager, "_tools", None), dict)
        or not callable(getattr(manager, "list_tools", None))
    ):
        raise CapabilityManifestError(
            "Pinned MCP SDK tool manager registration is unavailable"
        )
    return manager


def _register(mcp, fn) -> None:
    tool = _BoundedSchemaTool.from_function(fn)
    manager = _pinned_tool_manager(mcp)
    if tool.name in manager._tools:
        raise RuntimeError(f"duplicate MCP tool registration: {tool.name}")
    manager._tools[tool.name] = tool


def register_v2_tools(
    mcp,
    backend,
    *,
    caller_identity: Callable[[], str],
    caller_token: Callable[[], str | None],
) -> None:
    """Register the four versioned tools as one fail-closed schema set."""

    async def search_web_v2(
        query: Annotated[str, Field(min_length=1, max_length=500)],
        mode: Literal["recovery", "discovery", "grounding", "research"] = "discovery",
        max_results: Annotated[int, Field(ge=1, le=50)] = 10,
        session_id: Annotated[str | None, Field(max_length=128)] = None,
        include_attribution: bool = False,
        free_only: bool = False,
        caller: Annotated[str, Field(max_length=128)] = "mcp",
    ) -> V2ToolResult:
        """Search through the HTTP-v2 evidence authority."""
        envelope = await backend.search_web_v2(
            query=query,
            mode=mode,
            max_results=max_results,
            session_id=session_id,
            include_attribution=include_attribution,
            free_only=free_only,
            caller_label=caller,
            caller_identity=caller_identity(),
            token=caller_token(),
        )
        return _result("search_web_v2", envelope)

    async def recover_url_v2(
        url: Annotated[str, Field(min_length=1, max_length=2048)],
        title: Annotated[str | None, Field(max_length=500)] = None,
        domain: Annotated[str | None, Field(max_length=253)] = None,
        caller: Annotated[str, Field(max_length=128)] = "mcp",
    ) -> V2ToolResult:
        """Recover a URL through the HTTP-v2 evidence authority."""
        envelope = await backend.recover_url_v2(
            url,
            title,
            domain,
            caller_label=caller,
            caller_identity=caller_identity(),
            token=caller_token(),
        )
        return _result("recover_url_v2", envelope)

    async def expand_links_v2(
        query: Annotated[str, Field(min_length=1, max_length=500)],
        context: Annotated[str | None, Field(max_length=2000)] = None,
        caller: Annotated[str, Field(max_length=128)] = "mcp",
    ) -> V2ToolResult:
        """Expand related links through the HTTP-v2 evidence authority."""
        envelope = await backend.expand_links_v2(
            query,
            context,
            caller_label=caller,
            caller_identity=caller_identity(),
            token=caller_token(),
        )
        return _result("expand_links_v2", envelope)

    async def extract_content_v2(
        url: Annotated[str, Field(min_length=1, max_length=2048)],
        domain: Annotated[str | None, Field(max_length=253)] = None,
    ) -> V2ToolResult:
        """Extract content through the HTTP-v2 evidence authority."""
        envelope = await backend.extract_content_v2(
            url,
            domain,
            caller_label="mcp",
            caller_identity=caller_identity(),
            token=caller_token(),
        )
        return _result("extract_content_v2", envelope)

    for fn in (
        search_web_v2,
        recover_url_v2,
        expand_links_v2,
        extract_content_v2,
    ):
        _register(mcp, fn)


def _digest(schema: Mapping[str, Any] | None) -> str:
    return hashlib.sha256(
        json.dumps(
            schema,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()


def actual_v2_tool_registration(mcp) -> dict[str, object]:
    """Describe the actual bound pinned-SDK schemas for startup validation."""
    manager = _pinned_tool_manager(mcp)
    by_name = {
        tool.name: tool for tool in manager.list_tools() if tool.name.endswith("_v2")
    }
    names = tuple(by_name)
    return {
        "transport_version": "2026-07-28",
        "tool_contract_version": "2.0",
        "tools": names,
        "schemas": {
            name: {
                "input_sha256": _digest(by_name[name].parameters),
                "output_sha256": _digest(by_name[name].output_schema),
            }
            for name in names
        },
    }
