"""
Database connection, schema, constants, and audit helper.
All other modules import constants and DB helpers from here.
Uses PostgreSQL (Supabase) via psycopg2.
"""

import os
import json
import threading
import psycopg2
import psycopg2.extras
import psycopg2.pool
import psycopg2.extensions
import pandas as pd
from datetime import date as _date

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# ── Business constants ─────────────────────────────────────────
INTEREST_RATE_PCT  = 24.0     # single source of truth
DEFAULT_GRACE_DAYS = 35
TXNS_PER_PAGE      = 20

# ── Firm names ─────────────────────────────────────────────────
FIRM_SP = "SP Spices"
FIRM_MT = "Mukund Traders"
FIRMS   = (FIRM_SP, FIRM_MT)

# ── Passbook source types ───────────────────────────────────────
SRC_MANUAL       = "Manual"
SRC_VENDOR_RTGS  = "VendorRTGS"
SRC_CUST_CHQ_TXN = "CustomerChequeTxn"
SRC_CUST_CHQ_PMT = "CustomerChequePmt"
SRC_ALLOCATION   = "CustomerAllocation"
SRC_OPENING      = "Opening"

# ── Cash in Hand source types ──────────────────────────────────
SRC_CUSTOMER_CASH = "CustomerCash"
SRC_VENDOR_UB     = "VendorUB"
SRC_CIH_OPENING   = "CIHOpening"

# ── Cheque statuses ─────────────────────────────────────────────
CHQ_PENDING = "Pending"
CHQ_CLEARED = "Cleared"

# ── Goods catalogues ──────────────────────────────────────────
ARECA_NUT_GOODS = [
    "Jini","Jam","Patent","Vichras","Moti","P. Fadcha","P. Vichras",
    "Goa 1 No.","Goa 2 No.","Goa 3 No.","Goa 4 No.","Goa 5 No.","Goa 6 No.",
    "Kumta Argera","Sagar Argera","K.P","Bitte","Kumta Fator","Kumta Rass","Kerala Rass",
]
BLACK_PEPPER_GOODS = [
    "Trishul BP","Super BP","Vasu BP","Shakti BP",
    "Green Global BP","Silver BP","Mahadev BP",
]
GOODS_OPTIONS = ARECA_NUT_GOODS + BLACK_PEPPER_GOODS


class _PgConn:
    """
    Thin wrapper around a psycopg2 connection that adds a conn.execute()
    method matching sqlite3's API, so the rest of the app needs minimal changes.

    Each call to .execute() creates a fresh cursor, executes the query,
    and returns that cursor (which has .fetchone() / .fetchall()).
    Rows are returned as RealDictRow objects (dict-like, accessed by column name).
    """

    def __init__(self, pg_conn, pool=None):
        self._conn = pg_conn
        self._pool = pool  # if set, close() returns conn to pool instead of closing
        self._closed = False

    def execute(self, sql, params=None):
        cur = self._conn.cursor()  # RealDictCursor (set at connection level)
        cur.execute(sql, params or ())
        return cur

    def cursor(self):
        """Plain (non-dict) cursor — used by pandas.read_sql."""
        import psycopg2.extensions
        return self._conn.cursor(cursor_factory=psycopg2.extensions.cursor)

    def commit(self):
        self._conn.commit()

    def rollback(self):
        self._conn.rollback()

    def close(self):
        """Return connection to pool (if pooled) or close it. Safe to call multiple times."""
        if self._closed:
            return
        self._closed = True
        if self._pool is not None:
            self._pool.putconn(self._conn)
        else:
            self._conn.close()

    @property
    def raw(self):
        """Underlying psycopg2 connection — for advanced use."""
        return self._conn

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type:
            self.rollback()
        else:
            self.commit()
        self.close()


def _get_db_url():
    url = os.environ.get("DATABASE_URL")
    if not url:
        try:
            import streamlit as st
            url = st.secrets.get("DATABASE_URL")
        except Exception:
            pass
    if not url:
        raise RuntimeError(
            "DATABASE_URL is not set. Add it to .env or .streamlit/secrets.toml"
        )
    return url


# ── Connection pool (reused across Streamlit reruns) ──────────
_pool: psycopg2.pool.ThreadedConnectionPool | None = None
_pool_lock = threading.Lock()

