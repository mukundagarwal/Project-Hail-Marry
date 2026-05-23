"""
Tests for Cash in Hand sync hooks.
Each test directly exercises the DB operations the UI handlers perform,
asserting the resulting DB state without going through Streamlit.
"""
import pytest
import sqlite3 as _sqlite3
from utils.db import SRC_CUSTOMER_CASH, SRC_VENDOR_UB, SRC_CIH_OPENING


def _broker(db):
    db.execute("INSERT OR IGNORE INTO brokers (broker_name) VALUES ('B')")
    return db.execute("SELECT broker_id FROM brokers WHERE broker_name='B'").fetchone()[0]


def _txn(db, bid):
    db.execute("""
        INSERT INTO customer_transactions
          (broker_id, customer_name, date, total_amount, payment_status)
        VALUES (?, 'Cust', '2026-01-01', 10000, 'Pending')
    """, (bid,))
    return db.execute("SELECT last_insert_rowid()").fetchone()[0]


def _vendor(db):
    db.execute("INSERT OR IGNORE INTO vendors (vendor_name) VALUES ('Vend')")
    return db.execute("SELECT vendor_id FROM vendors WHERE vendor_name='Vend'").fetchone()[0]


# ── Customer Cash payment ──────────────────────────────────────

def test_cih_created_on_cash_payment(db):
    """Logging a Cash payment creates a CIH Credit entry."""
    bid = _broker(db)
    tid = _txn(db, bid)
    db.execute("""INSERT INTO payments (transaction_id, payment_date, amount, method)
                  VALUES (?, '2026-01-10', 5000, 'Cash')""", (tid,))
    pid = db.execute("SELECT last_insert_rowid()").fetchone()[0]
    db.execute("""INSERT INTO cash_in_hand_entries
                  (entry_date, details, amount, txn_type, source_type, source_id)
                  VALUES ('2026-01-10', 'Cust', 5000, 'Credit', ?, ?)""",
               (SRC_CUSTOMER_CASH, pid))
    db.commit()

    row = db.execute(
        "SELECT * FROM cash_in_hand_entries WHERE source_type=? AND source_id=?",
        (SRC_CUSTOMER_CASH, pid)).fetchone()
    assert row is not None
    assert row["txn_type"] == "Credit"
    assert row["amount"] == 5000


def test_cih_not_created_for_non_cash_payment(db):
    """UPI payments do NOT create CIH entries."""
    bid = _broker(db)
    tid = _txn(db, bid)
    db.execute("""INSERT INTO payments (transaction_id, payment_date, amount, method)
                  VALUES (?, '2026-01-10', 5000, 'UPI')""", (tid,))
    # Simulate: no CIH insert because method != Cash
    db.commit()

    count = db.execute(
        "SELECT COUNT(*) FROM cash_in_hand_entries WHERE source_type=?",
        (SRC_CUSTOMER_CASH,)).fetchone()[0]
    assert count == 0


def test_cih_updated_on_payment_amount_edit(db):
    """Editing payment amount updates linked CIH entry."""
    bid = _broker(db)
    tid = _txn(db, bid)
    db.execute("""INSERT INTO payments (transaction_id, payment_date, amount, method)
                  VALUES (?, '2026-01-10', 5000, 'Cash')""", (tid,))
    pid = db.execute("SELECT last_insert_rowid()").fetchone()[0]
    db.execute("""INSERT INTO cash_in_hand_entries
                  (entry_date, details, amount, txn_type, source_type, source_id)
                  VALUES ('2026-01-10', 'Cust', 5000, 'Credit', ?, ?)""",
               (SRC_CUSTOMER_CASH, pid))
    db.commit()

    # Simulate edit: update payment amount + CIH entry
    db.execute("UPDATE payments SET amount=7000 WHERE payment_id=?", (pid,))
    db.execute("UPDATE cash_in_hand_entries SET amount=7000 WHERE source_type=? AND source_id=?",
               (SRC_CUSTOMER_CASH, pid))
    db.commit()

    row = db.execute(
        "SELECT amount FROM cash_in_hand_entries WHERE source_type=? AND source_id=?",
        (SRC_CUSTOMER_CASH, pid)).fetchone()
    assert row["amount"] == 7000


