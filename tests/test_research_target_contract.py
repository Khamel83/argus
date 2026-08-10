"""Contract tests for the acceptance-v3 research-target request.

These tests intentionally exercise the transport-neutral models and helpers.  No
provider, browser, or network operation belongs in this contract.
"""

from __future__ import annotations

import hashlib
import json
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from argus.api import routes_workflows
from argus.api.schemas import (
    BuildResearchPackWorkflowRequest,
    ResearchRequirement,
    ResearchTarget,
)
from argus.workflows.research_targets import (
    canonical_request_json,
    canonical_request_projection,
    canonical_request_sha256,
    normalize_source_prefix,
    prefix_matches,
)


def _requirement(claim_class: str = "capabilities", query: str = "what it does"):
    return {"claim_class": claim_class, "query": query}


def _target(
    name: str = "Example",
    *,
    prefix: str = "https://example.com/",
    requirements: list[dict[str, str]] | None = None,
):
    return {
        "name": name,
        "source_prefixes": [prefix],
        "requirements": requirements or [_requirement()],
    }


def _frozen_request() -> dict:
    """The exact request body from the v3 frozen-invocation contract."""

    return {
        "topic": "Managed web research and extraction stacks for AI agents",
        "official_url": None,
        "max_research_pages": 17,
        "free_only": True,
        "caller": "tonight-acceptance-v3",
        "research_targets": [
            {
                "name": "Parallel",
                "source_prefixes": [
                    "https://parallel.ai/",
                    "https://docs.parallel.ai/",
                ],
                "requirements": [
                    {
                        "claim_class": "capabilities",
                        "query": "Parallel official search extraction research API citations Basis documentation",
                    },
                    {
                        "claim_class": "pricing_eligibility",
                        "query": "Parallel official pricing free credits eligibility usage limits",
                    },
                    {
                        "claim_class": "privacy_data_handling",
                        "query": "Parallel official privacy zero data retention data protection documentation",
                    },
                ],
            },
            {
                "name": "Bright Data",
                "source_prefixes": [
                    "https://brightdata.com/",
                    "https://docs.brightdata.com/",
                ],
                "requirements": [
                    {
                        "claim_class": "protected_execution",
                        "query": "Bright Data official Web Unlocker Scraping Browser CAPTCHA residential documentation",
                    },
                    {
                        "claim_class": "pricing_eligibility",
                        "query": "Bright Data official pricing free trial credits eligibility",
                    },
                    {
                        "claim_class": "privacy_data_handling",
                        "query": "Bright Data official privacy data retention compliance policy",
                    },
                ],
            },
            {
                "name": "Linkup",
                "source_prefixes": [
                    "https://linkup.so/",
                    "https://docs.linkup.so/",
                ],
                "requirements": [
                    {
                        "claim_class": "capabilities",
                        "query": "Linkup official search fetch research JavaScript MCP documentation",
                    },
                    {
                        "claim_class": "pricing_eligibility",
                        "query": "Linkup official pricing free credits eligibility balance",
                    },
                    {
                        "claim_class": "privacy_data_handling",
                        "query": "Linkup official privacy zero data retention security documentation",
                    },
                ],
            },
            {
                "name": "Firecrawl",
                "source_prefixes": [
                    "https://firecrawl.dev/",
                    "https://www.firecrawl.dev/",
                    "https://docs.firecrawl.dev/",
                ],
                "requirements": [
                    {
                        "claim_class": "capabilities",
                        "query": "Firecrawl official scrape search crawl browser MCP documentation",
                    },
                    {
                        "claim_class": "protected_execution",
                        "query": "Firecrawl official stealth browser CAPTCHA proxy residential documentation",
                    },
                    {
                        "claim_class": "privacy_data_handling",
                        "query": "Firecrawl official privacy data retention security documentation",
                    },
                ],
            },
            {
                "name": "Argus",
                "source_prefixes": ["https://github.com/Khamel83/argus/"],
                "requirements": [
                    {
                        "claim_class": "capabilities",
                        "query": "Khamel83 Argus official search extraction workflow documentation",
                    },
                    {
                        "claim_class": "provenance_governance",
                        "query": "Khamel83 Argus official provenance caller caps spend policy HTTP MCP documentation",
                    },
                    {
                        "claim_class": "privacy_data_handling",
                        "query": "Khamel83 Argus official data retention privacy secrets documentation",
                    },
                ],
            },
        ],
    }


