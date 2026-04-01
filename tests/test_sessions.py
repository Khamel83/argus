"""Tests for session management and query refinement."""

import pytest

from argus.sessions.models import Session, QueryRecord
from argus.sessions.store import SessionStore
from argus.sessions.refinement import refine_query


# --- Models ---

class TestSessionModels:
    def test_query_record_defaults(self):
        record = QueryRecord(query="test query")
        assert record.query == "test query"
        assert record.mode == "discovery"
        assert record.results_count == 0
        assert record.extracted_urls == []

    def test_session_defaults(self):
        session = Session(id="abc123")
        assert session.id == "abc123"
        assert session.queries == []
        assert session.extracted_urls == []

    def test_session_extracts_urls_from_all_queries(self):
        session = Session(id="abc")
        session.queries = [
            QueryRecord(query="q1", extracted_urls=["https://a.com"]),
            QueryRecord(query="q2", extracted_urls=["https://b.com", "https://c.com"]),
        ]
        assert session.extracted_urls == ["https://a.com", "https://b.com", "https://c.com"]


# --- Store ---

class TestSessionStore:
    def test_create_session(self):
        store = SessionStore()
        session = store.create_session()
        assert session.id is not None
        assert len(session.id) == 8

    def test_create_session_with_id(self):
        store = SessionStore()
        session = store.create_session(session_id="myid")
        assert session.id == "myid"

    def test_create_session_returns_existing(self):
        store = SessionStore()
        s1 = store.create_session(session_id="same")
        s2 = store.create_session(session_id="same")
        assert s1 is s2

    def test_get_session(self):
        store = SessionStore()
        store.create_session(session_id="findme")
        session = store.get_session("findme")
        assert session is not None
        assert session.id == "findme"

    def test_get_session_missing(self):
        store = SessionStore()
        assert store.get_session("missing") is None

    def test_add_query(self):
        store = SessionStore()
        session = store.create_session(session_id="s1")
        store.add_query("s1", query="python web frameworks", mode="discovery", results_count=10)
        assert len(session.queries) == 1
        assert session.queries[0].query == "python web frameworks"

    def test_add_query_missing_session(self):
        store = SessionStore()
        result = store.add_query("missing", query="test")
        assert result is None

    def test_add_extracted_url(self):
        store = SessionStore()
        session = store.create_session(session_id="s1")
        store.add_query("s1", query="test")
        store.add_extracted_url("s1", query_index=0, url="https://example.com")
        assert session.queries[0].extracted_urls == ["https://example.com"]

    def test_list_sessions(self):
        store = SessionStore()
        store.create_session()
        store.create_session()
        assert len(store.list_sessions()) == 2


# --- Refinement ---

class TestRefinement:
    def test_no_session_returns_query(self):
        assert refine_query("fastapi", None) == "fastapi"

    def test_empty_session_returns_query(self):
        session = Session(id="s1")
        assert refine_query("fastapi", session) == "fastapi"

    def test_single_prior_query_follow_up(self):
        session = Session(id="s1")
        session.queries = [
            QueryRecord(query="python web frameworks"),  # prior
            QueryRecord(query="fastapi"),  # current (last)
        ]
        result = refine_query("fastapi", session)
        # "fastapi" is short (1 word) and a follow-up → prepend context
        assert "python web frameworks" in result
        assert result.endswith("fastapi")

    def test_long_query_not_modified(self):
        session = Session(id="s1")
        session.queries = [
            QueryRecord(query="python web frameworks"),
        ]
        result = refine_query("what is the best python web framework for building apis", session)
        # Long query, clearly not a follow-up → unchanged
        assert result == "what is the best python web framework for building apis"

    def test_no_prior_queries_unchanged(self):
        session = Session(id="s1")
        session.queries = [
            QueryRecord(query="only query so far"),
        ]
        result = refine_query("only query so far", session)
        assert result == "only query so far"

    def test_skip_short_prior_queries(self):
        session = Session(id="s1")
        session.queries = [
            QueryRecord(query="x"),  # too short, skipped
            QueryRecord(query="python async"),  # prior, has 2 words
            QueryRecord(query="await"),  # current query (last)
        ]
        # prior = ["x", "python async"], "x" skipped, "python async" kept
        # current = "await" — 1 word, follow-up → prepend context
        result = refine_query("await", session)
        assert "python async" in result
        assert result.endswith("await")