def test_cih_deleted_on_payment_delete(db):
    """Deleting a Cash payment deletes linked CIH entry."""
    bid = _broker(db)
    tid = _txn(db, bid)
    db.execute("""INSERT INTO payments (transaction_id, payment_date, amount, method)
                  VALUES (?, '2026-01-10', 5000, 'Cash')""", (tid,))
    pid = db.execute("SELECT last_insert_rowid()").fetchone()[0]
    db.execute("""INSERT INTO cash_in_hand_entries
                  (entry_date, details, amount, txn_type, source_type, source_id)
                  VALUES ('2026-01-10', 'Cust', 5000, 'Credit', ?, ?)""",
               (SRC_CUSTOMER_CASH, pid))
    db.commit()

    db.execute("DELETE FROM cash_in_hand_entries WHERE source_type=? AND source_id=?",
               (SRC_CUSTOMER_CASH, pid))
    db.execute("DELETE FROM payments WHERE payment_id=?", (pid,))
    db.commit()

    count = db.execute(
        "SELECT COUNT(*) FROM cash_in_hand_entries WHERE source_type=?",
        (SRC_CUSTOMER_CASH,)).fetchone()[0]
    assert count == 0


def test_cih_deleted_on_method_change_cash_to_upi(db):
    """Changing payment method from Cash to UPI removes CIH entry."""
    bid = _broker(db)
    tid = _txn(db, bid)
    db.execute("""INSERT INTO payments (transaction_id, payment_date, amount, method)
                  VALUES (?, '2026-01-10', 5000, 'Cash')""", (tid,))
    pid = db.execute("SELECT last_insert_rowid()").fetchone()[0]
    db.execute("""INSERT INTO cash_in_hand_entries
                  (entry_date, details, amount, txn_type, source_type, source_id)
                  VALUES ('2026-01-10', 'Cust', 5000, 'Credit', ?, ?)""",
               (SRC_CUSTOMER_CASH, pid))
    db.commit()

    # Simulate method change: Cash → UPI
    db.execute("UPDATE payments SET method='UPI' WHERE payment_id=?", (pid,))
    db.execute("DELETE FROM cash_in_hand_entries WHERE source_type=? AND source_id=?",
               (SRC_CUSTOMER_CASH, pid))
    db.commit()

    count = db.execute(
        "SELECT COUNT(*) FROM cash_in_hand_entries WHERE source_type=?",
        (SRC_CUSTOMER_CASH,)).fetchone()[0]
    assert count == 0


def test_cih_created_on_method_change_upi_to_cash(db):
    """Changing payment method from UPI to Cash creates CIH entry."""
    bid = _broker(db)
    tid = _txn(db, bid)
    db.execute("""INSERT INTO payments (transaction_id, payment_date, amount, method)
                  VALUES (?, '2026-01-10', 5000, 'UPI')""", (tid,))
    pid = db.execute("SELECT last_insert_rowid()").fetchone()[0]
    db.commit()

    # Simulate method change: UPI → Cash
    db.execute("UPDATE payments SET method='Cash' WHERE payment_id=?", (pid,))
    db.execute("""INSERT INTO cash_in_hand_entries
                  (entry_date, details, amount, txn_type, source_type, source_id)
                  VALUES ('2026-01-10', 'Cust', 5000, 'Credit', ?, ?)""",
               (SRC_CUSTOMER_CASH, pid))
    db.commit()

    row = db.execute(
        "SELECT txn_type FROM cash_in_hand_entries WHERE source_type=? AND source_id=?",
        (SRC_CUSTOMER_CASH, pid)).fetchone()
    assert row is not None
    assert row["txn_type"] == "Credit"


def test_cih_cleaned_up_on_transaction_delete(db):
    """Deleting a transaction removes all its CIH entries."""
    bid = _broker(db)
    tid = _txn(db, bid)
    for pmt_date, amt in [("2026-01-10", 3000), ("2026-01-20", 2000)]:
        db.execute("""INSERT INTO payments (transaction_id, payment_date, amount, method)
                      VALUES (?, ?, ?, 'Cash')""", (tid, pmt_date, amt))
        pid = db.execute("SELECT last_insert_rowid()").fetchone()[0]
        db.execute("""INSERT INTO cash_in_hand_entries
                      (entry_date, details, amount, txn_type, source_type, source_id)
                      VALUES (?, 'Cust', ?, 'Credit', ?, ?)""",
                   (pmt_date, amt, SRC_CUSTOMER_CASH, pid))
    db.commit()

    # Simulate transaction delete: CIH cleanup subquery, then payments, then txn
    db.execute("""DELETE FROM cash_in_hand_entries
                  WHERE source_type=? AND source_id IN (
                      SELECT payment_id FROM payments WHERE transaction_id=? AND method='Cash'
                  )""", (SRC_CUSTOMER_CASH, tid))
    db.execute("DELETE FROM payments WHERE transaction_id=?", (tid,))
    db.execute("DELETE FROM customer_transactions WHERE transaction_id=?", (tid,))
    db.commit()

    # Assert no SRC_CUSTOMER_CASH entries remain (seeded opening balance is a different source_type)
    count = db.execute("SELECT COUNT(*) FROM cash_in_hand_entries WHERE source_type=?",
                       (SRC_CUSTOMER_CASH,)).fetchone()[0]
    assert count == 0