def test_frozen_five_target_request_is_valid_and_bounded():
    request = BuildResearchPackWorkflowRequest.model_validate(_frozen_request())

    assert len(request.research_targets) == 5
    assert sum(len(target.requirements) for target in request.research_targets) == 15
    assert request.max_research_pages == 17
    assert request.official_url is None
    assert request.model_dump(mode="json")["official_url"] is None


@pytest.mark.parametrize(
    "model, payload",
    [
        (ResearchRequirement, {"claim_class": "capabilities", "query": "q", "x": 1}),
        (
            ResearchTarget,
            {
                **_target(),
                "x": 1,
            },
        ),
        (BuildResearchPackWorkflowRequest, {"topic": "t", "x": 1}),
    ],
)
def test_contract_models_forbid_unknown_keys(model, payload):
    with pytest.raises(ValidationError):
        model.model_validate(payload)


def test_requirement_claim_class_is_literal_and_query_is_bounded():
    with pytest.raises(ValidationError):
        ResearchRequirement(claim_class="not-a-claim", query="q")
    with pytest.raises(ValidationError):
        ResearchRequirement(claim_class="capabilities", query="")
    with pytest.raises(ValidationError):
        ResearchRequirement(claim_class="capabilities", query="q" * 501)


def test_target_name_prefix_and_requirement_lengths_are_bounded():
    with pytest.raises(ValidationError):
        ResearchTarget(name="", source_prefixes=["https://example.com/"], requirements=[_requirement()])
    with pytest.raises(ValidationError):
        ResearchTarget(name="n" * 81, source_prefixes=["https://example.com/"], requirements=[_requirement()])
    with pytest.raises(ValidationError):
        ResearchTarget(name="n", source_prefixes=[], requirements=[_requirement()])
    with pytest.raises(ValidationError):
        ResearchTarget(
            name="n",
            source_prefixes=["https://example.com/"] * 5,
            requirements=[_requirement()],
        )
    with pytest.raises(ValidationError):
        ResearchTarget(name="n", source_prefixes=["https://example.com/"], requirements=[])
    with pytest.raises(ValidationError):
        ResearchTarget(
            name="n",
            source_prefixes=["https://example.com/"],
            requirements=[_requirement("capabilities", "q")] * 4,
        )


@pytest.mark.parametrize(
    "value",
    [
        "line\nbreak",
        "Authorization: Bearer abcdefghijkl",
        "X-API-Key: secret-value",
        "-----BEGIN PRIVATE KEY-----",
        "/Users/alice/private/report.txt",
        "C:\\Users\\alice\\token.txt",
        "../private/token.txt",
    ],
)
def test_public_topic_target_and_requirement_strings_reject_controls_credentials_and_paths(value):
    with pytest.raises(ValidationError):
        BuildResearchPackWorkflowRequest(topic=value)
    with pytest.raises(ValidationError):
        ResearchRequirement(claim_class="capabilities", query=value)
    with pytest.raises(ValidationError):
        ResearchTarget(
            name=value,
            source_prefixes=["https://example.com/"],
            requirements=[_requirement()],
        )


@pytest.mark.parametrize(
    "value",
    [
        "Bearer x",
        "Authorization: Basic YQ==",
        "Basic YQ==",
        "api_key=x",
        "X-API-Key: x",
        "-----BEGIN OPENSSH PRIVATE KEY-----",
        "ssh-ed25519 AAAA",
        "/Applications/Argus/config.json",
        "/Volumes/Secrets/token.txt",
        "/dev/null",
        "/workspace/project/.env",
        "/tmp/argus-token",
        "file://example.com/public",
        "query%0d%0aAuthorization: Bearer x",
    ],
)
def test_public_text_rejects_short_auth_markers_encoded_controls_and_common_paths(value):
    with pytest.raises(ValidationError):
        BuildResearchPackWorkflowRequest(topic=value)
    with pytest.raises(ValidationError):
        ResearchRequirement(claim_class="capabilities", query=value)
    with pytest.raises(ValidationError):
        ResearchTarget(
            name=value,
            source_prefixes=["https://example.com/"],
            requirements=[_requirement()],
        )


