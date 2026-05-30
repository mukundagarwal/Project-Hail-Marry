"""
Comprehensive tests for all stock-register update paths:
  - deduct_stock_for_sale
  - add_unidentified_stock / reverse_unidentified_stock
  - stock transfer logic (UPDATE source + UPSERT destination + stock_transfers record)
"""
import pytest
from utils.db import (
    deduct_stock_for_sale,
    add_unidentified_stock,
    reverse_unidentified_stock,
)


# ── shared helpers ────────────────────────────────────────────────

def _get_level(db, good_id, location):
    row = db.execute(
        "SELECT bags, quantity_kg FROM stock_levels "
        "WHERE good_id=? AND location=? AND batch_label=''",
        (good_id, location)
    ).fetchone()
    assert row is not None, f"No stock_levels row for good_id={good_id} at {location}"
    return float(row["bags"]), float(row["quantity_kg"])


def _set_level(db, good_id, location, bags, kg):
    db.execute(
        "UPDATE stock_levels SET bags=?, quantity_kg=? "
        "WHERE good_id=? AND location=? AND batch_label=''",
        (bags, kg, good_id, location)
    )


def _first_good(db):
    row = db.execute("SELECT good_id, good_name FROM stock_goods LIMIT 1").fetchone()
    return row["good_id"], row["good_name"]


def _cat_id(db, name="Arecanut"):
    row = db.execute(
        "SELECT category_id FROM stock_categories WHERE category_name=?", (name,)
    ).fetchone()
    assert row is not None, f"Category '{name}' not seeded"
    return row["category_id"]


def _do_transfer(db, gid, from_loc, to_loc, batch_label, bags_mv, kg_mv,
                 transfer_date, note=""):
    """
    SQLite-compatible mirror of the stock-transfer save block in
    3_Stock_Register.py (which uses PostgreSQL %s syntax in production).
    """
    db.execute(
        "UPDATE stock_levels SET bags=bags-?, quantity_kg=quantity_kg-? "
        "WHERE good_id=? AND location=? AND batch_label=?",
        (bags_mv, kg_mv, gid, from_loc, batch_label)
    )
    db.execute(
        "INSERT INTO stock_levels (good_id, location, batch_label, bags, quantity_kg) "
        "VALUES (?, ?, ?, ?, ?) "
        "ON CONFLICT (good_id, location, batch_label) DO UPDATE "
        "SET bags        = stock_levels.bags        + excluded.bags,"
        "    quantity_kg = stock_levels.quantity_kg + excluded.quantity_kg",
        (gid, to_loc, batch_label, bags_mv, kg_mv)
    )
    db.execute(
        "INSERT INTO stock_transfers "
        "(transfer_date, good_id, from_location, to_location, bags_moved, kg_moved, note) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (str(transfer_date), gid, from_loc, to_loc, bags_mv, kg_mv, note)
    )


# ── deduct_stock_for_sale ─────────────────────────────────────────

