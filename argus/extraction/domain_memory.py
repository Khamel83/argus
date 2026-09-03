"""Extraction-facing facade for durable domain-routing preferences."""

from __future__ import annotations

import threading
from datetime import datetime
from typing import Callable

from argus.persistence.domain_policy import (
    DomainPolicyRepository,
    DomainPolicyValue,
    create_domain_policy_repository,
    normalize_domain,
)


class DomainMemory:
    """Delegate routing policy commands to the canonical repository.

    The repository is injected in tests and can be created lazily for the
    process-wide extraction facade. Lazy construction avoids opening a
    database merely by importing the extraction package.
    """

    def __init__(
        self,
        repository: DomainPolicyRepository | None = None,
        *,
        db_url: str | None = None,
        repository_factory: Callable[..., DomainPolicyRepository] | None = None,
    ) -> None:
        self._repository = repository
        self._db_url = db_url
        self._repository_factory = repository_factory or create_domain_policy_repository
        self._repository_lock = threading.Lock()

    @property
    def repository(self) -> DomainPolicyRepository:
        if self._repository is None:
            with self._repository_lock:
                if self._repository is None:
                    self._repository = self._repository_factory(self._db_url)
        return self._repository

    def get_policy(self, domain: object) -> DomainPolicyValue | None:
        normalized = normalize_domain(domain)
        if normalized is None:
            return None
        return self.repository.get_policy(normalized)

    def record_datacenter_failure(
        self,
        domain: object,
        reason: str | None = None,
        *,
        event_identity: str | None = None,
        idempotency_key: str | None = None,
        request_hash: str | None = None,
        occurred_at: datetime | None = None,
        event_at: datetime | None = None,
        fault_hook=None,
    ) -> DomainPolicyValue | None:
        normalized = normalize_domain(domain)
        if normalized is None:
            return None
        return self.repository.record_datacenter_failure(
            normalized,
            reason=reason,
            event_identity=event_identity,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            occurred_at=occurred_at,
            event_at=event_at,
            fault_hook=fault_hook,
        )

    def record_residential_success(
        self,
        domain: object,
        *,
        event_identity: str | None = None,
        idempotency_key: str | None = None,
        request_hash: str | None = None,
        occurred_at: datetime | None = None,
        event_at: datetime | None = None,
        fault_hook=None,
    ) -> DomainPolicyValue | None:
        normalized = normalize_domain(domain)
        if normalized is None:
            return None
        return self.repository.record_residential_success(
            normalized,
            event_identity=event_identity,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            occurred_at=occurred_at,
            event_at=event_at,
            fault_hook=fault_hook,
        )

    def should_prefer_residential(
        self,
        domain: object,
        task_type: str = "extraction",
    ) -> bool:
        policy = self.get_policy(domain)
        if policy is None:
            return False
        if task_type == "search":
            return policy.prefer_residential_search
        return policy.prefer_residential_extraction


_domain_memory = DomainMemory()


def get_domain_memory() -> DomainMemory:
    return _domain_memory


__all__ = ["DomainMemory", "get_domain_memory"]
