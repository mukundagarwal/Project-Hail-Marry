"""
Tests for AUDIT-036: compute_passbook_view and compute_cash_view accept an
optional conn parameter and reuse it instead of opening a second connection.
"""
import pytest
from utils.passbook_helpers import compute_passbook_view, compute_cash_view
from utils.db import SRC_OPENING, SRC_CIH_OPENING, FIRM_SP, FIRM_MT


def test_passbook_view_with_conn_returns_dataframe(db):
    """compute_passbook_view(firm, conn=db) must return a DataFrame without error."""
    df = compute_passbook_view(FIRM_SP, conn=db)
    assert df is not None
    assert hasattr(df, "columns")


def test_passbook_view_without_conn_raises_no_error(db):
    """
    compute_passbook_view called without conn opens its own connection.
    In a test environment without a real PostgreSQL server this will fail
    to connect — we only verify the function signature accepts conn=None.
    """
    import inspect
    sig = inspect.signature(compute_passbook_view)
    assert "conn" in sig.parameters
    assert sig.parameters["conn"].default is None


def test_passbook_view_conn_shows_seeded_opening_row(db):
    """The view must include the opening balance row seeded by ensure_schema."""
    df = compute_passbook_view(FIRM_SP, conn=db)
    opening_rows = df[df["source_type"] == SRC_OPENING]
    assert len(opening_rows) >= 1


def test_passbook_view_both_firms(db):
    """compute_passbook_view works for both firms using the same conn."""
    df_sp = compute_passbook_view(FIRM_SP, conn=db)
    df_mt = compute_passbook_view(FIRM_MT, conn=db)
    assert df_sp is not None
    assert df_mt is not None


def test_passbook_view_conn_not_closed_after_call(db):
    """
    When conn is provided, compute_passbook_view must NOT close it.
    Verify by executing another query on the same conn after the call.
    """
    compute_passbook_view(FIRM_SP, conn=db)
    # If the function closed db, this next execute would raise an OperationalError.
    result = db.execute("SELECT 1 AS alive").fetchone()
    assert result["alive"] == 1


def test_cash_view_with_conn_returns_dataframe(db):
    """compute_cash_view(conn=db) must return a DataFrame without error."""
    df = compute_cash_view(conn=db)
    assert df is not None
    assert hasattr(df, "columns")


def test_cash_view_conn_not_closed_after_call(db):
    """compute_cash_view must NOT close the provided conn."""
    compute_cash_view(conn=db)
    result = db.execute("SELECT 1 AS alive").fetchone()
    assert result["alive"] == 1


def test_cash_view_includes_cih_opening(db):
    """compute_cash_view must include the CIH opening row seeded by ensure_schema."""
    df = compute_cash_view(conn=db)
    cih_rows = df[df["source_type"] == SRC_CIH_OPENING]
    assert len(cih_rows) >= 1
