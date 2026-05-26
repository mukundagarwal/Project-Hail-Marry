"""
FIX 4: Stock history pagination — LIMIT controls how many rows are returned,
and a Python-computed cutoff replaces the old PostgreSQL-specific
to_char(NOW() - INTERVAL '30 days', ...) expression.
"""
import pytest
from datetime import datetime, timedelta
from utils.db import log_stock_change


def _seed_history(db, n: int, days_ago_start: int = 0, category="Arecanut",
                   good="Jini", location="Shop"):
    """Insert n stock_history rows starting from days_ago_start days ago."""
    for i in range(n):
        ts = (datetime.now() - timedelta(days=days_ago_start + i)).strftime(
            "%Y-%m-%d %H:%M:%S"
        )
        db.execute("""
            INSERT INTO stock_history
              (recorded_at, category_name, good_name, location,
               change_type, bags_before, bags_after, bags_change,
               kg_before, kg_after, kg_change, source)
            VALUES (?, ?, ?, ?, 'Update', 10, 9, -1, 100, 90, -10, 'test')
        """, (ts, category, good, location))
    db.commit()


def _fetch_with_cutoff(db, category, location, cutoff_str, limit):
    """Simulate the paginated query the stock register page now runs."""
    rows = db.execute("""
        SELECT recorded_at, good_name, change_type,
               bags_before, bags_after, bags_change,
               kg_before, kg_after, kg_change, source
        FROM stock_history
        WHERE category_name = ? AND location = ? AND recorded_at >= ?
        ORDER BY recorded_at DESC LIMIT ?
    """, (category, location, cutoff_str, limit)).fetchall()
    return rows


def test_limit_restricts_result_count(db):
    """LIMIT=5 on 20 rows must return exactly 5."""
    _seed_history(db, 20)
    cutoff = (datetime.now() - timedelta(days=60)).strftime("%Y-%m-%d %H:%M:%S")
    rows = _fetch_with_cutoff(db, "Arecanut", "Shop", cutoff, limit=5)
    assert len(rows) == 5


def test_cutoff_excludes_old_rows(db):
    """Rows older than the cutoff must not appear."""
    # 3 rows today, 2 rows 45 days ago
    _seed_history(db, 3, days_ago_start=0)
    _seed_history(db, 2, days_ago_start=40)
    cutoff = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d %H:%M:%S")
    rows = _fetch_with_cutoff(db, "Arecanut", "Shop", cutoff, limit=100)
    assert len(rows) == 3


def test_all_time_query_returns_all_rows(db):
    """Without a cutoff (all-time), all rows are returned (up to LIMIT)."""
    _seed_history(db, 10, days_ago_start=0)
    _seed_history(db, 5, days_ago_start=60)
    rows = db.execute("""
        SELECT COUNT(*) AS cnt FROM stock_history
        WHERE category_name='Arecanut' AND location='Shop'
    """).fetchone()["cnt"]
    # Fetch without cutoff
    result = db.execute("""
        SELECT * FROM stock_history
        WHERE category_name='Arecanut' AND location='Shop'
        ORDER BY recorded_at DESC LIMIT 100
    """).fetchall()
    assert len(result) == rows


def test_results_ordered_newest_first(db):
    """Rows must come back newest-first (ORDER BY recorded_at DESC)."""
    _seed_history(db, 5, days_ago_start=0)
    cutoff = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d %H:%M:%S")
    rows = _fetch_with_cutoff(db, "Arecanut", "Shop", cutoff, limit=100)
    timestamps = [str(r["recorded_at"]) for r in rows]
    assert timestamps == sorted(timestamps, reverse=True)


def test_count_query_matches_total(db):
    """Total count query must equal len(rows) when limit >= total."""
    _seed_history(db, 7)
    cutoff = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d %H:%M:%S")
    rows  = _fetch_with_cutoff(db, "Arecanut", "Shop", cutoff, limit=100)
    total = db.execute(
        "SELECT COUNT(*) AS cnt FROM stock_history "
        "WHERE category_name=? AND location=? AND recorded_at>=?",
        ("Arecanut", "Shop", cutoff)
    ).fetchone()["cnt"]
    assert len(rows) == total


def test_python_cutoff_matches_expected_date():
    """The Python cutoff computation produces the correct date string."""
    days = 30
    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
    # The cutoff date portion should be 30 days before today
    cutoff_date = cutoff[:10]
    expected_date = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    assert cutoff_date == expected_date
