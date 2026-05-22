"""
Tests for get_merged_goods() in utils.db.
"""
import pytest
from utils.db import get_merged_goods, ARECA_NUT_GOODS, BLACK_PEPPER_GOODS


def test_hardcoded_goods_always_present(db):
    merged = get_merged_goods(db, ARECA_NUT_GOODS, BLACK_PEPPER_GOODS)
    for g in ARECA_NUT_GOODS:
        assert g in merged["Arecanut"], f"{g} missing from Arecanut"
    for g in BLACK_PEPPER_GOODS:
        assert g in merged["Black Pepper"], f"{g} missing from Black Pepper"


def test_hardcoded_goods_come_first(db):
    merged = get_merged_goods(db, ARECA_NUT_GOODS, BLACK_PEPPER_GOODS)
    # Insert a new DB good that is not in hardcoded list
    cat_id = db.execute(
        "SELECT category_id FROM stock_categories WHERE category_name='Arecanut'"
    ).fetchone()[0]
    db.execute(
        "INSERT INTO stock_goods (category_id, good_name) VALUES (%s,%s) ON CONFLICT DO NOTHING",
        (cat_id, "Z New Good"),
    )
    db.commit()

    merged2 = get_merged_goods(db, ARECA_NUT_GOODS, BLACK_PEPPER_GOODS)
    assert merged2["Arecanut"][0] == ARECA_NUT_GOODS[0], "First hardcoded good must be first"
    z_idx    = merged2["Arecanut"].index("Z New Good")
    last_hc  = merged2["Arecanut"].index(ARECA_NUT_GOODS[-1])
    assert z_idx > last_hc, "DB-only good must come after all hardcoded goods"


def test_db_good_not_in_hardcoded_appended(db):
    cat_id = db.execute(
        "SELECT category_id FROM stock_categories WHERE category_name='Arecanut'"
    ).fetchone()[0]
    db.execute(
        "INSERT INTO stock_goods (category_id, good_name) VALUES (%s,%s) ON CONFLICT DO NOTHING",
        (cat_id, "Mng. Vichras"),
    )
    db.commit()

    merged = get_merged_goods(db, ARECA_NUT_GOODS, BLACK_PEPPER_GOODS)
    assert "Mng. Vichras" in merged["Arecanut"]
    assert "Mng. Vichras" not in ARECA_NUT_GOODS


def test_extra_category_added(db):
    db.execute(
        "INSERT INTO stock_categories (category_name) VALUES ('Cashew') ON CONFLICT DO NOTHING"
    )
    cashew_id = db.execute(
        "SELECT category_id FROM stock_categories WHERE category_name='Cashew'"
    ).fetchone()[0]
    db.execute(
        "INSERT INTO stock_goods (category_id, good_name) VALUES (%s,%s) ON CONFLICT DO NOTHING",
        (cashew_id, "W180"),
    )
    db.commit()

    merged = get_merged_goods(db, ARECA_NUT_GOODS, BLACK_PEPPER_GOODS)
    assert "Cashew" in merged
    assert "W180" in merged["Cashew"]
    assert "W180" in merged["__all__"]


def test_no_duplicates_when_good_already_hardcoded(db):
    # "Jini" is already in ARECA_NUT_GOODS and seeded into stock_goods
    merged = get_merged_goods(db, ARECA_NUT_GOODS, BLACK_PEPPER_GOODS)
    assert merged["Arecanut"].count("Jini") == 1, "Jini must appear exactly once"


def test_empty_flag_when_no_goods(db):
    db.execute("DELETE FROM stock_levels")
    db.execute("DELETE FROM stock_goods")
    db.commit()
    merged = get_merged_goods(db, [], [])
    assert merged["__empty__"] is True


def test_not_empty_when_hardcoded_goods_present(db):
    db.execute("DELETE FROM stock_levels")
    db.execute("DELETE FROM stock_goods")
    db.commit()
    merged = get_merged_goods(db, ARECA_NUT_GOODS, BLACK_PEPPER_GOODS)
    assert merged["__empty__"] is False
    assert "Jini" in merged["Arecanut"]