# ── Schema initialization flag ─────────────────────────────────
_schema_initialized = False


def _get_pool() -> psycopg2.pool.ThreadedConnectionPool:
    global _pool
    if _pool is None:
        with _pool_lock:
            if _pool is None:
                _pool = psycopg2.pool.ThreadedConnectionPool(
                    minconn=1,
                    maxconn=10,
                    dsn=_get_db_url(),
                    cursor_factory=psycopg2.extras.RealDictCursor,
                )
    return _pool


def get_conn() -> _PgConn:
    """Open a new PostgreSQL connection wrapped in _PgConn."""
    pool = _get_pool()
    pg_conn = pool.getconn()
    pg_conn.autocommit = False
    return _PgConn(pg_conn, pool=pool)


def pg_read_sql(sql, conn, params=None):
    """
    Replacement for pd.read_sql() that works with psycopg2 RealDictCursor.
    Use this everywhere instead of pd.read_sql(sql, conn).
    """
    cur = conn.execute(sql, params) if params else conn.execute(sql)
    rows = cur.fetchall()
    if not rows:
        cols = [d[0] for d in cur.description] if cur.description else []
        return pd.DataFrame(columns=cols)
    return pd.DataFrame([dict(r) for r in rows])


def invalidate_lookup_cache():
    """Call after adding/deleting brokers, vendors, or stock goods to bust stale caches."""
    try:
        import streamlit as st
        st.cache_data.clear()
    except Exception:
        pass


def ensure_schema(conn=None):
    """
    Seeds reference/initial data on first startup.
    The actual DDL schema lives in Supabase (created via SQL Editor).
    Runs only once per process — subsequent calls return immediately.
    """
    global _schema_initialized
    if _schema_initialized:
        return
    _schema_initialized = True

    _own = conn is None
    if _own:
        conn = get_conn()
    try:
        _today_iso = _date.today().isoformat()
        _STOCK_LOCATIONS = ["Transport", "Shop", "Anandpuri"]
        _STOCK_SEEDS = {
            "Arecanut":     ARECA_NUT_GOODS,
            "Black Pepper": BLACK_PEPPER_GOODS,
        }

        # ── Seed stock categories & goods ──────────────────────
        for cat_name, goods_list in _STOCK_SEEDS.items():
            conn.execute(
                "INSERT INTO stock_categories (category_name) VALUES (%s) "
                "ON CONFLICT DO NOTHING",
                (cat_name,))
            row = conn.execute(
                "SELECT category_id FROM stock_categories WHERE category_name=%s",
                (cat_name,)).fetchone()
            if row:
                cid = row["category_id"]
                for g in goods_list:
                    conn.execute(
                        "INSERT INTO stock_goods (category_id, good_name) "
                        "VALUES (%s, %s) ON CONFLICT DO NOTHING",
                        (cid, g))

        # ── Seed stock levels for every good × location ────────
        goods = conn.execute("SELECT good_id FROM stock_goods").fetchall()
        for r in goods:
            gid = r["good_id"]
            for loc in _STOCK_LOCATIONS:
                conn.execute(
                    "INSERT INTO stock_levels (good_id, location, bags, quantity_kg) "
                    "VALUES (%s, %s, 0, 0) ON CONFLICT DO NOTHING",
                    (gid, loc))

        # ── Seed unidentified_stock ────────────────────────────
        conn.execute("""
            INSERT INTO unidentified_stock (category_id, location, bags, quantity_kg)
            SELECT sc.category_id, loc.location, 0, 0
            FROM stock_categories sc
            CROSS JOIN (VALUES ('Transport'),('Shop'),('Anandpuri')) AS loc(location)
            ON CONFLICT DO NOTHING
        """)

        # ── Passbook opening balances ──────────────────────────
        for _firm in FIRMS:
            conn.execute(
                "INSERT INTO passbook_opening_balance "
                "(firm, opening_amount, opening_date) VALUES (%s, 0, %s) "
                "ON CONFLICT DO NOTHING",
                (_firm, _today_iso))
            _ob = conn.execute(
                "SELECT opening_amount, opening_date "
                "FROM passbook_opening_balance WHERE firm=%s",
                (_firm,)).fetchone()
            _exists = conn.execute(
                "SELECT 1 FROM passbook_entries WHERE firm=%s AND source_type=%s",
                (_firm, SRC_OPENING)).fetchone()
            if _ob and not _exists:
                _oa    = float(_ob["opening_amount"])
                _otype = 'Credit' if _oa >= 0 else 'Debit'
                conn.execute(
                    "INSERT INTO passbook_entries "
                    "(firm, entry_date, details, amount, txn_type, source_type) "
                    "VALUES (%s, %s, 'Opening Balance', %s, %s, %s)",
                    (_firm, _ob["opening_date"], abs(_oa), _otype, SRC_OPENING))

        # ── Cash in Hand opening ───────────────────────────────
        conn.execute(
            "INSERT INTO cash_in_hand_opening (id, opening_amount, opening_date, notes) "
            "VALUES (1, 0, %s, '') ON CONFLICT DO NOTHING",
            (_today_iso,))
        _cih_ob = conn.execute(
            "SELECT opening_amount, opening_date FROM cash_in_hand_opening WHERE id=1"
        ).fetchone()
        _cih_exists = conn.execute(
            "SELECT 1 FROM cash_in_hand_entries WHERE source_type=%s",
            (SRC_CIH_OPENING,)).fetchone()
        if _cih_ob and not _cih_exists:
            _coa   = float(_cih_ob["opening_amount"])
            _ctype = 'Credit' if _coa >= 0 else 'Debit'
            conn.execute(
                "INSERT INTO cash_in_hand_entries "
                "(entry_date, details, amount, txn_type, source_type) "
                "VALUES (%s, %s, %s, %s, %s)",
                (_cih_ob["opening_date"], 'Opening Balance',
                 abs(_coa), _ctype, SRC_CIH_OPENING))

        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        if _own:
            conn.close()


