from argus.broker.budgets import BudgetTracker
from argus.models import ProviderName


def test_valyu_usd_budget():
    tracker = BudgetTracker()
    # Set budget to $10.0
    tracker.set_budget(ProviderName.VALYU, 10.0)

    # Record a search call ($0.0015)
    tracker.record_usage(ProviderName.VALYU, cost=0.0015)

    # Record an extraction call ($0.001)
    tracker.record_usage(ProviderName.VALYU, cost=0.001)

    assert tracker.get_usage_count(ProviderName.VALYU) == 2
    assert tracker.get_monthly_usage(ProviderName.VALYU) == 0.0025
    assert tracker.get_remaining_budget(ProviderName.VALYU) == 9.9975
    assert not tracker.is_budget_exhausted(ProviderName.VALYU)


def test_valyu_budget_exhaustion():
    tracker = BudgetTracker()
    tracker.set_budget(ProviderName.VALYU, 0.01)

    tracker.record_usage(ProviderName.VALYU, cost=0.006)
    assert not tracker.is_budget_exhausted(ProviderName.VALYU)

    tracker.record_usage(ProviderName.VALYU, cost=0.005)
    assert tracker.is_budget_exhausted(ProviderName.VALYU)
    assert tracker.get_remaining_budget(ProviderName.VALYU) == 0.0


def test_valyu_answer_usage_persists(monkeypatch, tmp_path):
    from argus.providers.valyu_answer import _record_valyu_answer_usage

    db_path = tmp_path / "budgets.db"
    monkeypatch.setenv("ARGUS_BUDGET_DB_PATH", str(db_path))

    _record_valyu_answer_usage(0.1234)

    tracker = BudgetTracker(persist_path=str(db_path))
    tracker.set_budget(ProviderName.VALYU, 10.0)
    assert tracker.get_monthly_usage(ProviderName.VALYU) == 0.1234
    assert tracker.get_remaining_budget(ProviderName.VALYU) == 9.8766
    tracker.close()
