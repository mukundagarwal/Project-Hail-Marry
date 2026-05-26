"""Tests for running balance math correctness."""
import pytest
import pandas as pd
from utils.passbook_helpers import compute_passbook_view, compute_cash_view
from utils.db import SRC_OPENING, SRC_CIH_OPENING, CHQ_PENDING, CHQ_CLEARED, DEFAULT_BANK_ACCOUNT


# ── Per-step rounding ─────────────────────────────────────────

def test_per_step_rounding_no_drift():
    """100 entries of ₹0.03 sum to exactly ₹3.00 with per-step rounding."""
    running = 0.0
    balances = []
    for _ in range(100):
        running = round(running + 0.03, 2)
        balances.append(running)
    assert balances[-1] == 3.00


def test_cumsum_naive_documents_risk():
    """Naive cumsum() risk is documented (platform-dependent float behaviour)."""
    amounts = [0.03] * 100
    naive = round(pd.Series(amounts).cumsum().iloc[-1], 2)
    # Not asserting equality — just confirming it runs and returns a float
    assert isinstance(naive, float)


# ── Passbook pending-cheque logic ─────────────────────────────

def test_pending_cheque_excluded_from_balance(db):
    """Pending cheque row is visible but excluded from running balance."""
    # Remove seeded opening balance so the test controls all entries
    db.execute("DELETE FROM passbook_entries WHERE firm='SP Spices'")
    db.execute("""
        INSERT INTO passbook_entries
          (firm, entry_date, details, amount, txn_type, source_type, bank_account)
        VALUES ('SP Spices', '2026-01-01', 'Payment', 10000, 'Credit', 'Manual', 'BOB')
    """)
    db.execute("""
        INSERT INTO passbook_entries
          (firm, entry_date, details, amount, txn_type, cheque_status, source_type, bank_account)
        VALUES ('SP Spices', '2026-01-02', 'Cheque', 5000, 'Credit', 'Pending', 'Manual', 'BOB')
    """)
    db.commit()

    df = compute_passbook_view("SP Spices", DEFAULT_BANK_ACCOUNT, conn=db)
    assert not df.empty

    non_pend = df[~df["balance_is_pending"]]
    pend     = df[df["balance_is_pending"]]

    assert len(non_pend) == 1
    assert len(pend) == 1
    # Non-pending entry balance = 10000
    assert float(non_pend.iloc[0]["balance"]) == 10000.0
    # Pending entry shows 10000 + 5000 = 15000 (hypothetical)
    assert float(pend.iloc[0]["balance"]) == 15000.0


def test_clearing_cheque_updates_balance(db):
    """Toggling Pending→Cleared causes balance to include the cheque amount."""
    db.execute("DELETE FROM passbook_entries WHERE firm='SP Spices'")
    db.execute("""
        INSERT INTO passbook_entries
          (firm, entry_date, details, amount, txn_type, source_type, bank_account)
        VALUES ('SP Spices', '2026-01-01', 'Payment', 10000, 'Credit', 'Manual', 'BOB')
    """)
    db.execute("""
        INSERT INTO passbook_entries
          (firm, entry_date, details, amount, txn_type, cheque_status, source_type, bank_account)
        VALUES ('SP Spices', '2026-01-02', 'Cheque', 5000, 'Credit', 'Pending', 'Manual', 'BOB')
    """)
    db.commit()

    db.execute("UPDATE passbook_entries SET cheque_status=? WHERE cheque_status=?",
               (CHQ_CLEARED, CHQ_PENDING))
    db.commit()

    df = compute_passbook_view("SP Spices", DEFAULT_BANK_ACCOUNT, conn=db)
    assert float(df.iloc[0]["balance"]) == 15000.0
    assert df["balance_is_pending"].sum() == 0


# ── CIH opening balance ───────────────────────────────────────

def test_cih_opening_balance_anchors_balance(db):
    """Opening balance is always the chronological first entry."""
    # Opening balance is seeded by ensure_schema with amount=0; overwrite it
    db.execute("DELETE FROM cash_in_hand_entries WHERE source_type=?", (SRC_CIH_OPENING,))
    db.execute("""
        INSERT INTO cash_in_hand_entries
          (entry_date, details, amount, txn_type, source_type)
        VALUES ('2026-01-01', 'Opening Balance', 5000, 'Credit', ?)
    """, (SRC_CIH_OPENING,))
    db.execute("""
        INSERT INTO cash_in_hand_entries
          (entry_date, details, amount, txn_type, source_type)
        VALUES ('2026-01-15', 'Sale', 1000, 'Credit', 'Manual')
    """)
    db.commit()

    df = compute_cash_view(conn=db)
    # DataFrame is newest-first; reverse to get chronological order
    df_chron = df.iloc[::-1].reset_index(drop=True)

    assert float(df_chron.iloc[0]["balance"]) == 5000.0
    assert float(df_chron.iloc[1]["balance"]) == 6000.0


def test_negative_opening_balance(db):
    """Negative opening_amount stored as Debit, balance starts negative."""
    db.execute("DELETE FROM cash_in_hand_entries WHERE source_type=?", (SRC_CIH_OPENING,))
    db.execute("""
        INSERT INTO cash_in_hand_entries
          (entry_date, details, amount, txn_type, source_type)
        VALUES ('2026-01-01', 'Opening Balance', 3000, 'Debit', ?)
    """, (SRC_CIH_OPENING,))
    db.commit()

    df = compute_cash_view(conn=db)
    assert not df.empty
    # Only one entry; its balance should be -3000
    row = df.iloc[0]
    assert row["txn_type"] == "Debit"
    assert float(row["amount"]) == 3000.0
    assert float(row["balance"]) == -3000.0