def get_merged_goods(conn, base_areca: list, base_bp: list) -> dict:
    rows = conn.execute("""
        SELECT sc.category_name, sg.good_name
        FROM stock_goods sg
        JOIN stock_categories sc
          ON sg.category_id = sc.category_id
        ORDER BY sc.category_name ASC, sg.good_id ASC
    """).fetchall()

    result = {
        "Arecanut":     list(base_areca),
        "Black Pepper": list(base_bp),
    }

    for row in rows:
        cat  = row["category_name"]
        good = row["good_name"]
        if cat == "Arecanut":
            if good not in result["Arecanut"]:
                result["Arecanut"].append(good)
        elif cat == "Black Pepper":
            if good not in result["Black Pepper"]:
                result["Black Pepper"].append(good)
        else:
            if cat not in result:
                result[cat] = []
            if good not in result[cat]:
                result[cat].append(good)

    all_goods = []
    for cat, goods in result.items():
        all_goods.extend(goods)

    result["__all__"]   = all_goods
    result["__empty__"] = len(all_goods) == 0
    return result


def add_unidentified_stock(conn, category_id: int, location: str,
                            bags: float, quantity_kg: float) -> None:
    conn.execute("""
        INSERT INTO unidentified_stock
            (category_id, location, bags, quantity_kg)
        VALUES (%s, %s, %s, %s)
        ON CONFLICT(category_id, location) DO UPDATE SET
            bags        = unidentified_stock.bags        + excluded.bags,
            quantity_kg = unidentified_stock.quantity_kg + excluded.quantity_kg
    """, (category_id, location,
          round(float(bags), 2),
          round(float(quantity_kg), 2)))


def reverse_unidentified_stock(conn, category_id: int, location: str,
                                bags: float, quantity_kg: float) -> None:
    conn.execute("""
        UPDATE unidentified_stock
           SET bags        = bags        - %s,
               quantity_kg = quantity_kg - %s
         WHERE category_id = %s AND location = %s
    """, (round(float(bags), 2),
          round(float(quantity_kg), 2),
          category_id, location))


def log_audit(conn, table_name: str, record_id: int, action: str,
              old_value: dict = None, new_value: dict = None):
    conn.execute(
        "INSERT INTO audit_log (table_name, record_id, action, old_value, new_value) "
        "VALUES (%s, %s, %s, %s, %s)",
        (table_name, record_id, action,
         json.dumps(old_value) if old_value is not None else None,
         json.dumps(new_value) if new_value is not None else None)
    )


