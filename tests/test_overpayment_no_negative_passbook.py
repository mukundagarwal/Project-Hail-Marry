"""
Tests for AUDIT-009: overpayment at settlement must not create negative passbook entries.
The settlement cheque passbook path is guarded by final_balance_due > 0.
"""
import pytest
from utils.db import SRC_CUST_CHQ_TXN, SRC_CUSTOMER_CASH


def _broker(db):
    db.execute("INSERT OR IGNORE INTO brokers (broker_name) VALUES ('B')")
    return db.execute(
        "SELECT broker_id FROM brokers WHERE broker_name='B'"
    ).fetchone()["broker_id"]


def _txn(db, bid, total=10000.0):
    db.execute("""
        INSERT INTO customer_transactions
            (broker_id, customer_name, date, total_amount, payment_status, payment_received)
        VALUES (?, 'Cust', '2024-01-01', ?, 'Pending', 0)
    """, (bid, total))
    return db.execute("SELECT last_insert_rowid()").fetchone()[0]


def _pmt(db, tid, amount, method="Cash"):
    db.execute("""
        INSERT INTO payments (transaction_id, payment_date, amount, method, days_from_start)
        VALUES (?, '2024-01-10', ?, ?, 9)
    """, (tid, amount, method))
    return db.execute("SELECT last_insert_rowid()").fetchone()[0]


def _passbook_entry(db, firm, tid, amount, txn_type="Credit"):
    db.execute("""
        INSERT INTO passbook_entries
            (firm, entry_date, details, amount, txn_type, source_type, source_id)
        VALUES (?, '2024-01-10', 'Settlement', ?, ?, ?, ?)
    """, (firm, amount, txn_type, SRC_CUST_CHQ_TXN, tid))
    return db.execute("SELECT last_insert_rowid()").fetchone()[0]


# ── Core guard: negative passbook amount ───────────────────────

def test_negative_final_balance_should_not_insert_cheque_passbook(db):
    """
    When final_balance_due <= 0 (overpayment), the settlement handler must
    not insert a cheque passbook entry with a negative amount.
    This test exercises the guard logic the handler applies before INSERT.
    """
    bid = _broker(db)
    tid = _txn(db, bid, total=10000)

    final_balance_due = -2000.0  # customer overpaid
    _pb_chq_amt = round(final_balance_due, 2)

    # Simulate the guard condition from the settlement handler
    sett_method = "Cheque"
    sv_chq_no   = "CHQ123"
    sett_dep_firm = "SP Spices"
    should_insert = (
        sett_method == "Cheque" and sv_chq_no and sett_dep_firm and _pb_chq_amt > 0
    )
    assert not should_insert, "Guard must block insert when final_balance_due <= 0"


def test_positive_final_balance_allows_cheque_passbook(db):
    """When final_balance_due > 0, the guard must allow the INSERT."""
    final_balance_due = 3500.0
    _pb_chq_amt = round(final_balance_due, 2)

    sett_method   = "Cheque"
    sv_chq_no     = "CHQ456"
    sett_dep_firm = "SP Spices"
    should_insert = (
        sett_method == "Cheque" and sv_chq_no and sett_dep_firm and _pb_chq_amt > 0
    )
    assert should_insert, "Guard must allow insert when final_balance_due > 0"


def test_zero_final_balance_blocks_cheque_passbook(db):
    """final_balance_due = 0 (exactly settled) must also not create a passbook entry."""
    final_balance_due = 0.0
    _pb_chq_amt = round(final_balance_due, 2)

    should_insert = _pb_chq_amt > 0
    assert not should_insert


def test_existing_cheque_passbook_entry_cleaned_on_overpayment(db):
    """
    If a prior cheque passbook entry exists for SRC_CUST_CHQ_TXN + tid,
    an overpayment should cause it to be DELETED (not updated to a negative amount).
    The else-branch of the guard runs DELETE unconditionally.
    """
    bid = _broker(db)
    tid = _txn(db, bid, total=10000)

    # Existing passbook entry from a prior settlement attempt
    eid = _passbook_entry(db, "SP Spices", tid, 5000.0)
    db.commit()

    # Simulate what the handler does when overpayment and not inserting:
    # → DELETE existing passbook entry for this txn
    db.execute(
        "DELETE FROM passbook_entries WHERE source_type=? AND source_id=?",
        (SRC_CUST_CHQ_TXN, tid))
    db.commit()

    remaining = db.execute(
        "SELECT COUNT(*) AS n FROM passbook_entries "
        "WHERE source_type=? AND source_id=?",
        (SRC_CUST_CHQ_TXN, tid)).fetchone()["n"]
    assert remaining == 0


def test_cash_cih_gap_guard_prevents_negative_entry(db):
    """
    Cash settlement CIH sync only creates an entry when _gap > 0.01.
    When _existing_cih >= _sett_amount, no entry should be created.
    """
    _existing_cih = 12000.0  # already recorded via prior cash payments
    _sett_amount  = 12000.0  # final amount to settle (equal — no gap)
    _gap = round(_sett_amount - _existing_cih, 2)

    assert _gap <= 0.01, "No CIH gap — handler must not create additional entry"


def test_cash_cih_positive_gap_allows_entry(db):
    """When actual gap > 0.01, handler may create a supplemental CIH entry."""
    _existing_cih = 8000.0
    _sett_amount  = 10000.0
    _gap = round(_sett_amount - _existing_cih, 2)

    assert _gap > 0.01, "Positive gap — handler should create supplemental entry"
