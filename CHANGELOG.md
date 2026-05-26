# Changelog

All notable changes to the S P Spices Business Diary are documented here.

## [Unreleased] — Polish Batch (2026-05-26)

### Added
- Named constants for every magic number: `BROKERAGE_RATE_PCT`, `DAYS_PER_YEAR_30_360`, `DAYS_PER_MONTH_30_360`, `STOCK_HISTORY_RETENTION_DAYS`, `LOGIN_ATTEMPTS_RETENTION_DAYS`, `LOGIN_MAX_FAILED_ATTEMPTS`, `LOGIN_LOCKOUT_WINDOW_MINUTES`
- Docstrings on all previously undocumented public utility functions

### Changed
- `check_lockout` and `purge_old_login_attempts` default parameters now reference the named constants (no behaviour change)
- `auth.py` lockout-message text reads minutes directly from `LOGIN_LOCKOUT_WINDOW_MINUTES` instead of back-computing from seconds

### Fixed
- POLISH 1: Drop legacy `audit_log.created_at` column on PostgreSQL via `ALTER TABLE … DROP COLUMN IF EXISTS`

### Tests
- Strengthened: `test_passbook_view_conn_reuse` — replaced `assert df is not None` with `isinstance(df, pd.DataFrame)` plus column-set checks
- Strengthened: `test_log_audit_ts_is_set` — replaced `row["ts"] != ""` with ISO-format regex match

---

## [Batch 5] — 2026-05-26 (commit b840f99)

### Added
- `_ensure_schema_once()` — `@cache_resource` wrapper so schema setup runs exactly once per Streamlit session, not on every page rerun
- DB-backed login lockout via `login_attempts` table; `check_lockout()`, `record_login_attempt()`, `purge_old_login_attempts()` helpers wired into `auth.py`
- `login_attempts` table + 3 performance indexes (`idx_stock_history_recorded_at`, `idx_audit_log_table_ts`, `idx_login_attempts_ip_time`) in SQLite DDL and PostgreSQL migration
- Stock history pagination: period selector (7/30/60/90 d / All), row-count selector (50–500), total-count caption; Python-computed cutoff replaces PostgreSQL-specific `to_char(NOW() - INTERVAL …)` expression
- `_now_ts()` helper returns ISO-format string, avoiding Python 3.12 deprecated sqlite3 datetime adapter
- 12 `ALTER TABLE ADD COLUMN IF NOT EXISTS` statements for `payments`, `passbook_entries`, `passbook_opening_balance`, `customer_transactions`, `audit_log`

### Fixed
- AUDIT-022: `log_audit()` uses explicit `_now_ts()` string instead of DB-level `datetime('now')` default; cross-dialect
- AUDIT-035: Brute-force lockout now persists across browser sessions via DB, not just session state
- AUDIT-038: SQLite DDL now includes all 12 columns added in Batch 3/4 migrations; `audit_log.changed_at` renamed to `ts`

### Tests (193 total)
- 5 tests: `test_ensure_schema_once.py`
- 8 tests: `test_login_lockout.py`
- 17 tests: `test_schema_parity.py`
- 6 tests: `test_audit_log_timestamps.py`
- 6 tests: `test_stock_history_pagination.py`
- 10 tests: `test_no_sqlite_specific_sql.py`

---

## [Batch 4] — 2026-05-26 (commit a8a52d8)

### Fixed
- AUDIT-007: ISDA 30/360 day-count with all 4 month-end rules (last-Feb, d1=31, d2=31)
- AUDIT-015: Interest waiver threshold is inclusive (`remaining ≤ 7.5%`, not `<`)
- AUDIT-036: Passbook allocation uses `passbook_entry_id` FK; unlink query uses FK instead of fragile note LIKE match
- AUDIT-028: Running balance computed via `SUM OVER` SQL window function; no Python loop accumulation
- N+1 query eliminated in Customer Payments ledger — payments and items fetched once before render loop

### Tests (143 total)
- 6 tests: `test_passbook_source_matching.py`
- 5 tests: `test_ledger_batch_payments.py`

---

## [Batch 3] — 2026-05-25 (commit 73f3c94)

### Fixed
- AUDIT-016: SQL `IN` clause built with proper parameterised placeholders via `execute_in_clause()`; no string interpolation
- AUDIT-019: `ensure_good_at_all_locations()` / `ensure_category_at_all_locations()` seed all 4 locations on new good/category creation
- AUDIT-022: `purge_old_stock_history()` uses Python-computed cutoff string instead of `datetime('now', '-30 days')`
- AUDIT-030: Lookup caches (`get_all_brokers_cached` etc.) invalidated after writes via `invalidate_lookup_cache()`
- AUDIT-036: `compute_passbook_view` / `compute_cash_view` accept `conn=` to avoid opening a second connection

---

## [Batch 2] — 2026-05-25 (commit 9775b0d)

### Fixed
- AUDIT-004: Unhandled exceptions in bill-save path now surface via `st.error` instead of silently passing
- AUDIT-009: Overpayment no longer creates a negative passbook debit; supplemental credit is inserted instead
- AUDIT-011: `lastrowid` replaced with dialect-safe `SELECT last_insert_rowid()` / `RETURNING id`
- AUDIT-012: RTGS passbook entries deleted before vendor entry to avoid FK violations on bill delete
- AUDIT-013: Passbook and cash-in-hand opening rows seeded with partial UNIQUE index to prevent duplicate sync

---

## [Batch 1] — 2026-05-25 (commit 8fac701)

### Fixed
- AUDIT-001: Stock levels restored correctly on bill delete via `reverse_stock_for_bill_delete()`
- AUDIT-002: Same-location stock transfer rejected with `CHECK` constraint and page-level guard
- AUDIT-003: Double-submit prevention via session-state debounce flag on payment forms

---

## [Earlier] — 2026-05-24

- Live-ping connections to survive Neon serverless hibernation (commit 0be33d9)
- Enter-key support and auto-close on all forms; stale connection fix (commit 57a68ff)
- Neon schema reference added; stale migration scripts removed (commit 85fb348)
- 54-test suite restored; Fly.io deployment files removed (commit e3899f9)
- Batch DB queries and cached lookups for faster page loads (commit 4463f02)
- Interest over-due flag suppressed when outstanding < 7.5% of bill (commit 3f9ec5d)
