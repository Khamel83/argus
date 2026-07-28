"""Pure, exact, provider-aware evidence fusion from ADR 0003."""

from __future__ import annotations

import ipaddress
import hashlib
import importlib.metadata
import importlib.resources
import math
from dataclasses import dataclass, replace
from datetime import date, datetime, time, timedelta, timezone
from fractions import Fraction
from urllib.parse import urlsplit, urlunsplit

from tld import get_fld

from argus.broker.planning import RetrievalPlan
from argus.broker.provider_evidence import (
    ContractConfidence,
    EvidenceKind,
    FailureCategory,
    FilterStrength,
    ProviderSearchBatch,
    PublicationEvidence,
    PublicationPrecision,
    PublicationSource,
    ResultObservation,
    TemporalClaimKind,
    TranslationPrecision,
)
from argus.contracts.outcomes import CanonicalOutcome
from argus.models import (
    ClusterRankingRecord,
    ComputedAnswerArtifact,
    DiversityDecision,
    DiversitySelection,
    DuplicateRelation,
    EvidenceFloorTrace,
    FilteredObservation,
    FusionOutcome,
    FusionPolicy,
    ProviderName,
    RRFContribution,
    RankedResultCluster,
    RankingTrace,
    SearchMode,
    SearchResult,
    SiteDiversityTrace,
)

_UNRESERVED = frozenset(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~"
)
_HEX = frozenset("0123456789abcdefABCDEF")
_APPROVED_CONFIDENCE = frozenset(
    {
        ContractConfidence.OFFICIAL_CONTRACT,
        ContractConfidence.OWNED_LIBRARY_CONTRACT,
        ContractConfidence.FIXTURE_BACKED,
    }
)
_PHASES = ("normalize_filter", "cluster", "rank", "diversify")

PSL_SNAPSHOT_SHA256 = (
    "abf32ce9987d505b89765d76f35760543851235508f1f426b5b259a2062b5f68"
)
PSL_SNAPSHOT_ID = f"tld-0.13.2-psl-{PSL_SNAPSHOT_SHA256[:16]}"
PSL_DOMAIN_POLICY_VERSION = f"domain-v1-{PSL_SNAPSHOT_ID}"
_HARD_MAX_PROVIDER_BATCHES = 14
_HARD_MAX_OBSERVATIONS_PER_PROVIDER = 50
_HARD_MAX_TOTAL_OBSERVATIONS = 700

_FRESHNESS_PROOF_REGISTRY = {
    "freshness-v1": frozenset(
        {
            (
                ProviderName.EXA,
                "2026-07-27-v1",
                "exa-search-contract",
                "iso8601-v1",
            ),
            (
                ProviderName.PARALLEL,
                "2026-07-27-v1",
                "parallel-search-contract",
                "iso8601-v1",
            ),
            (
                ProviderName.VALYU,
                "2026-07-27-v1",
                "valyu-search-contract",
                "iso8601-v1",
            ),
        }
    )
}
_SUCCESSFUL_EMPTY_REGISTRY = {
    "freshness-v1": frozenset(
        {
            (provider, "2026-07-27-v1", "successful-empty-v1")
            for provider in (
                ProviderName.EXA,
                ProviderName.PARALLEL,
                ProviderName.VALYU,
            )
        }
    )
}


@dataclass(frozen=True, slots=True)
class _Candidate:
    observation: ResultObservation
    document_key: str
    interval: tuple[datetime, datetime] | None


@dataclass(frozen=True, slots=True)
class _Cluster:
    document_key: str
    observations: tuple[ResultObservation, ...]


def _pinned_psl_is_verified() -> bool:
    try:
        version = importlib.metadata.version("tld")
        snapshot = (
            importlib.resources.files("tld")
            .joinpath("res/effective_tld_names.dat.txt")
            .read_bytes()
        )
    except (FileNotFoundError, ImportError, OSError):
        return False
    return (
        version == "0.13.2"
        and hashlib.sha256(snapshot).hexdigest() == PSL_SNAPSHOT_SHA256
    )


def _normalize_percent(value: str) -> str:
    output: list[str] = []
    index = 0
    while index < len(value):
        if (
            value[index] == "%"
            and index + 2 < len(value)
            and value[index + 1] in _HEX
            and value[index + 2] in _HEX
        ):
            byte = int(value[index + 1 : index + 3], 16)
            character = chr(byte)
            if character in _UNRESERVED:
                output.append(character)
            else:
                output.append(f"%{byte:02X}")
            index += 3
            continue
        output.append(value[index])
        index += 1
    return "".join(output)


