"""
Database connection, schema, constants, and audit helper.
All other modules import constants and DB helpers from here.
"""

import sqlite3
import json
from datetime import date as _date

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
SRC_CIH_OPENING   = "CIHOpening"   # distinct from SRC_OPENING to prevent cross-table confusion

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

DB_PATH = "spices.db"


def get_conn():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def ensure_schema(conn=None):
    _own = conn is None
    if _own:
        conn = get_conn()
    try:
        cur = conn.cursor()

        cur.execute('''CREATE TABLE IF NOT EXISTS brokers (
            broker_id   INTEGER PRIMARY KEY AUTOINCREMENT,
            broker_name TEXT NOT NULL UNIQUE)''')

        cur.execute('''CREATE TABLE IF NOT EXISTS customer_transactions (
            transaction_id  INTEGER PRIMARY KEY AUTOINCREMENT,
            broker_id       INTEGER,
            customer_name   TEXT NOT NULL,
            date            TEXT NOT NULL,
            type_of_goods   TEXT,
            bags            INTEGER DEFAULT 0,
            quantity        REAL    DEFAULT 0,
            rate            REAL    DEFAULT 0,
            total_amount    REAL    DEFAULT 0,
            payment_status  TEXT    DEFAULT "Pending",
            payment_method  TEXT    DEFAULT "Cash",
            interest_rate_pct  REAL    DEFAULT 0,
            discount_pct       REAL    DEFAULT 0,
            discount_amount    REAL    DEFAULT 0,
            brokerage_applied  INTEGER DEFAULT 0,
            brokerage_amount   REAL    DEFAULT 0,
            final_settlement   REAL    DEFAULT NULL,
            calc_status        TEXT    DEFAULT "Pending",
            brokerage_paid     TEXT    DEFAULT "Unpaid",
            FOREIGN KEY (broker_id) REFERENCES brokers (broker_id))''')

        cur.execute('''CREATE TABLE IF NOT EXISTS transaction_items (
            item_id           INTEGER PRIMARY KEY AUTOINCREMENT,
            transaction_id    INTEGER NOT NULL,
            type_of_goods     TEXT,
            bags              INTEGER DEFAULT 0,
            bag_rate          REAL    DEFAULT 0,
            quantity          REAL    DEFAULT 0,
            rate              REAL    DEFAULT 0,
            freight           REAL    DEFAULT 0,
            collection_point  TEXT    DEFAULT "",
            line_total        REAL    DEFAULT 0,
            FOREIGN KEY (transaction_id)
                REFERENCES customer_transactions(transaction_id) ON DELETE CASCADE)''')

        cur.execute('''CREATE TABLE IF NOT EXISTS payments (
            payment_id      INTEGER PRIMARY KEY AUTOINCREMENT,
            transaction_id  INTEGER NOT NULL,
            payment_date    TEXT    NOT NULL,
            amount          REAL    NOT NULL,
            method          TEXT    DEFAULT "Cash",
            note            TEXT    DEFAULT "",
            days_from_start INTEGER DEFAULT 0,
            interest_charged REAL   DEFAULT 0,
            FOREIGN KEY (transaction_id)
                REFERENCES customer_transactions(transaction_id) ON DELETE CASCADE)''')

        # ── migrate customer_transactions
        cur.execute("PRAGMA table_info(customer_transactions)")
        existing_ct = {c[1] for c in cur.fetchall()}
        for col, sql in {
            "bags":              "ALTER TABLE customer_transactions ADD COLUMN bags INTEGER DEFAULT 0",
            "quantity":          "ALTER TABLE customer_transactions ADD COLUMN quantity REAL DEFAULT 0",
            "rate":              "ALTER TABLE customer_transactions ADD COLUMN rate REAL DEFAULT 0",
            "interest_rate_pct": "ALTER TABLE customer_transactions ADD COLUMN interest_rate_pct REAL DEFAULT 0",
            "discount_pct":      "ALTER TABLE customer_transactions ADD COLUMN discount_pct REAL DEFAULT 0",
            "discount_amount":   "ALTER TABLE customer_transactions ADD COLUMN discount_amount REAL DEFAULT 0",
            "brokerage_applied": "ALTER TABLE customer_transactions ADD COLUMN brokerage_applied INTEGER DEFAULT 0",
            "brokerage_amount":  "ALTER TABLE customer_transactions ADD COLUMN brokerage_amount REAL DEFAULT 0",
            "final_settlement":  "ALTER TABLE customer_transactions ADD COLUMN final_settlement REAL DEFAULT NULL",
            "calc_status":       "ALTER TABLE customer_transactions ADD COLUMN calc_status TEXT DEFAULT 'Pending'",
            "brokerage_paid":    "ALTER TABLE customer_transactions ADD COLUMN brokerage_paid TEXT DEFAULT 'Unpaid'",
            "payment_received":  "ALTER TABLE customer_transactions ADD COLUMN payment_received REAL DEFAULT 0",
            "grace_days":        "ALTER TABLE customer_transactions ADD COLUMN grace_days INTEGER DEFAULT 0",
            "days_overdue":      "ALTER TABLE customer_transactions ADD COLUMN days_overdue INTEGER DEFAULT 0",
            "interest_amount":   "ALTER TABLE customer_transactions ADD COLUMN interest_amount REAL DEFAULT 0",
            "bill_sent":         "ALTER TABLE customer_transactions ADD COLUMN bill_sent REAL DEFAULT NULL",
        }.items():
            if col not in existing_ct:
                cur.execute(sql)

        # ── migrate customer_transactions — cheque + passbook fields
        for col, sql in {
            "cheque_number": "ALTER TABLE customer_transactions ADD COLUMN cheque_number TEXT DEFAULT NULL",
            "cheque_date":   "ALTER TABLE customer_transactions ADD COLUMN cheque_date   TEXT DEFAULT NULL",
            "deposit_firm":  "ALTER TABLE customer_transactions ADD COLUMN deposit_firm  TEXT DEFAULT NULL",
        }.items():
            if col not in existing_ct:
                cur.execute(sql)

        # ── migrate payments — cheque + passbook fields
        cur.execute("PRAGMA table_info(payments)")
        existing_pmts = {c[1] for c in cur.fetchall()}
        for col, sql in {
            "cheque_number": "ALTER TABLE payments ADD COLUMN cheque_number TEXT DEFAULT NULL",
            "cheque_date":   "ALTER TABLE payments ADD COLUMN cheque_date   TEXT DEFAULT NULL",
            "deposit_firm":  "ALTER TABLE payments ADD COLUMN deposit_firm  TEXT DEFAULT NULL",
        }.items():
            if col not in existing_pmts:
                cur.execute(sql)

        # ── migrate transaction_items
        cur.execute("PRAGMA table_info(transaction_items)")
        existing_ti = {c[1] for c in cur.fetchall()}
        for col, sql in {
            "freight":          "ALTER TABLE transaction_items ADD COLUMN freight REAL DEFAULT 0",
            "collection_point": "ALTER TABLE transaction_items ADD COLUMN collection_point TEXT DEFAULT ''",
        }.items():
            if col not in existing_ti:
                cur.execute(sql)

        cur.execute('''CREATE TABLE IF NOT EXISTS audit_log (
            log_id     INTEGER PRIMARY KEY AUTOINCREMENT,
            ts         TEXT    NOT NULL DEFAULT (datetime('now','localtime')),
            table_name TEXT    NOT NULL,
            record_id  INTEGER NOT NULL,
            action     TEXT    NOT NULL,
            old_value  TEXT,
            new_value  TEXT)''')

        # ── Stock Register tables ──────────────────────────────
        cur.execute('''CREATE TABLE IF NOT EXISTS stock_categories (
            category_id   INTEGER PRIMARY KEY AUTOINCREMENT,
            category_name TEXT NOT NULL UNIQUE)''')

        cur.execute('''CREATE TABLE IF NOT EXISTS stock_goods (
            good_id     INTEGER PRIMARY KEY AUTOINCREMENT,
            category_id INTEGER NOT NULL,
            good_name   TEXT NOT NULL,
            UNIQUE(category_id, good_name),
            FOREIGN KEY (category_id) REFERENCES stock_categories(category_id))''')

        cur.execute('''CREATE TABLE IF NOT EXISTS stock_levels (
            level_id    INTEGER PRIMARY KEY AUTOINCREMENT,
            good_id     INTEGER NOT NULL,
            location    TEXT NOT NULL,
            bags        INTEGER DEFAULT 0,
            quantity_kg REAL    DEFAULT 0,
            UNIQUE(good_id, location),
            FOREIGN KEY (good_id) REFERENCES stock_goods(good_id))''')

        cur.execute('''CREATE TABLE IF NOT EXISTS stock_transfers (
            transfer_id   INTEGER PRIMARY KEY AUTOINCREMENT,
            transfer_date TEXT NOT NULL,
            good_id       INTEGER NOT NULL,
            from_location TEXT,
            to_location   TEXT,
            bags_moved    INTEGER DEFAULT 0,
            kg_moved      REAL    DEFAULT 0,
            note          TEXT    DEFAULT '',
            FOREIGN KEY (good_id) REFERENCES stock_goods(good_id))''')

        cur.execute('''CREATE TABLE IF NOT EXISTS stock_history (
            history_id    INTEGER PRIMARY KEY AUTOINCREMENT,
            recorded_at   TEXT NOT NULL,
            category_name TEXT NOT NULL,
            good_name     TEXT NOT NULL,
            location      TEXT NOT NULL,
            change_type   TEXT NOT NULL,
            bags_before   REAL NOT NULL DEFAULT 0,
            bags_after    REAL NOT NULL DEFAULT 0,
            bags_change   REAL NOT NULL DEFAULT 0,
            kg_before     REAL NOT NULL DEFAULT 0,
            kg_after      REAL NOT NULL DEFAULT 0,
            kg_change     REAL NOT NULL DEFAULT 0,
            source        TEXT DEFAULT '')''')

        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_stock_history_date
            ON stock_history(recorded_at DESC)
        """)

        cur.execute('''CREATE TABLE IF NOT EXISTS unidentified_stock (
            unid_id       INTEGER PRIMARY KEY AUTOINCREMENT,
            category_id   INTEGER NOT NULL,
            location      TEXT NOT NULL,
            bags          REAL NOT NULL DEFAULT 0,
            quantity_kg   REAL NOT NULL DEFAULT 0,
            UNIQUE(category_id, location),
            FOREIGN KEY (category_id)
                REFERENCES stock_categories(category_id)
        )''')

        # Pre-seed stock categories and goods
        _STOCK_SEEDS = {
            "Arecanut":     ARECA_NUT_GOODS,
            "Black Pepper": BLACK_PEPPER_GOODS,
        }
        _STOCK_LOCATIONS = ["Transport", "Shop", "Anandpuri"]
        for cat_name, goods_list in _STOCK_SEEDS.items():
            cur.execute(
                "INSERT OR IGNORE INTO stock_categories (category_name) VALUES (?)", (cat_name,))
            row = cur.execute(
                "SELECT category_id FROM stock_categories WHERE category_name=?",
                (cat_name,)).fetchone()
            if row:
                for g in goods_list:
                    cur.execute(
                        "INSERT OR IGNORE INTO stock_goods (category_id, good_name) VALUES (?,?)",
                        (row[0], g))

        # Initialize stock_levels for every good × every location
        for (gid,) in cur.execute("SELECT good_id FROM stock_goods").fetchall():
            for loc in _STOCK_LOCATIONS:
                cur.execute(
                    "INSERT OR IGNORE INTO stock_levels "
                    "(good_id, location, bags, quantity_kg) VALUES (?,?,0,0)", (gid, loc))

        # Seed unidentified_stock for every category × location
        cur.execute("""
            INSERT OR IGNORE INTO unidentified_stock
                (category_id, location, bags, quantity_kg)
            SELECT sc.category_id, loc.location, 0, 0
            FROM stock_categories sc
            CROSS JOIN (
                SELECT 'Transport' AS location UNION
                SELECT 'Shop' UNION
                SELECT 'Anandpuri'
            ) loc
        """)

        # ── Vendor tables (also created by ensure_vendor_schema in Vendor Payments)
        cur.execute('''CREATE TABLE IF NOT EXISTS vendors (
            vendor_id   INTEGER PRIMARY KEY AUTOINCREMENT,
            vendor_name TEXT NOT NULL UNIQUE)''')
        cur.execute('''CREATE TABLE IF NOT EXISTS vendor_entries (
            entry_id    INTEGER PRIMARY KEY AUTOINCREMENT,
            vendor_id   INTEGER NOT NULL,
            entry_date  TEXT    NOT NULL,
            ledger_type TEXT    NOT NULL,
            firm        TEXT,
            entry_kind  TEXT    NOT NULL,
            particulars TEXT    NOT NULL,
            amount      REAL    NOT NULL,
            good_id     INTEGER,
            bags        INTEGER DEFAULT 0,
            quantity_kg REAL    DEFAULT 0,
            note        TEXT    DEFAULT '',
            created_at  TEXT    DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (vendor_id) REFERENCES vendors(vendor_id),
            FOREIGN KEY (good_id)   REFERENCES stock_goods(good_id))''')

        # ── migrate vendor_entries
        cur.execute("PRAGMA table_info(vendor_entries)")
        _ve_cols = {r["name"] for r in cur.fetchall()}
        if "note" not in _ve_cols:
            cur.execute(
                "ALTER TABLE vendor_entries ADD COLUMN note TEXT DEFAULT ''")

        # ── Passbook tables ────────────────────────────────────────
        cur.execute('''CREATE TABLE IF NOT EXISTS passbook_entries (
            entry_id      INTEGER PRIMARY KEY AUTOINCREMENT,
            firm          TEXT NOT NULL,
            entry_date    TEXT NOT NULL,
            details       TEXT NOT NULL,
            amount        REAL NOT NULL,
            txn_type      TEXT NOT NULL,
            cheque_number TEXT DEFAULT NULL,
            cheque_status TEXT DEFAULT NULL,
            source_type   TEXT DEFAULT 'Manual',
            source_id     INTEGER DEFAULT NULL,
            created_at    TEXT DEFAULT CURRENT_TIMESTAMP)''')

        cur.execute('''CREATE TABLE IF NOT EXISTS passbook_opening_balance (
            firm           TEXT PRIMARY KEY,
            opening_amount REAL NOT NULL DEFAULT 0,
            opening_date   TEXT NOT NULL,
            notes          TEXT DEFAULT '')''')

        _today_iso = _date.today().isoformat()
        for _firm in FIRMS:
            cur.execute(
                "INSERT OR IGNORE INTO passbook_opening_balance "
                "(firm, opening_amount, opening_date) VALUES (?,0,?)",
                (_firm, _today_iso))
            _ob = cur.execute(
                "SELECT opening_amount, opening_date "
                "FROM passbook_opening_balance WHERE firm=?", (_firm,)).fetchone()
            _exists = cur.execute(
                "SELECT 1 FROM passbook_entries WHERE firm=? AND source_type=?",
                (_firm, SRC_OPENING)).fetchone()
            if _ob and not _exists:
                _oa   = float(_ob[0])
                _otype = 'Credit' if _oa >= 0 else 'Debit'
                cur.execute(
                    "INSERT INTO passbook_entries "
                    "(firm,entry_date,details,amount,txn_type,source_type) "
                    "VALUES (?,?,'Opening Balance',?,?,?)",
                    (_firm, _ob[1], abs(_oa), _otype, SRC_OPENING))

        # ── Cash in Hand tables ───────────────────────────────────
        cur.execute('''CREATE TABLE IF NOT EXISTS cash_in_hand_entries (
            entry_id      INTEGER PRIMARY KEY AUTOINCREMENT,
            entry_date    TEXT NOT NULL,
            details       TEXT NOT NULL,
            amount        REAL NOT NULL,
            txn_type      TEXT NOT NULL,
            source_type   TEXT DEFAULT 'Manual',
            source_id     INTEGER DEFAULT NULL,
            created_at    TEXT DEFAULT CURRENT_TIMESTAMP)''')

        cur.execute('''CREATE TABLE IF NOT EXISTS cash_in_hand_opening (
            id             INTEGER PRIMARY KEY CHECK (id = 1),
            opening_amount REAL NOT NULL DEFAULT 0,
            opening_date   TEXT NOT NULL,
            notes          TEXT DEFAULT '')''')

        cur.execute(
            "INSERT OR IGNORE INTO cash_in_hand_opening "
            "(id, opening_amount, opening_date, notes) VALUES (1, 0, ?, '')",
            (_today_iso,))

        # Migrate existing Opening rows to CIHOpening (idempotent)
        cur.execute("""
            UPDATE cash_in_hand_entries
               SET source_type = 'CIHOpening'
             WHERE source_type = 'Opening'
        """)

        _cih_ob = cur.execute(
            "SELECT opening_amount, opening_date "
            "FROM cash_in_hand_opening WHERE id=1").fetchone()
        _cih_exists = cur.execute(
            "SELECT 1 FROM cash_in_hand_entries WHERE source_type=?",
            (SRC_CIH_OPENING,)).fetchone()
        if _cih_ob and not _cih_exists:
            _coa   = float(_cih_ob[0])
            _ctype = 'Credit' if _coa >= 0 else 'Debit'
            cur.execute(
                "INSERT INTO cash_in_hand_entries "
                "(entry_date,details,amount,txn_type,source_type) VALUES (?,?,?,?,?)",
                (_cih_ob[1], 'Opening Balance', abs(_coa), _ctype, SRC_CIH_OPENING))

        # Partial unique indexes — prevent duplicate auto-sync entries while
        # allowing multiple Manual entries (source_id IS NULL)
        cur.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS uq_cih_source
            ON cash_in_hand_entries(source_type, source_id)
            WHERE source_id IS NOT NULL
        """)
        cur.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS uq_pb_source
            ON passbook_entries(source_type, source_id)
            WHERE source_id IS NOT NULL
        """)

        # ── DB-level CHECK triggers ──────────────────────────────
        cur.execute("""
            CREATE TRIGGER IF NOT EXISTS chk_payment_status_insert
            BEFORE INSERT ON customer_transactions
            FOR EACH ROW
            WHEN NEW.payment_status NOT IN ('Pending', 'Paid', 'Partial')
             AND NEW.payment_status IS NOT NULL
            BEGIN
                SELECT RAISE(ABORT,
                    'payment_status must be Pending, Paid, or Partial');
            END
        """)
        cur.execute("""
            CREATE TRIGGER IF NOT EXISTS chk_payment_status_update
            BEFORE UPDATE OF payment_status ON customer_transactions
            FOR EACH ROW
            WHEN NEW.payment_status NOT IN ('Pending', 'Paid', 'Partial')
             AND NEW.payment_status IS NOT NULL
            BEGIN
                SELECT RAISE(ABORT,
                    'payment_status must be Pending, Paid, or Partial');
            END
        """)
        cur.execute("""
            CREATE TRIGGER IF NOT EXISTS chk_total_amount_insert
            BEFORE INSERT ON customer_transactions
            FOR EACH ROW
            WHEN NEW.total_amount < 0
            BEGIN
                SELECT RAISE(ABORT, 'total_amount cannot be negative');
            END
        """)
        cur.execute("""
            CREATE TRIGGER IF NOT EXISTS chk_total_amount_update
            BEFORE UPDATE OF total_amount ON customer_transactions
            FOR EACH ROW
            WHEN NEW.total_amount < 0
            BEGIN
                SELECT RAISE(ABORT, 'total_amount cannot be negative');
            END
        """)
        cur.execute("""
            CREATE TRIGGER IF NOT EXISTS chk_bill_sent_insert
            BEFORE INSERT ON customer_transactions
            FOR EACH ROW
            WHEN NEW.bill_sent IS NOT NULL AND NEW.bill_sent <= 0
            BEGIN
                SELECT RAISE(ABORT, 'bill_sent must be > 0 when set');
            END
        """)
        cur.execute("""
            CREATE TRIGGER IF NOT EXISTS chk_bill_sent_update
            BEFORE UPDATE OF bill_sent ON customer_transactions
            FOR EACH ROW
            WHEN NEW.bill_sent IS NOT NULL AND NEW.bill_sent <= 0
            BEGIN
                SELECT RAISE(ABORT, 'bill_sent must be > 0 when set');
            END
        """)
        cur.execute("""
            CREATE TRIGGER IF NOT EXISTS chk_pb_txn_type_insert
            BEFORE INSERT ON passbook_entries
            FOR EACH ROW
            WHEN NEW.txn_type NOT IN ('Credit', 'Debit')
            BEGIN
                SELECT RAISE(ABORT, 'txn_type must be Credit or Debit');
            END
        """)
        cur.execute("""
            CREATE TRIGGER IF NOT EXISTS chk_pb_txn_type_update
            BEFORE UPDATE OF txn_type ON passbook_entries
            FOR EACH ROW
            WHEN NEW.txn_type NOT IN ('Credit', 'Debit')
            BEGIN
                SELECT RAISE(ABORT, 'txn_type must be Credit or Debit');
            END
        """)
        cur.execute("""
            CREATE TRIGGER IF NOT EXISTS chk_cih_txn_type_insert
            BEFORE INSERT ON cash_in_hand_entries
            FOR EACH ROW
            WHEN NEW.txn_type NOT IN ('Credit', 'Debit')
            BEGIN
                SELECT RAISE(ABORT, 'txn_type must be Credit or Debit');
            END
        """)
        cur.execute("""
            CREATE TRIGGER IF NOT EXISTS chk_cih_txn_type_update
            BEFORE UPDATE OF txn_type ON cash_in_hand_entries
            FOR EACH ROW
            WHEN NEW.txn_type NOT IN ('Credit', 'Debit')
            BEGIN
                SELECT RAISE(ABORT, 'txn_type must be Credit or Debit');
            END
        """)
        cur.execute("""
            CREATE TRIGGER IF NOT EXISTS chk_vendor_bill_amount_insert
            BEFORE INSERT ON vendor_entries
            FOR EACH ROW
            WHEN NEW.entry_kind = 'Bill' AND NEW.amount >= 0
            BEGIN
                SELECT RAISE(ABORT,
                    'Bill entries must have negative amount');
            END
        """)
        cur.execute("""
            CREATE TRIGGER IF NOT EXISTS chk_vendor_bill_amount_update
            BEFORE UPDATE OF amount ON vendor_entries
            FOR EACH ROW
            WHEN NEW.entry_kind = 'Bill' AND NEW.amount >= 0
            BEGIN
                SELECT RAISE(ABORT,
                    'Bill entries must have negative amount');
            END
        """)
        cur.execute("""
            CREATE TRIGGER IF NOT EXISTS chk_vendor_payment_amount_insert
            BEFORE INSERT ON vendor_entries
            FOR EACH ROW
            WHEN NEW.entry_kind = 'Payment' AND NEW.amount <= 0
            BEGIN
                SELECT RAISE(ABORT,
                    'Payment entries must have positive amount');
            END
        """)
        cur.execute("""
            CREATE TRIGGER IF NOT EXISTS chk_vendor_payment_amount_update
            BEFORE UPDATE OF amount ON vendor_entries
            FOR EACH ROW
            WHEN NEW.entry_kind = 'Payment' AND NEW.amount <= 0
            BEGIN
                SELECT RAISE(ABORT,
                    'Payment entries must have positive amount');
            END
        """)

        conn.commit()
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
        VALUES (?, ?, ?, ?)
        ON CONFLICT(category_id, location) DO UPDATE SET
            bags        = bags        + excluded.bags,
            quantity_kg = quantity_kg + excluded.quantity_kg
    """, (category_id, location,
          round(float(bags), 2),
          round(float(quantity_kg), 2)))


