"""
Database connection, schema, constants, and audit helper.
All other modules import constants and DB helpers from here.
Uses PostgreSQL (Supabase) via psycopg2.
"""

import os
import json
import time
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
    last_exc: Exception | None = None
    for _attempt in range(5):
        try:
            pg_conn = pool.getconn()
        except Exception as e:
            last_exc = e
            if _attempt < 4:
                time.sleep(1)
            continue
        try:
            if pg_conn.closed:
                pool.putconn(pg_conn, close=True)
                continue
            if pg_conn.status != psycopg2.extensions.STATUS_READY:
                pg_conn.rollback()
            # Live ping: psycopg2 reports closed=0/STATUS_READY even when the
            # underlying TCP socket is dead (Neon serverless hibernation drops
            # connections after ~5 min of inactivity). SELECT 1 forces a real
            # round-trip so we catch the broken socket before returning.
            with pg_conn.cursor() as _ping:
                _ping.execute("SELECT 1")
            pg_conn.rollback()
            pg_conn.autocommit = False
            return _PgConn(pg_conn, pool=pool)
        except Exception as e:
            last_exc = e
            try:
                pool.putconn(pg_conn, close=True)
            except Exception:
                pass
            if _attempt < 4:
                time.sleep(1)
    raise RuntimeError(
        f"Could not obtain a healthy database connection after 5 attempts. "
        f"Last error: {last_exc}"
    )


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


try:
    import streamlit as _st_mod
    _cache_ttl_300 = _st_mod.cache_data(ttl=300, show_spinner=False)
except Exception:
    _cache_ttl_300 = lambda f: f  # identity — tests / non-Streamlit contexts


@_cache_ttl_300
def get_all_brokers_cached():
    """Broker list cached 5 min. Call invalidate_lookup_cache() after add/delete."""
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT broker_id, broker_name FROM brokers ORDER BY broker_name"
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


@_cache_ttl_300
def get_all_vendors_cached():
    """Vendor list cached 5 min. Call invalidate_lookup_cache() after add/delete."""
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT vendor_id, vendor_name FROM vendors ORDER BY vendor_name"
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


@_cache_ttl_300
def get_merged_goods_cached():
    """Merged goods dict cached 5 min. Call invalidate_lookup_cache() after add/delete."""
    conn = get_conn()
    try:
        return get_merged_goods(conn, ARECA_NUT_GOODS, BLACK_PEPPER_GOODS)
    finally:
        conn.close()


def invalidate_lookup_cache():
    # Call after any write to: brokers, vendors, stock_goods, stock_categories
    # Callers (update this list when adding new write paths):
    #   pages/1_Customer_Payments.py  — add_broker, delete_broker
    #   pages/2_Vendor_Payments.py    — add_vendor, delete_vendor, add_good (bill form)
    #   pages/3_Stock_Register.py     — add_category, add_good, edit_good, delete_good
    for _fn in (get_all_brokers_cached, get_all_vendors_cached, get_merged_goods_cached):
        try:
            _fn.clear()
        except Exception:
            pass


def _db_ph(conn) -> str:
    """Return SQL placeholder for this connection: '?' for SQLite, '%s' for PostgreSQL."""
    import sqlite3 as _sqlite3
    return "?" if isinstance(conn, _sqlite3.Connection) else "%s"


def execute_in_clause(conn, sql_template: str, ids, extra_params=()):
    """
    Execute SQL with an IN clause that works on both PostgreSQL and SQLite.
    sql_template must contain the literal text '{IN_CLAUSE}' where the list goes.
    extra_params: additional positional parameters that appear AFTER the IN list
    in the SQL template (use the dialect-appropriate placeholder in the template).
    Returns the cursor, or None if ids is empty.

    Example:
        cur = execute_in_clause(
            conn,
            "DELETE FROM passbook_entries WHERE source_id IN {IN_CLAUSE} AND firm=%s",
            [1, 2, 3], ("SP Spices",))
    """
    if not ids:
        return None
    ph = _db_ph(conn)
    placeholders = ",".join([ph] * len(ids))
    sql = sql_template.replace("{IN_CLAUSE}", f"({placeholders})")
    return conn.execute(sql, list(ids) + list(extra_params))