def _remove_dot_segments(path: str) -> str:
    """RFC 3986 dot removal without collapsing slash or trailing-slash meaning."""
    source = path
    output = ""
    while source:
        if source.startswith("../"):
            source = source[3:]
        elif source.startswith("./"):
            source = source[2:]
        elif source.startswith("/./"):
            source = "/" + source[3:]
        elif source == "/.":
            source = "/"
        elif source.startswith("/../"):
            source = "/" + source[4:]
            output = output.rsplit("/", 1)[0]
        elif source == "/..":
            source = "/"
            output = output.rsplit("/", 1)[0]
        elif source in {".", ".."}:
            source = ""
        else:
            slash = source.find("/", 1 if source.startswith("/") else 0)
            if slash < 0:
                output += source
                source = ""
            else:
                output += source[:slash]
                source = source[slash:]
    return output


def conservative_document_key(url: str) -> str | None:
    """Return the v1 proven-document URL key, or ``None`` for an invalid URL."""
    if not isinstance(url, str) or not url or len(url) > 8_192:
        return None
    try:
        parts = urlsplit(url)
        port = parts.port
    except (UnicodeError, ValueError):
        return None
    scheme = parts.scheme.lower()
    if (
        scheme not in {"http", "https"}
        or not parts.hostname
        or parts.username is not None
        or parts.password is not None
    ):
        return None
    try:
        host = parts.hostname.rstrip(".").lower().encode("idna").decode("ascii")
    except UnicodeError:
        return None
    if not host:
        return None
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        display_host = host
    else:
        display_host = f"[{address.compressed}]" if address.version == 6 else address.compressed
    default_port = (scheme == "http" and port == 80) or (
        scheme == "https" and port == 443
    )
    netloc = display_host if port is None or default_port else f"{display_host}:{port}"
    path = _remove_dot_segments(_normalize_percent(parts.path))
    query = _normalize_percent(parts.query)
    return urlunsplit((scheme, netloc, path, query, ""))


def _site_key(document_key: str) -> str:
    host = (urlsplit(document_key).hostname or "").lower()
    try:
        return ipaddress.ip_address(host).compressed
    except ValueError:
        pass
    return get_fld(
        document_key,
        fail_silently=True,
        fix_protocol=True,
        search_public=True,
        search_private=True,
    ) or host


def _domain_allowed(plan: RetrievalPlan, document_key: str) -> bool:
    host = (urlsplit(document_key).hostname or "").lower()

    def matches(domain: str) -> bool:
        return host == domain or host.endswith("." + domain)

    if plan.domains.include and not any(matches(item) for item in plan.domains.include):
        return False
    return not any(matches(item) for item in plan.domains.exclude)


def _approved_publication(
    plan: RetrievalPlan,
    batch: ProviderSearchBatch,
    publication: PublicationEvidence | None,
) -> bool:
    if publication is None:
        return False
    contract = (
        batch.provider,
        batch.provider_contract_version,
        publication.semantic_contract_ref,
        publication.parser_version,
    )
    field_name = (publication.raw_field_name or "").lower()
    non_publication_field = any(
        marker in field_name
        for marker in ("modified", "updated", "indexed", "created", "crawled")
    )
    return (
        publication.claim_kind is TemporalClaimKind.PUBLISHED
        and publication.source
        in {PublicationSource.PROVIDER_FIELD, PublicationSource.PROVIDER_AGE}
        and not non_publication_field
        and publication.contract_confidence in _APPROVED_CONFIDENCE
        and publication.semantic_contract_ref is not None
        and publication.parser_version is not None
        and contract
        in _FRESHNESS_PROOF_REGISTRY.get(
            plan.freshness_policy_version,
            frozenset(),
        )
    )


def _end_of_month(value: date) -> date:
    if value.month == 12:
        return date(value.year + 1, 1, 1) - timedelta(days=1)
    return date(value.year, value.month + 1, 1) - timedelta(days=1)