def reverse_unidentified_stock(conn, category_id: int, location: str,
                                bags: float, quantity_kg: float) -> None:
    conn.execute("""
        UPDATE unidentified_stock
           SET bags        = bags        - ?,
               quantity_kg = quantity_kg - ?
         WHERE category_id = ? AND location = ?
    """, (round(float(bags), 2),
          round(float(quantity_kg), 2),
          category_id, location))


def log_audit(conn, table_name: str, record_id: int, action: str,
              old_value: dict = None, new_value: dict = None):
    conn.execute(
        "INSERT INTO audit_log (table_name,record_id,action,old_value,new_value) VALUES (?,?,?,?,?)",
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
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
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
        WHERE recorded_at < datetime('now', '-30 days')
    """)


def deduct_stock_for_sale(conn, bill_items: list, sale_date, customer_name: str) -> list:
    _VALID_LOCS = {"Transport", "Shop", "Anandpuri"}
    warnings = []

    for it in bill_items:
        good_row = conn.execute(
            "SELECT good_id FROM stock_goods WHERE good_name=?",
            (it["goods"],)).fetchone()

        if not good_row:
            warnings.append(
                f"'{it['goods']}' not found in Stock Register — stock not updated.")
            continue

        gid = good_row[0]
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
            WHERE sl.good_id = ? AND sl.location = ?
        """, (gid, loc)).fetchone()
        _cs_b_bags = float(_cs_row["bags"] or 0) if _cs_row else 0
        _cs_b_kg   = float(_cs_row["quantity_kg"] or 0) if _cs_row else 0.0
        _cs_cat    = _cs_row["category_name"] if _cs_row else ""
        _cs_good   = _cs_row["good_name"] if _cs_row else ""

        conn.execute(
            "UPDATE stock_levels SET bags=bags-?, quantity_kg=quantity_kg-? "
            "WHERE good_id=? AND location=?",
            (bags_sold, kg_sold, gid, loc))

        conn.execute(
            "INSERT INTO stock_transfers "
            "(transfer_date,good_id,from_location,to_location,bags_moved,kg_moved,note) "
            "VALUES (?,?,?,'Sold',?,?,?)",
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
            "WHERE good_id=? AND location=?", (gid, loc)).fetchone()
        if updated and (updated[0] < 0 or updated[1] < 0):
            warnings.append(
                f"⚠ {it['goods']} at {loc} is now below zero "
                f"(bags: {updated[0]}, kg: {updated[1]:.1f}) — update stock register.")

    return warnings
