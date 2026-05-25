"""
Tests for AUDIT-002: same-location stock transfers must be rejected.
"""
import pytest
import sqlite3


def test_same_location_blocked_by_check_constraint(db):
    """stock_transfers CHECK constraint rejects transfers where from == to."""
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("""
            INSERT INTO stock_transfers
                (transfer_date, from_location, to_location, bags_moved, kg_moved)
            VALUES ('2024-01-01', 'Shop', 'Shop', 1, 50.0)
        """)


def test_different_locations_allowed(db):
    """Transfers between different locations must succeed."""
    db.execute("""
        INSERT INTO stock_transfers
            (transfer_date, from_location, to_location, bags_moved, kg_moved)
        VALUES ('2024-01-01', 'Shop', 'Transport', 1, 50.0)
    """)
    db.commit()
    row = db.execute(
        "SELECT transfer_id FROM stock_transfers "
        "WHERE from_location='Shop' AND to_location='Transport'"
    ).fetchone()
    assert row is not None


def test_null_from_location_allowed(db):
    """NULL from_location (e.g. initial stock entry) passes the constraint."""
    db.execute("""
        INSERT INTO stock_transfers
            (transfer_date, from_location, to_location, bags_moved, kg_moved)
        VALUES ('2024-01-01', NULL, 'Shop', 5, 200.0)
    """)
    db.commit()


def test_sold_destination_allowed(db):
    """'Sold' destination used by deduct_stock_for_sale must not be blocked."""
    db.execute("""
        INSERT INTO stock_transfers
            (transfer_date, from_location, to_location, bags_moved, kg_moved)
        VALUES ('2024-01-01', 'Shop', 'Sold', 2, 80.0)
    """)
    db.commit()
    row = db.execute(
        "SELECT transfer_id FROM stock_transfers WHERE to_location='Sold'"
    ).fetchone()
    assert row is not None


def test_all_four_valid_locations_can_transfer_between_each_other(db):
    """Transfers among Transport, Shop, Anandpuri, Cold are all valid."""
    valid_locs = ["Transport", "Shop", "Anandpuri", "Cold"]
    i = 0
    for src in valid_locs:
        for dst in valid_locs:
            if src != dst:
                db.execute("""
                    INSERT INTO stock_transfers
                        (transfer_date, from_location, to_location, bags_moved, kg_moved)
                    VALUES (?, ?, ?, 1, 10.0)
                """, (f"2024-01-{i+1:02d}", src, dst))
                i += 1
    db.commit()
