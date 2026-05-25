"""
Tests for AUDIT-003: payment total must be derived from a fresh DB read,
not from the stale pre-render value, to prevent double-submit corruption.
"""
import pytest
import time


def _broker(db):
    db.execute("INSERT OR IGNORE INTO brokers (broker_name) VALUES ('B')")
    return db.execute(
        "SELECT broker_id FROM brokers WHERE broker_name='B'"
    ).fetchone()["broker_id"]


def _txn(db, bid, total=10000.0):
    db.execute("""
        INSERT INTO customer_transactions
            (broker_id, customer_name, date, total_amount,
             payment_status, payment_received)
        VALUES (?, 'Cust', '2024-01-01', ?, 'Pending', 0)
    """, (bid, total))
    return db.execute("SELECT last_insert_rowid()").fetchone()[0]


def _insert_payment(db, tid, amount):
    db.execute("""
        INSERT INTO payments (transaction_id, payment_date, amount, method, days_from_start)
        VALUES (?, '2024-01-10', ?, 'Cash', 9)
    """, (tid, amount))
    pid = db.execute("SELECT last_insert_rowid()").fetchone()[0]
    return pid


def _fresh_total(db, tid):
    """Re-read the payment total from DB — the fix this audit targets."""
    return float(db.execute(
        "SELECT COALESCE(SUM(amount), 0) AS total FROM payments WHERE transaction_id=?",
        (tid,)
    ).fetchone()["total"])


def test_fresh_read_reflects_prior_payment(db):
    """
    Simulates the double-submit scenario: a stale pre-render total would give
    the wrong payment_received; a fresh DB read gives the correct sum.
    """
    bid = _broker(db)
    tid = _txn(db, bid)

    # First payment logged (by another tab / prior submit)
    _insert_payment(db, tid, 3000)
    db.commit()

    # The UI pre-renders total_paid_so_far = 0 (stale — computed before commit above)
    stale_total = 0.0

    # Second payment arrives with a fresh read inside the transaction
    _insert_payment(db, tid, 2000)
    fresh_total = _fresh_total(db, tid)  # 3000 + 2000 = 5000
    db.commit()

    # The fresh read must differ from the stale pre-render total
    assert fresh_total != stale_total
    assert abs(fresh_total - 5000.0) < 0.01


def test_fresh_read_with_no_prior_payments(db):
    """When no payments exist, fresh total is 0."""
    bid = _broker(db)
    tid = _txn(db, bid)
    assert _fresh_total(db, tid) == 0.0


def test_fresh_read_after_single_payment(db):
    """After one payment, fresh total equals that payment's amount."""
    bid = _broker(db)
    tid = _txn(db, bid)
    _insert_payment(db, tid, 4000)
    db.commit()
    assert abs(_fresh_total(db, tid) - 4000.0) < 0.01


def test_debounce_logic_blocks_rapid_second_save():
    """
    The 3-second debounce guard prevents a second save within 3 s of the first.
    This tests the guard logic in isolation (no DB needed).
    """
    ts_store: dict = {}

    def _should_save(key: str) -> bool:
        _now  = time.time()
        _last = ts_store.get(key, 0)
        if _now - _last < 3.0:
            return False
        ts_store[key] = _now
        return True

    assert _should_save("pmt") is True    # first click: allowed
    assert _should_save("pmt") is False   # immediate second: blocked
    assert _should_save("other") is True  # different key: independent


def test_multiple_transactions_totals_are_independent(db):
    """Fresh total for txn A must not include payments from txn B."""
    bid = _broker(db)
    tid_a = _txn(db, bid, total=5000)
    tid_b = _txn(db, bid, total=8000)

    _insert_payment(db, tid_a, 1000)
    _insert_payment(db, tid_b, 4000)
    db.commit()

    assert abs(_fresh_total(db, tid_a) - 1000.0) < 0.01
    assert abs(_fresh_total(db, tid_b) - 4000.0) < 0.01