def _publication_interval(
    publication: PublicationEvidence,
) -> tuple[datetime, datetime] | None:
    if publication.published_at_utc is not None:
        instant = publication.published_at_utc.astimezone(timezone.utc)
        return instant, instant
    value = publication.published_date
    if value is None:
        return None
    if publication.precision in {
        PublicationPrecision.DATE,
        PublicationPrecision.PROVIDER_AGE,
    }:
        start_day, end_day = value, value
    elif publication.precision is PublicationPrecision.MONTH:
        start_day = value.replace(day=1)
        end_day = _end_of_month(start_day)
    elif publication.precision is PublicationPrecision.YEAR:
        start_day = date(value.year, 1, 1)
        end_day = date(value.year, 12, 31)
    else:
        return None
    return (
        datetime.combine(start_day, time.min, tzinfo=timezone.utc),
        datetime.combine(end_day, time.max, tzinfo=timezone.utc),
    )


def _window_interval(plan: RetrievalPlan) -> tuple[datetime | None, datetime | None]:
    start = (
        datetime.combine(plan.freshness.start_date, time.min, tzinfo=timezone.utc)
        if plan.freshness.start_date is not None
        else None
    )
    end = (
        datetime.combine(plan.freshness.end_date, time.max, tzinfo=timezone.utc)
        if plan.freshness.end_date is not None
        else None
    )
    return start, end


def _inside_window(
    interval: tuple[datetime, datetime],
    window: tuple[datetime | None, datetime | None],
) -> bool:
    start, end = interval
    window_start, window_end = window
    return (window_start is None or start >= window_start) and (
        window_end is None or end <= window_end
    )


def _freshness_requested(plan: RetrievalPlan) -> bool:
    return (
        plan.freshness.requested_relative is not None
        or plan.freshness.start_date is not None
        or plan.freshness.end_date is not None
    )


def _normalize_filter(
    plan: RetrievalPlan,
    batches: tuple[ProviderSearchBatch, ...],
) -> tuple[
    tuple[_Candidate, ...],
    tuple[FilteredObservation, ...],
    dict[str, tuple[tuple[datetime, datetime], ...]],
    tuple[ComputedAnswerArtifact, ...],
]:
    candidates: list[_Candidate] = []
    trace: list[FilteredObservation] = []
    claims: dict[str, list[tuple[datetime, datetime]]] = {}
    computed_answers: list[ComputedAnswerArtifact] = []
    requested = _freshness_requested(plan)
    window = _window_interval(plan)
    for batch in sorted(batches, key=lambda item: item.provider.value):
        for observation in batch.observations:
            key = conservative_document_key(observation.url)
            reason = "eligible"
            interval = None
            if observation.source_kind is EvidenceKind.COMPUTED_ANSWER:
                eligible_answer = (
                    plan.intent is SearchMode.GROUNDING
                    and not requested
                    and batch.provider in plan.candidate_providers
                    and bool(observation.snippet.primary_text.strip())
                )
                if requested:
                    disposition = "freshness_ineligible"
                elif plan.intent is not SearchMode.GROUNDING:
                    disposition = "mode_ineligible"
                elif batch.provider not in plan.candidate_providers:
                    disposition = "provider_ineligible"
                elif not observation.snippet.primary_text.strip():
                    disposition = "empty_answer"
                else:
                    disposition = "eligible_grounding"
                computed_answers.append(
                    ComputedAnswerArtifact(
                        provider=observation.provider,
                        provider_rank=observation.provider_rank,
                        url=observation.url,
                        title=observation.title,
                        text=observation.snippet.primary_text,
                        egress=observation.egress.value,
                        machine=observation.machine,
                        observed_at=observation.observed_at,
                        eligible_for_grounding=eligible_answer,
                        disposition=disposition,
                    )
                )
                reason = "computed_answer_artifact"
            elif key is None:
                reason = "invalid_url"
            elif not _domain_allowed(plan, key):
                reason = "domain_ineligible"
            elif requested:
                if _approved_publication(plan, batch, observation.publication):
                    interval = _publication_interval(observation.publication)  # type: ignore[arg-type]
                if interval is None:
                    reason = "freshness_unproven"
                else:
                    claims.setdefault(key, []).append(interval)
                    if not _inside_window(interval, window):
                        reason = "out_of_range"
            if key is not None and reason == "eligible":
                candidates.append(_Candidate(observation, key, interval))
            trace.append(
                FilteredObservation(
                    observation.provider,
                    observation.provider_rank,
                    observation.url,
                    key,
                    reason == "eligible",
                    reason,
                )
            )
    return (
        tuple(candidates),
        tuple(trace),
        {key: tuple(value) for key, value in claims.items()},
        tuple(computed_answers),
    )


def _claims_conflict(intervals: tuple[tuple[datetime, datetime], ...]) -> bool:
    return bool(intervals) and max(item[0] for item in intervals) > min(
        item[1] for item in intervals
    )


