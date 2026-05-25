"""
Tests for AUDIT-013: opening balance rows must be unique per firm (passbook)
and globally unique (CIH). Enforced via partial UNIQUE indexes added to the schema.
"""
import pytest
import sqlite3
from utils.db import SRC_OPENING, SRC_CIH_OPENING


# ── Passbook opening balance ───────────────────────────────────

def test_passbook_second_opening_entry_same_firm_blocked(db):
    """
    Two SRC_OPENING rows for the same firm must be rejected by the partial index.
    ensure_schema() already seeds one opening row for SP Spices; a second insert
    for the same firm must fail immediately.
    """
    # Verify ensure_schema seeded the row
    existing = db.execute(
        "SELECT COUNT(*) AS n FROM passbook_entries "
        "WHERE firm='SP Spices' AND source_type='Opening'"
    ).fetchone()["n"]
    assert existing == 1, "ensure_schema must have seeded one opening row"

    # A second insert for the same firm must fail
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("""
            INSERT INTO passbook_entries
                (firm, entry_date, details, amount, txn_type, source_type)
            VALUES ('SP Spices', '2024-02-01', 'Opening Balance', 15000, 'Credit', 'Opening')
        """)


def test_passbook_opening_entries_for_different_firms_coexist(db):
    """SRC_OPENING rows for different firms must be allowed."""
    # ensure_schema() already seeded one row for SP Spices and one for Mukund Traders
    count = db.execute(
        "SELECT COUNT(*) AS n FROM passbook_entries WHERE source_type='Opening'"
    ).fetchone()["n"]
    assert count == 2, (
        "ensure_schema must seed opening rows for both SP Spices and Mukund Traders"
    )


def test_passbook_non_opening_rows_not_restricted(db):
    """Non-Opening rows with the same firm must be unrestricted."""
    db.execute("""
        INSERT INTO passbook_entries
            (firm, entry_date, details, amount, txn_type, source_type)
        VALUES ('SP Spices', '2024-01-10', 'Manual', 5000, 'Credit', 'Manual')
    """)
    db.execute("""
        INSERT INTO passbook_entries
            (firm, entry_date, details, amount, txn_type, source_type)
        VALUES ('SP Spices', '2024-01-15', 'Manual', 2000, 'Credit', 'Manual')
    """)
    db.commit()
    count = db.execute(
        "SELECT COUNT(*) AS n FROM passbook_entries WHERE source_type='Manual'"
    ).fetchone()["n"]
    assert count == 2


def test_passbook_upsert_pattern_updates_not_duplicates(db):
    """
    Simulates the handler's upsert: DELETE old + INSERT new must produce exactly
    1 row after two saves. ensure_schema() seeds the initial row, so we start
    from that state.
    """
    # ensure_schema seeded a row; simulate a second save via DELETE+INSERT
    db.execute(
        "DELETE FROM passbook_entries WHERE firm=? AND source_type=?",
        ("SP Spices", SRC_OPENING))
    db.execute("""
        INSERT INTO passbook_entries
            (firm, entry_date, details, amount, txn_type, source_type)
        VALUES ('SP Spices', '2024-02-01', 'Opening Balance', 15000, 'Credit', 'Opening')
    """)
    db.commit()

    rows = db.execute(
        "SELECT amount FROM passbook_entries WHERE firm='SP Spices' AND source_type='Opening'"
    ).fetchall()
    assert len(rows) == 1
    assert abs(float(rows[0]["amount"]) - 15000.0) < 0.01


# ── CIH opening balance ────────────────────────────────────────

def test_cih_second_opening_entry_blocked(db):
    """Two SRC_CIH_OPENING rows must be rejected by the partial index."""
    # ensure_schema() already seeded one CIHOpening row; verify it exists
    existing = db.execute(
        "SELECT COUNT(*) AS n FROM cash_in_hand_entries WHERE source_type='CIHOpening'"
    ).fetchone()["n"]
    assert existing == 1, "ensure_schema must have seeded one CIH opening row"

    # A second insert must be rejected
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("""
            INSERT INTO cash_in_hand_entries
                (entry_date, details, amount, txn_type, source_type)
            VALUES ('2024-02-01', 'Opening Balance', 8000, 'Credit', 'CIHOpening')
        """)


def test_cih_non_opening_rows_not_restricted(db):
    """Multiple non-CIHOpening CIH entries must be allowed."""
    db.execute("""
        INSERT INTO cash_in_hand_entries
            (entry_date, details, amount, txn_type, source_type)
        VALUES ('2024-01-10', 'Cash sale', 1000, 'Credit', 'CustomerCash')
    """)
    db.execute("""
        INSERT INTO cash_in_hand_entries
            (entry_date, details, amount, txn_type, source_type)
        VALUES ('2024-01-15', 'Cash sale 2', 2000, 'Credit', 'CustomerCash')
    """)
    db.commit()
    count = db.execute(
        "SELECT COUNT(*) AS n FROM cash_in_hand_entries WHERE source_type='CustomerCash'"
    ).fetchone()["n"]
    assert count == 2


def test_cih_upsert_pattern_updates_not_duplicates(db):
    """Verify DELETE+INSERT pattern produces exactly 1 CIH opening row after two saves."""
    # ensure_schema() seeds the initial row; simulate a second save via DELETE+INSERT
    db.execute(
        "DELETE FROM cash_in_hand_entries WHERE source_type=?",
        (SRC_CIH_OPENING,))
    db.execute("""
        INSERT INTO cash_in_hand_entries
            (entry_date, details, amount, txn_type, source_type)
        VALUES ('2024-02-01', 'Opening Balance', 8000, 'Credit', 'CIHOpening')
    """)
    db.commit()

    rows = db.execute(
        "SELECT amount FROM cash_in_hand_entries WHERE source_type='CIHOpening'"
    ).fetchall()
    assert len(rows) == 1
    assert abs(float(rows[0]["amount"]) - 8000.0) < 0.01
