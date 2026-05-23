# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Run the app
streamlit run app.py

# Run all tests
pytest

# Run a single test file
pytest tests/test_calculator.py

# Run a single test by name
pytest tests/test_calculator.py::test_grace_period_zero_interest
```

## Architecture

This is a Streamlit multi-page app for S P Spices — a spice trading business that tracks customer payments, vendor payments, stock inventory, and bank passbooks.

**Entry point**: `app.py` — sets up page config, calls `ensure_schema()` to initialize/migrate the SQLite DB, and renders a navigation dashboard with buttons that call `st.switch_page()`.

**Pages** (`pages/`): Each page is a self-contained Streamlit module with its own `st.set_page_config()`. Sub-page routing within a page uses `st.session_state` (e.g., `page` in `1_Customer_Payments.py`, `pb_page` in `4_Passbook.py`).

**Shared utils** (`utils/`):
- `db.py` — single source of truth for all DB access, schema, constants, and business rules. `get_conn()` opens a WAL-mode SQLite connection. `ensure_schema()` is idempotent and handles all migrations via `ALTER TABLE ... IF NOT EXISTS` column checks. All business constants (interest rate, firm names, goods catalogues) live here.
- `calculator.py` — pure Python interest/settlement engine, no Streamlit imports. Uses the 30/360 day-count convention. `calculate_final_settlement()` accepts an optional `conn=` parameter to reuse an open connection.
- `passbook_helpers.py` — pure Python helpers for passbook balance computation and customer allocation logic, extracted so tests can import them without triggering Streamlit side-effects.
- `formatters.py` — pure Python display helpers (`fmt_inr` uses Indian lakh/crore grouping, `parse_slash_amount` accepts `/` as a decimal separator).
- `styles.py` — CSS and HTML brand bar injected via `st.markdown(unsafe_allow_html=True)`.

**Database**: SQLite file `spices.db` at the project root. The DB schema has two firms — `SP Spices` and `Mukund Traders` — tracked via the `FIRMS` constant. Passbook and Cash-in-Hand entries use `source_type`/`source_id` to link auto-generated rows back to their originating records, with partial unique indexes to prevent duplicate syncs.

**Tests**: All tests use the `db` fixture in `conftest.py` which creates a fresh in-memory SQLite DB with the full schema. Tests never hit the real `spices.db`. Business logic (calculator, allocation, balance) is tested by seeding the in-memory DB directly and calling the pure-Python util functions.

**Key business rules**:
- Interest accrues at 24% p.a. (30/360 convention) after a configurable grace period (default 35 days).
- Interest is waived when remaining principal < 7.5% of the bill total.
- Discount and interest are mutually exclusive: any `discount_pct > 0` zeroes interest.
- Vendor bill entries must have negative `amount`; payment entries must have positive `amount` (enforced by DB triggers).
- Passbook allocation never sets `payment_status = 'Paid'` — that is reserved for the "Calculate Bill" settlement step.