def _cluster(
    candidates: tuple[_Candidate, ...],
    filtered: tuple[FilteredObservation, ...],
    claims: dict[str, tuple[tuple[datetime, datetime], ...]],
) -> tuple[
    tuple[_Cluster, ...],
    tuple[FilteredObservation, ...],
    tuple[DuplicateRelation, ...],
]:
    conflicts = frozenset(
        key for key, intervals in claims.items() if _claims_conflict(intervals)
    )
    if conflicts:
        candidates = tuple(
            candidate
            for candidate in candidates
            if candidate.document_key not in conflicts
        )
        filtered = tuple(
            replace(item, eligible=False, reason="conflicting_publication")
            if item.document_key in conflicts
            else item
            for item in filtered
        )
    groups: dict[str, list[ResultObservation]] = {}
    for candidate in candidates:
        groups.setdefault(candidate.document_key, []).append(candidate.observation)
    clusters: list[_Cluster] = []
    relations: list[DuplicateRelation] = []
    for key in sorted(groups):
        observations = tuple(
            sorted(
                groups[key],
                key=lambda item: (item.provider.value, item.provider_rank, item.url),
            )
        )
        clusters.append(_Cluster(key, observations))
        first = observations[0]
        for item in observations[1:]:
            relations.append(
                DuplicateRelation(
                    key,
                    first.provider,
                    first.provider_rank,
                    item.provider,
                    item.provider_rank,
                )
            )
    return tuple(clusters), filtered, tuple(relations)


def fusion_order_key(cluster: RankedResultCluster) -> tuple[object, ...]:
    """Return the complete five-part exact ADR 0003 ordering key."""
    return (
        -Fraction(cluster.score_numerator, cluster.score_denominator),
        cluster.best_provider_rank,
        -cluster.contributor_count,
        cluster.smallest_provider.value,
        cluster.cluster_sort_key.encode("utf-8"),
    )


def _rank(
    plan: RetrievalPlan,
    clusters: tuple[_Cluster, ...],
    policy: FusionPolicy,
) -> tuple[tuple[RankedResultCluster, ...], RankingTrace]:
    ranked: list[RankedResultCluster] = []
    for cluster in clusters:
        best_by_provider: dict[ProviderName, int] = {}
        for observation in cluster.observations:
            best_by_provider[observation.provider] = min(
                observation.provider_rank,
                best_by_provider.get(observation.provider, observation.provider_rank),
            )
        contributions = tuple(
            RRFContribution(provider, rank, 1, policy.rrf_k + rank + 1)
            for provider, rank in sorted(
                best_by_provider.items(), key=lambda item: item[0].value
            )
        )
        score = sum(
            (Fraction(item.numerator, item.denominator) for item in contributions),
            Fraction(),
        )
        representative = min(
            cluster.observations,
            key=lambda item: (
                item.provider_rank,
                item.provider.value,
                item.url.encode("utf-8"),
            ),
        )
        ranked.append(
            RankedResultCluster(
                cluster_sort_key=cluster.document_key,
                observations=cluster.observations,
                representative_url=representative.url,
                representative_title=representative.title,
                representative_snippet=representative.snippet.primary_text,
                representative_provider=representative.provider,
                representative_rank=representative.provider_rank,
                site_key=_site_key(cluster.document_key),
                contributions=contributions,
                score_numerator=score.numerator,
                score_denominator=score.denominator,
                best_provider_rank=min(best_by_provider.values()),
                contributor_count=len(best_by_provider),
                smallest_provider=min(best_by_provider, key=lambda item: item.value),
                base_rank=-1,
                output_rank=-1,
            )
        )
    ranked.sort(key=fusion_order_key)
    ranked = [replace(item, base_rank=index) for index, item in enumerate(ranked)]
    records = tuple(
        ClusterRankingRecord(
            cluster_sort_key=item.cluster_sort_key,
            base_rank=item.base_rank,
            score_numerator=item.score_numerator,
            score_denominator=item.score_denominator,
            best_provider_rank=item.best_provider_rank,
            contributor_count=item.contributor_count,
            smallest_provider=item.smallest_provider,
            contributions=item.contributions,
        )
        for item in ranked
    )
    trace = RankingTrace(
        plan.ranking_policy_version,
        policy.rrf_k,
        tuple(item.cluster_sort_key for item in ranked),
        tuple(
            (
                item.cluster_sort_key,
                item.score_numerator,
                item.score_denominator,
            )
            for item in ranked
        ),
        records,
    )
    return tuple(ranked), trace


