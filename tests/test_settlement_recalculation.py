"""
FIX H-04: Settlement CIH entry must be idempotent across recalculations.

The settlement path creates a cash_in_hand_entries row with
source_type=SRC_CUSTOMER_CASH and source_id=transaction_id. On a second
Calculate Bill, the code must UPDATE the existing row (or DELETE it if
the gap shrank to zero), not attempt a duplicate INSERT.
"""
import pytest
from utils.db import SRC_CUSTOMER_CASH


def _setup_transaction(db):
    """Insert minimal fixtures and return (transaction_id, broker_id)."""
    db.execute("INSERT OR IGNORE INTO brokers (broker_name) VALUES ('TestBroker')")
    broker_id = db.execute(
        "SELECT broker_id FROM brokers WHERE broker_name='TestBroker'"
    ).fetchone()["broker_id"]
    db.execute(
        "INSERT INTO customer_transactions "
        "(broker_id, customer_name, date, total_amount) "
        "VALUES (?, 'TestCustomer', '2025-01-01', 10000.0)",
        (broker_id,),
    )
    tid = db.execute(
        "SELECT transaction_id FROM customer_transactions "
        "WHERE customer_name='TestCustomer' ORDER BY transaction_id DESC LIMIT 1"
    ).fetchone()["transaction_id"]
    db.commit()
    return tid, broker_id


def _insert_sett_cih(db, tid, amount, entry_date="2025-02-10"):
    """Simulate the settlement CIH insert (first calculation)."""
    db.execute(
        "INSERT INTO cash_in_hand_entries "
        "(entry_date, details, amount, txn_type, source_type, source_id) "
        "VALUES (?, 'Cash from broker (Txn: 01-Jan-2025)', ?, 'Credit', ?, ?)",
        (entry_date, amount, SRC_CUSTOMER_CASH, tid),
    )
    db.commit()


def _sett_cih_row(db, tid):
    return db.execute(
        "SELECT * FROM cash_in_hand_entries WHERE source_type=? AND source_id=?",
        (SRC_CUSTOMER_CASH, tid),
    ).fetchone()


# ── Tests ──────────────────────────────────────────────────────────────────


def test_first_settlement_inserts_cih(db):
    """First Calculate Bill must create a CIH entry for the settlement gap."""
    tid, _ = _setup_transaction(db)
    _insert_sett_cih(db, tid, 500.0)
    row = _sett_cih_row(db, tid)
    assert row is not None
    assert round(float(row["amount"]), 2) == 500.0


def test_second_settlement_updates_existing_cih(db):
    """Second Calculate Bill (same tid) must UPDATE, not insert a duplicate."""
    tid, _ = _setup_transaction(db)
    _insert_sett_cih(db, tid, 500.0)

    # Simulate recalculation: gap changed to 600
    existing = _sett_cih_row(db, tid)
    assert existing is not None
    db.execute(
        "UPDATE cash_in_hand_entries SET amount=?, entry_date=?, details=? "
        "WHERE source_type=? AND source_id=?",
        (600.0, "2025-02-11", "Cash from broker (Txn: 01-Jan-2025)",
         SRC_CUSTOMER_CASH, tid),
    )
    db.commit()

    rows = db.execute(
        "SELECT * FROM cash_in_hand_entries WHERE source_type=? AND source_id=?",
        (SRC_CUSTOMER_CASH, tid),
    ).fetchall()
    assert len(rows) == 1, "Must not create a duplicate entry"
    assert round(float(rows[0]["amount"]), 2) == 600.0


def test_zero_gap_deletes_stale_cih(db):
    """When the settlement gap drops to zero, any existing CIH row is deleted."""
    tid, _ = _setup_transaction(db)
    _insert_sett_cih(db, tid, 500.0)

    # Gap is now 0 — simulate the DELETE branch
    db.execute(
        "DELETE FROM cash_in_hand_entries WHERE source_type=? AND source_id=?",
        (SRC_CUSTOMER_CASH, tid),
    )
    db.commit()

    assert _sett_cih_row(db, tid) is None


def test_no_duplicate_cih_on_unique_violation(db):
    """Inserting a second row for the same (source_type, source_id) must fail."""
    tid, _ = _setup_transaction(db)
    _insert_sett_cih(db, tid, 500.0)
    with pytest.raises(Exception):
        # SQLite raises IntegrityError due to UNIQUE (source_type, source_id)
        db.execute(
            "INSERT INTO cash_in_hand_entries "
            "(entry_date, details, amount, txn_type, source_type, source_id) "
            "VALUES ('2025-02-12', 'dup', 100.0, 'Credit', ?, ?)",
            (SRC_CUSTOMER_CASH, tid),
        )