def test_public_text_keeps_ordinary_documentation_phrases_allowed():
    request = BuildResearchPackWorkflowRequest(
        topic="Basic capabilities and API key rotation guidance",
        caller="public-docs",
    )
    assert request.topic.startswith("Basic capabilities")


def test_caller_label_is_bounded_and_scanned():
    with pytest.raises(ValidationError):
        BuildResearchPackWorkflowRequest(topic="t", caller="c" * 101)
    with pytest.raises(ValidationError):
        BuildResearchPackWorkflowRequest(topic="t", caller="caller\x00")
    with pytest.raises(ValidationError):
        BuildResearchPackWorkflowRequest(topic="t", caller="Authorization: Bearer abcdefgh")


def test_target_names_and_claim_classes_are_unique():
    with pytest.raises(ValidationError, match="target name"):
        BuildResearchPackWorkflowRequest(
            topic="t",
            research_targets=[
                _target("Argus"),
                _target("argus", prefix="https://other.example.com/"),
            ],
        )
    with pytest.raises(ValidationError, match="claim_class"):
        ResearchTarget(
            name="n",
            source_prefixes=["https://example.com/"],
            requirements=[_requirement(), _requirement()],
        )


def test_request_limits_targets_requirements_and_targeted_page_budget():
    nine_targets = [
        _target(str(i), prefix=f"https://site{i}.example.com/") for i in range(9)
    ]
    with pytest.raises(ValidationError):
        BuildResearchPackWorkflowRequest(topic="t", research_targets=nine_targets)

    seventeen_requirements = [
        _target(
            str(i),
            prefix=f"https://site{i}.example.com/",
            requirements=[_requirement("capabilities", f"q-{i}")],
        )
        for i in range(8)
    ]
    seventeen_requirements[0]["requirements"] = [
        _requirement("capabilities", "q-0"),
        _requirement("pricing_eligibility", "q-1"),
        _requirement("privacy_data_handling", "q-2"),
    ]
    seventeen_requirements[1]["requirements"] = [
        _requirement("capabilities", "q-3"),
        _requirement("pricing_eligibility", "q-4"),
        _requirement("privacy_data_handling", "q-5"),
    ]
    seventeen_requirements[2]["requirements"] = [
        _requirement("capabilities", "q-6"),
        _requirement("pricing_eligibility", "q-7"),
        _requirement("privacy_data_handling", "q-8"),
    ]
    seventeen_requirements[3]["requirements"] = [
        _requirement("capabilities", "q-9"),
        _requirement("pricing_eligibility", "q-10"),
        _requirement("privacy_data_handling", "q-11"),
    ]
    seventeen_requirements[4]["requirements"] = [
        _requirement("capabilities", "q-12"),
        _requirement("pricing_eligibility", "q-13"),
        _requirement("privacy_data_handling", "q-14"),
    ]
    seventeen_requirements[5]["requirements"] = [
        _requirement("capabilities", "q-15"),
        _requirement("pricing_eligibility", "q-16"),
    ]
    with pytest.raises(ValidationError, match="16"):
        BuildResearchPackWorkflowRequest(
            topic="t",
            max_research_pages=100,
            research_targets=seventeen_requirements,
        )

    with pytest.raises(ValidationError, match="page"):
        BuildResearchPackWorkflowRequest(
            topic="t",
            max_research_pages=2,
            research_targets=[
                _target(
                    "one",
                    requirements=[
                        _requirement("capabilities"),
                        _requirement("pricing_eligibility", "price"),
                    ],
                )
            ],
        )


