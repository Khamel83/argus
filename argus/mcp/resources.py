"""
MCP resource definitions for Argus.
"""

import json

from argus.broker.router import SearchBroker
from argus.corpus import describe_corpus_paths


def provider_status_resource(broker: SearchBroker) -> str:
    """Resource: argus://providers/status

    Current status of all search providers.
    """
    from argus.models import ProviderName

    providers = {}
    for pname in ProviderName:
        providers[pname.value] = broker.get_provider_status(pname)

    return json.dumps(providers, indent=2)


def provider_budgets_resource(broker: SearchBroker) -> str:
    """Resource: argus://providers/budgets

    Budget status for all providers.
    """
    from argus.models import ProviderName

    budgets = {}
    for pname in ProviderName:
        budgets[pname.value] = broker.spend_repository.provider_summary(
            pname,
            budget_limit=broker.budget_tracker.get_budget_limit(pname),
        )

    return json.dumps(budgets, indent=2)


def routing_policies_resource(broker: SearchBroker) -> str:
    """Resource: argus://policies/current

    Current routing policies for each search mode.
    """
    from argus.broker.policies import ROUTING_POLICIES

    policies = {}
    for mode, providers in ROUTING_POLICIES.items():
        policies[mode.value] = [p.value for p in providers]

    return json.dumps(policies, indent=2)


def corpus_paths_resource() -> str:
    """Resource: argus://corpus/paths

    Resolved Argus runtime storage paths.
    """
    return json.dumps(describe_corpus_paths(), indent=2)
