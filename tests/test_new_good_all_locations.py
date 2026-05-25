"""
Tests for AUDIT-019: new goods must have stock_levels rows at ALL 4 locations.
Covers ensure_good_at_all_locations and ensure_category_at_all_locations helpers.
"""
import pytest
from utils.db import (
    ensure_good_at_all_locations, ensure_category_at_all_locations,
    _ALL_STOCK_LOCATIONS,
)


def _add_category(db, name="TestCat"):
    db.execute(
        "INSERT OR IGNORE INTO stock_categories (category_name) VALUES (?)", (name,))
    return db.execute(
        "SELECT category_id FROM stock_categories WHERE category_name=?",
        (name,)).fetchone()["category_id"]


def _add_good(db, cat_id, name="TestGood"):
    db.execute(
        "INSERT INTO stock_goods (category_id, good_name) VALUES (?,?)", (cat_id, name))
    return db.execute(
        "SELECT good_id FROM stock_goods WHERE good_name=? AND category_id=?",
        (name, cat_id)).fetchone()["good_id"]


# ── ensure_good_at_all_locations ───────────────────────────────

def test_seeds_all_four_locations(db):
    """After calling the helper, stock_levels must have a row for every location."""
    cid = _add_category(db)
    gid = _add_good(db, cid)
    ensure_good_at_all_locations(db, gid)
    db.commit()

    rows = db.execute(
        "SELECT location FROM stock_levels WHERE good_id=?", (gid,)).fetchall()
    locations_found = {r["location"] for r in rows}
    assert locations_found == set(_ALL_STOCK_LOCATIONS)


def test_cold_location_present(db):
    """Cold must be one of the seeded locations (commonly missing before this fix)."""
    cid = _add_category(db)
    gid = _add_good(db, cid)
    ensure_good_at_all_locations(db, gid)
    db.commit()

    cold_row = db.execute(
        "SELECT bags FROM stock_levels WHERE good_id=? AND location='Cold'",
        (gid,)).fetchone()
    assert cold_row is not None
    assert cold_row["bags"] == 0


def test_idempotent_calling_twice_is_safe(db):
    """Calling the helper twice must not raise or duplicate rows."""
    cid = _add_category(db)
    gid = _add_good(db, cid)
    ensure_good_at_all_locations(db, gid)
    db.commit()
    ensure_good_at_all_locations(db, gid)
    db.commit()

    count = db.execute(
        "SELECT COUNT(*) AS n FROM stock_levels "
        "WHERE good_id=? AND batch_label=''", (gid,)).fetchone()["n"]
    assert count == len(_ALL_STOCK_LOCATIONS)


def test_existing_stock_not_overwritten(db):
    """ensure_good_at_all_locations must not zero out pre-existing stock."""
    cid = _add_category(db)
    gid = _add_good(db, cid)
    db.execute(
        "INSERT INTO stock_levels (good_id, location, batch_label, bags, quantity_kg) "
        "VALUES (?,?,?,?,?)", (gid, "Transport", "", 50, 200.0))
    db.commit()

    ensure_good_at_all_locations(db, gid)
    db.commit()

    transport = db.execute(
        "SELECT bags FROM stock_levels WHERE good_id=? AND location='Transport' AND batch_label=''",
        (gid,)).fetchone()
    assert transport["bags"] == 50, "Pre-existing Transport stock must not be zeroed"


# ── ensure_category_at_all_locations ──────────────────────────

def test_category_seeds_all_four_locations(db):
    """After calling the helper, unidentified_stock has rows for all 4 locations."""
    cid = _add_category(db, "NewCat")
    # Remove any rows seeded by ensure_schema for this category
    db.execute("DELETE FROM unidentified_stock WHERE category_id=?", (cid,))
    db.commit()

    ensure_category_at_all_locations(db, cid)
    db.commit()

    rows = db.execute(
        "SELECT location FROM unidentified_stock WHERE category_id=?",
        (cid,)).fetchall()
    locations_found = {r["location"] for r in rows}
    assert locations_found == set(_ALL_STOCK_LOCATIONS)


def test_category_helper_idempotent(db):
    """Calling ensure_category_at_all_locations twice must not duplicate rows."""
    cid = _add_category(db, "NewCat2")
    db.execute("DELETE FROM unidentified_stock WHERE category_id=?", (cid,))
    db.commit()

    ensure_category_at_all_locations(db, cid)
    db.commit()
    ensure_category_at_all_locations(db, cid)
    db.commit()

    count = db.execute(
        "SELECT COUNT(*) AS n FROM unidentified_stock WHERE category_id=?",
        (cid,)).fetchone()["n"]
    assert count == len(_ALL_STOCK_LOCATIONS)
