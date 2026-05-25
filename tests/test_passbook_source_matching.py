"""
AUDIT-017: passbook_entry_id FK-based payment matching.

Verifies that allocate_to_customer sets passbook_entry_id on the payment row,
and that _unlink_allocation uses that FK (not a note LIKE pattern) to find and
delete the payment.
"""
import pytest
from utils.passbook_helpers import allocate_to_customer, _unlink_allocation
from utils.db import SRC_ALLOCATION, SRC_MANUAL


def _seed_broker(db):
    db.execute("INSERT OR IGNORE INTO brokers (broker_name) VALUES ('Test Broker')")
    return db.execute(
        "SELECT broker_id FROM brokers WHERE broker_name='Test Broker'"
    ).fetchone()[0]


def _seed_transaction(db, customer="Ravi", total=5000, bill_date="2026-01-01"):
    bid = _seed_broker(db)
    db.execute("""
        INSERT INTO customer_transactions
          (broker_id, customer_name, date, total_amount, payment_status, calc_status)
        VALUES (?, ?, ?, ?, 'Pending', 'Pending')
    """, (bid, customer, bill_date, total))
    return db.execute("SELECT last_insert_rowid()").fetchone()[0]


def _seed_passbook_entry(db, firm="SP Spices", amount=5000, entry_date="2026-02-01",
                          txn_type="Credit"):
    db.execute("""
        INSERT INTO passbook_entries
          (firm, entry_date, details, amount, txn_type, source_type)
        VALUES (?, ?, 'Suspense', ?, ?, 'Manual')
    """, (firm, entry_date, amount, txn_type))
    return db.execute("SELECT last_insert_rowid()").fetchone()[0]


def test_allocate_sets_passbook_entry_id(db):
    """allocate_to_customer must set passbook_entry_id on the inserted payment row."""
    tid = _seed_transaction(db)
    eid = _seed_passbook_entry(db)
    db.commit()

    result = allocate_to_customer(db, eid, "Ravi")
    db.commit()

    assert result["success"] is True
    pmt = db.execute(
        "SELECT passbook_entry_id FROM payments WHERE passbook_entry_id = ?",
        (eid,)
    ).fetchone()
    assert pmt is not None, "passbook_entry_id not set on payment row"
    assert pmt["passbook_entry_id"] == eid


def test_allocate_sets_source_type_allocation(db):
    """Passbook entry source_type changes to SRC_ALLOCATION after allocation."""
    tid = _seed_transaction(db)
    eid = _seed_passbook_entry(db)
    db.commit()

    allocate_to_customer(db, eid, "Ravi")
    db.commit()

    entry = db.execute(
        "SELECT source_type, source_id FROM passbook_entries WHERE entry_id=?",
        (eid,)
    ).fetchone()
    assert entry["source_type"] == SRC_ALLOCATION
    assert entry["source_id"] == tid


def test_unlink_uses_passbook_entry_id_not_note(db):
    """_unlink_allocation must locate the payment via passbook_entry_id FK,
    not via a note LIKE pattern — even when the note is blank."""
    tid = _seed_transaction(db)
    eid = _seed_passbook_entry(db)
    db.commit()
    allocate_to_customer(db, eid, "Ravi")
    db.commit()

    # Overwrite the note so a LIKE match would fail
    db.execute("UPDATE payments SET note='' WHERE passbook_entry_id=?", (eid,))
    db.commit()

    result = _unlink_allocation(db, eid)
    db.commit()

    assert result["success"] is True

    # Payment row should be gone
    pmt = db.execute(
        "SELECT payment_id FROM payments WHERE passbook_entry_id=?", (eid,)
    ).fetchone()
    assert pmt is None


def test_unlink_reverts_passbook_entry_to_suspense(db):
    """After _unlink_allocation the passbook entry returns to source_type=SRC_MANUAL."""
    tid = _seed_transaction(db)
    eid = _seed_passbook_entry(db)
    db.commit()
    allocate_to_customer(db, eid, "Ravi")
    db.commit()
    _unlink_allocation(db, eid)
    db.commit()

    entry = db.execute(
        "SELECT source_type, source_id FROM passbook_entries WHERE entry_id=?",
        (eid,)
    ).fetchone()
    assert entry["source_type"] == SRC_MANUAL
    assert entry["source_id"] is None


def test_unlink_recalculates_transaction_payment_status(db):
    """After unlink, the transaction payment_received resets to 0 and status to Pending."""
    tid = _seed_transaction(db, total=5000)
    eid = _seed_passbook_entry(db, amount=5000)
    db.commit()
    allocate_to_customer(db, eid, "Ravi")
    db.commit()

    txn_mid = db.execute(
        "SELECT payment_received, payment_status FROM customer_transactions "
        "WHERE transaction_id=?", (tid,)
    ).fetchone()
    assert float(txn_mid["payment_received"]) == 5000.0

    _unlink_allocation(db, eid)
    db.commit()

    txn_after = db.execute(
        "SELECT payment_received, payment_status FROM customer_transactions "
        "WHERE transaction_id=?", (tid,)
    ).fetchone()
    assert float(txn_after["payment_received"]) == 0.0
    assert txn_after["payment_status"] == "Pending"


def test_allocate_with_target_txn_id(db):
    """allocate_to_customer with explicit target_txn_id uses that transaction."""
    tid1 = _seed_transaction(db, customer="Ravi", total=3000)
    tid2 = _seed_transaction(db, customer="Ravi", total=7000)
    eid  = _seed_passbook_entry(db, amount=3000)
    db.commit()

    result = allocate_to_customer(db, eid, "Ravi", target_txn_id=tid1)
    db.commit()

    assert result["success"] is True
    assert result["target_txn_id"] == tid1

    pmt = db.execute(
        "SELECT transaction_id FROM payments WHERE passbook_entry_id=?", (eid,)
    ).fetchone()
    assert pmt["transaction_id"] == tid1
