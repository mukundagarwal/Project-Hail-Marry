"""
Tests for AUDIT-001: bill delete must reverse stock levels atomically.
"""
import pytest
from utils.db import reverse_stock_for_bill_delete


def _broker(db, name="TestBroker"):
    db.execute("INSERT OR IGNORE INTO brokers (broker_name) VALUES (?)", (name,))
    return db.execute(
        "SELECT broker_id FROM brokers WHERE broker_name=?", (name,)
    ).fetchone()["broker_id"]


def _txn(db, broker_id):
    db.execute("""
        INSERT INTO customer_transactions
            (broker_id, customer_name, date, total_amount, payment_status)
        VALUES (?, 'Test Customer', '2024-01-01', 1000, 'Pending')
    """, (broker_id,))
    return db.execute("SELECT last_insert_rowid()").fetchone()[0]


def _good(db, location="Shop"):
    row = db.execute("SELECT good_id, good_name FROM stock_goods LIMIT 1").fetchone()
    gid, gname = row["good_id"], row["good_name"]
    db.execute(
        "UPDATE stock_levels SET bags=10, quantity_kg=500.0 "
        "WHERE good_id=? AND location=? AND batch_label=''",
        (gid, location))
    return gid, gname


def test_reversal_restores_bags_and_kg(db):
    """reverse_stock_for_bill_delete adds sold bags/kg back to stock_levels."""
    bid = _broker(db)
    tid = _txn(db, bid)
    gid, gname = _good(db, "Shop")

    db.execute("""
        INSERT INTO transaction_items
            (transaction_id, type_of_goods, bags, quantity, collection_point)
        VALUES (?, ?, 3, 150.0, 'Shop')
    """, (tid, gname))

    reverse_stock_for_bill_delete(db, tid)
    db.commit()

    row = db.execute(
        "SELECT bags, quantity_kg FROM stock_levels "
        "WHERE good_id=? AND location='Shop' AND batch_label=''", (gid,)
    ).fetchone()
    assert row["bags"] == 13
    assert abs(row["quantity_kg"] - 650.0) < 0.01


def test_reversal_multiple_items(db):
    """All items in a multi-item bill are reversed independently."""
    bid = _broker(db)
    tid = _txn(db, bid)

    rows = db.execute(
        "SELECT sg.good_id, sg.good_name FROM stock_goods sg LIMIT 2"
    ).fetchall()
    assert len(rows) >= 2, "Need at least 2 goods seeded"

    for r in rows:
        db.execute(
            "UPDATE stock_levels SET bags=20, quantity_kg=1000.0 "
            "WHERE good_id=? AND location='Transport' AND batch_label=''",
            (r["good_id"],))
        db.execute("""
            INSERT INTO transaction_items
                (transaction_id, type_of_goods, bags, quantity, collection_point)
            VALUES (?, ?, 5, 250.0, 'Transport')
        """, (tid, r["good_name"]))

    reverse_stock_for_bill_delete(db, tid)
    db.commit()

    for r in rows:
        row = db.execute(
            "SELECT bags, quantity_kg FROM stock_levels "
            "WHERE good_id=? AND location='Transport' AND batch_label=''",
            (r["good_id"],)
        ).fetchone()
        assert row["bags"] == 25
        assert abs(row["quantity_kg"] - 1250.0) < 0.01


def test_reversal_unknown_good_skipped(db):
    """Items whose good name isn't in stock_goods are silently skipped."""
    bid = _broker(db)
    tid = _txn(db, bid)

    db.execute("""
        INSERT INTO transaction_items
            (transaction_id, type_of_goods, bags, quantity, collection_point)
        VALUES (?, 'NonExistentSpice', 5, 100.0, 'Shop')
    """, (tid,))

    # Must not raise
    reverse_stock_for_bill_delete(db, tid)
    db.commit()


def test_reversal_empty_location_skipped(db):
    """Items with blank collection_point are silently skipped."""
    bid = _broker(db)
    tid = _txn(db, bid)
    gid, gname = _good(db, "Shop")

    db.execute("""
        INSERT INTO transaction_items
            (transaction_id, type_of_goods, bags, quantity, collection_point)
        VALUES (?, ?, 3, 100.0, '')
    """, (tid, gname))

    before = db.execute(
        "SELECT bags FROM stock_levels WHERE good_id=? AND location='Shop' AND batch_label=''",
        (gid,)
    ).fetchone()["bags"]

    reverse_stock_for_bill_delete(db, tid)
    db.commit()

    after = db.execute(
        "SELECT bags FROM stock_levels WHERE good_id=? AND location='Shop' AND batch_label=''",
        (gid,)
    ).fetchone()["bags"]
    assert before == after  # no change


def test_reversal_no_items_is_noop(db):
    """A bill with no transaction_items should be a no-op."""
    bid = _broker(db)
    tid = _txn(db, bid)

    # No items inserted
    reverse_stock_for_bill_delete(db, tid)
    db.commit()  # Must not raise
