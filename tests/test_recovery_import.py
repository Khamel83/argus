import pytest


def test_import_is_dry_run_first_and_reconciles_after_apply(monkeypatch):
    from argus.recovery import importer

    calls = []
    search_reports = iter(
        [
            {"source": 3, "imported": 3, "skipped": 0, "conflicting": 0},
            {"source": 3, "imported": 3, "skipped": 0, "conflicting": 0},
            {"source": 3, "imported": 0, "skipped": 3, "conflicting": 0},
        ]
    )
    session_reports = iter(
        [
            {"source": 1, "imported": 1, "skipped": 0, "conflicting": 0},
            {"source": 1, "imported": 1, "skipped": 0, "conflicting": 0},
            {"source": 1, "imported": 0, "skipped": 1, "conflicting": 0},
        ]
    )

    def search(source, repository, *, apply=False):
        calls.append(("search", apply))
        return next(search_reports)

    def sessions(source, repository, *, apply=False):
        calls.append(("sessions", apply))
        return next(session_reports)

    monkeypatch.setattr(importer, "reconcile_legacy_state", search)
    monkeypatch.setattr(importer, "reconcile_legacy_sessions", sessions)

    report = importer.reconcile_import(
        search_source="sqlite:///search.db",
        session_source="sqlite:///sessions.db",
        repository=object(),
        apply=True,
    )

    assert calls == [
        ("search", False),
        ("sessions", False),
        ("search", True),
        ("sessions", True),
        ("search", False),
        ("sessions", False),
    ]
    assert report["verified"] is True
    assert report["rollback_boundary"] == "before_production_cutover"
    assert report["after"]["search"]["imported"] == 0


def test_import_refuses_to_apply_when_dry_run_finds_conflict(monkeypatch):
    from argus.recovery import importer

    monkeypatch.setattr(
        importer,
        "reconcile_legacy_state",
        lambda *args, **kwargs: {
            "source": 1,
            "imported": 0,
            "skipped": 0,
            "conflicting": 1,
        },
    )
    monkeypatch.setattr(
        importer,
        "reconcile_legacy_sessions",
        lambda *args, **kwargs: {
            "source": 0,
            "imported": 0,
            "skipped": 0,
            "conflicting": 0,
        },
    )

    with pytest.raises(ValueError, match="conflict"):
        importer.reconcile_import(
            search_source="sqlite:///search.db",
            session_source="sqlite:///sessions.db",
            repository=object(),
            apply=True,
        )


def test_import_reports_dry_run_without_mutation(monkeypatch):
    from argus.recovery import importer

    def report(*args, **kwargs):
        assert kwargs["apply"] is False
        return {"source": 2, "imported": 2, "skipped": 0, "conflicting": 0}

    monkeypatch.setattr(importer, "reconcile_legacy_state", report)
    monkeypatch.setattr(importer, "reconcile_legacy_sessions", report)

    result = importer.reconcile_import(
        search_source="sqlite:///search.db",
        session_source="sqlite:///sessions.db",
        repository=object(),
        apply=False,
    )

    assert result["applied"] is False
    assert result["verified"] is False
    assert "after" not in result
