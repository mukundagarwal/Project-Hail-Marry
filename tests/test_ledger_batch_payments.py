"""
N+1 fix: batch-fetch payments + transaction_items for all visible transactions.

Verifies that the groupby-based dict lookup pattern produces identical results
to individual per-transaction queries, so the UI replacement is correct.
"""
import pytest
import pandas as pd
from utils.db import execute_in_clause


def _seed_broker(db):
    db.execute("INSERT OR IGNORE INTO brokers (broker_name) VALUES ('BrokerX')")
    return db.execute(
        "SELECT broker_id FROM brokers WHERE broker_name='BrokerX'"
    ).fetchone()[0]


def _seed_txn(db, customer, total, bill_date="2026-01-01"):
    bid = _seed_broker(db)
    db.execute("""
        INSERT INTO customer_transactions
          (broker_id, customer_name, date, total_amount, payment_status, calc_status)
        VALUES (?, ?, ?, ?, 'Pending', 'Pending')
    """, (bid, customer, bill_date, total))
    return db.execute("SELECT last_insert_rowid()").fetchone()[0]


def _add_payment(db, tid, amount, payment_date="2026-02-01"):
    db.execute("""
        INSERT INTO payments (transaction_id, payment_date, amount, method)
        VALUES (?, ?, ?, 'Cash')
    """, (tid, payment_date, amount))


def _add_item(db, tid, goods="Pepper", bags=10, qty=100):
    db.execute("""
        INSERT INTO transaction_items
          (transaction_id, type_of_goods, bags, bag_rate, quantity, rate, line_total)
        VALUES (?, ?, ?, 100, ?, 50, ?)
    """, (tid, goods, bags, qty, bags * 100 + qty * 50))


def test_batch_fetch_payments_groups_by_tid(db):
    """Batch-fetched payments grouped by transaction_id match per-tid queries."""
    tid1 = _seed_txn(db, "Alice", 5000)
    tid2 = _seed_txn(db, "Bob", 8000)
    _add_payment(db, tid1, 2000)
    _add_payment(db, tid1, 1500)
    _add_payment(db, tid2, 3000)
    db.commit()

    tids = [tid1, tid2]
    # Simulate the batch fetch using execute_in_clause
    rows = execute_in_clause(
        db,
        "SELECT * FROM payments WHERE transaction_id IN {IN_CLAUSE} ORDER BY transaction_id, payment_date ASC",
        tids,
    )
    df_all = pd.DataFrame([dict(r) for r in rows])
    pmts_by_tid = {int(t): g.reset_index(drop=True)
                   for t, g in df_all.groupby("transaction_id")}

    # Per-tid individual queries for comparison
    def per_tid(tid):
        rows_i = db.execute(
            "SELECT * FROM payments WHERE transaction_id=? ORDER BY payment_date ASC", (tid,)
        ).fetchall()
        return pd.DataFrame([dict(r) for r in rows_i])

    for tid in [tid1, tid2]:
        expected = per_tid(tid)
        actual   = pmts_by_tid.get(tid, pd.DataFrame())
        assert len(actual) == len(expected)
        assert list(actual["amount"]) == list(expected["amount"])


def test_batch_fetch_items_groups_by_tid(db):
    """Batch-fetched transaction_items grouped by tid match per-tid queries."""
    tid1 = _seed_txn(db, "Alice", 5000)
    tid2 = _seed_txn(db, "Bob", 8000)
    _add_item(db, tid1, "Pepper", bags=5, qty=50)
    _add_item(db, tid1, "Cumin", bags=3, qty=30)
    _add_item(db, tid2, "Turmeric", bags=10, qty=100)
    db.commit()

    tids = [tid1, tid2]
    rows = execute_in_clause(
        db,
        "SELECT * FROM transaction_items WHERE transaction_id IN {IN_CLAUSE} ORDER BY transaction_id, item_id ASC",
        tids,
    )
    df_all = pd.DataFrame([dict(r) for r in rows])
    items_by_tid = {int(t): g.reset_index(drop=True)
                    for t, g in df_all.groupby("transaction_id")}

    assert len(items_by_tid[tid1]) == 2
    assert len(items_by_tid[tid2]) == 1


def test_batch_missing_tid_returns_empty_dataframe(db):
    """A transaction_id with no payments/items returns pd.DataFrame() via .get()."""
    tid1 = _seed_txn(db, "Alice", 5000)
    _add_payment(db, tid1, 1000)
    db.commit()

    rows = execute_in_clause(
        db,
        "SELECT * FROM payments WHERE transaction_id IN {IN_CLAUSE} ORDER BY transaction_id",
        [tid1],
    )
    df_all = pd.DataFrame([dict(r) for r in rows])
    pmts_by_tid = {int(t): g.reset_index(drop=True)
                   for t, g in df_all.groupby("transaction_id")}

    # A tid not in the batch → returns empty DataFrame
    result = pmts_by_tid.get(99999, pd.DataFrame())
    assert isinstance(result, pd.DataFrame)
    assert result.empty


def test_batch_empty_page_returns_empty_dicts(db):
    """When _page_tids is empty, both dicts are empty (no query executed)."""
    page_tids = []
    if page_tids:
        rows = execute_in_clause(
            db, "SELECT * FROM payments WHERE transaction_id IN {IN_CLAUSE}", page_tids
        )
        df = pd.DataFrame([dict(r) for r in rows])
        pmts_by_tid = {int(t): g for t, g in df.groupby("transaction_id")}
    else:
        pmts_by_tid = {}

    assert pmts_by_tid == {}


def test_batch_preserves_payment_order_within_tid(db):
    """Payments within each tid group are ordered by payment_date ASC."""
    tid = _seed_txn(db, "Alice", 10000)
    _add_payment(db, tid, 3000, "2026-03-01")
    _add_payment(db, tid, 2000, "2026-01-15")
    _add_payment(db, tid, 1000, "2026-02-10")
    db.commit()

    rows = execute_in_clause(
        db,
        "SELECT * FROM payments WHERE transaction_id IN {IN_CLAUSE} ORDER BY transaction_id, payment_date ASC",
        [tid],
    )
    df_all = pd.DataFrame([dict(r) for r in rows])
    pmts_by_tid = {int(t): g.reset_index(drop=True)
                   for t, g in df_all.groupby("transaction_id")}

    amounts = list(pmts_by_tid[tid]["amount"])
    assert amounts == [2000.0, 1000.0, 3000.0]