def _diversify(
    plan: RetrievalPlan,
    base: tuple[RankedResultCluster, ...],
    computed_answers: tuple[ComputedAnswerArtifact, ...],
) -> tuple[
    tuple[RankedResultCluster, ...],
    SiteDiversityTrace,
    EvidenceFloorTrace,
]:
    required_clusters = min(3, plan.result_limit)
    required_sites = min(2, required_clusters)
    available_sites = len({item.site_key for item in base})
    events: dict[str, list[str]] = {
        item.cluster_sort_key: [] for item in base
    }
    if plan.intent is not SearchMode.RESEARCH:
        selected = list(base[: plan.result_limit])
        for item in selected:
            events[item.cluster_sort_key].append("base_order")
        for item in base[plan.result_limit :]:
            events[item.cluster_sort_key].append("omit_result_limit")
        floor_clusters = (
            1 if plan.intent in {SearchMode.GROUNDING, SearchMode.RECOVERY} else 0
        )
        floor_sites = 1 if floor_clusters else 0
        applied = False
    else:
        selected = []
        selected_keys: set[str] = set()
        selected_sites: set[str] = set()
        for item in base:
            if item.site_key not in selected_sites:
                selected.append(item)
                selected_keys.add(item.cluster_sort_key)
                selected_sites.add(item.site_key)
                events[item.cluster_sort_key].append("coverage")
            if len(selected_sites) == required_sites:
                break
        counts: dict[str, int] = {}
        for item in selected:
            counts[item.site_key] = counts.get(item.site_key, 0) + 1
        deferred: list[RankedResultCluster] = []
        for item in base:
            if item.cluster_sort_key in selected_keys:
                continue
            if len(selected) >= plan.result_limit:
                events[item.cluster_sort_key].append("omit_result_limit")
                continue
            if counts.get(item.site_key, 0) < 2:
                selected.append(item)
                selected_keys.add(item.cluster_sort_key)
                counts[item.site_key] = counts.get(item.site_key, 0) + 1
                events[item.cluster_sort_key].append("fill")
            else:
                deferred.append(item)
                events[item.cluster_sort_key].append("defer_soft_cap")
        for item in deferred:
            if len(selected) >= plan.result_limit:
                events[item.cluster_sort_key].append("omit_result_limit")
                continue
            selected.append(item)
            selected_keys.add(item.cluster_sort_key)
            counts[item.site_key] = counts.get(item.site_key, 0) + 1
            events[item.cluster_sort_key].append("backfill_relaxed")
        floor_clusters, floor_sites, applied = required_clusters, required_sites, True
    output = tuple(
        replace(item, output_rank=index) for index, item in enumerate(selected)
    )
    output_by_key = {item.cluster_sort_key: item for item in output}
    selections = tuple(
        DiversitySelection(
            item.cluster_sort_key,
            item.base_rank,
            item.output_rank,
            item.site_key,
            (
                "relax"
                if "backfill_relaxed" in events[item.cluster_sort_key]
                else events[item.cluster_sort_key][-1]
            ),
            available_sites,
        )
        for item in output
    )
    actual_sites = len({item.site_key for item in output})
    eligible_computed = sum(
        item.eligible_for_grounding for item in computed_answers
    )
    floor_passed = (
        (len(output) + eligible_computed) >= floor_clusters
        and (actual_sites >= floor_sites or eligible_computed > 0)
    )
    decisions: list[DiversityDecision] = []
    selected_so_far: set[str] = set()
    per_site: dict[str, int] = {}
    for item in sorted(
        output,
        key=lambda candidate: candidate.output_rank,
    ):
        before_sites = len(selected_so_far)
        before_site = per_site.get(item.site_key, 0)
        selected_so_far.add(item.site_key)
        per_site[item.site_key] = before_site + 1
        decisions.append(
            DiversityDecision(
                cluster_sort_key=item.cluster_sort_key,
                base_rank=item.base_rank,
                output_rank=item.output_rank,
                site_key=item.site_key,
                events=tuple(events[item.cluster_sort_key]),
                selected_site_count_before=before_sites,
                selected_site_count_after=len(selected_so_far),
                site_selected_count_before=before_site,
                site_selected_count_after=before_site + 1,
            )
        )
    for item in base:
        if item.cluster_sort_key in output_by_key:
            continue
        if not events[item.cluster_sort_key]:
            events[item.cluster_sort_key].append("omit_result_limit")
        decisions.append(
            DiversityDecision(
                cluster_sort_key=item.cluster_sort_key,
                base_rank=item.base_rank,
                output_rank=None,
                site_key=item.site_key,
                events=tuple(events[item.cluster_sort_key]),
                selected_site_count_before=actual_sites,
                selected_site_count_after=actual_sites,
                site_selected_count_before=per_site.get(item.site_key, 0),
                site_selected_count_after=per_site.get(item.site_key, 0),
            )
        )
    decisions.sort(key=lambda item: item.base_rank)
    floor = EvidenceFloorTrace(
        floor_clusters,
        floor_sites,
        len(output),
        actual_sites,
        floor_passed,
        eligible_computed,
        "floor_passed" if floor_passed else "floor_unmet",
    )
    return (
        output,
        SiteDiversityTrace(
            applied=applied,
            psl_snapshot=PSL_SNAPSHOT_ID,
            psl_snapshot_sha256=PSL_SNAPSHOT_SHA256,
            available_sites=available_sites,
            required_sites=floor_sites,
            selections=selections,
            decisions=tuple(decisions),
            selected_sites=actual_sites,
            passed=floor_passed,
            disposition="floor_passed" if floor_passed else "floor_unmet",
        ),
        floor,
    )


