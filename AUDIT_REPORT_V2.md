# S P Spices — Verification Audit Report (Round 2)
**Date:** 2026-05-26
**Auditor:** Claude Code (claude-sonnet-4-6)
**Scope:** Full project post 5 batches + polish — all production files read from scratch
**Approach:** Fresh independent audit, no prior trust assumed

---

## Executive Summary

This audit covers the complete codebase of the S P Spices Business Management Diary after five rounds of fixes and a polish pass. The application has matured significantly: critical data integrity flaws from the first audit have been addressed, the authentication and lockout system is properly implemented, the interest calculation engine correctly follows ISDA 30/360 rules, and the test suite has grown to 26 files with strong coverage of business-critical paths.

Three areas still require attention before this application can be considered production-ready. First, the Vendor Payments page has a schema drift issue — `_ensure_vendor_schema_once()` silently creates a vendor_entries table with a `note` column and `created_at` column that do not exist in the SQLite DDL used by tests, meaning vendor-entry tests would silently miss coverage of those columns. Second, the settlement "Save Settlement" code path uses `P_disp` (the value typed into the form) rather than the fresh DB total when deciding how much has already been received via cash — this can result in a missed cash-in-hand supplemental entry when P_disp is stale. Third, the `reverse_stock_for_bill_delete` function uses PostgreSQL-only `%s` placeholders directly in `log_stock_change`, but `log_stock_change` itself hard-codes `%s` in its INSERT — this means the function would raise a sqlite3 error in tests if the try/except around `log_stock_change` weren't catching it. The try/except saves correctness in tests but hides the fact that the call is expected to succeed in production.

**Total issues: 23 (Critical: 2 | High: 4 | Medium: 8 | Low: 6 | Info: 3)**
- Initial audit found: 14C, 24H, 29M, 19L, 6I
- Current (after fixes): 2C, 4H, 8M, 6L, 3I
- Top 3 remaining concerns:
  1. [C-01] Cash-in-hand settlement gap uses stale `P_disp` instead of fresh DB total
  2. [C-02] `log_stock_change` hard-codes `%s` placeholders — breaks in SQLite test path
  3. [H-01] `_ensure_vendor_schema_once` in page 2 creates tables outside the canonical DDL, causing schema drift
- **Overall grade: B+**

---

## Quality Rating

| Dimension | Grade | Justification |
|-----------|-------|---------------|
| Correctness | B+ | ISDA 30/360 and waiver threshold correct; two subtle bugs remain in CIH gap calc and log_stock_change dialect |
| Data Integrity | A- | Bill delete, vendor delete, passbook sync all atomic; FK passbook_entry_id correctly used |
| Security | A | Bcrypt, lockout, session TTL, parameterized queries, h() escaping all in place |
| Performance | A- | Batch queries on page render, cache with invalidation, connection pool with live ping |
| Reliability | B+ | Connection retry with 5 attempts, error surfaces to user; one suppressed except in require_login |
| Code Quality | B+ | Clean separation of concerns; some duplication in payment-sync code across save/edit paths |
| Test Coverage | A- | 26 test files, strong assertions; a few tests simulate logic inline rather than exercising real code |
| Schema Design | A- | Partial unique indexes, FK constraints, batch_label compound key; minor drift in vendor DDL |
| Documentation | B | Docstrings on key functions; inline comments explain business rules; some functions undocumented |
| Maintainability | B+ | Well-structured; page files are large (2000+ lines) making review difficult |
| **OVERALL** | **B+** | Production-quality with two critical bugs to fix before launch |

---

## Findings

### 🔴 CRITICAL

**C-01 — Stale `P_disp` used in Cash settlement CIH gap calculation**
- **File:line:** `pages/1_Customer_Payments.py:1937`
- **Description:** When a settlement is saved with method="Cash", the code computes `_sett_amount = round(float(P_disp), 2)` where `P_disp` comes from `st.session_state.get(f"preview_P_{tid}", 0)` — the value the user typed into "Payment Already Received" at preview time. This is then compared against `_existing_cih` (the sum of CIH entries already linked to this transaction). If the user typed a different number than what was actually paid (e.g., previewing mid-session), the gap calculation is wrong. More critically, if intermediate Cash payments were already auto-logged via CIH (with `source_type=SRC_CUSTOMER_CASH, source_id=payment_id`), the gap query at line 1929 correctly sums them — but the settlement total uses the stale P_disp rather than reading fresh from the DB.
- **Reproduction:** Log two cash payments of ₹3000 each on a ₹10,000 bill. Preview settlement with P=₹6000. Change a payment amount externally. Save settlement — the gap is now calculated incorrectly.
- **Impact:** CIH entries can be over-created (supplemental entry when none needed) or under-created (no entry when one is needed). Financial books become inaccurate.
- **Fix:** Replace `_sett_amount = round(float(P_disp), 2)` with a fresh DB read: `_sett_amount = round(float(conn.execute("SELECT COALESCE(SUM(amount),0) AS v FROM payments WHERE transaction_id=%s AND method='Cash'", (tid,)).fetchone()["v"]), 2)`.

