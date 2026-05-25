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


# ── AUDIT-005: Interest waiver threshold is INCLUSIVE (<=) ──────────────


def test_threshold_boundary_exactly_at(db):
    """Remaining principal == 7.5% of bill → interest waived (inclusive boundary)."""
    from utils.calculator import INTEREST_WAIVER_THRESHOLD_PCT
    total = 10000
    tid = _seed_transaction(db, total=total, bill_date="2026-01-01")
    # 9250 paid within grace → remaining = 750.00 = exactly 7.5%
    _add_payment(db, tid, 9250, "2026-01-10")
    db.commit()

    res = calculate_final_settlement(
        tid, settlement_date=date(2026, 6, 1), grace_days=35, conn=db)

    threshold = round(total * INTEREST_WAIVER_THRESHOLD_PCT / 100.0, 2)
    assert res["remaining_principal"] == threshold
    assert res["interest_waived"] is True
    assert res["remaining_interest"] == 0.0


def test_threshold_boundary_just_above(db):
    """Remaining principal > 7.5% of bill → interest is NOT waived."""
    total = 10000
    tid = _seed_transaction(db, total=total, bill_date="2026-01-01")
    # 9249.99 paid → remaining = 750.01 > 7.5%
    _add_payment(db, tid, 9249.99, "2026-01-10")
    db.commit()

    res = calculate_final_settlement(
        tid, settlement_date=date(2026, 6, 1), grace_days=35, conn=db)

    assert res["interest_waived"] is False
    assert res["remaining_interest"] > 0.0


def test_threshold_boundary_just_below(db):
    """Remaining principal < 7.5% of bill → interest is waived."""
    total = 10000
    tid = _seed_transaction(db, total=total, bill_date="2026-01-01")
    # 9250.01 paid → remaining = 749.99 < 7.5%
    _add_payment(db, tid, 9250.01, "2026-01-10")
    db.commit()

    res = calculate_final_settlement(
        tid, settlement_date=date(2026, 6, 1), grace_days=35, conn=db)

    assert res["interest_waived"] is True
    assert res["remaining_interest"] == 0.0


# ── AUDIT-007: ISDA 30/360 month-end rules ──────────────────────────────


def test_isda_rule1_last_feb_start(db):
    """ISDA Rule 1: last day of Feb as start → d1 treated as 30.
    Feb 28 → Mar 28 (non-leap): plain 30/360 gives 30, ISDA gives 28."""
    from utils.calculator import _days_30_360
    # Without rule: (3-2)*30 + (28-28) = 30
    # With rule 1:  d1=30 → (3-2)*30 + (28-30) = 28
    assert _days_30_360(date(2026, 2, 28), date(2026, 3, 28)) == 28


def test_isda_rule1_leap_year_last_feb_start(db):
    """ISDA Rule 1 applies to Feb 29 in a leap year as start.
    Feb 29 → Mar 29 (leap): plain gives 30, ISDA gives 29."""
    from utils.calculator import _days_30_360
    # Feb 2028 is leap (29 days). Without rule: 30 + (29-29) = 30
    # With rule 1: d1=30 → 30 + (29-30) = 29
    assert _days_30_360(date(2028, 2, 29), date(2028, 3, 29)) == 29


def test_isda_rule2_both_last_feb(db):
    """ISDA Rule 2: both start and end are last day of Feb → exactly 360 days."""
    from utils.calculator import _days_30_360
    # Feb 28, 2025 → Feb 28, 2026 (both last of Feb)
    # Rule 1: d1=30. Rule 2: d2=30. Result = 360 + 0 + 0 = 360
    assert _days_30_360(date(2025, 2, 28), date(2026, 2, 28)) == 360


def test_isda_rule3_d1_31(db):
    """ISDA Rule 3: start day = 31 → treated as 30.
    Jan 31 → Feb 28: plain gives 27, ISDA gives 28."""
    from utils.calculator import _days_30_360
    # Without rule: (2-1)*30 + (28-31) = 30-3 = 27
    # With rule 3: d1=30 → (2-1)*30 + (28-30) = 28
    assert _days_30_360(date(2026, 1, 31), date(2026, 2, 28)) == 28


def test_isda_rule4_d2_31_and_d1_30(db):
    """ISDA Rule 4: end day = 31 AND adjusted start = 30 → end treated as 30.
    Jan 30 → Mar 31: plain gives 61, ISDA gives 60."""
    from utils.calculator import _days_30_360
    # d1=30 (naturally), d2=31, d1=30 → d2=30
    # Without rule 4: 2*30 + (31-30) = 61
    # With rule 4:    2*30 + (30-30) = 60
    assert _days_30_360(date(2026, 1, 30), date(2026, 3, 31)) == 60


def test_isda_rule4_via_rule3_then_rule4(db):
    """Rules 3+4 chain: Jan 31 → Mar 31 → d1 goes 31→30, then d2 goes 31→30."""
    from utils.calculator import _days_30_360
    # Rule 3: d1=30. Rule 4: d2=30.
    # Result = 2*30 + (30-30) = 60
    assert _days_30_360(date(2026, 1, 31), date(2026, 3, 31)) == 60


def test_days_30_360_end_before_start_returns_zero(db):
    """end <= start always returns 0 (no negative day counts)."""
    from utils.calculator import _days_30_360
    assert _days_30_360(date(2026, 3, 1), date(2026, 1, 1)) == 0
    assert _days_30_360(date(2026, 1, 1), date(2026, 1, 1)) == 0
