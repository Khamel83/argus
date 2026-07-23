import sqlite3


def test_init_db_does_not_create_schema_for_postgresql(monkeypatch):
    from argus.persistence import db

    sentinel_engine = object()
    create_calls = []
    compat_calls = []
    monkeypatch.setattr(db, "create_engine", lambda *args, **kwargs: sentinel_engine)
    monkeypatch.setattr(
        db.Base.metadata,
        "create_all",
        lambda engine: create_calls.append(engine),
    )
    monkeypatch.setattr(
        db,
        "_ensure_schema_compat",
        lambda engine: compat_calls.append(engine),
    )

    db.init_db("postgresql://disposable.invalid/argus_test")

    assert create_calls == []
    assert compat_calls == []


def test_init_db_adds_missing_provenance_columns_to_existing_tables(tmp_path):
    from sqlalchemy import inspect

    from argus.persistence import db

    db_path = tmp_path / "legacy.db"
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            """
            CREATE TABLE search_results (
                id INTEGER PRIMARY KEY,
                run_id INTEGER NOT NULL,
                url TEXT NOT NULL,
                title TEXT DEFAULT '',
                snippet TEXT DEFAULT '',
                domain VARCHAR(255) DEFAULT '',
                provider VARCHAR(50) DEFAULT '',
                score FLOAT DEFAULT 0.0,
                final_rank INTEGER DEFAULT 0,
                created_at DATETIME
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE corpus_documents (
                id INTEGER PRIMARY KEY,
                workflow_run_id VARCHAR(64) NOT NULL,
                citation_id VARCHAR(32) NOT NULL,
                source_type VARCHAR(64) DEFAULT 'web',
                role VARCHAR(64) DEFAULT 'source',
                title TEXT DEFAULT '',
                url TEXT NOT NULL,
                domain VARCHAR(255) DEFAULT '',
                artifact_path TEXT NOT NULL,
                extractor VARCHAR(64),
                word_count INTEGER DEFAULT 0,
                created_at DATETIME
            )
            """
        )
        conn.commit()
    finally:
        conn.close()

    db.init_db(f"sqlite:///{db_path}")
    inspector = inspect(db.get_engine())

    search_columns = {column["name"] for column in inspector.get_columns("search_results")}
    corpus_columns = {column["name"] for column in inspector.get_columns("corpus_documents")}

    assert {"egress", "machine", "metadata_json"} <= search_columns
    assert {"egress", "machine", "metadata_json"} <= corpus_columns