**C-02 — `log_stock_change` hard-codes PostgreSQL `%s` placeholders**
- **File:line:** `utils/db.py:917-931`
- **Description:** `log_stock_change` contains a hard-coded `%s` INSERT statement. It is called from `reverse_stock_for_bill_delete` (which correctly detects SQLite vs PostgreSQL via `ph = "?" if isinstance(conn, _sqlite3.Connection) else "%s"`), but the call to `log_stock_change` inside that function at line 1083 passes the `conn` but `log_stock_change` itself always uses `%s`. In SQLite test environments this raises `sqlite3.OperationalError`. The surrounding `except Exception:` at line 1093 silently catches this, so stock history logging is silently skipped during every test that exercises `reverse_stock_for_bill_delete`. The same applies to `deduct_stock_for_sale` at line 1013.
- **Impact:** Stock history is not logged during tests. In production (PostgreSQL) this works, but the test suite provides no coverage of stock history in bill-delete scenarios. The silent swallowing of a real exception also masks any future breakage.
- **Fix:** Make `log_stock_change` dialect-aware by accepting a `conn` parameter and using `_db_ph(conn)` for the INSERT placeholder, or pass `ph` from callers. Alternatively, add `add_unidentified_stock` and `reverse_unidentified_stock` to the same fix (they also hard-code `%s`).

---

### 🟠 HIGH

**H-01 — `_ensure_vendor_schema_once` creates tables outside canonical DDL, causing schema drift**
- **File:line:** `pages/2_Vendor_Payments.py:44-72`
- **Description:** Page 2 defines its own `@st.cache_resource` function that creates `vendors` and `vendor_entries` tables. This vendor_entries DDL includes columns `note TEXT DEFAULT ''` and `created_at TEXT DEFAULT CURRENT_TIMESTAMP` that are NOT in the `_sqlite_ddl()` function in `utils/db.py`. The canonical SQLite DDL for vendor_entries does not have a `note` column or `created_at` column. This means: (a) any test seeding vendor_entries rows that include these columns will fail silently if using the conftest.py db fixture, (b) schema_parity tests do not check vendor_entries columns, (c) future migrations may miss these columns. Additionally, `ensure_vendor_schema_once` opens/closes its own connection but does not use the same retry/pool logic as `get_conn()`.
- **Impact:** Potential `no such column: note` errors if test code is updated to use vendor entry notes. Schema drift makes maintenance harder.
- **Fix:** Move the vendor DDL entirely into `_sqlite_ddl()` in `utils/db.py`, including the `note` and `created_at` columns. Remove `_ensure_vendor_schema_once` from page 2.

**H-02 — Vendor bill edit Mode B (unidentified stock) does not handle negative-stock confirmation**
- **File:line:** `pages/2_Vendor_Payments.py:590-629`
- **Description:** The `_do_confirm` (negative-stock warning before destructive edit) is only triggered when the old bill had an identified good (`_old_good_id_val is not None`). If the old bill was Mode B (unidentified stock: bags > 0, good_id = None) and the edit would result in negative unidentified stock, no warning is shown and the reversal proceeds without confirmation.
- **Impact:** User can accidentally drive unidentified stock negative without a confirmation step.
- **Fix:** Extend the confirmation check to also compute the projected unidentified stock level and show a warning when it would go negative.

**H-03 — `_ensure_vendor_schema_once` missing `Cold` location in unidentified_stock seeding**
- **File:line:** `pages/2_Vendor_Payments.py:175-179`
- **Description:** When adding a new category in the bill popover's "Add new category" expander, the code inserts unidentified_stock rows for `["Transport", "Shop", "Anandpuri"]` but omits "Cold". However, the canonical `_ALL_STOCK_LOCATIONS` in `utils/db.py` includes "Cold". This means new categories added from the Vendor Payments page will not have a Cold unidentified_stock row.
- **Impact:** If Cold stock needs to be tracked for a newly-added category, it will fail with a constraint violation or missing data. Stock Register may show incomplete data.
- **Fix:** Use `ensure_category_at_all_locations` (already defined in `utils/db.py`) instead of the manual inline loop.

**H-04 — Settlement CIH sync inserts with `source_id=tid` (transaction id), not a payment id**
- **File:line:** `pages/1_Customer_Payments.py:1943-1951`
- **Description:** The cash settlement path inserts a supplemental CIH entry with `source_type=SRC_CUSTOMER_CASH, source_id=tid` (the transaction_id). But the `cash_in_hand_entries` table has `UNIQUE(source_type, source_id)`. Intermediate cash payments insert with `source_type=SRC_CUSTOMER_CASH, source_id=payment_id`. Since transaction_id and payment_id share an INTEGER sequence in PostgreSQL (distinct sequences actually) but logically could overlap, if any prior cash payment for transaction 5 exists with source_id=5 (payment_id=5), this supplemental insert would collide with the first payment's entry. More directly: the supplemental entry uses tid as source_id, so if the same transaction has a settlement run twice (recalculate), the second run cannot insert the supplemental entry (unique constraint) and the gap is silently missed.
- **Impact:** Second recalculation of a cash settlement may silently fail to update the CIH gap entry.
- **Fix:** Use `ON CONFLICT(source_type, source_id) DO UPDATE SET amount=amount+EXCLUDED.amount` or use a distinct source_type (e.g. `SRC_CUSTOMER_CASH_SETT`) for settlement-path entries.

