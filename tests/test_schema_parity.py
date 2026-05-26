"""
FIX 3 (AUDIT-038): SQLite DDL must contain all columns present in the production schema.
Prevents future drift between test environment and production.
"""
import pytest


def _cols(db, table: str) -> set:
    """Return the set of column names for a table in the SQLite test DB."""
    rows = db.execute(f"PRAGMA table_info({table})").fetchall()
    return {r["name"] for r in rows}


# ── payments ────────────────────────────────────────────────────


def test_payments_has_cheque_columns(db):
    cols = _cols(db, "payments")
    assert "cheque_number" in cols
    assert "cheque_date"   in cols
    assert "deposit_firm"  in cols
    assert "cheque_status" in cols


def test_payments_has_passbook_entry_id(db):
    assert "passbook_entry_id" in _cols(db, "payments")


# ── passbook_entries ────────────────────────────────────────────


def test_passbook_entries_has_cheque_columns(db):
    cols = _cols(db, "passbook_entries")
    assert "cheque_number" in cols
    assert "cheque_date"   in cols
    assert "cheque_status" in cols


def test_passbook_entries_has_source_columns(db):
    cols = _cols(db, "passbook_entries")
    assert "source_type" in cols
    assert "source_id"   in cols


# ── passbook_opening_balance ────────────────────────────────────


def test_passbook_opening_balance_has_notes(db):
    assert "notes" in _cols(db, "passbook_opening_balance")


# ── customer_transactions ───────────────────────────────────────


def test_customer_transactions_has_brokerage_paid(db):
    assert "brokerage_paid" in _cols(db, "customer_transactions")


def test_customer_transactions_has_bill_sent(db):
    assert "bill_sent" in _cols(db, "customer_transactions")


def test_customer_transactions_has_grace_days(db):
    assert "grace_days" in _cols(db, "customer_transactions")


def test_customer_transactions_has_notes(db):
    assert "notes" in _cols(db, "customer_transactions")


# ── audit_log ───────────────────────────────────────────────────


def test_audit_log_has_ts_column(db):
    assert "ts" in _cols(db, "audit_log")


def test_audit_log_no_changed_at(db):
    """The old 'changed_at' column must not exist — it was renamed to 'ts'."""
    assert "changed_at" not in _cols(db, "audit_log")


# ── login_attempts ──────────────────────────────────────────────


def test_login_attempts_table_exists(db):
    tables = {r["name"] for r in db.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()}
    assert "login_attempts" in tables


def test_login_attempts_columns(db):
    cols = _cols(db, "login_attempts")
    assert "attempt_id"   in cols
    assert "ip_address"   in cols
    assert "attempted_at" in cols
    assert "success"      in cols


# ── stock_history ───────────────────────────────────────────────


def test_stock_history_columns(db):
    cols = _cols(db, "stock_history")
    for col in ("recorded_at", "category_name", "good_name", "location",
                "change_type", "bags_before", "bags_after", "bags_change",
                "kg_before", "kg_after", "kg_change", "source"):
        assert col in cols, f"stock_history missing column: {col}"


# ── cash_in_hand_opening ────────────────────────────────────────


def test_cash_in_hand_opening_has_notes(db):
    assert "notes" in _cols(db, "cash_in_hand_opening")


# ── Meta: insert/select roundtrip for new columns ───────────────


def test_payments_cheque_columns_roundtrip(db):
    """New cheque columns accept default values and can be queried."""
    db.execute("INSERT OR IGNORE INTO brokers (broker_name) VALUES ('B')")
    bid = db.execute("SELECT broker_id FROM brokers WHERE broker_name='B'").fetchone()[0]
    db.execute("""
        INSERT INTO customer_transactions
          (broker_id, customer_name, date, total_amount, payment_status, calc_status)
        VALUES (?, 'C', '2026-01-01', 1000, 'Pending', 'Pending')
    """, (bid,))
    tid = db.execute("SELECT last_insert_rowid()").fetchone()[0]
    db.execute("""
        INSERT INTO payments
          (transaction_id, payment_date, amount, method,
           cheque_number, cheque_date, deposit_firm, cheque_status)
        VALUES (?, '2026-02-01', 1000, 'Cheque', 'CHQ001', '2026-02-01', 'SP Spices', 'Pending')
    """, (tid,))
    db.commit()
    row = db.execute(
        "SELECT cheque_number, cheque_date, deposit_firm, cheque_status "
        "FROM payments WHERE transaction_id=?", (tid,)
    ).fetchone()
    assert row["cheque_number"] == "CHQ001"
    assert row["cheque_date"]   == "2026-02-01"
    assert row["deposit_firm"]  == "SP Spices"
    assert row["cheque_status"] == "Pending"
