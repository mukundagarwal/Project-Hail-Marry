-- SP Spices — complete schema for Neon PostgreSQL
-- Run this in Neon's SQL Editor before migrating data

CREATE TABLE IF NOT EXISTS brokers (
    broker_id   SERIAL PRIMARY KEY,
    broker_name TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS stock_categories (
    category_id   SERIAL PRIMARY KEY,
    category_name TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS stock_goods (
    good_id     SERIAL PRIMARY KEY,
    category_id INTEGER NOT NULL REFERENCES stock_categories(category_id),
    good_name   TEXT NOT NULL,
    UNIQUE(category_id, good_name)
);

CREATE TABLE IF NOT EXISTS vendors (
    vendor_id   SERIAL PRIMARY KEY,
    vendor_name TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS customer_transactions (
    transaction_id    SERIAL PRIMARY KEY,
    broker_id         INTEGER REFERENCES brokers(broker_id),
    customer_name     TEXT NOT NULL,
    date              TEXT NOT NULL,
    total_amount      DOUBLE PRECISION NOT NULL DEFAULT 0,
    payment_received  DOUBLE PRECISION NOT NULL DEFAULT 0,
    payment_status    TEXT NOT NULL DEFAULT 'Pending',
    calc_status       TEXT NOT NULL DEFAULT 'Pending',
    grace_days        INTEGER DEFAULT 35,
    discount_pct      DOUBLE PRECISION DEFAULT 0,
    brokerage_applied INTEGER NOT NULL DEFAULT 0,
    interest_rate     DOUBLE PRECISION DEFAULT 24.0,
    final_settlement  DOUBLE PRECISION DEFAULT 0,
    interest_amount   DOUBLE PRECISION DEFAULT 0,
    discount_amount   DOUBLE PRECISION DEFAULT 0,
    settlement_date   TEXT,
    notes             TEXT DEFAULT '',
    created_at        TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS payments (
    payment_id     SERIAL PRIMARY KEY,
    transaction_id INTEGER NOT NULL REFERENCES customer_transactions(transaction_id) ON DELETE CASCADE,
    payment_date   TEXT NOT NULL,
    amount         DOUBLE PRECISION NOT NULL,
    method         TEXT NOT NULL,
    note           TEXT DEFAULT '',
    days_from_start  INTEGER DEFAULT 0,
    interest_charged DOUBLE PRECISION DEFAULT 0,
    cheque_number    TEXT DEFAULT '',
    cheque_date    TEXT DEFAULT '',
    deposit_firm   TEXT DEFAULT '',
    cheque_status  TEXT DEFAULT 'Pending',
    created_at     TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS transaction_items (
    item_id        SERIAL PRIMARY KEY,
    transaction_id INTEGER NOT NULL REFERENCES customer_transactions(transaction_id) ON DELETE CASCADE,
    goods          TEXT NOT NULL,
    bags           INTEGER DEFAULT 0,
    qty            DOUBLE PRECISION DEFAULT 0,
    rate           DOUBLE PRECISION DEFAULT 0,
    bag_rate       DOUBLE PRECISION DEFAULT 0,
    freight        DOUBLE PRECISION DEFAULT 0,
    line_total     DOUBLE PRECISION DEFAULT 0,
    collection_point TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS vendor_entries (
    entry_id    SERIAL PRIMARY KEY,
    vendor_id   INTEGER NOT NULL REFERENCES vendors(vendor_id),
    entry_date  TEXT NOT NULL,
    ledger_type TEXT NOT NULL,
    firm        TEXT,
    entry_kind  TEXT NOT NULL,
    particulars TEXT NOT NULL,
    amount      DOUBLE PRECISION NOT NULL,
    good_id     INTEGER REFERENCES stock_goods(good_id),
    bags        INTEGER DEFAULT 0,
    quantity_kg DOUBLE PRECISION DEFAULT 0,
    note        TEXT DEFAULT '',
    created_at  TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS passbook_opening_balance (
    id             SERIAL PRIMARY KEY,
    firm           TEXT NOT NULL UNIQUE,
    opening_amount DOUBLE PRECISION NOT NULL DEFAULT 0,
    opening_date   TEXT NOT NULL,
    notes          TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS passbook_entries (
    entry_id      SERIAL PRIMARY KEY,
    firm          TEXT NOT NULL,
    entry_date    TEXT NOT NULL,
    details       TEXT NOT NULL,
    amount        DOUBLE PRECISION NOT NULL,
    txn_type      TEXT NOT NULL,
    source_type   TEXT DEFAULT 'Manual',
    source_id     INTEGER,
    cheque_number TEXT DEFAULT '',
    cheque_date   TEXT DEFAULT '',
    cheque_status TEXT DEFAULT 'Pending',
    created_at    TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS cash_in_hand_opening (
    id             INTEGER PRIMARY KEY DEFAULT 1,
    opening_amount DOUBLE PRECISION NOT NULL DEFAULT 0,
    opening_date   TEXT NOT NULL,
    notes          TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS cash_in_hand_entries (
    entry_id    SERIAL PRIMARY KEY,
    entry_date  TEXT NOT NULL,
    details     TEXT NOT NULL,
    amount      DOUBLE PRECISION NOT NULL,
    txn_type    TEXT NOT NULL,
    source_type TEXT DEFAULT 'Manual',
    source_id   INTEGER,
    created_at  TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS stock_levels (
    level_id    SERIAL PRIMARY KEY,
    good_id     INTEGER NOT NULL REFERENCES stock_goods(good_id),
    location    TEXT NOT NULL,
    bags        DOUBLE PRECISION DEFAULT 0,
    quantity_kg DOUBLE PRECISION DEFAULT 0,
    UNIQUE(good_id, location)
);

CREATE TABLE IF NOT EXISTS stock_transfers (
    transfer_id   SERIAL PRIMARY KEY,
    transfer_date TEXT NOT NULL,
    good_id       INTEGER NOT NULL REFERENCES stock_goods(good_id),
    from_location TEXT NOT NULL,
    to_location   TEXT NOT NULL,
    bags_moved    DOUBLE PRECISION DEFAULT 0,
    kg_moved      DOUBLE PRECISION DEFAULT 0,
    note          TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS stock_history (
    history_id    SERIAL PRIMARY KEY,
    recorded_at   TEXT NOT NULL,
    category_name TEXT NOT NULL,
    good_name     TEXT NOT NULL,
    location      TEXT NOT NULL,
    change_type   TEXT NOT NULL,
    bags_before   DOUBLE PRECISION DEFAULT 0,
    bags_after    DOUBLE PRECISION DEFAULT 0,
    bags_change   DOUBLE PRECISION DEFAULT 0,
    kg_before     DOUBLE PRECISION DEFAULT 0,
    kg_after      DOUBLE PRECISION DEFAULT 0,
    kg_change     DOUBLE PRECISION DEFAULT 0,
    source        TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS unidentified_stock (
    id          SERIAL PRIMARY KEY,
    category_id INTEGER NOT NULL REFERENCES stock_categories(category_id),
    location    TEXT NOT NULL,
    bags        DOUBLE PRECISION DEFAULT 0,
    quantity_kg DOUBLE PRECISION DEFAULT 0,
    UNIQUE(category_id, location)
);

CREATE TABLE IF NOT EXISTS audit_log (
    log_id     SERIAL PRIMARY KEY,
    table_name TEXT NOT NULL,
    record_id  INTEGER,
    action     TEXT NOT NULL,
    old_value  JSONB,
    new_value  JSONB,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

-- Performance indexes
CREATE INDEX IF NOT EXISTS idx_payments_transaction_id         ON payments(transaction_id);
CREATE INDEX IF NOT EXISTS idx_transaction_items_transaction_id ON transaction_items(transaction_id);
CREATE INDEX IF NOT EXISTS idx_customer_transactions_broker_id  ON customer_transactions(broker_id);
CREATE INDEX IF NOT EXISTS idx_customer_transactions_date       ON customer_transactions(date);
CREATE INDEX IF NOT EXISTS idx_customer_transactions_status     ON customer_transactions(payment_status);
CREATE INDEX IF NOT EXISTS idx_customer_transactions_name       ON customer_transactions(customer_name);
CREATE INDEX IF NOT EXISTS idx_vendor_entries_vendor_id         ON vendor_entries(vendor_id);
CREATE INDEX IF NOT EXISTS idx_vendor_entries_good_id           ON vendor_entries(good_id) WHERE good_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_passbook_entries_firm            ON passbook_entries(firm);
CREATE INDEX IF NOT EXISTS idx_cih_entries_source_type          ON cash_in_hand_entries(source_type);
CREATE INDEX IF NOT EXISTS idx_stock_transfers_good_id          ON stock_transfers(good_id);
CREATE INDEX IF NOT EXISTS idx_stock_transfers_date             ON stock_transfers(transfer_date);
CREATE INDEX IF NOT EXISTS idx_audit_log_table_record           ON audit_log(table_name, record_id);