def _strict_empty_proven(
    plan: RetrievalPlan,
    batch: ProviderSearchBatch,
) -> bool:
    translation = batch.request_evidence.freshness_translation
    requested_relative = (
        plan.freshness.requested_relative.value
        if plan.freshness.requested_relative is not None
        else None
    )
    registry_key = (
        batch.provider,
        batch.provider_contract_version,
        translation.successful_empty_contract_ref if translation else None,
    )
    return (
        batch.failure is not None
        and batch.failure.category is FailureCategory.EMPTY
        and not batch.observations
        and translation is not None
        and translation.precision is TranslationPrecision.EXACT
        and translation.strength is FilterStrength.STRICT_CONTRACT
        and translation.requested_relative == requested_relative
        and translation.resolved_start_date == plan.freshness.start_date
        and translation.resolved_end_date == plan.freshness.end_date
        and translation.applied_start_date == plan.freshness.start_date
        and translation.applied_end_date == plan.freshness.end_date
        and registry_key
        in _SUCCESSFUL_EMPTY_REGISTRY.get(
            plan.freshness_policy_version,
            frozenset(),
        )
    )


def _validate_inputs(
    plan: RetrievalPlan,
    batches: tuple[ProviderSearchBatch, ...],
    policy: FusionPolicy,
) -> str | None:
    if not isinstance(plan, RetrievalPlan):
        raise TypeError("validated retrieval plan is required")
    if not isinstance(policy, FusionPolicy):
        raise TypeError("fusion policy must be typed")
    if not isinstance(batches, tuple):
        raise TypeError("provider batches must be an immutable normalized tuple")
    invalid_numeric = (
        policy.rrf_k != 60
        or type(policy.max_provider_batches) is not int
        or policy.max_provider_batches <= 0
        or policy.max_provider_batches > _HARD_MAX_PROVIDER_BATCHES
        or type(policy.max_observations_per_provider) is not int
        or policy.max_observations_per_provider <= 0
        or policy.max_observations_per_provider
        > _HARD_MAX_OBSERVATIONS_PER_PROVIDER
        or type(policy.max_total_observations) is not int
        or policy.max_total_observations <= 0
        or policy.max_total_observations > _HARD_MAX_TOTAL_OBSERVATIONS
        or not callable(policy.monotonic)
        or type(policy.activatable) is not bool
        or isinstance(policy.publication_reserve_seconds, bool)
        or not isinstance(policy.publication_reserve_seconds, (int, float))
        or not math.isfinite(float(policy.publication_reserve_seconds))
        or policy.publication_reserve_seconds < 0
        or (
            policy.operation_deadline is not None
            and (
                isinstance(policy.operation_deadline, bool)
                or not isinstance(policy.operation_deadline, (int, float))
                or not math.isfinite(float(policy.operation_deadline))
            )
        )
        or (
            policy.activatable
            and (
                policy.operation_deadline is None
                or policy.publication_reserve_seconds <= 0
            )
        )
    )
    if invalid_numeric:
        return "fusion_policy_invalid"
    if (
        plan.domain_policy_version != PSL_DOMAIN_POLICY_VERSION
        or not _pinned_psl_is_verified()
    ):
        return "domain_policy_unrecognized"
    if len(batches) > policy.max_provider_batches:
        return "fusion_input_bound_exceeded"
    if any(not isinstance(item, ProviderSearchBatch) for item in batches):
        raise TypeError("provider batches must be an immutable normalized tuple")
    counts = tuple(len(batch.observations) for batch in batches)
    if (
        any(count > policy.max_observations_per_provider for count in counts)
        or sum(counts) > policy.max_total_observations
        or len({batch.provider for batch in batches}) != len(batches)
    ):
        return "fusion_input_bound_exceeded"
    return None


