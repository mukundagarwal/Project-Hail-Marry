"""
Pure-Python passbook helpers, extracted from pages/4_Passbook.py so they can
be imported in tests without triggering Streamlit page side-effects.
"""

import pandas as pd
from datetime import date, datetime

from utils.db import (
    get_conn, _db_ph,
    SRC_ALLOCATION, SRC_MANUAL, SRC_OPENING, SRC_CIH_OPENING,
    CHQ_PENDING, CHQ_CLEARED,
)
from utils.formatters import days_between


# ══════════════════════════════════════════════════════════════════
#  BALANCE / VIEW HELPERS
# ══════════════════════════════════════════════════════════════════

def compute_passbook_view(firm: str, conn=None) -> pd.DataFrame:
    """
    Returns DataFrame newest-first with columns:
      s_no, entry_id, entry_date, details, amount, txn_type,
      cheque_number, cheque_status, source_type, source_id,
      signed_amount, balance, balance_is_pending

    Pending cheques are excluded from the running balance; their
    displayed balance is the hypothetical 'if-cleared' value, computed
    via a SQL window function (SUM OVER) for efficiency.
    When conn is provided it is used and NOT closed; when omitted
    the function opens and closes its own connection.
    """
    _own_conn = conn is None
    if _own_conn:
        conn = get_conn()
    ph = _db_ph(conn)
    try:
        rows = conn.execute(f"""
            SELECT *,
                CASE WHEN txn_type = 'Credit' THEN amount ELSE -amount END
                    AS signed_amount,
                SUM(
                    CASE WHEN (cheque_status IS NULL OR cheque_status != 'Pending')
                         THEN CASE WHEN txn_type = 'Credit' THEN amount ELSE -amount END
                         ELSE 0.0
                    END
                ) OVER (ORDER BY entry_date ASC, entry_id ASC
                        ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)
                    AS _running_cleared
            FROM passbook_entries WHERE firm = {ph}
            ORDER BY entry_date ASC, entry_id ASC
        """, (firm,)).fetchall()
    finally:
        if _own_conn:
            conn.close()

    _empty_cols = [
        "entry_id", "entry_date", "details", "amount", "txn_type",
        "cheque_number", "cheque_status", "source_type", "source_id",
        "signed_amount", "balance", "balance_is_pending", "s_no",
    ]
    if not rows:
        return pd.DataFrame(columns=_empty_cols)

    df = pd.DataFrame([dict(r) for r in rows])
    df["balance_is_pending"] = df["cheque_status"] == "Pending"
    df["balance"] = df.apply(
        lambda r: round(float(r["_running_cleared"]) + float(r["signed_amount"]), 2)
                  if r["balance_is_pending"]
                  else round(float(r["_running_cleared"]), 2),
        axis=1,
    )
    df.drop(columns=["_running_cleared"], inplace=True)
    df["s_no"] = range(1, len(df) + 1)
    return df.iloc[::-1].reset_index(drop=True)


def compute_cash_view(conn=None) -> pd.DataFrame:
    """
    Returns DataFrame newest-first with columns:
      s_no, entry_id, entry_date, details, amount, txn_type,
      source_type, source_id, signed_amount, balance

    Running balance computed via SQL window function (SUM OVER) for efficiency.
    When conn is provided it is used and NOT closed; when omitted
    the function opens and closes its own connection.
    """
    _own_conn = conn is None
    if _own_conn:
        conn = get_conn()
    try:
        rows = conn.execute("""
            SELECT *,
                CASE WHEN txn_type = 'Credit' THEN amount ELSE -amount END
                    AS signed_amount,
                SUM(CASE WHEN txn_type = 'Credit' THEN amount ELSE -amount END)
                    OVER (ORDER BY entry_date ASC, entry_id ASC
                          ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)
                    AS _running
            FROM cash_in_hand_entries
            ORDER BY entry_date ASC, entry_id ASC
        """).fetchall()
    finally:
        if _own_conn:
            conn.close()

    _empty_cols = [
        "entry_id", "entry_date", "details", "amount", "txn_type",
        "source_type", "source_id", "signed_amount", "balance", "s_no",
    ]
    if not rows:
        return pd.DataFrame(columns=_empty_cols)

    df = pd.DataFrame([dict(r) for r in rows])
    df["balance"] = df["_running"].round(2)
    df.drop(columns=["_running"], inplace=True)
    df["s_no"] = range(1, len(df) + 1)
    return df.iloc[::-1].reset_index(drop=True)


