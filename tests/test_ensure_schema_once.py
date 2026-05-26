"""
FIX 1: _ensure_schema_once wrapper — schema runs once per session, not every rerun.
"""
import pytest
from utils import db as db_module


def test_ensure_schema_once_exists():
    """`_ensure_schema_once` must be importable from utils.db."""
    from utils.db import _ensure_schema_once
    assert callable(_ensure_schema_once)


def test_ensure_schema_still_callable_directly(db):
    """ensure_schema(conn=...) must still work directly for tests and CLI."""
    from utils.db import ensure_schema
    # Calling with an explicit SQLite conn should not raise
    ensure_schema(conn=db)


def test_ensure_schema_once_is_idempotent(db):
    """
    Calling ensure_schema(conn=...) twice must not insert duplicate rows.
    (ON CONFLICT DO NOTHING / idempotency guarantee.)
    """
    from utils.db import ensure_schema
    ensure_schema(conn=db)
    ensure_schema(conn=db)
    # Passbook opening rows must still be exactly one per firm after two calls
    rows = db.execute(
        "SELECT COUNT(*) AS cnt FROM passbook_entries WHERE source_type='Opening'"
    ).fetchone()
    assert int(rows["cnt"]) == 2  # one per firm


def test_ensure_schema_once_in_non_streamlit_is_plain_function():
    """
    In a non-Streamlit environment (tests), _cache_resource falls back to the
    identity decorator, so _ensure_schema_once is a plain callable.
    """
    from utils.db import _ensure_schema_once
    # Should be callable without error (just calls ensure_schema() which may
    # hit the already-initialized guard)
    assert callable(_ensure_schema_once)


def test_schema_initialized_flag_prevents_double_run(monkeypatch):
    """
    The module-level _schema_initialized flag prevents ensure_schema from
    re-running the full seed in production (when conn=None path is used).
    After the flag is True, ensure_schema should return immediately.
    """
    import utils.db as _db_mod
    original = _db_mod._schema_initialized
    try:
        _db_mod._schema_initialized = True
        call_count = {"n": 0}

        real_get_conn = _db_mod.get_conn

        def _mock_get_conn():
            call_count["n"] += 1
            return real_get_conn()

        monkeypatch.setattr(_db_mod, "get_conn", _mock_get_conn)
        _db_mod.ensure_schema()  # should return immediately without calling get_conn
        assert call_count["n"] == 0
    finally:
        _db_mod._schema_initialized = original