def log_stock_change(conn, category_name: str, good_name: str,
                     location: str, change_type: str,
                     bags_before: float, bags_after: float,
                     kg_before: float, kg_after: float,
                     source: str = "") -> None:
    from datetime import datetime
    conn.execute("""
        INSERT INTO stock_history
            (recorded_at, category_name, good_name, location,
             change_type, bags_before, bags_after, bags_change,
             kg_before, kg_after, kg_change, source)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    """, (
        datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        category_name, good_name, location, change_type,
        round(float(bags_before), 2), round(float(bags_after), 2),
        round(float(bags_after - bags_before), 2),
        round(float(kg_before), 2), round(float(kg_after), 2),
        round(float(kg_after - kg_before), 2),
        source
    ))


def purge_old_stock_history(conn) -> None:
    conn.execute("""
        DELETE FROM stock_history
        WHERE recorded_at < to_char(NOW() - INTERVAL '30 days', 'YYYY-MM-DD HH24:MI:SS')
    """)


def deduct_stock_for_sale(conn, bill_items: list, sale_date, customer_name: str) -> list:
    _VALID_LOCS = {"Transport", "Shop", "Anandpuri"}
    warnings = []

    for it in bill_items:
        good_row = conn.execute(
            "SELECT good_id FROM stock_goods WHERE good_name=%s",
            (it["goods"],)).fetchone()

        if not good_row:
            warnings.append(
                f"'{it['goods']}' not found in Stock Register — stock not updated.")
            continue

        gid = good_row["good_id"]
        loc = it.get("collection_point", "")

        if not loc:
            raise ValueError(
                f"Cannot deduct stock for '{it['goods']}': collection_point is empty. "
                "This indicates a data integrity issue. Please verify the bill.")

        if loc not in _VALID_LOCS:
            warnings.append(
                f"'{it['goods']}': unknown collection point '{loc}' — stock not updated.")
            continue

        bags_sold = int(it["bags"])
        kg_sold   = float(it["qty"])

        _cs_row = conn.execute("""
            SELECT sl.bags, sl.quantity_kg,
                   sg.good_name, sc.category_name
            FROM stock_levels sl
            JOIN stock_goods sg ON sl.good_id = sg.good_id
            JOIN stock_categories sc ON sg.category_id = sc.category_id
            WHERE sl.good_id = %s AND sl.location = %s
        """, (gid, loc)).fetchone()
        _cs_b_bags = float(_cs_row["bags"] or 0) if _cs_row else 0
        _cs_b_kg   = float(_cs_row["quantity_kg"] or 0) if _cs_row else 0.0
        _cs_cat    = _cs_row["category_name"] if _cs_row else ""
        _cs_good   = _cs_row["good_name"] if _cs_row else ""

        conn.execute(
            "UPDATE stock_levels SET bags=bags-%s, quantity_kg=quantity_kg-%s "
            "WHERE good_id=%s AND location=%s",
            (bags_sold, kg_sold, gid, loc))

        conn.execute(
            "INSERT INTO stock_transfers "
            "(transfer_date, good_id, from_location, to_location, bags_moved, kg_moved, note) "
            "VALUES (%s, %s, %s, 'Sold', %s, %s, %s)",
            (str(sale_date), gid, loc, bags_sold, kg_sold,
             f"Bill sale — {customer_name}"))

        try:
            log_stock_change(
                conn, _cs_cat, _cs_good, loc,
                "Customer Sale",
                _cs_b_bags, _cs_b_bags - bags_sold,
                _cs_b_kg,   _cs_b_kg   - kg_sold,
                source=f"Customer: {customer_name}"
            )
        except Exception as _e:
            import logging
            logging.getLogger(__name__).warning(
                "stock_history log failed: %s", _e)

        updated = conn.execute(
            "SELECT bags, quantity_kg FROM stock_levels "
            "WHERE good_id=%s AND location=%s", (gid, loc)).fetchone()
        if updated and (updated["bags"] < 0 or updated["quantity_kg"] < 0):
            warnings.append(
                f"⚠ {it['goods']} at {loc} is now below zero "
                f"(bags: {updated['bags']}, kg: {updated['quantity_kg']:.1f}) "
                "— update stock register.")

    return warnings
