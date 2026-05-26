"""
FIX 5 (AUDIT-022 remainder): audit_log uses cross-dialect timestamps via
Python datetime rather than SQLite-specific datetime('now') DEFAULT.
"""
import pytest
from datetime import datetime
from utils.db import log_audit


def test_log_audit_inserts_row(db):
    """log_audit must insert a row into audit_log."""
    log_audit(db, "customer_transactions", 1, "test_action")
    db.commit()
    row = db.execute("SELECT * FROM audit_log WHERE table_name='customer_transactions'").fetchone()
    assert row is not None
    assert row["action"] == "test_action"


def test_log_audit_ts_is_set(db):
    """The ts column must be a non-empty ISO-format string after log_audit."""
    import re
    log_audit(db, "payments", 42, "INSERT")
    db.commit()
    row = db.execute(
        "SELECT ts FROM audit_log WHERE table_name='payments' AND record_id=42"
    ).fetchone()
    assert row is not None
    ts_str = str(row["ts"])
    assert re.match(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}", ts_str), (
        f"ts must be ISO format YYYY-MM-DDTHH:MM:SS, got: {ts_str!r}"
    )


def test_log_audit_ts_is_recent(db):
    """The ts value must be within 5 seconds of now."""
    before = datetime.now()
    log_audit(db, "vendors", 7, "DELETE")
    db.commit()
    after = datetime.now()
    row = db.execute(
        "SELECT ts FROM audit_log WHERE table_name='vendors' AND record_id=7"
    ).fetchone()
    ts_str = str(row["ts"])
    # SQLite stores as text ISO format; parse it
    try:
        ts = datetime.fromisoformat(ts_str.replace("T", " ").split(".")[0])
    except ValueError:
        ts = datetime.strptime(ts_str[:19], "%Y-%m-%d %H:%M:%S")
    assert before <= ts <= after or abs((ts - before).total_seconds()) < 5


def test_log_audit_with_old_and_new_values(db):
    """log_audit stores old_value and new_value as JSON strings."""
    import json
    log_audit(db, "stock_levels", 5, "UPDATE",
              old_value={"bags": 10}, new_value={"bags": 8})
    db.commit()
    row = db.execute(
        "SELECT old_value, new_value FROM audit_log "
        "WHERE table_name='stock_levels' AND record_id=5"
    ).fetchone()
    assert json.loads(row["old_value"]) == {"bags": 10}
    assert json.loads(row["new_value"]) == {"bags": 8}


def test_log_audit_works_without_old_new(db):
    """log_audit must work when old_value and new_value are omitted."""
    log_audit(db, "brokers", 1, "CREATE")
    db.commit()
    row = db.execute(
        "SELECT old_value, new_value FROM audit_log WHERE table_name='brokers'"
    ).fetchone()
    assert row is not None
    assert row["old_value"] is None
    assert row["new_value"] is None


def test_audit_log_ts_range_query(db):
    """Rows inserted close together must be retrievable by ts range."""
    t_before = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    log_audit(db, "test_table", 99, "RANGE_TEST")
    db.commit()
    t_after = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")

    rows = db.execute(
        "SELECT * FROM audit_log WHERE table_name='test_table' "
        "AND ts >= ? AND ts <= ?",
        (t_before, t_after)
    ).fetchall()
    assert len(rows) >= 1