class TestDeductStockForSale:
    def test_happy_path_deducts_bags_and_kg(self, db):
        gid, gname = _first_good(db)
        _set_level(db, gid, "Shop", bags=10, kg=500.0)

        items = [{"goods": gname, "collection_point": "Shop", "bags": 3, "qty": 150.0}]
        warns = deduct_stock_for_sale(db, items, "2024-01-01", "Test Customer")
        db.commit()

        assert warns == []
        bags, kg = _get_level(db, gid, "Shop")
        assert bags == 7.0
        assert abs(kg - 350.0) < 0.01

    def test_inserts_stock_transfer_row_with_sold_destination(self, db):
        gid, gname = _first_good(db)
        _set_level(db, gid, "Transport", bags=5, kg=200.0)

        items = [{"goods": gname, "collection_point": "Transport", "bags": 2, "qty": 80.0}]
        deduct_stock_for_sale(db, items, "2024-06-15", "Ram")
        db.commit()

        row = db.execute(
            "SELECT * FROM stock_transfers WHERE good_id=? AND to_location='Sold'", (gid,)
        ).fetchone()
        assert row is not None
        assert row["from_location"] == "Transport"
        assert row["bags_moved"] == 2
        assert abs(row["kg_moved"] - 80.0) < 0.01
        assert "Ram" in row["note"]

    def test_unknown_good_returns_warning_not_raise(self, db):
        items = [{"goods": "GhostSpice9999", "collection_point": "Shop", "bags": 1, "qty": 50.0}]
        warns = deduct_stock_for_sale(db, items, "2024-01-01", "X")
        assert len(warns) == 1
        assert "GhostSpice9999" in warns[0]

    def test_no_batch_empty_row_returns_warning(self, db):
        gid, gname = _first_good(db)
        db.execute(
            "DELETE FROM stock_levels WHERE good_id=? AND location='Shop' AND batch_label=''",
            (gid,)
        )
        db.commit()

        items = [{"goods": gname, "collection_point": "Shop", "bags": 1, "qty": 50.0}]
        warns = deduct_stock_for_sale(db, items, "2024-01-01", "Y")
        assert len(warns) == 1
        assert "stock NOT updated" in warns[0] or "No stock record" in warns[0]

    def test_invalid_location_returns_warning(self, db):
        _, gname = _first_good(db)
        items = [{"goods": gname, "collection_point": "Warehouse", "bags": 1, "qty": 50.0}]
        warns = deduct_stock_for_sale(db, items, "2024-01-01", "Z")
        assert len(warns) == 1
        assert "Warehouse" in warns[0]

    def test_empty_collection_point_raises_value_error(self, db):
        _, gname = _first_good(db)
        items = [{"goods": gname, "collection_point": "", "bags": 1, "qty": 50.0}]
        with pytest.raises(ValueError, match="collection_point is empty"):
            deduct_stock_for_sale(db, items, "2024-01-01", "Z")

    def test_negative_stock_after_deduction_adds_warning(self, db):
        gid, gname = _first_good(db)
        _set_level(db, gid, "Shop", bags=1, kg=50.0)

        items = [{"goods": gname, "collection_point": "Shop", "bags": 5, "qty": 200.0}]
        warns = deduct_stock_for_sale(db, items, "2024-01-01", "W")
        db.commit()

        assert any("below zero" in w for w in warns)

    def test_multi_item_bill_deducts_each_independently(self, db):
        rows = db.execute("SELECT good_id, good_name FROM stock_goods LIMIT 2").fetchall()
        assert len(rows) >= 2, "Need at least 2 seeded goods"
        for r in rows:
            _set_level(db, r["good_id"], "Anandpuri", bags=10, kg=300.0)

        items = [
            {"goods": rows[0]["good_name"], "collection_point": "Anandpuri",
             "bags": 3, "qty": 90.0},
            {"goods": rows[1]["good_name"], "collection_point": "Anandpuri",
             "bags": 4, "qty": 120.0},
        ]
        warns = deduct_stock_for_sale(db, items, "2024-02-01", "MultiCust")
        db.commit()

        assert warns == []
        bags0, kg0 = _get_level(db, rows[0]["good_id"], "Anandpuri")
        bags1, kg1 = _get_level(db, rows[1]["good_id"], "Anandpuri")
        assert bags0 == 7.0 and abs(kg0 - 210.0) < 0.01
        assert bags1 == 6.0 and abs(kg1 - 180.0) < 0.01

    def test_logs_stock_history_row(self, db):
        gid, gname = _first_good(db)
        _set_level(db, gid, "Shop", bags=10, kg=400.0)

        items = [{"goods": gname, "collection_point": "Shop", "bags": 2, "qty": 80.0}]
        deduct_stock_for_sale(db, items, "2024-01-01", "HistTest")
        db.commit()

        hist = db.execute(
            "SELECT * FROM stock_history "
            "WHERE good_name=? AND location='Shop' AND change_type='Customer Sale'",
            (gname,)
        ).fetchone()
        assert hist is not None
        assert hist["bags_before"] == 10.0
        assert hist["bags_after"] == 8.0

    def test_unknown_good_does_not_touch_other_items(self, db):
        """A bad item produces a warning but valid subsequent items still deduct."""
        rows = db.execute("SELECT good_id, good_name FROM stock_goods LIMIT 2").fetchall()
        assert len(rows) >= 2
        gid, gname = rows[0]["good_id"], rows[0]["good_name"]
        _set_level(db, gid, "Shop", bags=8, kg=320.0)

        items = [
            {"goods": "Ghost999", "collection_point": "Shop", "bags": 1, "qty": 50.0},
            {"goods": gname,      "collection_point": "Shop", "bags": 3, "qty": 120.0},
        ]
        warns = deduct_stock_for_sale(db, items, "2024-03-01", "Mixed")
        db.commit()

        assert len(warns) == 1  # only the ghost spice
        bags, kg = _get_level(db, gid, "Shop")
        assert bags == 5.0
        assert abs(kg - 200.0) < 0.01

    def test_all_four_locations_accepted(self, db):
        """Verify all valid locations process without warning."""
        gid, gname = _first_good(db)
        for loc in ("Transport", "Shop", "Anandpuri", "Cold"):
            _set_level(db, gid, loc, bags=10, kg=200.0)

        for loc in ("Transport", "Shop", "Anandpuri", "Cold"):
            items = [{"goods": gname, "collection_point": loc, "bags": 1, "qty": 10.0}]
            warns = deduct_stock_for_sale(db, items, "2024-04-01", "LocTest")
            assert warns == [], f"Unexpected warning for location '{loc}': {warns}"
        db.commit()


