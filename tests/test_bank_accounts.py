"""
Tests for multi-bank-account Passbook feature.

Covers:
  1. BANK_ACCOUNTS constant structure
  2. bank_account column exists in passbook_entries
  3. Unique opening balance per (firm, bank_account)
  4. compute_passbook_view filters by bank_account
  5. _firm_summary aggregates across all accounts
  6. Opening balance row seeded with DEFAULT_BANK_ACCOUNT
  7. Unknown pill logic — Unknown rows exist check
"""
import pytest
import pandas as pd
from utils.db import (
    BANK_ACCOUNTS, DEFAULT_BANK_ACCOUNT,
    FIRM_SP, FIRM_MT, FIRMS, SRC_OPENING,
)

UNKNOWN_BANK_ACCOUNT = "Unknown"
from utils.passbook_helpers import compute_passbook_view, _firm_summary


# ── 1. BANK_ACCOUNTS constant structure ───────────────────────────────────────

def test_bank_accounts_has_both_firms():
    assert FIRM_SP in BANK_ACCOUNTS
    assert FIRM_MT in BANK_ACCOUNTS


def test_bank_accounts_default_is_bob():
    assert DEFAULT_BANK_ACCOUNT == "BOB"
    for firm in FIRMS:
        assert DEFAULT_BANK_ACCOUNT in BANK_ACCOUNTS[firm]


# ── 2. bank_account column exists in passbook_entries ─────────────────────────

def test_bank_account_column_exists(db):
    db.execute(
        "INSERT INTO passbook_entries "
        "(firm, entry_date, details, amount, txn_type, source_type, bank_account) "
        "VALUES (?, ?, 'Test', 100.0, 'Credit', 'Manual', ?)",
        (FIRM_SP, "2024-01-01", DEFAULT_BANK_ACCOUNT))
    row = db.execute(
        "SELECT bank_account FROM passbook_entries "
        "WHERE firm=? AND source_type='Manual'",
        (FIRM_SP,)).fetchone()
    assert row is not None
    assert row["bank_account"] == DEFAULT_BANK_ACCOUNT


# ── 3. Unique opening balance per (firm, bank_account) ────────────────────────

def test_opening_balance_unique_per_firm_and_account(db):
    db.execute(
        "INSERT INTO passbook_entries "
        "(firm, entry_date, details, amount, txn_type, source_type, bank_account) "
        "VALUES (?, ?, 'Opening Balance', 0, 'Credit', ?, ?)",
        (FIRM_MT, "2024-01-01", SRC_OPENING, "CBI"))
    # Second insert for same firm+account must conflict
    with pytest.raises(Exception):
        db.execute(
            "INSERT INTO passbook_entries "
            "(firm, entry_date, details, amount, txn_type, source_type, bank_account) "
            "VALUES (?, ?, 'Opening Balance', 500, 'Credit', ?, ?)",
            (FIRM_MT, "2024-01-02", SRC_OPENING, "CBI"))


def test_two_firms_can_share_same_account_name(db):
    # BOB opening balance for SP and MT are separate rows — no conflict
    db.execute(
        "INSERT INTO passbook_entries "
        "(firm, entry_date, details, amount, txn_type, source_type, bank_account) "
        "VALUES (?, ?, 'Opening Balance', 1000, 'Credit', ?, ?)",
        (FIRM_MT, "2024-01-01", SRC_OPENING, "CBI"))
    # Should not raise — different firm
    db.execute(
        "INSERT INTO passbook_entries "
        "(firm, entry_date, details, amount, txn_type, source_type, bank_account) "
        "VALUES (?, ?, 'Opening Balance', 2000, 'Credit', ?, ?)",
        (FIRM_SP, "2024-01-01", SRC_OPENING, "CBI"))
    count = db.execute(
        "SELECT COUNT(*) AS n FROM passbook_entries WHERE source_type=?",
        (SRC_OPENING,)).fetchone()["n"]
    assert count >= 2


# ── 4. compute_passbook_view filters by bank_account ─────────────────────────

def test_compute_passbook_view_filters_by_account(db):
    db.execute(
        "INSERT INTO passbook_entries "
        "(firm, entry_date, details, amount, txn_type, source_type, bank_account) "
        "VALUES (?, ?, 'BOB entry', 500, 'Credit', 'Manual', ?)",
        (FIRM_SP, "2024-06-01", "BOB"))
    db.execute(
        "INSERT INTO passbook_entries "
        "(firm, entry_date, details, amount, txn_type, source_type, bank_account) "
        "VALUES (?, ?, 'CBI entry', 300, 'Credit', 'Manual', ?)",
        (FIRM_SP, "2024-06-02", "CBI"))

    df_bob = compute_passbook_view(FIRM_SP, "BOB", conn=db)
    df_cbi = compute_passbook_view(FIRM_SP, "CBI", conn=db)

    bob_details = set(df_bob["details"].tolist())
    cbi_details = set(df_cbi["details"].tolist())

    assert "BOB entry" in bob_details
    assert "CBI entry" not in bob_details
    assert "CBI entry" in cbi_details
    assert "BOB entry" not in cbi_details


# ── 5. _firm_summary aggregates across all accounts ──────────────────────────

def test_firm_summary_sums_all_accounts(db):
    db.execute(
        "INSERT INTO passbook_entries "
        "(firm, entry_date, details, amount, txn_type, source_type, bank_account) "
        "VALUES (?, ?, 'BOB txn', 1000, 'Credit', 'Manual', 'BOB')",
        (FIRM_MT, "2024-07-01"))
    db.execute(
        "INSERT INTO passbook_entries "
        "(firm, entry_date, details, amount, txn_type, source_type, bank_account) "
        "VALUES (?, ?, 'CBI txn', 500, 'Credit', 'Manual', 'CBI')",
        (FIRM_MT, "2024-07-02"))

    summary = _firm_summary(FIRM_MT, conn=db)
    assert summary["total_credits"] >= 1500.0


# ── 6. Opening balance row seeded with DEFAULT_BANK_ACCOUNT ──────────────────

def test_seeded_opening_row_uses_default_account(db):
    row = db.execute(
        "SELECT bank_account FROM passbook_entries "
        "WHERE firm=? AND source_type=?",
        (FIRM_SP, SRC_OPENING)).fetchone()
    assert row is not None
    assert row["bank_account"] == DEFAULT_BANK_ACCOUNT


# ── 7. Unknown pill logic — query for Unknown rows ────────────────────────────

def test_has_unknown_rows_when_inserted(db):
    db.execute(
        "INSERT INTO passbook_entries "
        "(firm, entry_date, details, amount, txn_type, source_type, bank_account) "
        "VALUES (?, ?, 'Old entry', 100, 'Credit', 'Manual', ?)",
        (FIRM_SP, "2023-01-01", UNKNOWN_BANK_ACCOUNT))
    row = db.execute(
        "SELECT 1 FROM passbook_entries "
        "WHERE firm=? AND bank_account=? LIMIT 1",
        (FIRM_SP, UNKNOWN_BANK_ACCOUNT)).fetchone()
    assert row is not None


def test_no_unknown_rows_when_not_inserted(db):
    row = db.execute(
        "SELECT 1 FROM passbook_entries "
        "WHERE firm=? AND bank_account=? LIMIT 1",
        (FIRM_MT, UNKNOWN_BANK_ACCOUNT)).fetchone()
    assert row is None