---

### 🟡 MEDIUM

**M-01 — `reverse_bill_stock` in Vendor Payments uses hard-coded `batch_label` assumption**
- **File:line:** `pages/2_Vendor_Payments.py:101-123`
- **Description:** `reverse_bill_stock` queries `stock_levels WHERE good_id=%s AND location='Transport'` without filtering on `batch_label=''`. If a good has multiple batch rows (batch_label != ''), this query returns multiple rows and `.fetchone()` silently uses only the first. The reversal UPDATE does not include `batch_label=''` either, so it would update the wrong batch row.
- **Impact:** Stock reversal picks wrong batch in multi-batch scenarios.
- **Fix:** Add `AND batch_label=''` to both the SELECT and UPDATE in `reverse_bill_stock`.

**M-02 — `add_unidentified_stock` and `reverse_unidentified_stock` hard-code `%s`**
- **File:line:** `utils/db.py:797-819`
- **Description:** Both functions use hard-coded `%s` placeholder (PostgreSQL-only). They are called from page code that is production-only, but also from test utilities via `_bill_popover`. Any test that exercises these through an SQLite connection will fail.
- **Impact:** Silent test failures; potential for missed coverage.
- **Fix:** Use `_db_ph(conn)` inside these functions, consistent with the rest of the module.

**M-03 — Payment edit form uses `with conn:` context manager inside a running `with conn:` block**
- **File:line:** `pages/1_Customer_Payments.py:1286`
- **Description:** The outer `conn = get_conn()` is opened at line 330 and wrapped in `try/finally: conn.close()`. Inside the ledger render loop, payment edits use `with conn:` which calls `commit()` (or `rollback()`) on the same connection object. This is correct as long as `_PgConn.__exit__` commits and doesn't close the connection when used as a nested context. However, `_PgConn.__exit__` calls `self.close()` at line 125, which would mark `_closed=True` and return the connection to the pool prematurely. Subsequent DB calls within the same `try` block would then fail because `self._closed=True` but `_pool.putconn` has already been called.
- **Impact:** After any `with conn:` block inside a page, subsequent DB calls on the same `conn` object silently fail or raise pool errors. This affects every payment save, delete, and edit inside the transaction loop.
- **Fix:** The `_PgConn.__exit__` should commit/rollback but NOT close the connection when used as a nested context manager. Either implement a `transaction()` context manager that doesn't close, or use explicit `conn.commit()` / `conn.rollback()` at call sites.

**M-04 — Session state `authenticated` is a simple boolean; no per-page re-check after timeout**
- **File:line:** `utils/auth.py:195`
- **Description:** `_check_session_expiry()` is called only inside `require_login()`, which is called at the top of each page. But within a single long page session (e.g., a user opens Customer Payments and leaves the tab open for 9 hours), session expiry is only rechecked on the next user interaction that triggers a Streamlit rerun. After 8 hours the session is expired but the user can still interact with widgets already rendered on screen. Subsequent submits will succeed because `require_login()` is not re-called mid-render.
- **Impact:** Low severity in practice, but a user whose session should have expired can still submit forms during the same render cycle.
- **Fix:** Call `_check_session_expiry()` or add a guard before any write operation, or reduce session TTL.

**M-05 — `_firm_summary` reads the newest balance from `df.iloc[0]` (newest-first order)**
- **File:line:** `utils/passbook_helpers.py:128-130`
- **Description:** `compute_passbook_view` returns DataFrame newest-first (`df.iloc[::-1]`). `_firm_summary` filters non-pending rows and takes `non_pend.iloc[0]["balance"]` — this is the most-recent non-pending balance, which is correct. However, `balance_is_pending` rows use a "hypothetical cleared" value. The firm summary shows the live balance, which correctly excludes pending cheques. This is correct behavior, but `pending_sum` at line 130 shows the face value of pending cheques, not the delta to cleared balance.
- **Impact:** Display-only, no data corruption. Pending cheque display slightly misleads.
- **Fix:** Document this in a comment; or show "(pending)" tag alongside the sum.

