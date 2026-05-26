"""
FIX M-03: _PgConn.__exit__ must commit/rollback the transaction but NOT close
the connection, so subsequent DB calls in the same render cycle keep working.
"""
import pytest
from unittest.mock import MagicMock
from utils.db import _PgConn


def _make_conn(pool=None):
    """Return a _PgConn wrapping a MagicMock psycopg2 connection."""
    mock_pg = MagicMock()
    return _PgConn(mock_pg, pool=pool), mock_pg


def test_exit_commits_on_success():
    """Successful with-block must commit the underlying connection."""
    conn, mock_pg = _make_conn()
    with conn:
        pass
    mock_pg.commit.assert_called_once()


def test_exit_does_not_close_on_success():
    """Successful with-block must NOT close the connection."""
    conn, mock_pg = _make_conn()
    with conn:
        pass
    mock_pg.close.assert_not_called()
    assert conn._closed is False


def test_exit_rolls_back_on_exception():
    """Exception inside with-block must rollback the transaction."""
    conn, mock_pg = _make_conn()
    try:
        with conn:
            raise ValueError("deliberate test error")
    except ValueError:
        pass
    mock_pg.rollback.assert_called_once()
    mock_pg.commit.assert_not_called()


def test_exit_does_not_close_on_exception():
    """Exception inside with-block must NOT close the connection."""
    conn, mock_pg = _make_conn()
    try:
        with conn:
            raise ValueError("deliberate test error")
    except ValueError:
        pass
    mock_pg.close.assert_not_called()
    assert conn._closed is False


def test_connection_usable_after_context_manager(db):
    """Connection must remain queryable after a with-block (SQLite fixture path)."""
    with db:
        db.execute("INSERT OR IGNORE INTO brokers (broker_name) VALUES ('CtxTest')")
    # Connection must still be open and usable here
    row = db.execute(
        "SELECT broker_name FROM brokers WHERE broker_name='CtxTest'"
    ).fetchone()
    assert row is not None
    assert row["broker_name"] == "CtxTest"


def test_two_sequential_context_managers_both_commit(db):
    """Two sequential with-blocks on the same connection must both take effect."""
    with db:
        db.execute("INSERT OR IGNORE INTO brokers (broker_name) VALUES ('CtxA')")
    with db:
        db.execute("INSERT OR IGNORE INTO brokers (broker_name) VALUES ('CtxB')")
    rows = db.execute(
        "SELECT broker_name FROM brokers WHERE broker_name IN ('CtxA','CtxB')"
    ).fetchall()
    names = {r["broker_name"] for r in rows}
    assert names == {"CtxA", "CtxB"}


def test_close_is_idempotent():
    """Calling conn.close() twice must not raise."""
    conn, mock_pg = _make_conn(pool=None)
    conn.close()
    conn.close()  # second call must be a no-op
    assert mock_pg.close.call_count == 1


def test_pool_putconn_called_on_close():
    """When a pool is set, close() must return the connection to the pool."""
    mock_pool = MagicMock()
    conn, mock_pg = _make_conn(pool=mock_pool)
    conn.close()
    mock_pool.putconn.assert_called_once_with(mock_pg)
    mock_pg.close.assert_not_called()