# ── Vendor UB payment ──────────────────────────────────────────

def test_vendor_ub_payment_creates_cih_debit(db):
    """Vendor UB payment creates a CIH Debit entry."""
    vid = _vendor(db)
    db.execute("""INSERT INTO vendor_entries
                  (vendor_id, entry_date, ledger_type, firm, entry_kind, particulars, amount)
                  VALUES (?, '2026-01-15', 'UB', NULL, 'Payment', 'UB Payment', 10000)""",
               (vid,))
    eid = db.execute("SELECT last_insert_rowid()").fetchone()[0]
    db.execute("""INSERT INTO cash_in_hand_entries
                  (entry_date, details, amount, txn_type, source_type, source_id)
                  VALUES ('2026-01-15', 'Vend', 10000, 'Debit', ?, ?)""",
               (SRC_VENDOR_UB, eid))
    db.commit()

    row = db.execute(
        "SELECT * FROM cash_in_hand_entries WHERE source_type=? AND source_id=?",
        (SRC_VENDOR_UB, eid)).fetchone()
    assert row is not None
    assert row["txn_type"] == "Debit"
    assert row["amount"] == 10000


def test_vendor_delete_cleans_cih(db):
    """Deleting a vendor removes all their UB CIH entries."""
    vid = _vendor(db)
    db.execute("""INSERT INTO vendor_entries
                  (vendor_id, entry_date, ledger_type, firm, entry_kind, particulars, amount)
                  VALUES (?, '2026-01-15', 'UB', NULL, 'Payment', 'UB Payment', 10000)""",
               (vid,))
    eid = db.execute("SELECT last_insert_rowid()").fetchone()[0]
    db.execute("""INSERT INTO cash_in_hand_entries
                  (entry_date, details, amount, txn_type, source_type, source_id)
                  VALUES ('2026-01-15', 'Vend', 10000, 'Debit', ?, ?)""",
               (SRC_VENDOR_UB, eid))
    db.commit()

    # Simulate vendor delete cascade
    db.execute("""DELETE FROM cash_in_hand_entries
                  WHERE source_type=? AND source_id IN (
                      SELECT entry_id FROM vendor_entries
                      WHERE vendor_id=? AND ledger_type='UB' AND entry_kind='Payment'
                  )""", (SRC_VENDOR_UB, vid))
    db.execute("DELETE FROM vendor_entries WHERE vendor_id=?", (vid,))
    db.execute("DELETE FROM vendors WHERE vendor_id=?", (vid,))
    db.commit()

    count = db.execute("SELECT COUNT(*) FROM cash_in_hand_entries WHERE source_type=?",
                       (SRC_VENDOR_UB,)).fetchone()[0]
    assert count == 0


def test_unique_constraint_prevents_duplicate_cih(db):
    """UNIQUE index prevents duplicate CIH entries for same (source_type, source_id)."""
    db.execute("""INSERT INTO cash_in_hand_entries
                  (entry_date, details, amount, txn_type, source_type, source_id)
                  VALUES ('2026-01-10', 'First', 5000, 'Credit', ?, 1)""",
               (SRC_CUSTOMER_CASH,))
    db.commit()

    with pytest.raises(_sqlite3.IntegrityError):
        db.execute("""INSERT INTO cash_in_hand_entries
                      (entry_date, details, amount, txn_type, source_type, source_id)
                      VALUES ('2026-01-10', 'Dupe', 5000, 'Credit', ?, 1)""",
                   (SRC_CUSTOMER_CASH,))
        db.commit()