**M-06 — `save_bill_btn` debounce stores timestamp in session state but missing key for payment form**
- **File:line:** `pages/1_Customer_Payments.py:578-583`
- **Description:** Bill save debounce uses key `"_last_bill_save_ts"` (global per session, not per-transaction). This means if a user saves Bill for broker A, then immediately opens another broker and saves a bill within 3 seconds, the second save is blocked. While functional as a double-submit guard, it may frustrate legitimate rapid-entry workflows. More importantly, the payment save debounce at line 1433 uses `"_last_payment_save_ts"` — also global, not per-transaction. Two concurrent payment saves for different transactions are incorrectly serialized.
- **Impact:** UX friction; unlikely data corruption.
- **Fix:** Use transaction-scoped keys: `f"_last_bill_save_ts_{bid}"` and `f"_last_pmt_save_ts_{tid}"`.

**M-07 — INSERT into `customer_transactions` at bill save references columns that may not exist**
- **File:line:** `pages/1_Customer_Payments.py:607-619`
- **Description:** The INSERT at bill save references columns `type_of_goods`, `bags`, `quantity`, `rate`, `payment_method`, `cheque_number`, `cheque_date`, `deposit_firm`, `interest_rate_pct`, `days_overdue`. These are legacy columns that exist in the production PostgreSQL schema (via prior migrations) but are NOT defined in the SQLite `_sqlite_ddl()` in `utils/db.py`. The `customer_transactions` DDL in `_sqlite_ddl()` defines only: `transaction_id, broker_id, customer_name, date, total_amount, payment_status, payment_received, discount_pct, brokerage_applied, brokerage_paid, calc_status, final_settlement, interest_amount, bill_sent, grace_days, notes`. Columns like `type_of_goods`, `bags`, `quantity`, `rate`, `payment_method`, `interest_rate_pct`, `days_overdue` are not there.
- **Impact:** Bill-save tests against SQLite would fail if they exercised this INSERT path. Currently there is no test for the full bill-save path, hiding the discrepancy.
- **Fix:** Either add missing columns to `_sqlite_ddl()` for `customer_transactions`, or use `ON CONFLICT DO NOTHING` pattern. More importantly, write a test for the bill-save INSERT path.

**M-08 — `render_settlement_preview` references `res.get("discount_pct")` but key is not in result dict**
- **File:line:** `pages/1_Customer_Payments.py:161`
- **Description:** `calculate_final_settlement()` returns a dict that does NOT include `"discount_pct"` as a key (see `utils/calculator.py:176-196`). The preview template at line 161 renders `res.get("discount_pct","–")` which will always show "–" even when a discount percentage is selected. The caller sets `res["discount_pct"] = d_pct` at line 1783 before storing in session_state, but the `render_settlement_preview` function is called with the raw `res` from `calculate_final_settlement`, which does not have this key.
- **Impact:** Display bug: "Discount (–%)" shown instead of the correct percentage in the preview.
- **Fix:** Add `"discount_pct"` to the returned dict in `calculate_final_settlement()`, or always set it before calling `render_settlement_preview`.

---

### 🟢 LOW

**L-01 — `_PgConn.cursor()` returns a non-dict cursor, not used consistently**
- **File:line:** `utils/db.py:91-94`
- **Description:** `_PgConn.cursor()` returns a plain `psycopg2.extensions.cursor` (non-dict) described as "used by pandas.read_sql". However, `pg_read_sql` is used instead of `pd.read_sql` everywhere, and no call to `pd.read_sql` exists in the codebase. This method is dead code that could confuse future maintainers.
- **Fix:** Remove or document the method with a note that it is only needed if `pd.read_sql` is ever introduced.

**L-02 — `fmt_inr` uses `integer[:-2]` grouping which truncates single-character strings**
- **File:line:** `utils/formatters.py:61`
- **Description:** The Indian grouping loop does `integer[-2:]` then `integer = integer[:-2]`. If `integer` has exactly 1 digit remaining (e.g. "1,23,456" → last pass: `integer="1"`), `integer[-2:]` returns "1" and `integer = integer[:-2]` returns `""`. On next iteration `integer` is falsy, loop stops. This is correct, but the grouped result becomes `"1" + "," + ...`. Testing: `fmt_inr(1234567)` should produce "₹ 12,34,567.00" — the loop handles this correctly. However `fmt_inr(12345)` produces "₹ 12,345.00" — the first 2-digit group may be wrong. Minor edge cases only.
- **Impact:** Display only; rare edge case. Amounts above ₹1,000 are correctly grouped.
- **Fix:** Verify with `fmt_inr(100000)` → should be "₹ 1,00,000.00".

**L-03 — Broker ID is manually computed as `MAX(broker_id)+1` instead of using SERIAL**
- **File:line:** `pages/1_Customer_Payments.py:230-233`
- **Description:** Broker insert computes `next_id = int(max_row["mx"]) + 1` then inserts with explicit ID. This is a manual sequence that is not race-safe (two concurrent inserts could compute the same ID). The `brokers` table uses `SERIAL PRIMARY KEY` in PostgreSQL, so omitting the ID column would let the DB assign the next serial value automatically and safely.
- **Fix:** Remove the `MAX(broker_id)` lookup and omit `broker_id` from the INSERT; let the SERIAL do its job.

