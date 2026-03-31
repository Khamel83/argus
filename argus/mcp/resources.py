"""
MCP resource definitions for Argus.
"""

import json
from typing import Optional

from argus.broker.router import SearchBroker


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
        budgets[pname.value] = {
            "remaining": broker.budget_tracker.get_remaining_budget(pname),
            "monthly_usage": broker.budget_tracker.get_monthly_usage(pname),
            "exhausted": broker.budget_tracker.is_budget_exhausted(pname),
        }

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
