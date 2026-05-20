"""
Tests for the Suspense-match allocation cascade.
Imports allocate_to_customer and _unlink_allocation from
utils.passbook_helpers (extracted to allow testing without Streamlit).
"""
import pytest
from utils.passbook_helpers import allocate_to_customer, _unlink_allocation
from utils.db import SRC_ALLOCATION, SRC_MANUAL


def _seed(db):
    """Return (broker_id, customer_name) after inserting a broker."""
    db.execute("INSERT OR IGNORE INTO brokers (broker_name) VALUES ('TestBroker')")
    bid = db.execute(
        "SELECT broker_id FROM brokers WHERE broker_name='TestBroker'"
    ).fetchone()[0]
    return bid, "Ramesh"


def _txn(db, bid, cname, total, bill_date="2026-01-01"):
    db.execute("""
        INSERT INTO customer_transactions
          (broker_id, customer_name, date, total_amount, payment_status, calc_status)
        VALUES (?, ?, ?, ?, 'Pending', 'Pending')
    """, (bid, cname, bill_date, total))
    return db.execute("SELECT last_insert_rowid()").fetchone()[0]


def _pb_entry(db, amount, firm="SP Spices"):
    db.execute("""
        INSERT INTO passbook_entries
          (firm, entry_date, details, amount, txn_type, source_type)
        VALUES (?, '2026-02-01', 'Suspense', ?, 'Credit', 'Manual')
    """, (firm, amount))
    return db.execute("SELECT last_insert_rowid()").fetchone()[0]


# ── CRIT-01 regression ─────────────────────────────────────────

def test_allocate_respects_target_txn_id(db):
    """CRIT-01: clicking a specific row must allocate to THAT transaction."""
    bid, cname = _seed(db)
    t1 = _txn(db, bid, cname, 5000)   # lower outstanding
    t2 = _txn(db, bid, cname, 20000)  # higher outstanding
    eid = _pb_entry(db, 5000)
    db.commit()

    with db:
        res = allocate_to_customer(db, eid, cname, target_txn_id=t1)

    assert res["success"] is True
    assert res["target_txn_id"] == t1

    # T1 must have a payment; T2 must NOT
    p1 = db.execute(
        "SELECT * FROM payments WHERE transaction_id=?", (t1,)).fetchone()
    p2 = db.execute(
        "SELECT * FROM payments WHERE transaction_id=?", (t2,)).fetchone()
    assert p1 is not None
    assert p2 is None


def test_allocate_auto_picks_highest_outstanding(db):
    """Without target_txn_id, auto-allocation picks highest outstanding."""
    bid, cname = _seed(db)
    _txn(db, bid, cname, 5000)         # lower outstanding → T1
    t2 = _txn(db, bid, cname, 20000)   # higher outstanding → T2
    eid = _pb_entry(db, 5000)
    db.commit()

    with db:
        res = allocate_to_customer(db, eid, cname)  # no target_txn_id

    assert res["success"] is True
    assert res["target_txn_id"] == t2


def test_allocate_invalid_target_returns_error(db):
    """Selecting a target no longer eligible → success=False with message."""
    bid, cname = _seed(db)
    t1 = _txn(db, bid, cname, 5000)
    # T2 is eligible; T1 is Calculated (ineligible)
    _txn(db, bid, cname, 8000)
    db.execute("UPDATE customer_transactions SET calc_status='Calculated' WHERE transaction_id=?", (t1,))
    eid = _pb_entry(db, 5000)
    db.commit()

    with db:
        res = allocate_to_customer(db, eid, cname, target_txn_id=t1)

    assert res["success"] is False
    assert "no longer eligible" in res["message"]


def test_allocate_no_eligible_transactions(db):
    """Customer with all Calculated transactions → graceful abort."""
    bid, cname = _seed(db)
    t1 = _txn(db, bid, cname, 5000)
    t2 = _txn(db, bid, cname, 8000)
    db.execute("UPDATE customer_transactions SET calc_status='Calculated' WHERE transaction_id IN (?,?)", (t1, t2))
    eid = _pb_entry(db, 5000)
    db.commit()

    with db:
        res = allocate_to_customer(db, eid, cname)

    assert res["success"] is False

    # Passbook entry should still be Suspense
    pb = db.execute("SELECT details FROM passbook_entries WHERE entry_id=?", (eid,)).fetchone()
    assert pb["details"] == "Suspense"


def test_allocate_overpayment_stays_partial(db):
    """Overpayment (passbook amount > total_amount) keeps status='Partial'."""
    bid, cname = _seed(db)
    t1 = _txn(db, bid, cname, 5000)
    eid = _pb_entry(db, 8000)  # more than T1 total
    db.commit()

    with db:
        res = allocate_to_customer(db, eid, cname, target_txn_id=t1)

    assert res["success"] is True
    row = db.execute(
        "SELECT payment_status FROM customer_transactions WHERE transaction_id=?",
        (t1,)).fetchone()
    assert row["payment_status"] == "Partial"


def test_unlink_reverts_to_suspense(db):
    """Unlink removes payment row and resets passbook to Suspense."""
    bid, cname = _seed(db)
    t1 = _txn(db, bid, cname, 5000)
    eid = _pb_entry(db, 5000)
    db.commit()

    with db:
        allocate_to_customer(db, eid, cname, target_txn_id=t1)

    with db:
        res = _unlink_allocation(db, eid)

    assert res["success"] is True

    pmt = db.execute(
        "SELECT * FROM payments WHERE transaction_id=? AND note LIKE ?",
        (t1, f"Auto-allocated from passbook #{eid}%")).fetchone()
    assert pmt is None

    pb = db.execute("SELECT details, source_type FROM passbook_entries WHERE entry_id=?", (eid,)).fetchone()
    assert pb["details"] == "Suspense"
    assert pb["source_type"] == SRC_MANUAL


def test_unlink_handles_missing_payment_gracefully(db):
    """Unlink after manual payment deletion → no crash, entry still reverts."""
    bid, cname = _seed(db)
    t1 = _txn(db, bid, cname, 5000)
    eid = _pb_entry(db, 5000)
    db.commit()

    with db:
        allocate_to_customer(db, eid, cname, target_txn_id=t1)

    # Manually delete the auto-allocated payment
    db.execute(
        "DELETE FROM payments WHERE transaction_id=? AND note LIKE ?",
        (t1, f"Auto-allocated from passbook #{eid}%"))
    db.commit()

    with db:
        res = _unlink_allocation(db, eid)

    assert res["success"] is True

    pb = db.execute("SELECT details FROM passbook_entries WHERE entry_id=?", (eid,)).fetchone()
    assert pb["details"] == "Suspense"