**L-04 — `check_lockout` passes `False` as a parameter to PostgreSQL, which treats Python `False` as the string `'False'` via psycopg2**
- **File:line:** `utils/db.py:881-883`
- **Description:** The query `WHERE success = %s` with parameter `False` works in psycopg2 because psycopg2 adapts Python `False` to PostgreSQL `false`. In SQLite (tests), the `success` column is `INTEGER NOT NULL DEFAULT 0`, and `False` is adapted to `0`, which also works. This is actually correct behavior — mentioning it as informational.
- **Impact:** None in practice.
- **Fix:** No action required; document the dialect compatibility.

**L-05 — Missing `_ensure_schema_once()` call in `pages/2_Vendor_Payments.py`**
- **File:line:** `pages/2_Vendor_Payments.py` (entire file)
- **Description:** Page 2 does not call `_ensure_schema_once()` from `utils.db`. Instead it calls its own `_ensure_vendor_schema_once()`. This means if a user navigates directly to Vendor Payments without visiting the Home page, the stock tables (stock_categories, stock_goods, stock_levels) may not be seeded. The vendor bill popover immediately queries `stock_categories`, which would return 0 rows.
- **Impact:** New deployment: first navigation to Vendor Payments may show "No categories" until user visits Home page.
- **Fix:** Add `_ensure_schema_once()` call at the top of page 2 (after `require_login`), same as pages 1, 3, 4.

**L-06 — `test_overpayment_no_negative_passbook.py` tests simulate guard logic in Python, not actual DB behavior**
- **File:line:** `tests/test_overpayment_no_negative_passbook.py:44-86`
- **Description:** Tests like `test_negative_final_balance_should_not_insert_cheque_passbook` compute the guard condition in plain Python and assert on the Python boolean — they do not actually exercise the page-level code. They are "specification tests" not integration tests. If the page code is changed to use `_pb_chq_amt >= 0` instead of `_pb_chq_amt > 0`, these tests would still pass.
- **Impact:** Test suite cannot catch regressions in the actual guard implementation.
- **Fix:** Refactor into a helper function in `utils/` that can be unit-tested, or write integration tests that call the actual DB-writing code path.

---

### ℹ️ INFO

**I-01 — `_days_30_360` ISDA rule ordering: Rule 1 (last-Feb) checked before Rule 3 (d1=31)**
- **File:line:** `utils/calculator.py:43-49`
- **Description:** The implementation checks last-Feb rules (1 & 2) before the d1=31 rule (3). For Feb 28 non-leap year: last-Feb is True so d1=30, then d1==31 is False (d1 is already 30), so Rule 3 doesn't fire. This is correct ISDA 30/360. The test suite confirms all 4 rules (test_isda_rule1 through test_isda_rule4_via_rule3_then_rule4 all pass). Verified correct.

**I-02 — Connection pool `maxconn=10` may be low for multi-user Streamlit Cloud**
- **File:line:** `utils/db.py:156-162`
- **Description:** With `maxconn=10` and Neon serverless auto-scaling, each Streamlit session can hold one connection from the pool. With 10+ concurrent users, new connections will block waiting for pool slots. Neon's free tier also has a concurrent connection limit (typically 10 compute units).
- **Impact:** Performance degradation under concurrent load.
- **Fix:** Consider increasing to `maxconn=20` or implementing connection-per-request pattern with short-lived connections.

**I-03 — `log_stock_change` uses `datetime.now()` instead of `_now_ts()` for consistency**
- **File:line:** `utils/db.py:922`
- **Description:** `log_stock_change` calls `datetime.now().strftime("%Y-%m-%d %H:%M:%S")` directly while `log_audit` calls `_now_ts()` which also calls `datetime.now()` but uses `"%Y-%m-%dT%H:%M:%S"` (with `T` separator). The stock_history table uses the space format; the audit_log table uses the ISO `T` format. This is intentional but could be unified.
- **Impact:** Cosmetic inconsistency.
- **Fix:** Document intentional difference; or unify under a configurable `_now_ts(sep=" ")`.

---

## Verification of Previous Fixes

### Batch 1

| Fix | Status | Notes |
|-----|--------|-------|
| reverse_stock_for_bill_delete loops all items | **VERIFIED** | db.py:1039-1098; iterates `items` list, UPDATE per item, log_stock_change per item |
| Same-location transfer CHECK constraint in SQLite DDL | **VERIFIED** | db.py:432-435 has `CHECK (from_location IS NULL OR to_location IS NULL OR from_location != to_location)` |
| Same-location guard in page validation | **VERIFIED** | test_same_location_transfer.py tests this |
| Debounce: session state checked before save, set after | **VERIFIED** | `_last_bill_save_ts` at lines 579-583; `_last_payment_save_ts` at lines 1432-1437 |

### Batch 2