# ── add_unidentified_stock ────────────────────────────────────────

class TestAddUnidentifiedStock:
    def test_inserts_new_row(self, db):
        cid = _cat_id(db, "Arecanut")
        add_unidentified_stock(db, cid, "Shop", bags=5.0, quantity_kg=200.0)
        db.commit()

        row = db.execute(
            "SELECT bags, quantity_kg FROM unidentified_stock "
            "WHERE category_id=? AND location='Shop'", (cid,)
        ).fetchone()
        assert row is not None
        assert row["bags"] == 5.0
        assert abs(row["quantity_kg"] - 200.0) < 0.01

    def test_upserts_accumulates_into_existing_row(self, db):
        cid = _cat_id(db, "Arecanut")
        add_unidentified_stock(db, cid, "Shop", bags=5.0, quantity_kg=200.0)
        add_unidentified_stock(db, cid, "Shop", bags=3.0, quantity_kg=100.0)
        db.commit()

        row = db.execute(
            "SELECT bags, quantity_kg FROM unidentified_stock "
            "WHERE category_id=? AND location='Shop'", (cid,)
        ).fetchone()
        assert row["bags"] == 8.0
        assert abs(row["quantity_kg"] - 300.0) < 0.01

    def test_different_locations_are_independent(self, db):
        cid = _cat_id(db, "Arecanut")
        add_unidentified_stock(db, cid, "Shop",      bags=5.0, quantity_kg=200.0)
        add_unidentified_stock(db, cid, "Transport", bags=2.0, quantity_kg=80.0)
        db.commit()

        shop_row = db.execute(
            "SELECT bags FROM unidentified_stock WHERE category_id=? AND location='Shop'", (cid,)
        ).fetchone()
        trans_row = db.execute(
            "SELECT bags FROM unidentified_stock WHERE category_id=? AND location='Transport'",
            (cid,)
        ).fetchone()
        assert shop_row["bags"] == 5.0
        assert trans_row["bags"] == 2.0

    def test_different_categories_are_independent(self, db):
        cid_a = _cat_id(db, "Arecanut")
        cid_b = _cat_id(db, "Black Pepper")
        add_unidentified_stock(db, cid_a, "Shop", bags=4.0, quantity_kg=160.0)
        add_unidentified_stock(db, cid_b, "Shop", bags=2.0, quantity_kg=80.0)
        db.commit()

        row_a = db.execute(
            "SELECT bags FROM unidentified_stock WHERE category_id=? AND location='Shop'", (cid_a,)
        ).fetchone()
        row_b = db.execute(
            "SELECT bags FROM unidentified_stock WHERE category_id=? AND location='Shop'", (cid_b,)
        ).fetchone()
        assert row_a["bags"] == 4.0
        assert row_b["bags"] == 2.0


