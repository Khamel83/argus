"""
Adaptive domain memory for routing acquisition.

Tracks which domains perform better on residential vs datacenter egress
and stores these preferences in SQLite.
"""

from datetime import datetime
from typing import Optional

from sqlalchemy import select

from argus.persistence.db import get_session
from argus.persistence.models import DomainPolicyRow


class DomainMemory:
    """Gateway to domain-level routing preferences."""

    def get_policy(self, domain: str) -> Optional[DomainPolicyRow]:
        """Fetch the current policy for a domain."""
        if not domain:
            return None
        with get_session() as session:
            stmt = select(DomainPolicyRow).where(DomainPolicyRow.domain == domain)
            return session.execute(stmt).scalar_one_or_none()

    def record_datacenter_failure(self, domain: str, reason: str = None):
        """Increment failure count for datacenter egress on this domain."""
        if not domain:
            return
        with get_session() as session:
            stmt = select(DomainPolicyRow).where(DomainPolicyRow.domain == domain)
            row = session.execute(stmt).scalar_one_or_none()
            if not row:
                row = DomainPolicyRow(domain=domain)
                session.add(row)

            row.datacenter_failure_count += 1
            row.last_datacenter_failure = datetime.now()
            row.failure_reason = reason

            # If we've failed 3 times from datacenter, start preferring residential
            if row.datacenter_failure_count >= 3:
                row.prefer_residential_extraction = True
                row.prefer_residential_search = True

    def record_residential_success(self, domain: str):
        """Increment success count for residential egress on this domain."""
        if not domain:
            return
        with get_session() as session:
            stmt = select(DomainPolicyRow).where(DomainPolicyRow.domain == domain)
            row = session.execute(stmt).scalar_one_or_none()
            if not row:
                row = DomainPolicyRow(domain=domain, prefer_residential_extraction=True, prefer_residential_search=True)
                session.add(row)

            row.residential_success_count += 1
            row.last_residential_success = datetime.now()

            # Reinforce preference
            row.prefer_residential_extraction = True
            row.prefer_residential_search = True

    def should_prefer_residential(self, domain: str, task_type: str = "extraction") -> bool:
        """Check if residential egress is preferred for this domain."""
        policy = self.get_policy(domain)
        if not policy:
            return False
        if task_type == "search":
            return policy.prefer_residential_search
        return policy.prefer_residential_extraction


_domain_memory = DomainMemory()


def get_domain_memory() -> DomainMemory:
    return _domain_memory