| Fix | Status | Notes |
|-----|--------|-------|
| No bare `except: pass` in pages/ | **VERIFIED** | test_no_bare_excepts.py covers this; no violations found in manual review |
| Settlement overpayment guard: `_pb_chq_amt > 0` | **VERIFIED** | pages/1_Customer_Payments.py:1893-1894 |
| RETURNING transaction_id used (not lastrowid) | **VERIFIED** | Lines 614 and 1463 use `RETURNING` |
| RTGS cleanup: passbook DELETE before vendor_entries DELETE | **VERIFIED** | pages/2_Vendor_Payments.py:746-757; passbook/CIH deletion happens before `DELETE FROM vendor_entries` |
| Opening balance: partial UNIQUE index syntax correct | **VERIFIED** | `ON passbook_entries (firm) WHERE source_type = 'Opening'` — correct PostgreSQL partial index syntax |

### Batch 3

| Fix | Status | Notes |
|-----|--------|-------|
| execute_in_clause handles empty ids → returns None | **VERIFIED** | db.py:297-303; `if not ids: return None` |
| execute_in_clause builds correct placeholders | **VERIFIED** | db.py:300-303 |
| ensure_good_at_all_locations called from page on add | **VERIFIED** | pages/2_Vendor_Payments.py:212 calls it after new good insert |
| purge_old_stock_history uses Python timedelta | **VERIFIED** | db.py:936-937 |
| compute_passbook_view accepts conn= and uses _own_conn pattern | **VERIFIED** | passbook_helpers.py:36-37; `_own_conn = conn is None` |

### Batch 4

| Fix | Status | Notes |
|-----|--------|-------|
| _days_30_360 all 4 ISDA rules in order | **VERIFIED** | calculator.py:43-49; rules applied correctly, all tests pass |
| Waiver threshold: `max(remaining_principal, 0) <= _threshold` (inclusive) | **VERIFIED** | calculator.py:154 |
| payments.passbook_entry_id column exists in SQLite DDL | **VERIFIED** | db.py:340 |
| allocate_to_customer inserts with passbook_entry_id | **VERIFIED** | passbook_helpers.py:228-234 |
| SQL window function SUM OVER in passbook view | **VERIFIED** | passbook_helpers.py:43-52 |

### Batch 5

| Fix | Status | Notes |
|-----|--------|-------|
| _ensure_schema_once decorated with @_cache_resource | **VERIFIED** | db.py:741-742 |
| login_attempts table in _sqlite_ddl() | **VERIFIED** | db.py:473-479 |
| check_lockout used in require_login() before session lockout | **VERIFIED** | auth.py:218-226; DB lockout checked first, then session lockout |
| audit_log uses ts column with _now_ts() string | **VERIFIED** | db.py:838-840 |
| paginated stock history with Python-computed cutoff | **VERIFIED** | 3_Stock_Register.py uses timedelta-computed cutoff |

### Polish

| Fix | Status | Notes |
|-----|--------|-------|
| ALTER TABLE audit_log DROP COLUMN IF EXISTS created_at | **VERIFIED** | db.py:627 |
| BROKERAGE_RATE_PCT, DAYS_PER_YEAR_30_360, DAYS_PER_MONTH_30_360 in calculator.py | **VERIFIED** | calculator.py:8-11 imports all three |
| LOGIN_MAX_FAILED_ATTEMPTS, LOGIN_LOCKOUT_WINDOW_MINUTES in auth.py | **VERIFIED** | auth.py:14-15 imports from db.py |
| Docstrings on invalidate_lookup_cache | **VERIFIED** | db.py:264-266 |
| Docstrings on get_merged_goods | **VERIFIED** | db.py:752-754 |
| Docstrings on add_unidentified_stock | **VERIFIED** | db.py:795-796 |
| Docstrings on reverse_unidentified_stock | **VERIFIED** | db.py:809-810 |
| Docstrings on log_audit | **VERIFIED** | db.py:833-834 |
| Docstrings on log_stock_change | **VERIFIED** | db.py:910-915 |
| Docstrings on purge_old_stock_history | **VERIFIED** | db.py:934-935 |
| Docstrings on deduct_stock_for_sale | **VERIFIED** | db.py:946-950 |
| Docstrings on line_total | **VERIFIED** | calculator.py:63-64 |
| Docstrings on fmt_date | **VERIFIED** | formatters.py:19-20 |
| Docstrings on days_between | **VERIFIED** | formatters.py:68-69 |

---

## New Issues Discovered

Issues not identified in the first audit:

1. **C-01** (Critical): Stale `P_disp` in cash settlement CIH gap — the first audit flagged overpayment not creating negative entries, but the specific calculation using a stale form variable was not caught.

2. **C-02** (Critical): `log_stock_change` and sibling functions hard-code `%s` — the first audit focused on page-level SQL but missed that utility functions are called cross-dialect.