def test_official_url_and_targets_are_mutually_exclusive_but_explicit_null_is_valid():
    request = BuildResearchPackWorkflowRequest(
        topic="t",
        official_url=None,
        research_targets=[_target()],
        max_research_pages=2,
    )
    assert request.official_url is None

    with pytest.raises(ValidationError):
        BuildResearchPackWorkflowRequest(
            topic="t",
            official_url="https://example.com/",
            research_targets=[_target()],
            max_research_pages=2,
        )


@pytest.mark.parametrize(
    "url",
    [
        "https://user:pass@example.com/",
        "https://example.com/?q=unsafe",
        "https://example.com/#fragment",
        "https://*.example.com/",
        "https://192.0.2.1/",
        "https://localhost/",
        "https://service.local/",
        "https://10.0.0.4/",
        "https://com/",
        "https://co.uk/",
        "https://foo.test/",
        "https://foo.invalid/",
        "https://foo.home.arpa/",
        "https://github.io/",
    ],
)
def test_official_url_rejects_noncanonical_or_nonpublic_https(url):
    with pytest.raises(ValidationError):
        BuildResearchPackWorkflowRequest(topic="t", official_url=url)


def test_official_url_is_https_public_and_bounded():
    with pytest.raises(ValidationError):
        BuildResearchPackWorkflowRequest(topic="t", official_url="http://example.com/")
    with pytest.raises(ValidationError):
        BuildResearchPackWorkflowRequest(
            topic="t", official_url="https://example.com/" + "a" * 2048
        )
    request = BuildResearchPackWorkflowRequest(
        topic="t", official_url="HTTPS://EXAMPLE.COM/docs"
    )
    assert str(request.official_url) == "https://example.com/docs"


@pytest.mark.parametrize(
    "prefix",
    [
        "https://user:pass@example.com/",
        "https://example.com/?q=unsafe",
        "https://example.com/#fragment",
        "https://*.example.com/",
        "https://192.0.2.1/",
        "https://localhost/",
        "https://service.local/",
        "https://10.0.0.4/",
        "https://com/",
        "https://co.uk/",
    ],
)
def test_source_prefix_rejects_noncanonical_or_nonpublic_https(prefix):
    with pytest.raises(ValidationError):
        ResearchTarget(name="n", source_prefixes=[prefix], requirements=[_requirement()])


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com/a/../b",
        "https://example.com/a/%2e%2e/b",
        "https://example.com/a/%2E%2E%2Fb",
        "https://example.com/a/..%2Fsecret",
        "https://example.com/a/%0d%0aX",
        "https://example.com/a/%250aX",
    ],
)
def test_official_url_rejects_raw_dot_traversal_and_encoded_controls(url):
    with pytest.raises(ValidationError):
        BuildResearchPackWorkflowRequest(topic="t", official_url=url)


@pytest.mark.parametrize(
    "prefix",
    [
        "https://example.com/a/../b",
        "https://example.com/a/%2e%2e/b",
        "https://example.com/a/%2E%2E%2Fb",
        "https://example.com/a/..%2Fsecret",
        "https://example.com/a/%0d%0aX",
        "https://example.com/a/%250aX",
    ],
)
def test_source_prefix_rejects_raw_dot_traversal_and_encoded_controls(prefix):
    with pytest.raises(ValidationError):
        ResearchTarget(name="n", source_prefixes=[prefix], requirements=[_requirement()])


def test_source_prefixes_normalize_default_port_host_case_idna_and_slash():
    assert normalize_source_prefix("HTTPS://EXAMPLE.COM:443/docs/") == (
        "https://example.com/docs"
    )
    assert normalize_source_prefix("https://bücher.example.com/") == (
        "https://xn--bcher-kva.example.com"
    )


def test_duplicate_or_ancestor_source_prefixes_fail_within_one_target():
    with pytest.raises(ValidationError, match="overlap"):
        ResearchTarget(
            name="n",
            source_prefixes=["https://example.com/docs", "https://EXAMPLE.com:443/docs/"],
            requirements=[_requirement()],
        )
    with pytest.raises(ValidationError, match="overlap"):
        ResearchTarget(
            name="n",
            source_prefixes=["https://example.com/docs", "https://example.com/docs/api"],
            requirements=[_requirement()],
        )