def _sqlite_ddl(conn):
    """Create all tables in a SQLite connection. Used by tests only."""
    stmts = [
        """CREATE TABLE IF NOT EXISTS brokers (
            broker_id   INTEGER PRIMARY KEY AUTOINCREMENT,
            broker_name TEXT UNIQUE NOT NULL
        )""",
        """CREATE TABLE IF NOT EXISTS customer_transactions (
            transaction_id    INTEGER PRIMARY KEY AUTOINCREMENT,
            broker_id         INTEGER NOT NULL REFERENCES brokers(broker_id),
            customer_name     TEXT NOT NULL,
            date              TEXT NOT NULL,
            total_amount      REAL NOT NULL DEFAULT 0,
            payment_status    TEXT NOT NULL DEFAULT 'Pending',
            payment_received  REAL NOT NULL DEFAULT 0,
            discount_pct      REAL NOT NULL DEFAULT 0,
            brokerage_applied INTEGER NOT NULL DEFAULT 0,
            calc_status       TEXT NOT NULL DEFAULT 'Pending',
            final_settlement  REAL,
            interest_amount   REAL NOT NULL DEFAULT 0
        )""",
        """CREATE TABLE IF NOT EXISTS payments (
            payment_id       INTEGER PRIMARY KEY AUTOINCREMENT,
            transaction_id   INTEGER NOT NULL REFERENCES customer_transactions(transaction_id),
            payment_date     TEXT NOT NULL,
            amount           REAL NOT NULL,
            method           TEXT NOT NULL DEFAULT 'Cash',
            note             TEXT,
            days_from_start  INTEGER NOT NULL DEFAULT 0,
            interest_charged REAL NOT NULL DEFAULT 0
        )""",
        """CREATE TABLE IF NOT EXISTS vendors (
            vendor_id   INTEGER PRIMARY KEY AUTOINCREMENT,
            vendor_name TEXT UNIQUE NOT NULL
        )""",
        """CREATE TABLE IF NOT EXISTS vendor_entries (
            entry_id    INTEGER PRIMARY KEY AUTOINCREMENT,
            vendor_id   INTEGER NOT NULL REFERENCES vendors(vendor_id),
            entry_date  TEXT NOT NULL,
            ledger_type TEXT NOT NULL,
            firm        TEXT,
            entry_kind  TEXT NOT NULL,
            particulars TEXT,
            amount      REAL NOT NULL DEFAULT 0,
            bags        REAL,
            quantity_kg REAL,
            good_id     INTEGER
        )""",
        """CREATE TABLE IF NOT EXISTS passbook_entries (
            entry_id      INTEGER PRIMARY KEY AUTOINCREMENT,
            firm          TEXT NOT NULL,
            entry_date    TEXT NOT NULL,
            details       TEXT,
            amount        REAL NOT NULL DEFAULT 0,
            txn_type      TEXT NOT NULL,
            cheque_status TEXT,
            source_type   TEXT NOT NULL DEFAULT 'Manual',
            source_id     INTEGER
        )""",
        """CREATE TABLE IF NOT EXISTS passbook_opening_balance (
            firm           TEXT PRIMARY KEY,
            opening_amount REAL NOT NULL DEFAULT 0,
            opening_date   TEXT NOT NULL
        )""",
        """CREATE TABLE IF NOT EXISTS cash_in_hand_entries (
            entry_id    INTEGER PRIMARY KEY AUTOINCREMENT,
            entry_date  TEXT NOT NULL,
            details     TEXT,
            amount      REAL NOT NULL DEFAULT 0,
            txn_type    TEXT NOT NULL,
            source_type TEXT NOT NULL DEFAULT 'Manual',
            source_id   INTEGER,
            UNIQUE (source_type, source_id)
        )""",
        """CREATE TABLE IF NOT EXISTS cash_in_hand_opening (
            id             INTEGER PRIMARY KEY,
            opening_amount REAL NOT NULL DEFAULT 0,
            opening_date   TEXT NOT NULL,
            notes          TEXT NOT NULL DEFAULT ''
        )""",
        """CREATE TABLE IF NOT EXISTS stock_categories (
            category_id   INTEGER PRIMARY KEY AUTOINCREMENT,
            category_name TEXT UNIQUE NOT NULL
        )""",
        """CREATE TABLE IF NOT EXISTS stock_goods (
            good_id     INTEGER PRIMARY KEY AUTOINCREMENT,
            category_id INTEGER NOT NULL REFERENCES stock_categories(category_id),
            good_name   TEXT NOT NULL
        )""",
        """CREATE TABLE IF NOT EXISTS stock_levels (
            good_id     INTEGER NOT NULL REFERENCES stock_goods(good_id),
            location    TEXT NOT NULL,
            batch_label TEXT NOT NULL DEFAULT '',
            batch_notes TEXT,
            bags        REAL NOT NULL DEFAULT 0,
            quantity_kg REAL NOT NULL DEFAULT 0,
            UNIQUE (good_id, location, batch_label)
        )""",
        """CREATE TABLE IF NOT EXISTS unidentified_stock (
            category_id INTEGER NOT NULL REFERENCES stock_categories(category_id),
            location    TEXT NOT NULL,
            bags        REAL NOT NULL DEFAULT 0,
            quantity_kg REAL NOT NULL DEFAULT 0,
            notes       TEXT,
            UNIQUE (category_id, location)
        )""",
        """CREATE TABLE IF NOT EXISTS stock_transfers (
            transfer_id   INTEGER PRIMARY KEY AUTOINCREMENT,
            transfer_date TEXT NOT NULL,
            good_id       INTEGER,
            from_location TEXT,
            to_location   TEXT,
            bags_moved    REAL NOT NULL DEFAULT 0,
            kg_moved      REAL NOT NULL DEFAULT 0,
            note          TEXT,
            CHECK (from_location IS NULL OR to_location IS NULL
                   OR from_location != to_location)
        )""",
        """CREATE TABLE IF NOT EXISTS transaction_items (
            item_id          INTEGER PRIMARY KEY AUTOINCREMENT,
            transaction_id   INTEGER NOT NULL
                             REFERENCES customer_transactions(transaction_id),
            type_of_goods    TEXT NOT NULL DEFAULT '',
            bags             INTEGER DEFAULT 0,
            bag_rate         REAL DEFAULT 0,
            quantity         REAL DEFAULT 0,
            rate             REAL DEFAULT 0,
            freight          REAL DEFAULT 0,
            collection_point TEXT DEFAULT '',
            line_total       REAL DEFAULT 0
        )""",
        """CREATE TABLE IF NOT EXISTS stock_history (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            recorded_at   TEXT NOT NULL,
            category_name TEXT,
            good_name     TEXT,
            location      TEXT,
            change_type   TEXT,
            bags_before   REAL,
            bags_after    REAL,
            bags_change   REAL,
            kg_before     REAL,
            kg_after      REAL,
            kg_change     REAL,
            source        TEXT
        )""",
        """CREATE TABLE IF NOT EXISTS audit_log (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            table_name TEXT,
            record_id  INTEGER,
            action     TEXT,
            old_value  TEXT,
            new_value  TEXT,
            changed_at TEXT DEFAULT (datetime('now'))
        )""",
    ]
    for stmt in stmts:
        conn.execute(stmt)
    # Partial unique indexes (enforce idempotency for opening balance rows)
    conn.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS uq_passbook_opening_per_firm
        ON passbook_entries (firm) WHERE source_type = 'Opening'
    """)
    conn.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS uq_cih_opening
        ON cash_in_hand_entries (source_type) WHERE source_type = 'CIHOpening'
    """)
    conn.commit()


