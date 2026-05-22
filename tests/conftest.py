import pytest
import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from utils.db import get_conn, ensure_schema

_ALL_TABLES = (
    "audit_log, vendor_entries, vendors, "
    "cash_in_hand_entries, cash_in_hand_opening, "
    "passbook_entries, passbook_opening_balance, "
    "payments, transaction_items, customer_transactions, brokers, "
    "stock_transfers, stock_history, stock_levels, "
    "unidentified_stock, stock_goods, stock_categories"
)


@pytest.fixture
def db():
    """Fresh PostgreSQL DB state for each test: truncate all tables then re-seed."""
    conn = get_conn()
    conn.execute(f"TRUNCATE TABLE {_ALL_TABLES} RESTART IDENTITY")
    conn.commit()
    ensure_schema(conn=conn)
    yield conn
    conn.close()
