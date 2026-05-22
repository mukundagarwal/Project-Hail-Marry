"""
One-time migration: copies all data from local spices.db (SQLite)
into the Neon PostgreSQL database.

Run from the project root:
    python scripts/migrate_sqlite_to_neon.py

Safety: the script ONLY inserts — it never deletes Neon data.
Run ensure_schema() first to create all tables before migrating.
Sequences (SERIAL columns) are reset at the end so future inserts
start above the highest migrated ID.
"""

import sqlite3
import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from utils.db import get_conn, ensure_schema

SQLITE_PATH = os.path.join(os.path.dirname(__file__), "..", "spices.db")

# Tables to migrate in dependency order (parents before children)
TABLES = [
    "brokers",
    "customer_transactions",
    "transaction_items",
    "payments",
    "audit_log",
    "stock_categories",
    "stock_goods",
    "stock_levels",
    "stock_transfers",
    "stock_history",
    "unidentified_stock",
    "vendors",
    "vendor_entries",
    "passbook_opening_balance",
    "passbook_entries",
    "cash_in_hand_opening",
    "cash_in_hand_entries",
]

# SERIAL (auto-increment) columns for each table — used to reset sequences
SERIAL_COLS = {
    "brokers":               ("broker_id",        "brokers_broker_id_seq"),
    "customer_transactions": ("transaction_id",   "customer_transactions_transaction_id_seq"),
    "transaction_items":     ("item_id",           "transaction_items_item_id_seq"),
    "payments":              ("payment_id",        "payments_payment_id_seq"),
    "audit_log":             ("log_id",            "audit_log_log_id_seq"),
    "stock_categories":      ("category_id",       "stock_categories_category_id_seq"),
    "stock_goods":           ("good_id",           "stock_goods_good_id_seq"),
    "stock_levels":          ("level_id",          "stock_levels_level_id_seq"),
    "stock_transfers":       ("transfer_id",       "stock_transfers_transfer_id_seq"),
    "stock_history":         ("history_id",        "stock_history_history_id_seq"),
    "unidentified_stock":    ("unid_id",           "unidentified_stock_unid_id_seq"),
    "vendors":               ("vendor_id",         "vendors_vendor_id_seq"),
    "vendor_entries":        ("entry_id",          "vendor_entries_entry_id_seq"),
    "passbook_entries":      ("entry_id",          "passbook_entries_entry_id_seq"),
    "cash_in_hand_entries":  ("entry_id",          "cash_in_hand_entries_entry_id_seq"),
}


def _placeholders(n: int) -> str:
    return ", ".join(["%s"] * n)


def migrate():
    if not os.path.exists(SQLITE_PATH):
        print(f"ERROR: SQLite database not found at: {SQLITE_PATH}")
        sys.exit(1)

    print(f"Connecting to SQLite: {SQLITE_PATH}")
    sq = sqlite3.connect(SQLITE_PATH)
    sq.row_factory = sqlite3.Row

    print("Connecting to Neon PostgreSQL...")
    pg = get_conn()

    print("Running ensure_schema() to create tables if needed...")
    ensure_schema(conn=pg)

    # Build good_name -> Neon good_id map (Neon IDs may differ from SQLite IDs)
    _good_name_to_neon_id = {
        r["good_name"]: r["good_id"]
        for r in pg.execute("SELECT good_id, good_name FROM stock_goods").fetchall()
    }
    _sq_goods = {
        r["good_id"]: r["good_name"]
        for r in sq.execute("SELECT good_id, good_name FROM stock_goods").fetchall()
    }

    total_rows = 0
    for table in TABLES:
        rows = sq.execute(f"SELECT * FROM {table}").fetchall()
        if not rows:
            print(f"  {table}: 0 rows — skipped")
            continue

        cols  = list(rows[0].keys())
        ph    = _placeholders(len(cols))
        cols_sql = ", ".join(cols)
        insert_sql = (
            f"INSERT INTO {table} ({cols_sql}) VALUES ({ph}) "
            f"ON CONFLICT DO NOTHING"
        )

        # Tables whose good_id column must be remapped from SQLite IDs to Neon IDs
        _remap_good_id = table in ("vendor_entries", "stock_levels",
                                   "stock_transfers", "stock_history")

        inserted = 0
        skipped  = 0
        for row in rows:
            values = list(row[c] for c in cols)
            if _remap_good_id and "good_id" in cols:
                idx = cols.index("good_id")
                sq_gid = values[idx]
                if sq_gid is not None:
                    gname = _sq_goods.get(sq_gid)
                    neon_gid = _good_name_to_neon_id.get(gname) if gname else None
                    values[idx] = neon_gid
            pg.execute("SAVEPOINT _row")
            try:
                pg.execute(insert_sql, values)
                pg.execute("RELEASE SAVEPOINT _row")
                inserted += 1
            except Exception as e:
                pg.execute("ROLLBACK TO SAVEPOINT _row")
                pg.execute("RELEASE SAVEPOINT _row")
                skipped += 1

        pg.commit()
        msg = f"  {table}: {inserted} rows migrated"
        if skipped:
            msg += f" ({skipped} skipped)"
        print(msg)
        total_rows += inserted

    # Reset sequences so future inserts don't collide with migrated IDs
    print("\nResetting sequences...")
    for table, (col, seq) in SERIAL_COLS.items():
        row = pg.execute(
            f"SELECT COALESCE(MAX({col}), 0) FROM {table}").fetchone()
        max_id = int(row[0]) if row else 0
        if max_id > 0:
            pg.execute(f"SELECT setval('{seq}', {max_id})")
            pg.commit()
            print(f"  {seq} -> {max_id}")

    sq.close()
    pg.close()
    print(f"\nDone. {total_rows} total rows migrated.")


if __name__ == "__main__":
    migrate()