def _firm_summary(firm: str, conn=None) -> dict:
    df = compute_passbook_view(firm, conn=conn)
    if df.empty:
        return {"balance": 0.0, "pending_sum": 0.0,
                "total_credits": 0.0, "total_debits": 0.0,
                "entry_count": 0, "last_date": None}
    non_pend      = df[~df["balance_is_pending"]]
    balance       = float(non_pend.iloc[0]["balance"]) if not non_pend.empty else 0.0
    pending_sum   = float(df.loc[df["balance_is_pending"], "amount"].sum())
    total_credits = float(df.loc[df["txn_type"] == "Credit", "amount"].sum())
    total_debits  = float(df.loc[df["txn_type"] == "Debit",  "amount"].sum())
    return {
        "balance":       round(balance, 2),
        "pending_sum":   round(pending_sum, 2),
        "total_credits": round(total_credits, 2),
        "total_debits":  round(total_debits, 2),
        "entry_count":   len(df),
        "last_date":     df.iloc[0]["entry_date"],
    }


def _cih_summary(conn=None) -> dict:
    df = compute_cash_view(conn=conn)
    if df.empty:
        return {"balance": 0.0, "total_credits": 0.0, "total_debits": 0.0,
                "entry_count": 0, "last_date": None}
    balance       = float(df.iloc[0]["balance"])
    total_credits = float(df.loc[df["txn_type"] == "Credit", "amount"].sum())
    total_debits  = float(df.loc[df["txn_type"] == "Debit",  "amount"].sum())
    return {
        "balance":       round(balance, 2),
        "total_credits": round(total_credits, 2),
        "total_debits":  round(total_debits, 2),
        "entry_count":   len(df),
        "last_date":     df.iloc[0]["entry_date"],
    }


# ══════════════════════════════════════════════════════════════════
#  ALLOCATION CASCADE  (does NOT commit — caller must commit)
# ══════════════════════════════════════════════════════════════════

