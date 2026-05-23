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
    """Payment on 30/360 day 39, grace=35 → 4 days of interest (39-35)."""
    tid = _seed_transaction(db, total=36500, bill_date="2026-01-01")
    _add_payment(db, tid, 36500, "2026-02-10")  # 40 calendar / 39 30-360 days
    db.commit()

    res = calculate_final_settlement(
        tid, settlement_date=date(2026, 2, 10), grace_days=35, conn=db)

    # 4 30/360-days of interest on 36500 at INTEREST_RATE_PCT
    expected = round(36500 * (INTEREST_RATE_PCT / 100) * (4 / 360), 2)
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


def test_interest_factor_uses_360_days():
    """_interest_factor uses 30/360: Jan 1 → Jul 1 = 6 months = 180/360 = 0.5 exactly."""
    from utils.calculator import _interest_factor
    start = date(2026, 1, 1)
    end   = date(2026, 7, 1)   # 6 months × 30 = 180 days in 30/360
    result = _interest_factor(start, end)
    assert abs(result - 0.5) < 0.0001


def test_days_30_360():
    """_days_30_360 matches the 30/360 formula: (Y2-Y1)×360 + (M2-M1)×30 + (D2-D1)."""
    from utils.calculator import _days_30_360
    # Conversation example: 07 Aug 2025 → 14 Jan 2026
    assert _days_30_360(date(2025, 8, 7), date(2026, 1, 14)) == 157
    # Same month: 1 Jan → 31 Jan = 30 days (not 30 calendar)
    assert _days_30_360(date(2026, 1, 1), date(2026, 1, 31)) == 30
    # Exactly one year
    assert _days_30_360(date(2025, 1, 1), date(2026, 1, 1)) == 360
    # Start == End
    assert _days_30_360(date(2026, 1, 1), date(2026, 1, 1)) == 0