# ── reverse_unidentified_stock ────────────────────────────────────

class TestReverseUnidentifiedStock:
    def test_subtracts_bags_and_kg(self, db):
        cid = _cat_id(db, "Arecanut")
        add_unidentified_stock(db, cid, "Shop", bags=10.0, quantity_kg=400.0)
        db.commit()

        reverse_unidentified_stock(db, cid, "Shop", bags=3.0, quantity_kg=120.0)
        db.commit()

        row = db.execute(
            "SELECT bags, quantity_kg FROM unidentified_stock "
            "WHERE category_id=? AND location='Shop'", (cid,)
        ).fetchone()
        assert row["bags"] == 7.0
        assert abs(row["quantity_kg"] - 280.0) < 0.01

    def test_can_reduce_to_zero(self, db):
        cid = _cat_id(db, "Arecanut")
        add_unidentified_stock(db, cid, "Cold", bags=5.0, quantity_kg=250.0)
        db.commit()

        reverse_unidentified_stock(db, cid, "Cold", bags=5.0, quantity_kg=250.0)
        db.commit()

        row = db.execute(
            "SELECT bags, quantity_kg FROM unidentified_stock "
            "WHERE category_id=? AND location='Cold'", (cid,)
        ).fetchone()
        assert abs(row["bags"]) < 0.01
        assert abs(row["quantity_kg"]) < 0.01

    def test_only_affects_target_location(self, db):
        cid = _cat_id(db, "Arecanut")
        add_unidentified_stock(db, cid, "Shop",      bags=10.0, quantity_kg=400.0)
        add_unidentified_stock(db, cid, "Transport", bags=8.0,  quantity_kg=320.0)
        db.commit()

        reverse_unidentified_stock(db, cid, "Shop", bags=4.0, quantity_kg=160.0)
        db.commit()

        shop_row = db.execute(
            "SELECT bags FROM unidentified_stock WHERE category_id=? AND location='Shop'", (cid,)
        ).fetchone()
        trans_row = db.execute(
            "SELECT bags FROM unidentified_stock WHERE category_id=? AND location='Transport'",
            (cid,)
        ).fetchone()
        assert shop_row["bags"] == 6.0
        assert trans_row["bags"] == 8.0  # unchanged


# ── stock transfer logic ──────────────────────────────────────────
# The transfer UI lives in 3_Stock_Register.py with PostgreSQL %s syntax.
# _do_transfer() (defined above) is the SQLite-compatible equivalent,
# testing the same DB-layer contract.

