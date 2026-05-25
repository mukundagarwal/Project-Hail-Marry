"""
Tests for AUDIT-012: vendor RTGS/UB payment delete and edit must clean up
passbook / CIH entries atomically.
"""
import pytest
from utils.db import SRC_VENDOR_RTGS, SRC_VENDOR_UB


def _vendor(db):
    db.execute("INSERT OR IGNORE INTO vendors (vendor_name) VALUES ('TestVendor')")
    return db.execute(
        "SELECT vendor_id FROM vendors WHERE vendor_name='TestVendor'"
    ).fetchone()["vendor_id"]


def _rtgs_payment(db, vendor_id, amount=5000.0):
    """Insert an RTGS payment entry and its linked passbook entry."""
    db.execute("""
        INSERT INTO vendor_entries
            (vendor_id, entry_date, ledger_type, firm, entry_kind,
             particulars, amount, bags, quantity_kg)
        VALUES (?, '2024-01-10', 'RTGS', 'SP Spices', 'Payment',
                'RTGS Payment', ?, 0, 0)
    """, (vendor_id, amount))
    entry_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]

    db.execute("""
        INSERT INTO passbook_entries
            (firm, entry_date, details, amount, txn_type, source_type, source_id)
        VALUES ('SP Spices', '2024-01-10', 'TestVendor', ?, 'Debit', ?, ?)
    """, (amount, SRC_VENDOR_RTGS, entry_id))
    db.commit()
    return entry_id


def _ub_payment(db, vendor_id, amount=3000.0):
    """Insert a UB payment entry and its linked CIH entry."""
    db.execute("""
        INSERT INTO vendor_entries
            (vendor_id, entry_date, ledger_type, firm, entry_kind,
             particulars, amount, bags, quantity_kg)
        VALUES (?, '2024-01-15', 'UB', NULL, 'Payment',
                'UB Payment', ?, 0, 0)
    """, (vendor_id, amount))
    entry_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]

    db.execute("""
        INSERT INTO cash_in_hand_entries
            (entry_date, details, amount, txn_type, source_type, source_id)
        VALUES ('2024-01-15', 'TestVendor', ?, 'Debit', ?, ?)
    """, (amount, SRC_VENDOR_UB, entry_id))
    db.commit()
    return entry_id


# ── RTGS delete cleanup ────────────────────────────────────────

def test_rtgs_delete_removes_passbook_entry(db):
    """Deleting an RTGS payment must remove the linked passbook entry."""
    vid = _vendor(db)
    eid = _rtgs_payment(db, vid, 5000.0)

    # Simulate the delete handler (inside with conn:)
    db.execute(
        "DELETE FROM passbook_entries WHERE source_type=? AND source_id=?",
        (SRC_VENDOR_RTGS, eid))
    db.execute("DELETE FROM vendor_entries WHERE entry_id=?", (eid,))
    db.commit()

    pb_count = db.execute(
        "SELECT COUNT(*) AS n FROM passbook_entries WHERE source_type=? AND source_id=?",
        (SRC_VENDOR_RTGS, eid)).fetchone()["n"]
    ve_count = db.execute(
        "SELECT COUNT(*) AS n FROM vendor_entries WHERE entry_id=?",
        (eid,)).fetchone()["n"]
    assert pb_count == 0
    assert ve_count == 0


def test_rtgs_delete_does_not_affect_other_entries(db):
    """Deleting one RTGS payment must not touch other passbook entries."""
    vid = _vendor(db)
    eid_a = _rtgs_payment(db, vid, 5000.0)
    eid_b = _rtgs_payment(db, vid, 2000.0)

    db.execute(
        "DELETE FROM passbook_entries WHERE source_type=? AND source_id=?",
        (SRC_VENDOR_RTGS, eid_a))
    db.execute("DELETE FROM vendor_entries WHERE entry_id=?", (eid_a,))
    db.commit()

    pb_b = db.execute(
        "SELECT COUNT(*) AS n FROM passbook_entries WHERE source_type=? AND source_id=?",
        (SRC_VENDOR_RTGS, eid_b)).fetchone()["n"]
    assert pb_b == 1, "Second RTGS payment passbook entry must remain"


# ── RTGS edit cleanup ──────────────────────────────────────────

def test_rtgs_edit_updates_passbook_amount(db):
    """Editing an RTGS payment must update the passbook entry amount."""
    vid = _vendor(db)
    eid = _rtgs_payment(db, vid, 5000.0)

    new_amount  = 7500.0
    new_date    = "2024-02-01"
    new_firm    = "SP Spices"

    # Simulate the payment edit handler
    db.execute(
        "UPDATE vendor_entries SET entry_date=?, amount=?, firm=? WHERE entry_id=?",
        (new_date, new_amount, new_firm, eid))
    db.execute(
        "UPDATE passbook_entries SET entry_date=?, amount=?, firm=? "
        "WHERE source_type=? AND source_id=?",
        (new_date, new_amount, new_firm, SRC_VENDOR_RTGS, eid))
    db.commit()

    pb_row = db.execute(
        "SELECT amount, entry_date FROM passbook_entries "
        "WHERE source_type=? AND source_id=?",
        (SRC_VENDOR_RTGS, eid)).fetchone()
    assert abs(float(pb_row["amount"]) - 7500.0) < 0.01
    assert pb_row["entry_date"] == new_date


# ── UB delete cleanup ──────────────────────────────────────────

def test_ub_delete_removes_cih_entry(db):
    """Deleting a UB payment must remove the linked CIH entry."""
    vid = _vendor(db)
    eid = _ub_payment(db, vid, 3000.0)

    # Simulate the delete handler (no longer wrapped in try/except)
    db.execute(
        "DELETE FROM cash_in_hand_entries WHERE source_type=? AND source_id=?",
        (SRC_VENDOR_UB, eid))
    db.execute("DELETE FROM vendor_entries WHERE entry_id=?", (eid,))
    db.commit()

    cih_count = db.execute(
        "SELECT COUNT(*) AS n FROM cash_in_hand_entries WHERE source_type=? AND source_id=?",
        (SRC_VENDOR_UB, eid)).fetchone()["n"]
    ve_count = db.execute(
        "SELECT COUNT(*) AS n FROM vendor_entries WHERE entry_id=?",
        (eid,)).fetchone()["n"]
    assert cih_count == 0
    assert ve_count == 0


def test_ub_edit_updates_cih_entry(db):
    """Editing a UB payment must update the CIH entry amount and date."""
    vid = _vendor(db)
    eid = _ub_payment(db, vid, 3000.0)

    new_amount = 4500.0
    new_date   = "2024-03-01"

    db.execute(
        "UPDATE vendor_entries SET entry_date=?, amount=? WHERE entry_id=?",
        (new_date, new_amount, eid))
    db.execute(
        "UPDATE cash_in_hand_entries SET entry_date=?, amount=? "
        "WHERE source_type=? AND source_id=?",
        (new_date, new_amount, SRC_VENDOR_UB, eid))
    db.commit()

    cih_row = db.execute(
        "SELECT amount, entry_date FROM cash_in_hand_entries "
        "WHERE source_type=? AND source_id=?",
        (SRC_VENDOR_UB, eid)).fetchone()
    assert abs(float(cih_row["amount"]) - 4500.0) < 0.01
    assert cih_row["entry_date"] == new_date