def ensure_schema(conn=None):
    """
    Seeds reference/initial data on first startup.
    Production: DDL lives in Supabase; this only seeds initial rows (runs once per process).
    Tests: pass a sqlite3.Connection explicitly — tables are created, then seeded, every call.
    """
    global _schema_initialized

    _own = conn is None
    if _own:
        # Production path — guard against running twice in the same process
        if _schema_initialized:
            return
        conn = get_conn()

    import sqlite3 as _sqlite3
    _is_sqlite = isinstance(conn, _sqlite3.Connection)
    ph = "?" if _is_sqlite else "%s"

    try:
        _today_iso = _date.today().isoformat()
        _STOCK_LOCATIONS = ["Transport", "Shop", "Anandpuri", "Cold"]
        _STOCK_SEEDS = {
            "Arecanut":     ARECA_NUT_GOODS,
            "Black Pepper": BLACK_PEPPER_GOODS,
        }

        # SQLite (tests): create tables since Supabase DDL isn't available
        if _is_sqlite:
            _sqlite_ddl(conn)

        # ── Migration: batch columns on stock_levels (PostgreSQL) ──
        if not _is_sqlite:
            for _msql in [
                "ALTER TABLE stock_levels ADD COLUMN IF NOT EXISTS batch_label TEXT NOT NULL DEFAULT ''",
                "ALTER TABLE stock_levels ADD COLUMN IF NOT EXISTS batch_notes TEXT",
            ]:
                conn.execute(_msql)
            conn.execute(
                "ALTER TABLE stock_levels DROP CONSTRAINT IF EXISTS stock_levels_good_id_location_key"
            )
            conn.execute("""
                DO $$ BEGIN
                    IF NOT EXISTS (
                        SELECT 1 FROM pg_constraint
                        WHERE conname = 'stock_levels_good_id_location_batch_key'
                    ) THEN
                        ALTER TABLE stock_levels
                            ADD CONSTRAINT stock_levels_good_id_location_batch_key
                            UNIQUE (good_id, location, batch_label);
                    END IF;
                END $$
            """)
            conn.execute(
                "ALTER TABLE unidentified_stock ADD COLUMN IF NOT EXISTS notes TEXT"
            )
            conn.execute("""
                DO $$ BEGIN
                    IF NOT EXISTS (
                        SELECT 1 FROM pg_constraint
                        WHERE conname = 'stock_transfers_no_self_transfer'
                    ) THEN
                        ALTER TABLE stock_transfers
                            ADD CONSTRAINT stock_transfers_no_self_transfer
                            CHECK (from_location IS NULL OR to_location IS NULL
                                   OR from_location != to_location);
                    END IF;
                END $$
            """)
            conn.execute("""
                CREATE UNIQUE INDEX IF NOT EXISTS uq_passbook_opening_per_firm
                ON passbook_entries (firm) WHERE source_type = 'Opening'
            """)
            conn.execute("""
                CREATE UNIQUE INDEX IF NOT EXISTS uq_cih_opening
                ON cash_in_hand_entries (source_type) WHERE source_type = 'CIHOpening'
            """)
            conn.commit()

        # ── Seed stock categories & goods ──────────────────────
        for cat_name, goods_list in _STOCK_SEEDS.items():
            conn.execute(
                f"INSERT INTO stock_categories (category_name) VALUES ({ph}) "
                f"ON CONFLICT DO NOTHING",
                (cat_name,))
            row = conn.execute(
                f"SELECT category_id FROM stock_categories WHERE category_name={ph}",
                (cat_name,)).fetchone()
            if row:
                cid = row["category_id"]
                for g in goods_list:
                    conn.execute(
                        f"INSERT INTO stock_goods (category_id, good_name) "
                        f"VALUES ({ph}, {ph}) ON CONFLICT DO NOTHING",
                        (cid, g))

        # ── Seed stock levels for every good × location ────────
        goods = conn.execute("SELECT good_id FROM stock_goods").fetchall()
        for r in goods:
            gid = r["good_id"]
            for loc in _STOCK_LOCATIONS:
                conn.execute(
                    f"INSERT INTO stock_levels (good_id, location, bags, quantity_kg) "
                    f"VALUES ({ph}, {ph}, 0, 0) ON CONFLICT DO NOTHING",
                    (gid, loc))

        # ── Seed unidentified_stock ────────────────────────────
        for _cr in conn.execute("SELECT category_id FROM stock_categories").fetchall():
            for _loc in _STOCK_LOCATIONS:
                conn.execute(
                    f"INSERT INTO unidentified_stock "
                    f"(category_id, location, bags, quantity_kg) VALUES ({ph},{ph},0,0) "
                    f"ON CONFLICT DO NOTHING",
                    (_cr["category_id"], _loc))

        # ── Passbook opening balances ──────────────────────────
        for _firm in FIRMS:
            conn.execute(
                f"INSERT INTO passbook_opening_balance "
                f"(firm, opening_amount, opening_date) VALUES ({ph}, 0, {ph}) "
                f"ON CONFLICT DO NOTHING",
                (_firm, _today_iso))
            _ob = conn.execute(
                f"SELECT opening_amount, opening_date "
                f"FROM passbook_opening_balance WHERE firm={ph}",
                (_firm,)).fetchone()
            _exists = conn.execute(
                f"SELECT 1 FROM passbook_entries WHERE firm={ph} AND source_type={ph}",
                (_firm, SRC_OPENING)).fetchone()
            if _ob and not _exists:
                _oa    = float(_ob["opening_amount"])
                _otype = 'Credit' if _oa >= 0 else 'Debit'
                conn.execute(
                    f"INSERT INTO passbook_entries "
                    f"(firm, entry_date, details, amount, txn_type, source_type) "
                    f"VALUES ({ph}, {ph}, 'Opening Balance', {ph}, {ph}, {ph})",
                    (_firm, _ob["opening_date"], abs(_oa), _otype, SRC_OPENING))

        # ── Cash in Hand opening ───────────────────────────────
        conn.execute(
            f"INSERT INTO cash_in_hand_opening (id, opening_amount, opening_date, notes) "
            f"VALUES (1, 0, {ph}, '') ON CONFLICT DO NOTHING",
            (_today_iso,))
        _cih_ob = conn.execute(
            "SELECT opening_amount, opening_date FROM cash_in_hand_opening WHERE id=1"
        ).fetchone()
        _cih_exists = conn.execute(
            f"SELECT 1 FROM cash_in_hand_entries WHERE source_type={ph}",
            (SRC_CIH_OPENING,)).fetchone()
        if _cih_ob and not _cih_exists:
            _coa   = float(_cih_ob["opening_amount"])
            _ctype = 'Credit' if _coa >= 0 else 'Debit'
            conn.execute(
                f"INSERT INTO cash_in_hand_entries "
                f"(entry_date, details, amount, txn_type, source_type) "
                f"VALUES ({ph}, {ph}, {ph}, {ph}, {ph})",
                (_cih_ob["opening_date"], 'Opening Balance',
                 abs(_coa), _ctype, SRC_CIH_OPENING))

        conn.commit()
        if _own:
            _schema_initialized = True
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
    from datetime import datetime, timedelta
    cutoff = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d %H:%M:%S")
    ph = _db_ph(conn)
    conn.execute(
        f"DELETE FROM stock_history WHERE recorded_at < {ph}",
        (cutoff,)
    )


