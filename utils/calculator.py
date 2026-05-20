"""
Interest / settlement calculation engine.
No Streamlit imports here — pure Python only.
"""

from datetime import datetime, date, timedelta

from utils.db import get_conn, INTEREST_RATE_PCT


def _is_leap(year: int) -> bool:
    return year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)


def _interest_factor(start: date, end: date) -> float:
    """
    Returns the time-fraction for interest accrual between start (exclusive)
    and end (inclusive). Splits the accrual window by calendar year so a bill
    that straddles a leap year gets 366 in the denominator for the leap year
    portion and 365 for non-leap portions.
    """
    if end <= start:
        return 0.0
    total = 0.0
    cur = start
    while cur < end:
        year_end     = date(cur.year, 12, 31)
        segment_end  = min(end, year_end)
        days         = (segment_end - cur).days
        year_days    = 366 if _is_leap(cur.year) else 365
        total       += days / year_days
        cur          = segment_end + timedelta(days=1) if segment_end < end else end
    return total


def line_total(bags, bag_rate, qty, rate) -> float:
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
    try:
        txn = conn.execute(
            "SELECT * FROM customer_transactions WHERE transaction_id=?",
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
            "SELECT * FROM payments WHERE transaction_id=? ORDER BY payment_date ASC",
            (transaction_id,)
        ).fetchall()

        pmts = [dict(r) for r in pmts_rows]
        running_principal = total_bill
        total_paid        = 0.0
        total_interest    = 0.0
        payment_details   = []

        for p in pmts:
            try:
                pdate = datetime.strptime(str(p["payment_date"]), "%Y-%m-%d").date()
            except Exception:
                pdate = start_date
            pdate = min(pdate, settle_date)   # cap future-dated payments
            amt           = float(p["amount"])
            interest_days = max((pdate - interest_start).days, 0)
            factor        = _interest_factor(interest_start, pdate)
            interest      = round(amt * (rate / 100) * factor, 2)
            payment_details.append({
                "payment_id":        p["payment_id"],
                "date":              pdate,
                "amount":            amt,
                "method":            p.get("method", "Cash"),
                "note":              p.get("note", ""),
                "days":              interest_days,
                "days_from_start":   max((pdate - start_date).days, 0),
                "within_grace":      pdate < interest_start,
                "interest":          interest,
                "running_principal": running_principal,
            })
            running_principal -= amt
            total_paid        += amt
            total_interest    += interest

        _raw_remaining      = total_bill - total_paid
        remaining_principal = _raw_remaining                           # negative = overpayment
        overpayment         = round(max(-_raw_remaining, 0), 2)
        remaining_days_raw  = max((settle_date - start_date).days, 0)
        remaining_int_days  = max((settle_date - interest_start).days, 0)
        _rem_factor        = _interest_factor(interest_start, settle_date)
        remaining_interest  = round(max(remaining_principal, 0) * (rate / 100) * _rem_factor, 2)
        total_interest      = round(total_interest + remaining_interest, 2)

        discount_amount  = round(total_bill * disc_pct / 100, 2)
        brokerage_amount = round(total_bill * 0.01, 2) if brok_flag else 0.0

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
        }
    finally:
        if _own_conn:
            conn.close()