def _empty_outcome(
    plan: RetrievalPlan,
    outcome: CanonicalOutcome,
    reason: str,
    *,
    completed: tuple[str, ...] = (),
    filtered: tuple[FilteredObservation, ...] = (),
    relations: tuple[DuplicateRelation, ...] = (),
    ranking: RankingTrace | None = None,
    diversity: SiteDiversityTrace | None = None,
    floor: EvidenceFloorTrace | None = None,
    provider_batches: tuple[ProviderSearchBatch, ...] = (),
    computed_answers: tuple[ComputedAnswerArtifact, ...] = (),
) -> FusionOutcome:
    return FusionOutcome(
        outcome,
        reason,
        filtered_observations=filtered,
        duplicate_relations=relations,
        site_diversity_trace=diversity
        or SiteDiversityTrace(
            applied=False,
            psl_snapshot=PSL_SNAPSHOT_ID,
            psl_snapshot_sha256=PSL_SNAPSHOT_SHA256,
            available_sites=0,
            required_sites=0,
            selections=(),
            decisions=(),
            selected_sites=0,
            passed=True,
            disposition="not_evaluated",
        ),
        evidence_floor_trace=floor or EvidenceFloorTrace(0, 0, 0, 0, True),
        ranking_trace=ranking
        or RankingTrace(plan.ranking_policy_version, 60, (), ()),
        completed_phases=completed,
        provider_batches=provider_batches,
        computed_answers=computed_answers,
    )


