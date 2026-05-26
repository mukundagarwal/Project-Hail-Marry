"""
Interest / settlement calculation engine.
No Streamlit imports here — pure Python only.
"""

from datetime import datetime, date, timedelta

from utils.db import (
    get_conn, INTEREST_RATE_PCT, _db_ph,
    BROKERAGE_RATE_PCT, DAYS_PER_YEAR_30_360, DAYS_PER_MONTH_30_360,
)

# Interest waiver threshold — when remaining principal is at or below this
# fraction of the original bill, no interest is charged on the remainder.
# Boundary is inclusive: exactly 7.5% qualifies for the waiver.
INTEREST_WAIVER_THRESHOLD_PCT = 7.5


def _days_30_360(start: date, end: date) -> int:
    """
    ISDA 30/360 day-count convention with month-end adjustments.

    Rules applied in order:
      1. If start is the last day of February, treat start day as 30.
      2. If end is also the last day of February AND start was too, treat end as 30.
      3. If start day = 31, treat it as 30.
      4. If end day = 31 AND adjusted start day = 30, treat end day as 30.

    Formula after adjustments: (Y2-Y1)*360 + (M2-M1)*30 + (D2-D1)
    Returns 0 if end <= start.
    """
    from calendar import monthrange

    if end <= start:
        return 0

    d1, m1, y1 = start.day, start.month, start.year
    d2, m2, y2 = end.day,   end.month,   end.year

    def _last_feb(dt: date) -> bool:
        return dt.month == 2 and dt.day == monthrange(dt.year, 2)[1]

    if _last_feb(start):
        d1 = 30
    if _last_feb(end) and _last_feb(start):
        d2 = 30
    if d1 == 31:
        d1 = 30
    if d2 == 31 and d1 == 30:
        d2 = 30

    return (y2 - y1) * DAYS_PER_YEAR_30_360 + (m2 - m1) * DAYS_PER_MONTH_30_360 + (d2 - d1)


def _interest_factor(start: date, end: date) -> float:
    """
    Returns the time fraction for interest accrual
    using the 30/360 day-count convention.
    """
    return _days_30_360(start, end) / DAYS_PER_YEAR_30_360


def line_total(bags, bag_rate, qty, rate) -> float:
    """Compute the total value of one bill line: (bags × bag_rate) + (qty × rate)."""
    return round(bags * bag_rate + qty * rate, 2)


def calculate_final_settlement(transaction_id: int,
                                settlement_date: date = None,
                                grace_days: int = 0,
                                conn=None) -> dict:
    """
    Compute full settlement for a transaction.
    Pass conn= to reuse the caller's open connection (avoids double-open).
    """
    _own_conn = (conn is None)
    if _own_conn:
        conn = get_conn()
    ph = _db_ph(conn)
    try:
        txn = conn.execute(
            f"SELECT * FROM customer_transactions WHERE transaction_id={ph}",
            (transaction_id,)
        ).fetchone()
        if txn is None:
            return {}

        start_str  = txn["date"]
        total_bill = float(txn["total_amount"] or 0)
        rate       = INTEREST_RATE_PCT
        disc_pct   = float(txn["discount_pct"] or 0)
        brok_flag  = bool(txn["brokerage_applied"] or 0)

        try:
            start_date = datetime.strptime(start_str, "%Y-%m-%d").date()
        except Exception:
            start_date = date.today()

        settle_date    = settlement_date or date.today()
        interest_start = start_date + timedelta(days=grace_days)

        pmts_rows = conn.execute(
            f"SELECT * FROM payments WHERE transaction_id={ph} ORDER BY payment_date ASC",
            (transaction_id,)
        ).fetchall()

        pmts = [dict(r) for r in pmts_rows]
        running_principal = total_bill
        total_paid        = 0.0
        total_interest    = 0.0
        payment_details   = []
        prev_int_days     = 0   # interest days at previous event (0 = interest_start)

        for p in pmts:
            try:
                pdate = datetime.strptime(str(p["payment_date"]), "%Y-%m-%d").date()
            except Exception:
                pdate = start_date
            pdate = min(pdate, settle_date)   # cap future-dated payments
            amt           = float(p["amount"])
            # Total 30/360 interest days from interest_start to this payment (for display)
            interest_days = max(_days_30_360(start_date, pdate) - grace_days, 0)
            # Incremental days since last payment (or since interest_start)
            _increment    = max(interest_days - prev_int_days, 0)
            # Interest on the full outstanding balance for the incremental period only
            _int_principal = max(0.0, running_principal)
            interest      = round(_int_principal * (rate / 100) * (_increment / DAYS_PER_YEAR_30_360), 2)
            payment_details.append({
                "payment_id":        p["payment_id"],
                "date":              pdate,
                "amount":            amt,
                "method":            p.get("method", "Cash"),
                "note":              p.get("note", ""),
                "days":              interest_days,
                "days_from_start":   _days_30_360(start_date, pdate),
                "within_grace":      pdate < interest_start,
                "interest":          interest,
                "running_principal": running_principal,
            })
            prev_int_days      = interest_days
            running_principal -= amt
            total_paid        += amt
            total_interest    += interest

        _raw_remaining      = total_bill - total_paid
        remaining_principal = _raw_remaining                           # negative = overpayment
        overpayment         = round(max(-_raw_remaining, 0), 2)
        remaining_days_raw  = max(_days_30_360(start_date, settle_date), 0)
        remaining_int_days  = max(_days_30_360(start_date, settle_date) - grace_days, 0)
        # Remaining interest only covers the period from last payment to settlement
        _rem_increment      = max(remaining_int_days - prev_int_days, 0)
        _rem_factor         = _rem_increment / DAYS_PER_YEAR_30_360
        _threshold          = round(total_bill * INTEREST_WAIVER_THRESHOLD_PCT / 100.0, 2)
        if max(remaining_principal, 0) <= _threshold:
            remaining_interest = 0.0
            _interest_waived   = True
        else:
            remaining_interest = round(
                max(remaining_principal, 0) * (rate / 100) * _rem_factor, 2)
            _interest_waived   = False
        total_interest      = round(total_interest + remaining_interest, 2)

        discount_amount  = round(total_bill * disc_pct / 100, 2)
        brokerage_amount = round(total_bill * BROKERAGE_RATE_PCT / 100.0, 2) if brok_flag else 0.0

        if disc_pct > 0:
            total_interest     = 0.0
            remaining_interest = 0.0
            for p in payment_details:
                p["interest"] = 0.0

        final_balance = round(
            remaining_principal - discount_amount - brokerage_amount + total_interest, 2
        )

        return {
            "start_date":          start_date,
            "interest_start":      interest_start,
            "grace_days":          grace_days,
            "total_bill":          total_bill,
            "rate":                rate,
            "settlement_date":     settle_date,
            "payments":            payment_details,
            "total_paid":          round(total_paid, 2),
            "total_interest":      total_interest,
            "remaining_principal": remaining_principal,
            "overpayment":         overpayment,
            "remaining_days":      remaining_days_raw,
            "remaining_int_days":  remaining_int_days,
            "remaining_interest":  remaining_interest,
            "discount_amount":     discount_amount,
            "brokerage_amount":    brokerage_amount,
            "final_balance_due":   final_balance,
            "interest_waived":     _interest_waived,
            "interest_threshold":  _threshold,
        }
    finally:
        if _own_conn:
            conn.close()