def allocate_to_customer(conn, entry_id: int, customer_name: str,
                          target_txn_id: int = None) -> dict:
    """
    Allocate a passbook Credit entry to a customer's transaction.
    If target_txn_id is given, uses that specific transaction (if still eligible).
    Otherwise picks the eligible transaction with the highest outstanding balance.
    Does NOT commit — caller is responsible for the commit.

    Returns {"success": bool, "target_txn_id": int|None, "message": str}
    """
    ph = _db_ph(conn)

    entry = conn.execute(
        f"SELECT * FROM passbook_entries WHERE entry_id={ph}", (entry_id,)).fetchone()
    if not entry:
        return {"success": False, "target_txn_id": None, "message": "Passbook entry not found."}

    pb_amount = round(float(entry["amount"]), 2)
    pb_date   = str(entry["entry_date"])

    rows = conn.execute(f"""
        SELECT ct.transaction_id,
               ct.date           AS bill_date,
               ct.total_amount,
               COALESCE(SUM(p.amount), 0) AS total_paid
        FROM   customer_transactions ct
        LEFT JOIN payments p ON p.transaction_id = ct.transaction_id
        WHERE  ct.customer_name = {ph}
          AND  ct.calc_status  != 'Calculated'
          AND  ct.payment_status != 'Paid'
        GROUP  BY ct.transaction_id
        ORDER  BY (ct.total_amount - COALESCE(SUM(p.amount), 0)) DESC,
                  ct.date ASC
    """, (customer_name,)).fetchall()

    if not rows:
        return {
            "success": False, "target_txn_id": None,
            "message": (f"Customer '{customer_name}' has no eligible unpaid transactions. "
                        "Allocation skipped — entry remains as Suspense."),
        }

    if target_txn_id is not None:
        eligible_ids = [r["transaction_id"] for r in rows]
        if target_txn_id in eligible_ids:
            target = next(r for r in rows if r["transaction_id"] == target_txn_id)
        else:
            return {
                "success": False,
                "target_txn_id": None,
                "message": (
                    "The selected transaction is no longer eligible "
                    "(it may have been paid or calculated since the "
                    "page loaded). Please re-open the match panel "
                    "and select again."
                )
            }
    else:
        # Auto-allocation: use highest-outstanding rule (unchanged)
        target = rows[0]
    tid      = int(target["transaction_id"])
    old_paid = round(float(target["total_paid"]), 2)
    d_from   = days_between(str(target["bill_date"]), date.fromisoformat(pb_date))

    conn.execute(f"""
        INSERT INTO payments
          (transaction_id, payment_date, amount, method, note,
           days_from_start, interest_charged, passbook_entry_id)
        VALUES ({ph}, {ph}, {ph}, 'Bank Transfer', {ph}, {ph}, 0, {ph})
    """, (tid, pb_date, pb_amount,
          f"Auto-allocated from passbook #{entry_id}", d_from, entry_id))

    new_total = round(old_paid + pb_amount, 2)
    # NOTE: Never set to 'Paid' here, even on overpayment.
    # Overpayments are reconciled later during the Calculate Bill
    # settlement step. Setting 'Paid' here would skip that step.
    new_st    = "Partial" if new_total > 0 else "Pending"
    conn.execute(f"""
        UPDATE customer_transactions
        SET    payment_status   = {ph},
               payment_received = {ph},
               calc_status      = 'Pending',
               final_settlement = NULL,
               interest_amount  = 0
        WHERE  transaction_id = {ph}
    """, (new_st, new_total, tid))

    conn.execute(f"""
        UPDATE passbook_entries
        SET    details     = {ph},
               source_type = {ph},
               source_id   = {ph}
        WHERE  entry_id = {ph}
    """, (customer_name, SRC_ALLOCATION, tid, entry_id))

    return {"success": True, "target_txn_id": tid,
            "message": f"Allocated to '{customer_name}' (Txn #{tid})."}


# ══════════════════════════════════════════════════════════════════
#  UNLINK HELPER  (does NOT commit — caller must commit)
# ══════════════════════════════════════════════════════════════════

def _unlink_allocation(conn, entry_id: int) -> dict:
    """
    Reverse a CustomerAllocation:
    - Delete the linked payment row from payments.
    - Recompute the target transaction's payment_received + payment_status.
    - Revert the passbook entry to Suspense (source_type='Manual').
    Does NOT commit.
    """
    ph = _db_ph(conn)

    entry = conn.execute(
        f"SELECT * FROM passbook_entries WHERE entry_id={ph}", (entry_id,)).fetchone()
    if not entry:
        return {"success": False, "message": "Entry not found."}

    tid = entry["source_id"]
    if tid:
        tid = int(tid)
        pmt = conn.execute(
            f"SELECT payment_id FROM payments WHERE passbook_entry_id = {ph}",
            (entry_id,)).fetchone()
        if pmt:
            conn.execute(f"DELETE FROM payments WHERE payment_id={ph}", (pmt["payment_id"],))

        txn_row = conn.execute(
            f"SELECT total_amount FROM customer_transactions WHERE transaction_id={ph}",
            (tid,)).fetchone()
        if txn_row:
            new_paid = round(float(conn.execute(
                f"SELECT COALESCE(SUM(amount),0) AS total FROM payments WHERE transaction_id={ph}",
                (tid,)).fetchone()["total"]), 2)
            new_st = "Partial" if new_paid > 0 else "Pending"
            conn.execute(f"""
                UPDATE customer_transactions
                SET    payment_status   = {ph},
                       payment_received = {ph},
                       calc_status      = 'Pending',
                       final_settlement = NULL,
                       interest_amount  = 0
                WHERE  transaction_id = {ph}
            """, (new_st, new_paid, tid))

    conn.execute(f"""
        UPDATE passbook_entries
        SET    details     = 'Suspense',
               source_type = {ph},
               source_id   = NULL
        WHERE  entry_id = {ph}
    """, (SRC_MANUAL, entry_id))
    return {"success": True, "message": "Entry unlinked and reverted to Suspense."}