class TestStockTransfer:
    def test_source_decreases_by_moved_amount(self, db):
        gid, _ = _first_good(db)
        _set_level(db, gid, "Transport", bags=20, kg=800.0)

        _do_transfer(db, gid, "Transport", "Shop", "", 5, 200.0, "2024-01-01")
        db.commit()

        bags, kg = _get_level(db, gid, "Transport")
        assert bags == 15.0
        assert abs(kg - 600.0) < 0.01

    def test_destination_increases_by_moved_amount(self, db):
        gid, _ = _first_good(db)
        _set_level(db, gid, "Transport", bags=20, kg=800.0)
        _set_level(db, gid, "Shop",      bags=5,  kg=200.0)

        _do_transfer(db, gid, "Transport", "Shop", "", 5, 200.0, "2024-01-01")
        db.commit()

        bags, kg = _get_level(db, gid, "Shop")
        assert bags == 10.0
        assert abs(kg - 400.0) < 0.01

    def test_upsert_creates_destination_row_when_missing(self, db):
        """Transfer to a location whose batch_label='' row was deleted creates it."""
        gid, _ = _first_good(db)
        db.execute(
            "DELETE FROM stock_levels WHERE good_id=? AND location='Cold' AND batch_label=''",
            (gid,)
        )
        _set_level(db, gid, "Transport", bags=10, kg=400.0)

        _do_transfer(db, gid, "Transport", "Cold", "", 4, 160.0, "2024-01-01")
        db.commit()

        row = db.execute(
            "SELECT bags, quantity_kg FROM stock_levels "
            "WHERE good_id=? AND location='Cold' AND batch_label=''", (gid,)
        ).fetchone()
        assert row is not None
        assert row["bags"] == 4.0
        assert abs(row["quantity_kg"] - 160.0) < 0.01

    def test_stock_transfers_record_inserted(self, db):
        gid, _ = _first_good(db)
        _set_level(db, gid, "Transport", bags=10, kg=400.0)

        _do_transfer(db, gid, "Transport", "Anandpuri", "", 3, 120.0, "2024-03-15", "unit test")
        db.commit()

        row = db.execute(
            "SELECT * FROM stock_transfers WHERE good_id=? AND from_location='Transport'", (gid,)
        ).fetchone()
        assert row is not None
        assert row["to_location"] == "Anandpuri"
        assert row["bags_moved"] == 3
        assert abs(row["kg_moved"] - 120.0) < 0.01
        assert row["note"] == "unit test"

    def test_other_locations_unaffected_by_transfer(self, db):
        gid, _ = _first_good(db)
        _set_level(db, gid, "Transport", bags=20, kg=800.0)
        _set_level(db, gid, "Shop",      bags=5,  kg=200.0)
        _set_level(db, gid, "Anandpuri", bags=8,  kg=320.0)
        _set_level(db, gid, "Cold",      bags=2,  kg=80.0)

        _do_transfer(db, gid, "Transport", "Shop", "", 5, 200.0, "2024-01-01")
        db.commit()

        anand_bags, _ = _get_level(db, gid, "Anandpuri")
        cold_bags, _  = _get_level(db, gid, "Cold")
        assert anand_bags == 8.0
        assert cold_bags == 2.0

    def test_consecutive_transfers_accumulate_correctly(self, db):
        """Two transfers into the same destination stack additively."""
        gid, _ = _first_good(db)
        _set_level(db, gid, "Transport", bags=20, kg=800.0)
        _set_level(db, gid, "Shop",      bags=5,  kg=200.0)

        _do_transfer(db, gid, "Transport", "Shop", "", 3, 120.0, "2024-01-01")
        _do_transfer(db, gid, "Transport", "Shop", "", 2, 80.0,  "2024-01-02")
        db.commit()

        src_bags, src_kg = _get_level(db, gid, "Transport")
        dst_bags, dst_kg = _get_level(db, gid, "Shop")
        assert src_bags == 15.0 and abs(src_kg - 600.0) < 0.01
        assert dst_bags == 10.0 and abs(dst_kg - 400.0) < 0.01

    def test_batch_label_row_transferred_independently(self, db):
        """A non-empty batch_label transfer does not touch the '' batch row."""
        gid, _ = _first_good(db)
        _set_level(db, gid, "Transport", bags=20, kg=800.0)  # batch_label=''

        # Seed a named-batch row at Transport
        db.execute(
            "INSERT INTO stock_levels (good_id, location, batch_label, bags, quantity_kg) "
            "VALUES (?, 'Transport', 'B1', 10, 400.0) ON CONFLICT DO NOTHING",
            (gid,)
        )
        db.commit()

        _do_transfer(db, gid, "Transport", "Shop", "B1", 4, 160.0, "2024-01-01")
        db.commit()

        # Named batch decreased
        b1_row = db.execute(
            "SELECT bags FROM stock_levels WHERE good_id=? AND location='Transport' AND batch_label='B1'",
            (gid,)
        ).fetchone()
        assert b1_row["bags"] == 6.0

        # Default batch (batch_label='') untouched
        def_bags, _ = _get_level(db, gid, "Transport")
        assert def_bags == 20.0