3. **M-07** (Medium): The bill-save INSERT references columns not in SQLite DDL (`type_of_goods`, `bags`, `quantity`, `rate`, `payment_method`, `interest_rate_pct`, `days_overdue`) — these are legacy columns from the pre-migration schema, still referenced in the INSERT but absent from the test DDL.

4. **M-08** (Medium): `render_settlement_preview` displays `res.get("discount_pct","–")` but the calculator's return dict does not include this key — persistent display bug in preview.

5. **H-03** (High): New category added from Vendor Payments page omits "Cold" from unidentified_stock seeding.

6. **M-03** (Medium): `with conn:` inside a longer `try/finally: conn.close()` block causes `conn.close()` to be called prematurely by `_PgConn.__exit__`, poisoning the connection for subsequent queries in the same page render cycle.

---

## Scenario Trace Results

**Scenario 1: Log a new bill with 2 items, collect from Shop**
- Bill INSERT uses RETURNING transaction_id ✓
- transaction_items INSERT per item ✓
- deduct_stock_for_sale called with correct items ✓
- Stock log wrapped in try/except (log failure non-fatal) ✓
- **Issue:** If two items are for the same good at the same location, two separate UPDATE stock_levels calls — no UPSERT logic. Second update applies to already-reduced stock. This is correct behavior (sequential deductions), but relies on DB ordering. ✓

**Scenario 2: Log a cash payment, then delete it**
- Payment INSERT with RETURNING payment_id ✓
- CIH entry INSERT with source_type=SRC_CUSTOMER_CASH, source_id=payment_id ✓
- Delete: CIH DELETE using (SRC_CUSTOMER_CASH, payment_id) ✓
- Passbook entry revert for auto-allocated payments ✓
- `with conn:` context used for atomicity — see M-03 for risk ⚠️

**Scenario 3: Calculate bill with discount, save settlement**
- Discount zeroes interest in calculator: `if disc_pct > 0: total_interest = 0.0` ✓
- Brokerage = 1% of total_bill when brok_flag ✓
- Settlement writes to customer_transactions with calc_status='Calculated' ✓
- Cheque passbook entry only when `_pb_chq_amt > 0` ✓
- Settlement not auto-setting payment_status='Paid' ✓

**Scenario 4: Passbook auto-allocation to customer**
- allocate_to_customer picks highest-outstanding by default ✓
- target_txn_id respected when provided ✓
- passbook_entry_id set on payment row ✓
- Payment status set to 'Partial' even on overpayment ✓
- source_type set to SRC_ALLOCATION on passbook entry ✓

**Scenario 5: Delete a bill with 3 items**
- reverse_stock_for_bill_delete loops all items ✓
- Passbook entries for cheque payments deleted ✓
- Allocation passbook entries reverted to Suspense ✓
- CIH entries for cash payments deleted ✓
- All in single `with conn:` block (atomic) ✓
- log_stock_change called but may fail silently in SQLite (C-02) ⚠️

**Scenario 6: Vendor RTGS payment, then delete**
- Payment INSERT with RETURNING entry_id ✓
- Passbook entry INSERT (Debit) with source_type=SRC_VENDOR_RTGS ✓
- Delete: passbook DELETE before vendor_entries DELETE ✓
- `with conn:` used for atomicity ✓

**Scenario 7: Login with 5 failed attempts**
- DB lockout check runs first (check_lockout) ✓
- Session-level counter also maintained ✓
- Record_login_attempt called on each failure ✓
- purge_old_login_attempts called on success ✓
- 8-hour session TTL enforced ✓

---

## Test Suite Quality Assessment

**Coverage estimate:** ~75-80% of business logic paths covered
- Interest calculation: Excellent (11 test cases including all ISDA rules and boundary conditions)
- Allocation cascade: Excellent (7 test cases including target_txn_id, overpayment, unlink)
- Bill delete stock reversal: Good (5 test cases)
- Login lockout: Good (8 test cases)
- Schema parity: Good (17 column/table assertions)
- Balance computation: Good (6 test cases including pending cheque exclusion)

**Weak tests identified:**

1. `test_overpayment_no_negative_passbook.py` — simulates guard logic in Python inline rather than calling actual page code. If page code changes the guard condition, these tests continue to pass (see L-06).

2. `test_concurrent_payment.py::test_debounce_logic_blocks_rapid_second_save` — tests the debounce in pure Python without any DB, session state, or Streamlit involvement. The actual debounce in the page uses `st.session_state`, not a local dict. This test proves the mathematical logic but cannot catch a regression if the page's debounce key is changed.

3. `test_balance_computation.py::test_per_step_rounding_no_drift` — tests a standalone loop, not the actual `compute_balances` or `compute_passbook_view` functions. The actual balance computation uses a SQL window function, not Python cumsum.

**Missing test scenarios:**

1. Full bill-save INSERT path (exercises the legacy columns issue in M-07)
2. Settlement with cash method — the CIH gap calculation (C-01)
3. Two sequential calls to `with conn:` inside the same page render cycle (M-03)
4. `_ensure_vendor_schema_once` schema vs `_sqlite_ddl` parity for vendor_entries
5. `reverse_bill_stock` with `batch_label != ''` (M-01)
6. `add_unidentified_stock` with SQLite connection (C-02 sibling)

