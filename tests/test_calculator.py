"""Tests for the interest/settlement calculation engine."""
import pytest
from datetime import date
from utils.calculator import calculate_final_settlement
from utils.db import INTEREST_RATE_PCT


def _seed_transaction(db, total=10000, bill_date="2026-01-01",
                       discount_pct=0.0, brokerage=False):
    """Helper: insert a broker + transaction, return transaction_id."""
    db.execute("INSERT OR IGNORE INTO brokers (broker_name) VALUES ('Test Broker')")
    bid = db.execute(
        "SELECT broker_id FROM brokers WHERE broker_name='Test Broker'"
    ).fetchone()[0]
    db.execute("""
        INSERT INTO customer_transactions
          (broker_id, customer_name, date, total_amount, payment_status,
           discount_pct, brokerage_applied)
        VALUES (?, 'Ramesh', ?, ?, 'Pending', ?, ?)
    """, (bid, bill_date, total, discount_pct, 1 if brokerage else 0))
    return db.execute("SELECT last_insert_rowid()").fetchone()[0]


def _add_payment(db, tid, amount, payment_date):
    db.execute("""
        INSERT INTO payments (transaction_id, payment_date, amount, method)
        VALUES (?, ?, ?, 'Cash')
    """, (tid, payment_date, amount))


def test_grace_period_zero_interest(db):
    """Payment on day 10 with grace=35 → interest = 0."""
    tid = _seed_transaction(db, total=10000, bill_date="2026-01-01")
    _add_payment(db, tid, 10000, "2026-01-11")
    db.commit()

    res = calculate_final_settlement(
        tid, settlement_date=date(2026, 1, 11), grace_days=35, conn=db)

    assert res["total_interest"] == 0.0
    assert res["payments"][0]["within_grace"] is True


def test_interest_starts_after_grace(db):
    """Payment on day 40 with grace=35 → 5 days of interest."""
    tid = _seed_transaction(db, total=36500, bill_date="2026-01-01")
    _add_payment(db, tid, 36500, "2026-02-10")  # 40 days later
    db.commit()

    res = calculate_final_settlement(
        tid, settlement_date=date(2026, 2, 10), grace_days=35, conn=db)

    # 5 days of interest on 36500 at INTEREST_RATE_PCT
    expected = round(36500 * (INTEREST_RATE_PCT / 100) * (5 / 365), 2)
    assert abs(res["total_interest"] - expected) < 0.10


def test_discount_zeroes_interest(db):
    """When discount_pct > 0, total_interest must be 0."""
    tid = _seed_transaction(db, total=10000, bill_date="2026-01-01",
                             discount_pct=1.0)
    _add_payment(db, tid, 10000, "2026-03-01")  # well past grace period
    db.commit()

    res = calculate_final_settlement(
        tid, settlement_date=date(2026, 3, 1), grace_days=35, conn=db)

    assert res["total_interest"] == 0.0
    for p in res["payments"]:
        assert p["interest"] == 0.0


def test_overpayment_final_balance_negative(db):
    """Payment > total_amount → final_balance_due is negative (overpayment amount)."""
    tid = _seed_transaction(db, total=10000, bill_date="2026-01-01")
    _add_payment(db, tid, 15000, "2026-01-10")
    db.commit()

    res = calculate_final_settlement(
        tid, settlement_date=date(2026, 1, 10), grace_days=35, conn=db)

    # Within grace: no interest. No discount. No brokerage.
    # remaining_principal = 10000 - 15000 = -5000
    # final_balance_due   = -5000 - 0 - 0 + 0 = -5000
    assert res["overpayment"] == 5000.0
    assert res["final_balance_due"] == -5000.0
    assert res["remaining_principal"] == -5000.0


def test_payment_on_exact_grace_boundary(db):
    """Payment on interest_start day → within_grace=False, interest_days=0."""
    # grace=30 → interest_start = 2026-01-31
    tid = _seed_transaction(db, total=10000, bill_date="2026-01-01")
    _add_payment(db, tid, 10000, "2026-01-31")
    db.commit()

    res = calculate_final_settlement(
        tid, settlement_date=date(2026, 1, 31), grace_days=30, conn=db)

    pmt = res["payments"][0]
    assert pmt["within_grace"] is False  # pdate < interest_start is False on same day
    assert pmt["days"] == 0              # 2026-01-31 - 2026-01-31 = 0 days
    assert pmt["interest"] == 0.0


def test_future_payment_capped_at_settlement_date(db):
    """Payment dated after settlement_date is capped to settlement_date."""
    tid = _seed_transaction(db, total=10000, bill_date="2026-01-01")
    _add_payment(db, tid, 10000, "2035-01-01")  # far-future payment
    db.commit()

    settle = date(2026, 6, 1)
    res = calculate_final_settlement(
        tid, settlement_date=settle, grace_days=35, conn=db)

    # Payment date should be capped, so interest computed up to 2026-06-01
    pmt = res["payments"][0]
    assert pmt["date"] == settle


def test_leap_year_interest_factor(db):
    """Bill straddling a leap year uses 366 for leap portion."""
    # 2024 is a leap year; bill from 2024-12-01, payment 2025-02-01
    tid = _seed_transaction(db, total=36500, bill_date="2024-12-01")
    _add_payment(db, tid, 36500, "2025-02-01")
    db.commit()

    res_leapaware = calculate_final_settlement(
        tid, settlement_date=date(2025, 2, 1), grace_days=0, conn=db)

    # Compare with naive 365-only calculation for same period
    naive_days = (date(2025, 2, 1) - date(2024, 12, 1)).days
    naive_interest = round(36500 * (INTEREST_RATE_PCT / 100) * (naive_days / 365), 2)

    # Leap-aware result should differ from naive 365-only calculation
    assert abs(res_leapaware["total_interest"] - naive_interest) > 0.001
