"""
FIX H-01: vendor_entries must be created by the canonical _sqlite_ddl,
not by the private _ensure_vendor_schema_once in 2_Vendor_Payments.py.
Verifies:
- vendor_entries table exists with note column after ensure_schema
- All 4 locations are seeded for a new stock category
- STOCK_LOCATIONS constant matches the 4 expected locations
"""
import pytest
from utils.db import STOCK_LOCATIONS, ensure_category_at_all_locations


EXPECTED_LOCATIONS = ("Transport", "Shop", "Anandpuri", "Cold")


def test_stock_locations_constant_has_all_four():
    assert set(STOCK_LOCATIONS) == set(EXPECTED_LOCATIONS)
    assert len(STOCK_LOCATIONS) == 4


def test_vendor_entries_has_note_column(db):
    """vendor_entries table must have a note column after ensure_schema."""
    db.execute(
        "INSERT INTO vendors (vendor_name) VALUES ('TestVendor')"
    )
    vendor_id = db.execute(
        "SELECT vendor_id FROM vendors WHERE vendor_name='TestVendor'"
    ).fetchone()["vendor_id"]
    # This INSERT will fail if note column doesn't exist
    db.execute(
        """INSERT INTO vendor_entries
           (vendor_id, entry_date, ledger_type, firm, entry_kind,
            particulars, amount, bags, quantity_kg, note)
           VALUES (?, ?, 'RTGS', 'SP Spices', 'Bill', 'Test', -100.0, 0, 0, 'test note')""",
        (vendor_id, "2025-01-01"),
    )
    db.commit()
    row = db.execute(
        "SELECT note FROM vendor_entries WHERE vendor_id=?", (vendor_id,)
    ).fetchone()
    assert row["note"] == "test note"


def test_ensure_category_at_all_locations_seeds_all_four(db):
    """Adding a new category must create unidentified_stock rows for all 4 locations."""
    db.execute(
        "INSERT INTO stock_categories (category_name) VALUES ('TestCat')"
    )
    cat_id = db.execute(
        "SELECT category_id FROM stock_categories WHERE category_name='TestCat'"
    ).fetchone()["category_id"]

    ensure_category_at_all_locations(db, cat_id)
    db.commit()

    rows = db.execute(
        "SELECT location FROM unidentified_stock WHERE category_id=?", (cat_id,)
    ).fetchall()
    seeded_locations = {r["location"] for r in rows}
    assert seeded_locations == set(EXPECTED_LOCATIONS)