def fuse_evidence(
    retrieval_plan: RetrievalPlan,
    provider_batches: tuple[ProviderSearchBatch, ...],
    fusion_policy: FusionPolicy,
    utc_clock,
) -> FusionOutcome:
    """Normalize, filter, cluster, rank, and diversify bounded provider evidence."""
    bound_error = _validate_inputs(retrieval_plan, provider_batches, fusion_policy)
    if bound_error:
        return _empty_outcome(
            retrieval_plan, CanonicalOutcome.INVALID_REQUEST, bound_error
        )
    sampled = utc_clock() if callable(utc_clock) else utc_clock
    if not isinstance(sampled, datetime) or sampled.tzinfo is None:
        raise ValueError("fusion UTC clock must return an aware datetime")

    completed: tuple[str, ...] = ()
    filtered: tuple[FilteredObservation, ...] = ()
    relations: tuple[DuplicateRelation, ...] = ()
    ranking: RankingTrace | None = None
    diversity: SiteDiversityTrace | None = None
    floor: EvidenceFloorTrace | None = None
    computed_answers: tuple[ComputedAnswerArtifact, ...] = ()

    def deadline_failure() -> str | None:
        deadline = fusion_policy.operation_deadline
        if deadline is None:
            return None
        try:
            raw_sample = fusion_policy.monotonic()
        except Exception:
            return "fusion_clock_invalid"
        if (
            isinstance(raw_sample, bool)
            or not isinstance(raw_sample, (int, float))
            or not math.isfinite(float(raw_sample))
        ):
            return "fusion_clock_invalid"
        effective_deadline = (
            float(deadline) - float(fusion_policy.publication_reserve_seconds)
        )
        return (
            "fusion_deadline_expired"
            if float(raw_sample) >= effective_deadline
            else None
        )

    def timeout_outcome(reason: str) -> FusionOutcome:
        return _empty_outcome(
            retrieval_plan,
            CanonicalOutcome.TIMEOUT,
            reason,
            completed=completed,
            filtered=filtered,
            relations=relations,
            ranking=ranking,
            diversity=diversity,
            floor=floor,
            provider_batches=provider_batches,
            computed_answers=computed_answers,
        )

    failure = deadline_failure()
    if failure:
        return timeout_outcome(failure)
    candidate_list, filtered, claims, computed_answers = _normalize_filter(
        retrieval_plan, provider_batches
    )
    completed += (_PHASES[0],)
    failure = deadline_failure()
    if failure:
        return timeout_outcome(failure)

    failure = deadline_failure()
    if failure:
        return timeout_outcome(failure)
    clusters, filtered, relations = _cluster(candidate_list, filtered, claims)
    completed += (_PHASES[1],)
    failure = deadline_failure()
    if failure:
        return timeout_outcome(failure)

    failure = deadline_failure()
    if failure:
        return timeout_outcome(failure)
    ranked, ranking = _rank(retrieval_plan, clusters, fusion_policy)
    completed += (_PHASES[2],)
    failure = deadline_failure()
    if failure:
        return timeout_outcome(failure)

    failure = deadline_failure()
    if failure:
        return timeout_outcome(failure)
    selected, diversity, floor = _diversify(
        retrieval_plan,
        ranked,
        computed_answers,
    )
    completed += (_PHASES[3],)
    failure = deadline_failure()
    if failure:
        return timeout_outcome(failure)

    if not selected:
        if _freshness_requested(retrieval_plan):
            reason = (
                "strict_empty"
                if any(
                    _strict_empty_proven(retrieval_plan, batch)
                    for batch in provider_batches
                )
                else "freshness_unproven"
            )
            outcome = CanonicalOutcome.EMPTY
        elif floor.passed and floor.eligible_computed_answers:
            if any(
                batch.failure is not None
                and batch.failure.category is not FailureCategory.EMPTY
                for batch in provider_batches
            ):
                reason, outcome = (
                    "partial_provider_failure",
                    CanonicalOutcome.DEGRADED,
                )
            else:
                reason, outcome = "accepted", CanonicalOutcome.SUCCESS
        elif provider_batches and all(
            batch.failure is not None
            and batch.failure.category is not FailureCategory.EMPTY
            for batch in provider_batches
        ):
            reason, outcome = "providers_failed", CanonicalOutcome.PROVIDERS_FAILED
        else:
            reason, outcome = "empty", CanonicalOutcome.EMPTY
    elif not floor.passed:
        reason, outcome = (
            "research_structural_floor_unmet",
            CanonicalOutcome.EMPTY,
        )
    elif any(
        batch.failure is not None
        and batch.failure.category is not FailureCategory.EMPTY
        for batch in provider_batches
    ):
        reason, outcome = "partial_provider_failure", CanonicalOutcome.DEGRADED
    else:
        reason, outcome = "accepted", CanonicalOutcome.SUCCESS
    return FusionOutcome(
        outcome=outcome,
        reason=reason,
        ranked_result_clusters=selected,
        filtered_observations=filtered,
        duplicate_relations=relations,
        site_diversity_trace=diversity,
        evidence_floor_trace=floor,
        ranking_trace=ranking,
        completed_phases=completed,
        provider_batches=provider_batches,
        computed_answers=computed_answers,
    )


def project_search_results(
    outcome: FusionOutcome,
    *,
    include_attribution: bool = False,
) -> list[SearchResult]:
    """Render compatibility results without mutating the immutable fusion outcome."""
    if not isinstance(outcome, FusionOutcome):
        raise TypeError("fusion outcome must be typed")
    results: list[SearchResult] = []
    for cluster in outcome.ranked_result_clusters:
        representative = next(
            item
            for item in cluster.observations
            if isinstance(item, ResultObservation)
            and item.provider is cluster.representative_provider
            and item.provider_rank == cluster.representative_rank
            and item.url == cluster.representative_url
        )
        attribution = (
            {
                item.provider.value: float(
                    Fraction(item.numerator, item.denominator)
                )
                for item in cluster.contributions
            }
            if include_attribution
            else {}
        )
        results.append(
            SearchResult(
                url=cluster.representative_url,
                title=cluster.representative_title,
                snippet=cluster.representative_snippet,
                domain=urlsplit(cluster.representative_url).hostname or "",
                provider=cluster.representative_provider,
                score=cluster.score,
                raw_rank=cluster.representative_rank,
                metadata={
                    "cluster_sort_key": cluster.cluster_sort_key,
                    "site_key": cluster.site_key,
                    "exact_rrf_numerator": cluster.score_numerator,
                    "exact_rrf_denominator": cluster.score_denominator,
                    "egress": representative.egress.value,
                    "machine": representative.machine,
                    "source_kind": representative.source_kind.value,
                    "observed_at": representative.observed_at.isoformat(),
                },
                score_attribution=attribution,
            )
        )
    return results