def test_prefix_overlap_is_rejected_across_targets():
    with pytest.raises(ValidationError, match="overlap"):
        BuildResearchPackWorkflowRequest(
            topic="t",
            research_targets=[
                _target("a", prefix="https://example.com/docs"),
                _target("b", prefix="https://example.com/docs/api"),
            ],
            max_research_pages=3,
        )


@pytest.mark.parametrize(
    "candidate, expected",
    [
        ("https://github.com/Khamel83/argus", True),
        ("https://github.com/Khamel83/argus/", True),
        ("https://github.com/Khamel83/argus/issues/1", True),
        ("https://github.com/Khamel83/argusmatic", False),
        ("https://github.com/Khamel83/argusmatic/issue", False),
        ("https://github.com/Khamel83/other", False),
    ],
)
def test_prefix_matching_is_path_boundary_aware(candidate, expected):
    assert prefix_matches("https://github.com/Khamel83/argus/", candidate) is expected


def test_prefix_matching_normalizes_host_port_idna_and_trailing_slash():
    assert prefix_matches(
        "HTTPS://EXAMPLE.COM:443/docs/",
        "https://example.com/docs/child",
    )
    assert prefix_matches(
        "https://bücher.example.com/",
        "https://xn--bcher-kva.example.com/",
    )


def test_legacy_path_has_empty_targets_and_default_page_budget():
    request = BuildResearchPackWorkflowRequest(topic="legacy")
    assert request.research_targets == []
    assert request.max_research_pages == 40
    assert "research_targets" in request.model_dump(mode="json")


def test_canonical_projection_uses_json_strings_and_exact_null_semantics():
    request = BuildResearchPackWorkflowRequest.model_validate(_frozen_request())
    projection = canonical_request_projection(request)
    assert projection["official_url"] is None
    assert isinstance(projection["research_targets"][0]["source_prefixes"][0], str)
    assert projection["research_targets"][0]["source_prefixes"][0] == (
        "https://parallel.ai/"
    )

    absent = BuildResearchPackWorkflowRequest(topic="t")
    explicit = BuildResearchPackWorkflowRequest(topic="t", official_url=None)
    assert "official_url" not in canonical_request_projection(absent)
    assert canonical_request_projection(explicit)["official_url"] is None
    assert canonical_request_sha256(absent) != canonical_request_sha256(explicit)


def test_canonical_json_and_sha256_are_exact_and_deterministic():
    request = BuildResearchPackWorkflowRequest.model_validate(_frozen_request())
    expected_json = json.dumps(
        canonical_request_projection(request),
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    expected_hash = hashlib.sha256(expected_json.encode("utf-8")).hexdigest()
    assert canonical_request_json(request) == expected_json
    assert canonical_request_sha256(request) == expected_hash
    assert canonical_request_sha256(request) == canonical_request_sha256(
        BuildResearchPackWorkflowRequest.model_validate(
            json.loads(json.dumps(_frozen_request()))
        )
    )


def test_canonical_projection_accepts_json_mode_mapping():
    request = BuildResearchPackWorkflowRequest(
        topic="t",
        research_targets=[_target()],
        max_research_pages=2,
    )
    projection = canonical_request_projection(request.model_dump(mode="json"))
    assert projection["official_url"] is None
    assert isinstance(projection["research_targets"][0]["source_prefixes"][0], str)


@pytest.mark.asyncio
async def test_legacy_build_route_serializes_official_url_before_service(monkeypatch):
    captured = {}

    class FakeWorkflows:
        async def start_build_research_pack(self, **kwargs):
            captured.update(kwargs)
            return captured

    monkeypatch.setattr(routes_workflows, "_to_response", lambda run: run)
    request = SimpleNamespace(
        state=SimpleNamespace(caller_identity="principal"),
        app=SimpleNamespace(state=SimpleNamespace()),
    )
    req = BuildResearchPackWorkflowRequest(
        topic="legacy",
        official_url="https://EXAMPLE.com/docs",
    )

    response = await routes_workflows.build_research_pack(req, request, FakeWorkflows())

    assert response is captured
    assert captured["official_url"] == "https://example.com/docs"
    assert isinstance(captured["official_url"], str)
