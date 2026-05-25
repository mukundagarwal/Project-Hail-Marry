"""
Tests for AUDIT-022: purge_old_stock_history uses Python-computed cutoff
and works correctly on both SQLite (test env) and PostgreSQL (production).
"""
import pytest
from datetime import datetime, timedelta
from utils.db import purge_old_stock_history


def _insert_history(db, recorded_at: str, good_name="TestGood"):
    db.execute("""
        INSERT INTO stock_history
            (recorded_at, category_name, good_name, location,
             change_type, bags_before, bags_after, bags_change,
             kg_before, kg_after, kg_change, source)
        VALUES (?,?,'TestGood','Transport','Test',0,1,1,0,10,10,'test')
    """, (recorded_at, "TestCat"))
    return db.execute("SELECT last_insert_rowid()").fetchone()[0]


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def test_old_entry_purged(db):
    """Entries older than 30 days must be deleted."""
    old_ts = _iso(datetime.now() - timedelta(days=31))
    eid = _insert_history(db, old_ts)
    db.commit()

    purge_old_stock_history(db)
    db.commit()

    row = db.execute(
        "SELECT id FROM stock_history WHERE id=?", (eid,)).fetchone()
    assert row is None, "31-day-old entry must be purged"


def test_recent_entry_kept(db):
    """Entries within 30 days must be retained."""
    recent_ts = _iso(datetime.now() - timedelta(days=5))
    eid = _insert_history(db, recent_ts)
    db.commit()

    purge_old_stock_history(db)
    db.commit()

    row = db.execute(
        "SELECT id FROM stock_history WHERE id=?", (eid,)).fetchone()
    assert row is not None, "5-day-old entry must be kept"


def test_boundary_29_days_kept(db):
    """An entry exactly 29 days old must be retained."""
    ts = _iso(datetime.now() - timedelta(days=29))
    eid = _insert_history(db, ts)
    db.commit()

    purge_old_stock_history(db)
    db.commit()

    row = db.execute(
        "SELECT id FROM stock_history WHERE id=?", (eid,)).fetchone()
    assert row is not None, "29-day-old entry must not be purged"


def test_selective_purge(db):
    """Old entries are removed while recent ones survive in the same table."""
    old_ts    = _iso(datetime.now() - timedelta(days=35))
    recent_ts = _iso(datetime.now() - timedelta(days=3))
    old_id    = _insert_history(db, old_ts)
    new_id    = _insert_history(db, recent_ts)
    db.commit()

    purge_old_stock_history(db)
    db.commit()

    assert db.execute(
        "SELECT id FROM stock_history WHERE id=?", (old_id,)).fetchone() is None
    assert db.execute(
        "SELECT id FROM stock_history WHERE id=?", (new_id,)).fetchone() is not None


def test_empty_table_does_not_raise(db):
    """Purge on an empty stock_history table must not raise."""
    db.execute("DELETE FROM stock_history")
    db.commit()
    purge_old_stock_history(db)  # must not raise
    db.commit()

    count = db.execute(
        "SELECT COUNT(*) AS n FROM stock_history").fetchone()["n"]
    assert count == 0