def deduct_stock_for_sale(conn, bill_items: list, sale_date, customer_name: str) -> list:
    _VALID_LOCS = {"Transport", "Shop", "Anandpuri", "Cold"}
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
            WHERE sl.good_id = %s AND sl.location = %s AND sl.batch_label = ''
        """, (gid, loc)).fetchone()
        _cs_b_bags = float(_cs_row["bags"] or 0) if _cs_row else 0
        _cs_b_kg   = float(_cs_row["quantity_kg"] or 0) if _cs_row else 0.0
        _cs_cat    = _cs_row["category_name"] if _cs_row else ""
        _cs_good   = _cs_row["good_name"] if _cs_row else ""

        conn.execute(
            "UPDATE stock_levels SET bags=bags-%s, quantity_kg=quantity_kg-%s "
            "WHERE good_id=%s AND location=%s AND batch_label=''",
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
            "WHERE good_id=%s AND location=%s AND batch_label=''", (gid, loc)).fetchone()
        if updated and (updated["bags"] < 0 or updated["quantity_kg"] < 0):
            warnings.append(
                f"⚠ {it['goods']} at {loc} is now below zero "
                f"(bags: {updated['bags']}, kg: {updated['quantity_kg']:.1f}) "
                "— update stock register.")

    return warnings


def reverse_stock_for_bill_delete(conn, transaction_id: int) -> None:
    """
    Restore stock levels for every item on a bill being deleted.
    Must be called inside an open transaction (before the DELETE on transaction_items).
    Works with both SQLite (tests) and PostgreSQL (production).
    """
    import sqlite3 as _sqlite3
    ph = "?" if isinstance(conn, _sqlite3.Connection) else "%s"

    items = conn.execute(
        f"SELECT type_of_goods, bags, quantity, collection_point "
        f"FROM transaction_items WHERE transaction_id={ph}",
        (transaction_id,)
    ).fetchall()

    for item in items:
        good_name = item["type_of_goods"]
        bags      = int(item["bags"] or 0)
        qty_kg    = float(item["quantity"] or 0)
        loc       = item["collection_point"] or ""

        if not loc or not good_name:
            continue

        good_row = conn.execute(
            f"SELECT good_id FROM stock_goods WHERE good_name={ph}",
            (good_name,)
        ).fetchone()

        if not good_row:
            continue

        gid = good_row["good_id"]

        before_row = conn.execute(
            f"SELECT sl.bags AS b, sl.quantity_kg AS k, "
            f"sg.good_name AS gn, sc.category_name AS cn "
            f"FROM stock_levels sl "
            f"JOIN stock_goods sg ON sl.good_id = sg.good_id "
            f"JOIN stock_categories sc ON sg.category_id = sc.category_id "
            f"WHERE sl.good_id={ph} AND sl.location={ph} AND sl.batch_label=''",
            (gid, loc)
        ).fetchone()

        conn.execute(
            f"UPDATE stock_levels "
            f"SET bags=bags+{ph}, quantity_kg=quantity_kg+{ph} "
            f"WHERE good_id={ph} AND location={ph} AND batch_label=''",
            (bags, qty_kg, gid, loc)
        )

        if before_row:
            try:
                log_stock_change(
                    conn,
                    before_row["cn"], before_row["gn"], loc,
                    "Bill Delete Reversal",
                    float(before_row["b"] or 0),
                    float(before_row["b"] or 0) + bags,
                    float(before_row["k"] or 0),
                    float(before_row["k"] or 0) + qty_kg,
                    source=f"Bill delete: txn {transaction_id}"
                )
            except Exception:
                import logging
                logging.getLogger(__name__).warning(
                    "stock_history log failed during bill delete reversal for txn %s",
                    transaction_id
                )


_ALL_STOCK_LOCATIONS = ["Transport", "Shop", "Anandpuri", "Cold"]


def ensure_good_at_all_locations(conn, good_id: int) -> None:
    """
    Insert zero-quantity stock_levels rows for all 4 locations if missing.
    Idempotent — safe to call multiple times.
    """
    ph = _db_ph(conn)
    for loc in _ALL_STOCK_LOCATIONS:
        conn.execute(
            f"INSERT INTO stock_levels "
            f"(good_id, location, batch_label, bags, quantity_kg) "
            f"VALUES ({ph}, {ph}, '', 0, 0) ON CONFLICT DO NOTHING",
            (good_id, loc),
        )


def ensure_category_at_all_locations(conn, category_id: int) -> None:
    """
    Insert zero unidentified_stock rows for all 4 locations if missing.
    Idempotent — safe to call multiple times.
    """
    ph = _db_ph(conn)
    for loc in _ALL_STOCK_LOCATIONS:
        conn.execute(
            f"INSERT INTO unidentified_stock "
            f"(category_id, location, bags, quantity_kg) "
            f"VALUES ({ph}, {ph}, 0, 0) ON CONFLICT DO NOTHING",
            (category_id, loc),
        )