---

## Comparison to Initial Audit

| Category | Initial | Current | Change |
|----------|---------|---------|--------|
| Critical | 14 | 2 | -12 |
| High | 24 | 4 | -20 |
| Medium | 29 | 8 | -21 |
| Low | 19 | 6 | -13 |
| Info | 6 | 3 | -3 |
| **Total** | **92** | **23** | **-69** |

The most significant improvements:
- Interest calculation engine is now correct (ISDA 30/360, waiver threshold, discount/interest mutual exclusivity)
- Data integrity atomicity: all bill/payment deletes properly clean up passbook and CIH
- Authentication: bcrypt, lockout, session TTL all properly implemented
- Test coverage grew from ~30% to ~75-80%
- `passbook_entry_id` FK-based payment matching correctly replaces note LIKE pattern

---

## Recommendations

### Must fix before production

1. **C-01** — Fix stale `P_disp` in cash settlement CIH gap (use fresh DB read)
2. **C-02** — Fix `log_stock_change` and sibling functions to use `_db_ph(conn)` for dialect portability
3. **M-03** — Fix `_PgConn.__exit__` to NOT call `self.close()` when used as a transaction context (or redesign the context manager pattern in pages)
4. **H-04** — Fix settlement CIH supplemental entry using `ON CONFLICT DO UPDATE` or distinct source_type to support recalculation

### Should fix soon

5. **H-01** — Move vendor DDL entirely into `_sqlite_ddl()`; remove `_ensure_vendor_schema_once`
6. **H-03** — Use `ensure_category_at_all_locations` in vendor bill popover (add Cold)
7. **L-05** — Add `_ensure_schema_once()` to page 2
8. **L-03** — Remove manual MAX(broker_id)+1 pattern; use SERIAL properly
9. **M-07** — Add missing legacy columns to SQLite DDL for `customer_transactions`, or remove them from the INSERT
10. **M-08** — Fix `render_settlement_preview` discount_pct display

### Nice to have

11. **H-02** — Add negative-stock confirmation for Mode B bill edits in Vendor Payments
12. **M-01** — Add `batch_label=''` filter in `reverse_bill_stock`
13. **M-06** — Use transaction-scoped debounce keys
14. **L-06** — Refactor overpayment guard into testable utility function
15. **I-02** — Consider increasing connection pool size for multi-user deployment
16. Write integration test for full bill-save path

---

## Positive Findings

1. **ISDA 30/360 implementation is correct** — all 6 rule tests pass, including leap-year Feb 29, both-last-Feb (360 days), and the chaining of rules 3+4.

2. **Interest waiver boundary is inclusive** — `max(remaining_principal, 0) <= _threshold` correctly waivers at exactly 7.5%.

3. **Atomic bill deletion** — `reverse_stock_for_bill_delete` + passbook cleanup + CIH cleanup + payments DELETE + transaction_items DELETE + customer_transactions DELETE all within a single `with conn:` block.

4. **passbook_entry_id FK** — `allocate_to_customer` correctly sets `passbook_entry_id` on the payment row, and `_unlink_allocation` uses it (not a fragile LIKE pattern).

5. **DB-backed lockout** — `check_lockout` queries `login_attempts` with a rolling time window before session-level lockout is applied. IP spoofing is partially mitigated.

6. **Live connection ping** — The pool's `SELECT 1` ping before returning a connection correctly handles Neon serverless hibernation (broken sockets).

7. **Window function for passbook balance** — SQL `SUM() OVER (ORDER BY ... ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)` is efficient and correct, with pending cheques excluded via CASE expression.

8. **Cache invalidation** — `invalidate_lookup_cache()` is correctly called after every broker/vendor/goods write.

9. **h() HTML escaping** — All user-supplied values rendered in f-string HTML are passed through `h()`. No XSS vectors found.

10. **`ensure_good_at_all_locations` called on good creation** — Page 2 calls this after inserting a new good, ensuring all 4 location rows exist.

---

## Final Verdict

The S P Spices Business Diary is **near production-ready** after five rounds of fixes. The critical data integrity issues, authentication weaknesses, and calculation bugs from the initial audit have been thoroughly addressed. The test suite is comprehensive and well-structured.

Two critical bugs remain that must be fixed before launch: the stale `P_disp` variable in cash-in-hand settlement sync (C-01) and the hard-coded `%s` placeholders in `log_stock_change` and sibling functions (C-02). The `_PgConn.__exit__` closing behavior (M-03) is a subtle architectural issue that likely causes silent failures in production today and must also be addressed.

**Recommended action:** Fix C-01, C-02, M-03, and H-04, then deploy. Address H-01, H-03, L-05 in the subsequent week.

**Production readiness: Conditional — fix the 2 Critical + M-03 first.**
