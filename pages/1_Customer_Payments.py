"""
Customer Payments page — broker directory + ledger view.
Internal sub-page routing: st.session_state.page in ("customer", "ledger")
"""

import streamlit as st
import psycopg2
import pandas as pd
from datetime import datetime, date
from decimal import InvalidOperation
import streamlit.components.v1 as components
import time

from utils.db import (
    pg_read_sql,
    get_conn, ensure_schema, log_audit, deduct_stock_for_sale,
    get_merged_goods,
    INTEREST_RATE_PCT, DEFAULT_GRACE_DAYS, TXNS_PER_PAGE,
    GOODS_OPTIONS, ARECA_NUT_GOODS, BLACK_PEPPER_GOODS,
    FIRM_SP, FIRM_MT, FIRMS,
    SRC_MANUAL, SRC_VENDOR_RTGS,
    SRC_CUST_CHQ_TXN, SRC_CUST_CHQ_PMT,
    SRC_ALLOCATION, SRC_OPENING,
    SRC_CUSTOMER_CASH,
    CHQ_PENDING, CHQ_CLEARED,
)
from utils.formatters import fmt_date, fmt_inr, parse_slash_amount, days_between, h
from utils.calculator import calculate_final_settlement, line_total, _days_30_360
from utils.styles import APP_CSS, BRAND_BAR_HTML
from utils.auth import require_login

# ── Page config ────────────────────────────────────────────────
st.set_page_config(
    page_title="Customer Payments – S P Spices",
    page_icon="💰",
    layout="wide",
    initial_sidebar_state="collapsed",
)

require_login()
ensure_schema()

st.markdown(APP_CSS, unsafe_allow_html=True)
st.markdown(BRAND_BAR_HTML, unsafe_allow_html=True)

# ── Session state defaults ─────────────────────────────────────
for _k, _v in {
    "page": "customer", "selected_txns": [], "sum_intercept": [],
    "show_sum_report": False, "filter_mode": "Payment Due",
    "filter_single": date.today(), "filter_start": date.today(),
    "filter_end": date.today(), "bill_items": [], "bill_adding_more": False,
    "bill_customer": "", "bill_date": date.today(),
    "bill_pstatus": "Pending",
    "ledger_page": 0,
}.items():
    if _k not in st.session_state:
        st.session_state[_k] = _v

if st.session_state.page not in ("customer", "ledger"):
    st.session_state.page = "customer"


# ──────────────────────────────────────────────────────────────
#  CART RENDERER
# ──────────────────────────────────────────────────────────────

def render_cart():
    items = st.session_state.bill_items
    if not items:
        return 0.0
    html_out = ""
    grand = 0.0
    for i, it in enumerate(items):
        lt = it["line_total"]; grand += lt
        freight_str = f" &nbsp;+&nbsp; Freight {fmt_inr(it.get('freight',0))}" if it.get('freight',0) > 0 else ""
        cp_str = f" &nbsp;|&nbsp; {h(it.get('collection_point',''))}" if it.get('collection_point') else ""
        html_out += (f'<div class="cart-item"><div class="ci-header">'
                     f'<span class="ci-name">#{i+1} · {h(it["goods"])}</span>'
                     f'<span class="ci-total">{fmt_inr(lt)}</span></div>'
                     f'<div class="ci-detail">{it["bags"]} bags × {fmt_inr(it["bag_rate"])}/bag'
                     f' &nbsp;+&nbsp; {it["qty"]} Kg × {fmt_inr(it["rate"])}/Kg'
                     f'{freight_str}{cp_str}</div></div>')
    bt_rows = "".join(f'<div class="bt-row"><span>{h(it["goods"])}</span>'
                      f'<span>{fmt_inr(it["line_total"])}</span></div>' for it in items)
    html_out += (f'<div class="bill-total-box">{bt_rows}'
                 f'<div class="bt-total">Grand Total &nbsp; {fmt_inr(grand)}</div></div>')
    st.markdown(html_out, unsafe_allow_html=True)
    return grand


# ──────────────────────────────────────────────────────────────
#  SETTLEMENT PREVIEW
# ──────────────────────────────────────────────────────────────

def render_settlement_preview(res: dict) -> str:
    if not res:
        return "<p>No data.</p>"
    grace      = res.get("grace_days", 0)
    istart     = res.get("interest_start")
    istart_str = fmt_date(istart) if istart else "–"
    rows = ""
    for p in res["payments"]:
        in_grace  = p.get("within_grace", False)
        int_days  = p.get("days", 0)
        raw_days  = p.get("days_from_start", int_days)
        grace_tag = ('<span style="font-size:.7rem;color:#b89040;background:#1e1808;'
                     'border:1px solid #3a2e10;border-radius:4px;padding:1px 6px;margin-left:6px">'
                     '⏳ Within grace</span>') if in_grace else ""
        int_str   = "₹ 0.00 (grace)" if in_grace else "+ " + fmt_inr(p["interest"])
        rows += (f'<div class="tl-node"><div class="tl-dot paid"></div>'
                 f'<div class="tl-card"><div class="tl-date">💳 Payment · {fmt_date(p["date"])}{grace_tag}</div>'
                 f'<div class="tl-row">'
                 f'<div><div class="tl-label">Principal Paid</div><div class="tl-amount">{fmt_inr(p["amount"])}</div></div>'
                 f'<div><div class="tl-label">Days from Bill</div><div class="tl-days">{raw_days} days</div></div>'
                 f'<div><div class="tl-label">Interest Days</div><div class="tl-days">{int_days} days</div></div>'
                 f'<div><div class="tl-label">Interest</div><div class="tl-interest">{int_str}</div></div>'
                 f'<div><div class="tl-label">Method</div>'
                 f'<div class="tl-label" style="color:#c8bfa8">{h(p["method"])}</div></div>'
                 f'</div></div></div>')
    rem_html = ""
    if res["remaining_principal"] > 0:
        rem_d = res.get("remaining_int_days", res.get("remaining_days", 0))
        _waived = res.get("interest_waived", False)
        _waived_note = (
            f'<div style="font-size:.72rem;color:#b89040;margin-top:3px">'
            f'⚠ waived — outstanding &lt; 7.5% of bill</div>'
        ) if _waived else ""
        rem_html = (f'<div class="tl-node"><div class="tl-dot remaining"></div>'
                    f'<div class="tl-remaining"><div class="tl-remaining-label">'
                    f'📌 Remaining Balance (as of {fmt_date(res["settlement_date"])})</div>'
                    f'<div class="tl-row">'
                    f'<div><div class="tl-label">Outstanding</div>'
                    f'<div class="tl-amount" style="color:#6a9fd4">{fmt_inr(res["remaining_principal"])}</div></div>'
                    f'<div><div class="tl-label">Interest Days (from {istart_str})</div>'
                    f'<div class="tl-days">{rem_d} days</div></div>'
                    f'<div><div class="tl-label">Interest on Remainder</div>'
                    f'<div class="tl-interest">+ {fmt_inr(res["remaining_interest"])}{_waived_note}</div></div>'
                    f'</div></div></div>')
    formula = (f"I = P × ({res['rate']}% ÷ 100) × (Days ÷ 360) "
               f"| Grace: {grace} days → interest starts {istart_str}")
    _fbd = res["final_balance_due"]
    if _fbd < -0.01:
        _final_color = "#6a9fd4"
        _final_label = "= Amount to Return (Overpaid)"
    elif abs(_fbd) < 0.01:
        _final_color = "#f0ebe0"
        _final_label = "= Final Balance Due"
    else:
        _final_color = "#8dd87a"
        _final_label = "= Final Balance Due"
    summary = (f'<div class="bill-breakdown" style="margin-top:1.2rem">'
               f'<div class="bb-row"><span class="bb-label">Total Bill</span>'
               f'<span class="bb-val">{fmt_inr(res["total_bill"])}</span></div>'
               f'<div class="bb-row bb-neg"><span class="bb-label">Payments Made</span>'
               f'<span class="bb-val">{fmt_inr(res["total_paid"])}</span></div>'
               f'<div class="bb-row bb-neg"><span class="bb-label">Discount ({res.get("discount_pct","–")}%)</span>'
               f'<span class="bb-val">{fmt_inr(res["discount_amount"])}</span></div>'
               f'<div class="bb-row bb-neg"><span class="bb-label">Brokerage (1%)</span>'
               f'<span class="bb-val">{fmt_inr(res["brokerage_amount"])}</span></div>'
               f'<div class="bb-row bb-pos"><span class="bb-label">Total Interest</span>'
               f'<span class="bb-val">+ {fmt_inr(res["total_interest"])}</span></div>'
               f'<div class="bb-row bb-total"><span class="bb-label">{_final_label}</span>'
               f'<span class="bb-val" style="color:{_final_color}">{fmt_inr(_fbd)}</span></div></div>')
    return (f'<div class="info-chip">📐 {formula}</div>'
            f'<div class="timeline-wrap">{rows}{rem_html}</div>{summary}')


# ──────────────────────────────────────────────────────────────
#  PAGE: CUSTOMER – BROKER LIST
# ──────────────────────────────────────────────────────────────

if st.session_state.page == "customer":
    bcol, _ = st.columns([1, 8])
    with bcol:
        st.markdown('<div class="back-btn">', unsafe_allow_html=True)
        if st.button("← Home"):
            st.switch_page("app.py")
        st.markdown('</div>', unsafe_allow_html=True)

    st.markdown('<div class="page-title">Customer Payments</div>', unsafe_allow_html=True)
    st.markdown('<div class="page-sub">Broker directory</div>', unsafe_allow_html=True)

    conn = get_conn()
    try:
        n_b  = int(pg_read_sql("SELECT COUNT(*) c FROM brokers", conn).fillna(0).iloc[0]["c"])
        n_t  = int(pg_read_sql("SELECT COUNT(*) c FROM customer_transactions", conn).fillna(0).iloc[0]["c"])
        rev  = float(pg_read_sql("SELECT COALESCE(SUM(total_amount),0) s FROM customer_transactions", conn).fillna(0).iloc[0]["s"])
        pend = float(pg_read_sql("""
            SELECT COALESCE(SUM(ct.total_amount - COALESCE(p.paid, 0)), 0) s
            FROM customer_transactions ct
            LEFT JOIN (
                SELECT transaction_id, SUM(amount) AS paid
                FROM payments
                GROUP BY transaction_id
            ) p ON ct.transaction_id = p.transaction_id
            WHERE ct.payment_status IN ('Pending', 'Partial')
        """, conn).fillna(0).iloc[0]["s"])
        st.markdown(f'<div class="stat-row">'
                    f'<div class="stat-pill"><span class="sp-label">Brokers</span>'
                    f'<span class="sp-value">{int(n_b)}</span><span class="sp-sub">in directory</span></div>'
                    f'<div class="stat-pill"><span class="sp-label">Transactions</span>'
                    f'<span class="sp-value">{int(n_t)}</span></div>'
                    f'<div class="stat-pill"><span class="sp-label">Total Revenue</span>'
                    f'<span class="sp-value">{fmt_inr(rev)}</span></div>'
                    f'<div class="stat-pill"><span class="sp-label">Pending Dues</span>'
                    f'<span class="sp-value" style="color:#d4864a">{fmt_inr(pend)}</span></div></div>',
                    unsafe_allow_html=True)

        s1, s2 = st.columns([3, 1], gap="small")
        with s1:
            search = st.text_input("", placeholder="🔍  Search broker name...", label_visibility="collapsed")
        with s2:
            with st.popover("＋  Add Broker", use_container_width=True):
                nb = st.text_input("Broker Name", key="new_broker_name")
                if st.button("Save Broker"):
                    name_clean = nb.strip()
                    if not name_clean:
                        st.error("Name cannot be empty.")
                    else:
                        max_row = conn.execute("SELECT COALESCE(MAX(broker_id),100) AS mx FROM brokers").fetchone()
                        next_id = int(max_row["mx"]) + 1
                        try:
                            conn.execute("INSERT INTO brokers (broker_id,broker_name) VALUES (%s,%s)",
                                         (next_id, name_clean))
                            conn.commit()
                            st.success(f"Added '{name_clean}'")
                            st.rerun()
                        except psycopg2.IntegrityError:
                            conn.rollback()
                            st.error("A broker with this name already exists.")

        st.markdown("<hr>", unsafe_allow_html=True)
        df_b = pg_read_sql("SELECT * FROM brokers ORDER BY broker_name ASC", conn)
        if search:
            df_b = df_b[df_b["broker_name"].str.contains(search, case=False, na=False)]

        _today_str = date.today().isoformat()
        df_ov = pg_read_sql(
            "SELECT broker_id, COUNT(*) as overdue_count FROM customer_transactions "
            "WHERE payment_status IN ('Pending','Partial') "
            "AND (%s::date - date::date) > 60 GROUP BY broker_id",
            conn, params=(_today_str,))
        overdue_map = dict(zip(df_ov["broker_id"], df_ov["overdue_count"]))

        if df_b.empty:
            st.markdown('<div class="empty-state"><div class="es-icon">📭</div>No brokers found.</div>',
                        unsafe_allow_html=True)
        else:
            for chunk in [df_b.iloc[i:i+3] for i in range(0, len(df_b), 3)]:
                cols = st.columns(3, gap="small")
                for idx, (_, row) in enumerate(chunk.iterrows()):
                    with cols[idx]:
                        c1c, c2c = st.columns([5, 1])
                        with c1c:
                            n_ov = int(overdue_map.get(row["broker_id"], 0))
                            ov_tag = f"  🔴 {n_ov} overdue" if n_ov > 0 else ""
                            if st.button(f"{row['broker_name']}{ov_tag}",
                                         key=f"b_{row['broker_id']}", use_container_width=True):
                                st.session_state.update({
                                    "broker_id": row["broker_id"], "broker_name": row["broker_name"],
                                    "page": "ledger", "selected_txns": [], "show_sum_report": False,
                                    "bill_items": [], "bill_adding_more": False,
                                    "ledger_page": 0, "filter_mode": "Payment Due",
                                }); st.rerun()
                        with c2c:
                            if st.button("🗑", key=f"del_{row['broker_id']}"):
                                try:
                                    conn.execute("DELETE FROM brokers WHERE broker_id=%s",
                                                 (row["broker_id"],))
                                    conn.commit(); st.rerun()
                                except psycopg2.IntegrityError:
                                    conn.rollback()
                                    st.error(f"Cannot delete '{row['broker_name']}' — "
                                             "existing transactions reference this broker.")
    finally:
        conn.close()


# ──────────────────────────────────────────────────────────────
#  PAGE: LEDGER VIEW
# ──────────────────────────────────────────────────────────────

elif st.session_state.page == "ledger":

    if "broker_id" not in st.session_state:
        st.session_state.page = "customer"
        st.rerun()

    bid   = st.session_state["broker_id"]
    bname = st.session_state["broker_name"]

    bcol, _ = st.columns([1, 8])
    with bcol:
        st.markdown('<div class="back-btn">', unsafe_allow_html=True)
        if st.button("← Brokers"):
            st.session_state.page = "customer"
            st.session_state.pop("broker_id", None)
            st.session_state.selected_txns   = []
            st.session_state.show_sum_report = False
            st.session_state.bill_items      = []
            for _ck in list(st.session_state.keys()):
                if any(_ck.startswith(p) for p in
                       ["edit_", "calc_", "view_", "lpmt_",
                        "chk_", "preview_", "edit_pmt_"]):
                    del st.session_state[_ck]
            st.session_state.pop("print_html_cache", None)
            st.rerun()
        st.markdown('</div>', unsafe_allow_html=True)

    conn = get_conn()
    try:
        _today_iso = date.today().isoformat()

        stats = pg_read_sql("""
            SELECT COUNT(*) cnt,
                COALESCE(SUM(total_amount),0) total,
                COALESCE(SUM(CASE WHEN payment_status='Pending' THEN total_amount ELSE 0 END),0) pending,
                COALESCE(SUM(CASE WHEN payment_status='Paid'    THEN total_amount ELSE 0 END),0) paid,
                COALESCE(SUM(CASE WHEN final_settlement IS NOT NULL THEN final_settlement ELSE 0 END),0) settled,
                COALESCE(SUM(CASE WHEN calc_status='Pending' THEN 1 ELSE 0 END),0) uncalc,
                COALESCE(SUM(CASE WHEN payment_status IN ('Pending','Partial')
                    AND (%s::date - date::date) > 60
                    THEN 1 ELSE 0 END),0) overdue_cnt
            FROM customer_transactions WHERE broker_id=%s""",
            conn, params=(_today_iso, bid)).iloc[0]

        st.markdown(f"""
        <div class="ledger-header">
            <div>
                <div class="lh-name">{h(bname)}</div>
                <div class="lh-id">{int(stats['cnt'])} transactions
                    &nbsp;·&nbsp; <span style="color:#b89040">{int(stats['uncalc'])} pending calc</span>
                    &nbsp;·&nbsp; <span style="color:#ff6060">{int(stats['overdue_cnt'])} overdue (&gt;60 days)</span>
                </div>
            </div>
            <div style="display:flex;gap:1.8rem;text-align:right;flex-wrap:wrap">
                <div><div style="font-size:.65rem;color:#3a3628;text-transform:uppercase;letter-spacing:.1em">Total Billed</div>
                     <div style="font-size:1.1rem;font-weight:600;color:#e8c97e">{fmt_inr(stats['total'])}</div></div>
                <div><div style="font-size:.65rem;color:#3a3628;text-transform:uppercase;letter-spacing:.1em">Collected</div>
                     <div style="font-size:1.1rem;font-weight:600;color:#6dbf67">{fmt_inr(stats['paid'])}</div></div>
                <div><div style="font-size:.65rem;color:#3a3628;text-transform:uppercase;letter-spacing:.1em">Pending</div>
                     <div style="font-size:1.1rem;font-weight:600;color:#d4864a">{fmt_inr(stats['pending'])}</div></div>
                <div><div style="font-size:.65rem;color:#3a3628;text-transform:uppercase;letter-spacing:.1em">Net Settlement</div>
                     <div style="font-size:1.1rem;font-weight:600;color:#8dd87a">{fmt_inr(stats['settled'])}</div></div>
            </div>
        </div>""", unsafe_allow_html=True)

        # ── FILTER BAR ──────────────────────────────────────────
        st.markdown('<div class="filter-bar no-print">', unsafe_allow_html=True)
        fa, fb, fc, fd, fe = st.columns([1.2, 1.2, 1.4, 1.4, 2.5], gap="small")
        with fa:
            st.markdown('<span class="filter-label">Date Filter</span>', unsafe_allow_html=True)
            _fopts = ["All", "Single Date", "Date Range", "Payment Due"]
            fmode  = st.selectbox("", _fopts, label_visibility="collapsed", key="fmode_sel",
                                  index=_fopts.index(st.session_state.filter_mode)
                                  if st.session_state.filter_mode in _fopts else 0)
            st.session_state.filter_mode = fmode
        with fb:
            if fmode == "Single Date":
                st.markdown('<span class="filter-label">Date</span>', unsafe_allow_html=True)
                st.session_state.filter_single = st.date_input("", value=st.session_state.filter_single,
                                                               label_visibility="collapsed", key="fd_s")
        with fc:
            if fmode == "Date Range":
                st.markdown('<span class="filter-label">From</span>', unsafe_allow_html=True)
                st.session_state.filter_start = st.date_input("", value=st.session_state.filter_start,
                                                              label_visibility="collapsed", key="fd_r1")
        with fd:
            if fmode == "Date Range":
                st.markdown('<span class="filter-label">To</span>', unsafe_allow_html=True)
                st.session_state.filter_end = st.date_input("", value=st.session_state.filter_end,
                                                            label_visibility="collapsed", key="fd_r2")
        with fe:
            cust_search = st.text_input("", placeholder="🔍  Search customer...",
                                        label_visibility="collapsed", key="csrch")
        st.markdown('</div>', unsafe_allow_html=True)

        # ── LOG NEW BILL POPOVER ─────────────────────────────────
        act1, act2, _ = st.columns([1.2, 1.2, 4], gap="small")
        with act1:
            with st.popover("📋  Log New Bill", use_container_width=True):
                # Deferred full-form reset — MUST be first, before any widget renders
                if st.session_state.pop("_reset_item_form", False):
                    st.session_state["inp_cust"] = ""
                    for _k in ["ibags", "iqty", "irate", "ibr", "ifreight"]:
                        st.session_state[_k] = ""

                st.markdown("##### New Transaction")
                new_cust = st.text_input("Customer Name",
                                         value=st.session_state.bill_customer,
                                         key="inp_cust")
                new_date = st.date_input("Bill Date", value=st.session_state.bill_date)
                new_pst  = st.selectbox("Payment Status", ["Pending", "Paid", "Partial"],
                                        index=["Pending","Paid","Partial"].index(st.session_state.bill_pstatus))
                st.session_state.bill_customer = new_cust
                st.session_state.bill_date     = new_date
                st.session_state.bill_pstatus  = new_pst

                st.markdown("---")
                st.markdown("##### Goods Details")
                render_cart()

                _goods_map = get_merged_goods(conn, ARECA_NUT_GOODS, BLACK_PEPPER_GOODS)

                if _goods_map["__empty__"]:
                    st.warning(
                        "⚠️ No goods found. Please add goods in "
                        "Stock Register first before logging a bill."
                    )
                    st.stop()

                _display_cats = [
                    ("Arecanut",     _goods_map["Arecanut"]),
                    ("Black Pepper", _goods_map["Black Pepper"]),
                ]
                for _cat, _goods in _goods_map.items():
                    if _cat not in ("Arecanut", "Black Pepper",
                                    "__all__", "__empty__") and _goods:
                        _display_cats.append((_cat, _goods))

                _grouped_options = []
                _selectable = []
                for _cat_name, _cat_goods in _display_cats:
                    if _cat_goods:
                        _grouped_options.append(f"── {_cat_name} ──")
                        for _g in _cat_goods:
                            _grouped_options.append(_g)
                            _selectable.append(_g)

                _raw_sel = st.selectbox(
                    "Type of Goods",
                    _grouped_options,
                    key="bill_ig_sel"
                )

                if _raw_sel and _raw_sel.startswith("──"):
                    ig = _selectable[0] if _selectable else ""
                else:
                    ig = _raw_sel or ""

                _areca_goods = _goods_map.get("Arecanut", [])
                _bp_goods    = _goods_map.get("Black Pepper", [])
                _default_bag_rate = "20" if ig in _areca_goods else ("5" if ig in _bp_goods else "0")

                # Only reset bag rate when goods TYPE changes, not on every rerun
                if st.session_state.get("_prev_bill_ig") != ig:
                    # Goods changed — reset bag rate to category default
                    if "ibags" not in st.session_state:
                        # Only wipe ibr if no submission is in progress
                        st.session_state["ibr"] = ""
                    st.session_state["_prev_bill_ig"] = ig
                if "_restore_bag_rate" in st.session_state:
                    st.session_state["ibr"] = st.session_state.pop("_restore_bag_rate")

                with st.form("item_form", clear_on_submit=False):
                    st.markdown(f"**Item #{len(st.session_state.bill_items)+1} · {ig}**")
                    ic1, ic2 = st.columns(2)
                    with ic1:
                        # No value= parameter — session state drives the value
                        # so typed values survive reruns
                        ibags_s    = st.text_input("No. of Bags", placeholder="0",
                                                   key="ibags")
                        ibagrate_s = st.text_input("Price / Bag (₹)", key="ibr",
                                                   placeholder=_default_bag_rate,
                                                   help="Use / as decimal e.g. 50/50")
                    with ic2:
                        iqty_s  = st.text_input("Quantity (Kg)", placeholder="0",
                                                key="iqty")
                        irate_s = st.text_input("Rate (₹/Kg)", placeholder="0",
                                                key="irate")
                    ic3, ic4 = st.columns(2)
                    with ic3:
                        ifreight_s = st.text_input("Freight (₹)", placeholder="0",
                                                   key="ifreight")
                    with ic4:
                        icollect = st.selectbox("Collected from",
                                                ["Shop","Transport","Anandpuri"], key="icollect")

                    fa_col, fb_col = st.columns(2)
                    with fa_col:
                        add_more = st.form_submit_button("＋  Add", use_container_width=True)
                    with fb_col:
                        add_done = st.form_submit_button("✔  Save & Finish", use_container_width=True)

                # ── Item form submit handler (OUTSIDE the with st.form block) ──
                if add_more or add_done:
                    try:
                        # Use widget local variables directly — these hold submitted values
                        # Fall back to "0" for optional fields (freight, bag rate)
                        _ibags_s    = ibags_s.strip()
                        _iqty_s     = iqty_s.strip()
                        _irate_s    = irate_s.strip()
                        # Bag rate: use typed value, fall back to category default
                        _ibagrate_s = ibagrate_s.strip() if ibagrate_s.strip() else _default_bag_rate
                        # Freight: optional, default 0
                        _ifreight_s = ifreight_s.strip() if ifreight_s.strip() else "0"

                        if not _ibags_s:
                            st.error("Please enter number of bags.")
                            st.stop()
                        if not _iqty_s:
                            st.error("Please enter quantity (Kg).")
                            st.stop()
                        if not _irate_s:
                            st.error("Please enter rate per Kg.")
                            st.stop()

                        ibags    = int(parse_slash_amount(_ibags_s))
                        ibagrate = parse_slash_amount(_ibagrate_s)
                        iqty     = parse_slash_amount(_iqty_s)
                        irate    = parse_slash_amount(_irate_s)
                        ifreight = parse_slash_amount(_ifreight_s)

                        if ibags < 0 or ibagrate < 0 or iqty < 0 or irate < 0 or ifreight < 0:
                            st.error("Rates and quantities cannot be negative.")
                        else:
                            ilt = line_total(ibags, ibagrate, iqty, irate) + ifreight
                            if ilt <= 0:
                                st.error("Item total must be > 0.")
                            else:
                                _icollect = icollect
                                st.session_state.bill_items.append({
                                    "goods": ig, "bags": ibags, "bag_rate": ibagrate,
                                    "qty": iqty, "rate": irate,
                                    "freight": ifreight, "collection_point": _icollect,
                                    "line_total": ilt,
                                })
                                st.session_state.bill_adding_more = add_more
                                st.session_state["_reset_item_form"] = True
                                st.rerun()
                    except (ValueError, InvalidOperation) as e:
                        st.error(f"Invalid number: {e}")

                # ── Save bill ─────────────────────────────────────
                if st.session_state.bill_items and not st.session_state.bill_adding_more:
                    grand       = sum(i["line_total"] for i in st.session_state.bill_items)
                    goods_label = ", ".join({i["goods"] for i in st.session_state.bill_items})
                    total_bags  = sum(i["bags"] for i in st.session_state.bill_items)
                    total_qty   = sum(i["qty"]  for i in st.session_state.bill_items)
                    st.markdown(f"**Grand Total: {fmt_inr(grand)}**")

                    # ── Bill Sent field (optional, for Passbook matching) ──
                    bill_sent_str = st.text_input(
                        "Bill Sent (₹) — optional",
                        value="", key="inp_bill_sent",
                        help="Official invoice amount paid to bank account. "
                             "Must be ≤ Grand Total. Used by Passbook auto-matching.")

                    sv1, sv2 = st.columns(2)
                    with sv1:
                        if st.button("💾  Save Bill", use_container_width=True, key="save_bill_btn"):
                            _now  = time.time()
                            _last = st.session_state.get("_last_bill_save_ts", 0)
                            if _now - _last < 3.0:
                                st.warning("Please wait a moment before saving again.")
                            else:
                                st.session_state["_last_bill_save_ts"] = _now
                                cust = st.session_state.bill_customer.strip()
                                if not cust:
                                    st.error("Customer name required.")
                                else:
                                    # Validate Bill Sent
                                    _bill_sent_save = None
                                    _bill_sent_ok   = True
                                    if bill_sent_str.strip():
                                        try:
                                            _bsf = parse_slash_amount(bill_sent_str)
                                            if _bsf <= 0:
                                                st.error("Bill Sent must be > 0.")
                                                _bill_sent_ok = False
                                            elif _bsf > grand:
                                                st.error(f"Bill Sent cannot exceed Grand Total ({fmt_inr(grand)}).")
                                                _bill_sent_ok = False
                                            else:
                                                _bill_sent_save = round(_bsf, 2)
                                        except (ValueError, InvalidOperation):
                                            st.error("Invalid Bill Sent amount.")
                                            _bill_sent_ok = False

                                    if _bill_sent_ok:
                                        cur = conn.cursor()
                                        cur.execute(
                                            """INSERT INTO customer_transactions
                                            (broker_id,customer_name,date,type_of_goods,bags,quantity,rate,
                                             total_amount,payment_status,payment_method,
                                             cheque_number,cheque_date,deposit_firm,bill_sent,
                                             interest_rate_pct,calc_status,brokerage_paid)
                                            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'Pending','Unpaid')
                                            RETURNING transaction_id""",
                                            (bid, cust, str(st.session_state.bill_date), goods_label,
                                             total_bags, total_qty, 0, grand,
                                             st.session_state.bill_pstatus, None,
                                             None, None, None,
                                             _bill_sent_save, 0.0))
                                        txn_id = cur.fetchone()[0]
                                        for it in st.session_state.bill_items:
                                            cur.execute(
                                                """INSERT INTO transaction_items
                                                (transaction_id,type_of_goods,bags,bag_rate,
                                                 quantity,rate,freight,collection_point,line_total)
                                                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                                                (txn_id, it["goods"], it["bags"], it["bag_rate"],
                                                 it["qty"], it["rate"],
                                                 it.get("freight", 0), it.get("collection_point", ""),
                                                 it["line_total"]))
                                        log_audit(conn, "customer_transactions", txn_id, "INSERT",
                                                  new_value={"customer": cust, "total": grand,
                                                             "date": str(st.session_state.bill_date)})
                                        try:
                                            stock_warns = deduct_stock_for_sale(
                                                conn, st.session_state.bill_items,
                                                st.session_state.bill_date, cust)
                                        except ValueError as _se:
                                            stock_warns = [f"⚠ Stock deduction error: {_se}"]
                                        conn.commit()
                                        st.session_state.bill_items       = []
                                        st.session_state.bill_adding_more = False
                                        st.session_state.bill_customer    = ""
                                        st.session_state.bill_date        = date.today()
                                        st.session_state.bill_pstatus     = "Pending"
                                        st.session_state["_reset_item_form"] = True
                                        for _k in ["bill_ig_sel", "_prev_bill_ig",
                                                   "_restore_bag_rate",
                                                   "inp_cust", "inp_date", "inp_pst"]:
                                            st.session_state.pop(_k, None)
                                        for _sw in stock_warns:
                                            st.warning(_sw)
                                        st.success("Bill saved — stock updated."); st.rerun()
                    with sv2:
                        if st.button("✕  Clear Cart", use_container_width=True, key="clear_cart_btn"):
                            st.session_state.bill_items       = []
                            st.session_state.bill_adding_more = False
                            st.session_state.bill_customer    = ""
                            st.session_state.bill_date        = date.today()
                            st.session_state.bill_pstatus     = "Pending"
                            st.session_state["_reset_item_form"] = True
                            for _k in ["bill_ig_sel", "_prev_bill_ig",
                                       "_restore_bag_rate",
                                       "inp_cust", "inp_date", "inp_pst"]:
                                st.session_state.pop(_k, None)
                            st.rerun()

        # ── SUM SELECTED ────────────────────────────────────────
        with act2:
            n_sel     = len(st.session_state.selected_txns)
            sum_label = f"Σ  Sum  ({n_sel})" if n_sel > 0 else "Σ  Sum Selected"
            if st.button(sum_label, use_container_width=True, disabled=(n_sel == 0)):
                _sel_ids = [int(i) for i in st.session_state.selected_txns]
                _ph      = ",".join(["%s"] * len(_sel_ids))
                pid = pg_read_sql(
                    f"SELECT transaction_id FROM customer_transactions "
                    f"WHERE transaction_id IN ({_ph}) AND calc_status='Pending'",
                    conn, params=tuple(_sel_ids))["transaction_id"].tolist()
                if pid:
                    st.session_state.sum_intercept   = pid
                    st.session_state.show_sum_report = False
                else:
                    st.session_state.sum_intercept   = []
                    st.session_state.show_sum_report = True
                st.rerun()

        if st.session_state.get("sum_intercept"):
            ids_warn = st.session_state.sum_intercept
            st.markdown(f'<div class="warn-box">⚠️ <strong>Cannot generate summary yet.</strong><br>'
                        f'{len(ids_warn)} transaction(s) need calculation first '
                        f'(IDs: {", ".join("#"+str(i) for i in ids_warn)}).</div>',
                        unsafe_allow_html=True)

        # ── BATCH SUM REPORT ────────────────────────────────────
        if st.session_state.show_sum_report and st.session_state.selected_txns:
            _sel_ids = [int(i) for i in st.session_state.selected_txns]
            _ph      = ",".join(["%s"] * len(_sel_ids))
            df_s = pg_read_sql(
                f"SELECT * FROM customer_transactions WHERE transaction_id IN ({_ph})",
                conn, params=tuple(_sel_ids))
            g_tot   = float(df_s["total_amount"].sum())
            g_int   = float(df_s["interest_amount"].fillna(0).sum())
            g_disc  = float(df_s["discount_amount"].fillna(0).sum())
            g_brok  = float(df_s["brokerage_amount"].fillna(0).sum())
            g_final = float(df_s["final_settlement"].fillna(0).sum())
            g_rcvd  = float(df_s["payment_received"].fillna(0).sum())

            st.markdown(f'<div class="sum-panel">'
                        f'<div class="sum-panel-title">📊 Batch Settlement Summary — {len(df_s)} transactions</div>'
                        f'<div class="sum-grid">'
                        f'<div class="sum-cell"><div class="sc-l">Gross Amount</div><div class="sc-v">{fmt_inr(g_tot)}</div></div>'
                        f'<div class="sum-cell"><div class="sc-l">Amount Received</div><div class="sc-v" style="color:#8dd87a">{fmt_inr(g_rcvd)}</div></div>'
                        f'<div class="sum-cell"><div class="sc-l">Total Discount</div><div class="sc-v" style="color:#d4864a">{fmt_inr(g_disc)}</div></div>'
                        f'<div class="sum-cell"><div class="sc-l">Total Brokerage</div><div class="sc-v" style="color:#d4864a">{fmt_inr(g_brok)}</div></div>'
                        f'<div class="sum-cell"><div class="sc-l">Total Interest</div><div class="sc-v" style="color:#6a9fd4">{fmt_inr(g_int)}</div></div>'
                        f'<div class="sum-cell"><div class="sc-l">Total Amount Due</div>'
                        f'<div class="sc-v" style="color:#8dd87a;font-size:1.4rem">{fmt_inr(g_final)}</div></div>'
                        f'</div></div>', unsafe_allow_html=True)

            rows_screen = ""
            for _, r in df_s.iterrows():
                _fs = float(r["final_settlement"] or 0)
                if _fs < -0.01:
                    _fsc = "#6a9fd4"
                elif abs(_fs) < 0.01:
                    _fsc = "#f0ebe0"
                else:
                    _fsc = "#8dd87a"
                rows_screen += (
                    f'<tr style="border-bottom:1px solid #1a2030;color:#c8bfa8">'
                    f'<td style="padding:6px">{h(r["customer_name"])}</td>'
                    f'<td style="padding:6px;color:#6a7060">{fmt_date(r["date"])}</td>'
                    f'<td style="padding:6px;color:#6a7060">{h(r["type_of_goods"])}</td>'
                    f'<td style="padding:6px;text-align:right">{fmt_inr(r["total_amount"])}</td>'
                    f'<td style="padding:6px;text-align:right;color:#d4864a">{fmt_inr(r["discount_amount"] or 0)}</td>'
                    f'<td style="padding:6px;text-align:right;color:#d4864a">{fmt_inr(r["brokerage_amount"] or 0)}</td>'
                    f'<td style="padding:6px;text-align:right;color:#6a9fd4">{fmt_inr(r["interest_amount"] or 0)}</td>'
                    f'<td style="padding:6px;text-align:right;color:{_fsc};font-weight:600">{fmt_inr(_fs)}</td>'
                    f'</tr>'
                )

            st.markdown(
                f'<table style="width:100%;border-collapse:collapse;font-size:.8rem;margin:0.8rem 0">'
                f'<thead><tr style="border-bottom:1px solid #2a3460;color:#3a4060">'
                f'<th style="text-align:left;padding:6px">Customer</th><th style="padding:6px">Date</th>'
                f'<th style="padding:6px">Goods</th><th style="text-align:right;padding:6px">Amount</th>'
                f'<th style="text-align:right;padding:6px">Discount</th><th style="text-align:right;padding:6px">Brokerage</th>'
                f'<th style="text-align:right;padding:6px">Interest</th><th style="text-align:right;padding:6px">Total Amount Due</th>'
                f'</tr></thead><tbody>{rows_screen}</tbody></table>', unsafe_allow_html=True)

            _ids_in     = ",".join(str(int(i)) for i in df_s["transaction_id"])
            _df_all_pmts = pg_read_sql(
                f"SELECT * FROM payments WHERE transaction_id IN ({_ids_in})"
                f" ORDER BY payment_date ASC",
                conn
            ) if not df_s.empty else pd.DataFrame()
            _pmts_by_txn = (
                {int(tid): grp.reset_index(drop=True)
                 for tid, grp in _df_all_pmts.groupby("transaction_id")}
                if not _df_all_pmts.empty else {}
            )

            per_txn_detail = ""
            for _, r in df_s.iterrows():
                txn_pmts = _pmts_by_txn.get(int(r["transaction_id"]), pd.DataFrame())
                pmt_rows = "".join(
                    f'<tr style="font-size:11px;color:#555">'
                    f'<td style="padding:3px 6px;padding-left:20px">↳ Payment</td>'
                    f'<td style="padding:3px 6px">{fmt_date(p["payment_date"])}</td><td></td>'
                    f'<td class="r" style="padding:3px 6px">{fmt_inr(p["amount"])}</td>'
                    f'<td colspan="3" style="padding:3px 6px;color:#888">'
                    f'{int(p.get("days_from_start",0))} days · Interest: {fmt_inr(p.get("interest_charged",0))}'
                    f'</td><td></td></tr>' for _, p in txn_pmts.iterrows()) if not txn_pmts.empty else ""
                per_txn_detail += (
                    f'<tr style="background:#f0f0f0;font-weight:bold">'
                    f'<td>{h(r["customer_name"])}</td><td>{fmt_date(r["date"])}</td>'
                    f'<td>{h(r["type_of_goods"])}</td>'
                    f'<td class="r">{fmt_inr(r["total_amount"])}</td>'
                    f'<td class="r disc">{fmt_inr(r["discount_amount"] or 0)}</td>'
                    f'<td class="r disc">{fmt_inr(r["brokerage_amount"] or 0)}</td>'
                    f'<td class="r int">{fmt_inr(r["interest_amount"] or 0)}</td>'
                    f'<td class="r bold">{fmt_inr(r["final_settlement"] or 0)}</td></tr>{pmt_rows}')

            print_html = f"""<!DOCTYPE html><html><head><meta charset="UTF-8">
<title>S P Spices – Settlement Report</title>
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:'Segoe UI',Arial,sans-serif;font-size:13px;color:#111;padding:28px;background:#fff}}
.header{{display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:20px;border-bottom:2px solid #333;padding-bottom:12px}}
.header h1{{font-size:20px;color:#1a3a1a}}
.meta{{font-size:11px;color:#666;text-align:right;line-height:1.8}}
.summary{{display:grid;grid-template-columns:repeat(6,1fr);gap:12px;background:#f5f5f5;border:1px solid #ddd;border-radius:6px;padding:14px;margin-bottom:20px}}
.s-cell .s-label{{font-size:9px;text-transform:uppercase;color:#888;letter-spacing:.06em}}
.s-cell .s-val{{font-size:15px;font-weight:700;margin-top:3px}}
.s-cell .s-val.green{{color:#1a6a1a}}
h3{{font-size:13px;text-transform:uppercase;letter-spacing:.06em;color:#444;margin-bottom:8px;padding-bottom:4px;border-bottom:1px solid #ccc}}
table{{width:100%;border-collapse:collapse;font-size:12px;margin-bottom:22px}}
thead tr{{background:#2a2a2a;color:#fff}}
th{{padding:7px 8px;text-align:left;font-size:10px;text-transform:uppercase;letter-spacing:.05em;font-weight:600}}
td{{padding:6px 8px;border-bottom:1px solid #eee;vertical-align:top}}
tr:nth-child(even) td{{background:#fafafa}}
tfoot tr td{{font-weight:700;background:#e8e8e8;border-top:2px solid #333;font-size:13px}}
.r{{text-align:right}}.bold{{font-weight:700}}.disc{{color:#c05000}}.int{{color:#2050a0}}
.footer{{margin-top:20px;font-size:10px;color:#999;border-top:1px solid #eee;padding-top:8px;display:flex;justify-content:space-between}}
@media print{{@page{{margin:15mm;size:A4 landscape}}body{{padding:0;font-size:11px}}}}
</style></head><body>
<div class="header">
  <div><h1>🌶️ S P Spices</h1>
       <div style="font-size:14px;font-weight:600;margin-top:4px">Batch Settlement Report</div>
       <div style="font-size:12px;color:#555;margin-top:2px">Broker: <strong>{h(bname)}</strong></div></div>
  <div class="meta">Generated: {fmt_date(date.today())}<br>Transactions: {len(df_s)}<br>Rate: {INTEREST_RATE_PCT}% p.a.</div>
</div>
<div class="summary">
  <div class="s-cell"><div class="s-label">Gross Amount</div><div class="s-val">{fmt_inr(g_tot)}</div></div>
  <div class="s-cell"><div class="s-label">Amount Received</div><div class="s-val" style="color:#1a6a1a">{fmt_inr(g_rcvd)}</div></div>
  <div class="s-cell"><div class="s-label">Discount</div><div class="s-val" style="color:#c05000">{fmt_inr(g_disc)}</div></div>
  <div class="s-cell"><div class="s-label">Brokerage</div><div class="s-val" style="color:#c05000">{fmt_inr(g_brok)}</div></div>
  <div class="s-cell"><div class="s-label">Interest</div><div class="s-val" style="color:#2050a0">{fmt_inr(g_int)}</div></div>
  <div class="s-cell"><div class="s-label">Total Amount Due</div><div class="s-val green">{fmt_inr(g_final)}</div></div>
</div>
<h3>Transaction Detail</h3>
<table><thead><tr><th>Customer</th><th>Date</th><th>Goods</th>
<th class="r">Amount</th><th class="r">Discount</th><th class="r">Brokerage</th>
<th class="r">Interest</th><th class="r">Total Amount Due</th></tr></thead>
<tbody>{per_txn_detail}</tbody></table>
<div class="footer"><span>S P Spices Business Management Diary</span><span>Printed: {fmt_date(date.today())}</span></div>
</body></html>"""

            pr1, pr2, _ = st.columns([1.2, 1, 4.8], gap="small")
            with pr1:
                st.download_button(
                    "⬇ Download Report",
                    data=print_html.encode("utf-8"),
                    file_name=f"sp_spices_{date.today().isoformat()}.html",
                    mime="text/html",
                    use_container_width=True,
                    key="dl_report_btn")
            with pr2:
                if st.button("✕  Clear", use_container_width=True):
                    st.session_state.show_sum_report = False
                    st.session_state.selected_txns   = []
                    st.session_state.sum_intercept   = []
                    st.rerun()
            st.caption("Open the downloaded file in your browser → Ctrl+P / Cmd+P to print.")
            components.html(print_html, height=500, scrolling=True)

        st.markdown("<hr>", unsafe_allow_html=True)

        # ── FETCH + FILTER TRANSACTIONS ──────────────────────────
        df_t = pg_read_sql(
            "SELECT * FROM customer_transactions WHERE broker_id=%s ORDER BY date ASC",
            conn, params=(bid,))
        if fmode == "Single Date":
            df_t = df_t[df_t["date"] == str(st.session_state.filter_single)]
        elif fmode == "Date Range":
            df_t = df_t[(df_t["date"] >= str(st.session_state.filter_start)) &
                        (df_t["date"] <= str(st.session_state.filter_end))]
        elif fmode == "Payment Due":
            df_t = df_t[df_t["payment_status"].isin(["Pending","Partial"])]
        if cust_search:
            df_t = df_t[df_t["customer_name"].str.contains(cust_search, case=False, na=False)]

        if df_t.empty:
            st.markdown('<div class="empty-state"><div class="es-icon">📋</div>No transactions found.</div>',
                        unsafe_allow_html=True)
        else:
            sc1, sc2, sc3, _ = st.columns([1, 1, 1.5, 4.5], gap="small")
            with sc1:
                if st.button("☑  Select All", use_container_width=True):
                    st.session_state.selected_txns = [int(i) for i in df_t["transaction_id"].tolist()]
                    for _tc in df_t["transaction_id"].tolist():
                        st.session_state.pop(f"chk_{int(_tc)}", None)
                    st.rerun()
            with sc2:
                if st.button("☐  Deselect All", use_container_width=True):
                    st.session_state.selected_txns = []
                    for _tc in df_t["transaction_id"].tolist():
                        st.session_state.pop(f"chk_{int(_tc)}", None)
                    st.rerun()
            with sc3:
                n_sel_now = len(st.session_state.selected_txns)
                st.markdown(f'<div style="padding:.4rem .8rem;background:#1e1c14;border:1px solid #2a2820;'
                            f'border-radius:8px;font-size:.82rem;color:#c8bfa8;text-align:center">'
                            f'<b>{n_sel_now}</b> selected</div>', unsafe_allow_html=True)

            # ── Pagination ───────────────────────────────────────
            # Reset page to 0 when any filter changes
            _filter_sig = (
                str(fmode),
                str(st.session_state.get("filter_single", "")),
                str(st.session_state.get("filter_start", "")),
                str(st.session_state.get("filter_end", "")),
                str(st.session_state.get("csrch", "")),
            )
            if st.session_state.get("_last_filter_sig") != _filter_sig:
                st.session_state["ledger_page"] = 0
                st.session_state["_last_filter_sig"] = _filter_sig
            _total_txns  = len(df_t)
            _total_pages = max(1, (_total_txns + TXNS_PER_PAGE - 1) // TXNS_PER_PAGE)
            _cur_page    = min(st.session_state.get("ledger_page", 0), _total_pages - 1)
            st.session_state["ledger_page"] = _cur_page
            df_page = df_t.iloc[_cur_page * TXNS_PER_PAGE : (_cur_page + 1) * TXNS_PER_PAGE]

            if _total_pages > 1:
                pg1, pg2, pg3, _ = st.columns([0.8, 0.8, 2, 5], gap="small")
                with pg1:
                    if st.button("← Prev", disabled=(_cur_page == 0), use_container_width=True):
                        st.session_state["ledger_page"] -= 1; st.rerun()
                with pg2:
                    if st.button("Next →", disabled=(_cur_page >= _total_pages - 1), use_container_width=True):
                        st.session_state["ledger_page"] += 1; st.rerun()
                with pg3:
                    st.markdown(
                        f'<div style="padding:.4rem .8rem;background:#1e1c14;border:1px solid #2a2820;'
                        f'border-radius:8px;font-size:.82rem;color:#c8bfa8;text-align:center">'
                        f'Page <b>{_cur_page+1}</b> of <b>{_total_pages}</b> '
                        f'&nbsp;·&nbsp; {_total_txns} total</div>',
                        unsafe_allow_html=True)

            st.markdown(
                '<div style="display:grid;'
                'grid-template-columns:36px 2fr 1.2fr 1.3fr 1.4fr 0.9fr 0.7fr 0.7fr;'
                'gap:0.5rem;padding:0.3rem 0.8rem 0.4rem 0.8rem;margin-bottom:0.2rem">'
                '<span style="font-size:.65rem;color:#3a3628;text-transform:uppercase;'
                'letter-spacing:.1em"></span>'
                '<span style="font-size:.65rem;color:#3a3628;text-transform:uppercase;'
                'letter-spacing:.1em">Customer</span>'
                '<span style="font-size:.65rem;color:#3a3628;text-transform:uppercase;'
                'letter-spacing:.1em">Date</span>'
                '<span style="font-size:.65rem;color:#3a3628;text-transform:uppercase;'
                'letter-spacing:.1em">Amount</span>'
                '<span style="font-size:.65rem;color:#3a3628;text-transform:uppercase;'
                'letter-spacing:.1em">Goods</span>'
                '<span style="font-size:.65rem;color:#3a3628;text-transform:uppercase;'
                'letter-spacing:.1em">Status</span>'
                '<span style="font-size:.65rem;color:#3a3628;text-transform:uppercase;'
                'letter-spacing:.1em">Calc</span>'
                '<span style="font-size:.65rem;color:#3a3628;text-transform:uppercase;'
                'letter-spacing:.1em">Brok</span>'
                '</div>',
                unsafe_allow_html=True)

            # ── TRANSACTION ROWS ─────────────────────────────────
            for _, txn in df_page.iterrows():
                tid         = int(txn["transaction_id"])
                A           = float(txn["total_amount"])
                calc_status = txn.get("calc_status","Pending") or "Pending"
                brok_paid   = txn.get("brokerage_paid","Unpaid") or "Unpaid"
                has_settle  = ("final_settlement" in txn.index and
                               txn["final_settlement"] is not None and
                               not pd.isna(txn["final_settlement"]))
                settle_val  = float(txn["final_settlement"]) if has_settle else None

                status_cls = {"Paid":"badge-paid","Pending":"badge-pending","Partial":"badge-partial"}.get(
                    txn["payment_status"], "badge-pending")
                calc_cls = "cs-calculated" if calc_status == "Calculated" else "cs-pending"
                bp_cls   = "badge-bp-paid" if brok_paid == "Paid" else "badge-bp-unpaid"

                days_old   = days_between(txn["date"])
                is_overdue = (txn["payment_status"] in ("Pending","Partial") and days_old > 60)
                ov_over    = days_old - 60 if is_overdue else 0

                is_chk    = tid in st.session_state.selected_txns
                _row_cols = st.columns([0.35, 2, 1.2, 1.3, 1.4, 0.9, 0.7, 0.7], gap="small")
                with _row_cols[0]:
                    if st.button("✔" if is_chk else "○", key=f"chk_{tid}",
                                 help="Deselect" if is_chk else "Select for batch sum",
                                 use_container_width=True):
                        if is_chk:
                            st.session_state.selected_txns.remove(tid)
                        else:
                            st.session_state.selected_txns.append(tid)
                        st.rerun()
                with _row_cols[1]:
                    st.markdown(
                        f'<div style="font-size:.88rem;font-weight:600;'
                        f'color:#e0d8c8;padding:.35rem 0">'
                        f'{h(txn["customer_name"])}</div>',
                        unsafe_allow_html=True)
                with _row_cols[2]:
                    st.markdown(
                        f'<div style="font-size:.82rem;color:#8a8070;'
                        f'padding:.35rem 0">{fmt_date(txn["date"])}</div>',
                        unsafe_allow_html=True)
                with _row_cols[3]:
                    st.markdown(
                        f'<div style="font-size:.88rem;font-weight:600;'
                        f'color:#e8c97e;padding:.35rem 0">'
                        f'{fmt_inr(A)}</div>',
                        unsafe_allow_html=True)
                with _row_cols[4]:
                    st.markdown(
                        f'<div style="font-size:.78rem;color:#6a8060;'
                        f'padding:.35rem 0">'
                        f'{h(txn["type_of_goods"] or "—")}</div>',
                        unsafe_allow_html=True)
                with _row_cols[5]:
                    st.markdown(
                        f'<div style="padding:.35rem 0">'
                        f'<span class="txn-badge {status_cls}">'
                        f'{h(txn["payment_status"])}</span></div>',
                        unsafe_allow_html=True)
                with _row_cols[6]:
                    _calc_icon  = "✓" if calc_status == "Calculated" else "○"
                    _calc_color = "#8dd87a" if calc_status == "Calculated" else "#5a5448"
                    st.markdown(
                        f'<div style="font-size:.8rem;color:{_calc_color};'
                        f'padding:.35rem 0;text-align:center">'
                        f'{_calc_icon}</div>',
                        unsafe_allow_html=True)
                with _row_cols[7]:
                    _brok_icon  = "✓" if brok_paid == "Paid" else "○"
                    _brok_color = "#8dd87a" if brok_paid == "Paid" else "#5a5448"
                    st.markdown(
                        f'<div style="font-size:.8rem;color:{_brok_color};'
                        f'padding:.35rem 0;text-align:center">'
                        f'{_brok_icon}</div>',
                        unsafe_allow_html=True)

                if is_overdue:
                    st.markdown(f'<div class="overdue-alert"><span class="oa-icon">⚠️</span>'
                                f'<span>Payment overdue by <span class="oa-days">{ov_over} days</span> '
                                f'(bill date: {fmt_date(txn["date"])}, status: {txn["payment_status"]}).'
                                f' Bill unpaid for over 2 months.</span></div>',
                                unsafe_allow_html=True)

                _exp_label = "▸  View / Edit"
                with st.expander(_exp_label, expanded=False):

                    df_items = pg_read_sql(
                        "SELECT * FROM transaction_items WHERE transaction_id=%s ORDER BY item_id",
                        conn, params=(tid,))
                    if not df_items.empty:
                        rows_i = ""
                        for _, it in df_items.iterrows():
                            freight_cell = (f' <span style="font-size:.7rem;color:#5a5448">'
                                            f'+ Freight {fmt_inr(it.get("freight",0))}</span>'
                                            if float(it.get("freight",0) or 0) > 0 else "")
                            cp_cell = (f'<div style="font-size:.7rem;color:#4a4438">'
                                       f'{h(it.get("collection_point",""))}</div>'
                                       if it.get("collection_point") else "")
                            rows_i += (f'<tr><td class="td-goods">{h(it["type_of_goods"])}{cp_cell}</td>'
                                       f'<td class="td-num">{int(it["bags"])}</td>'
                                       f'<td class="td-num">{fmt_inr(it["bag_rate"])}</td>'
                                       f'<td class="td-num">{it["quantity"]} Kg</td>'
                                       f'<td class="td-num">{fmt_inr(it["rate"])}/Kg{freight_cell}</td>'
                                       f'<td class="td-total">{fmt_inr(it["line_total"])}</td></tr>')
                        st.markdown(f'<table class="items-table"><thead><tr>'
                                    f'<th>Goods</th><th style="text-align:right">Bags</th>'
                                    f'<th style="text-align:right">Bag Rate</th>'
                                    f'<th style="text-align:right">Qty</th>'
                                    f'<th style="text-align:right">Rate/Kg</th>'
                                    f'<th style="text-align:right">Line Total</th>'
                                    f'</tr></thead><tbody>{rows_i}</tbody></table>'
                                    f'<div class="items-footer">Grand Total &nbsp; {fmt_inr(A)}</div>',
                                    unsafe_allow_html=True)
                    else:
                        st.markdown(f'<div class="detail-grid">'
                                    f'<div class="detail-cell"><div class="dc-label">Goods</div>'
                                    f'<div class="dc-value">{h(txn["type_of_goods"])}</div></div>'
                                    f'<div class="detail-cell"><div class="dc-label">Bags</div>'
                                    f'<div class="dc-value">{int(txn["bags"])}</div></div>'
                                    f'<div class="detail-cell"><div class="dc-label">Quantity</div>'
                                    f'<div class="dc-value">{txn["quantity"]} Kg</div></div>'
                                    f'<div class="detail-cell"><div class="dc-label">Total</div>'
                                    f'<div class="dc-value">{fmt_inr(A)}</div></div></div>',
                                    unsafe_allow_html=True)

                    # Bill Sent display
                    _bill_sent_raw  = txn.get("bill_sent")
                    _bill_sent_disp = (fmt_inr(float(_bill_sent_raw))
                                       if _bill_sent_raw is not None and not pd.isna(_bill_sent_raw)
                                       else '<span style="color:#3a3628">— not set —</span>')

                    st.markdown(f'<div class="detail-grid" style="margin-top:0.5rem">'
                                f'<div class="detail-cell"><div class="dc-label">Bill Date</div>'
                                f'<div class="dc-value">{fmt_date(txn["date"])}</div></div>'
                                f'<div class="detail-cell"><div class="dc-label">Payment Status</div>'
                                f'<div class="dc-value"><span class="txn-badge {status_cls}">'
                                f'{h(txn["payment_status"])}</span></div></div>'
                                f'<div class="detail-cell"><div class="dc-label">Calc Status</div>'
                                f'<div class="dc-value"><span class="{calc_cls}">'
                                f'{"✓" if calc_status=="Calculated" else "○"} {h(calc_status)}</span></div></div>'
                                f'<div class="detail-cell"><div class="dc-label">Brokerage Paid</div>'
                                f'<div class="dc-value"><span class="txn-badge {bp_cls}">'
                                f'{h(brok_paid)}</span></div></div>'
                                f'<div class="detail-cell"><div class="dc-label">Bill Sent</div>'
                                f'<div class="dc-value">{_bill_sent_disp}</div></div>'
                                f'</div>',
                                unsafe_allow_html=True)

                    # ── PAYMENT TIMELINE ──────────────────────────
                    st.markdown("---")
                    st.markdown("##### 📅 Payment Timeline")

                    df_pmts = pg_read_sql(
                        "SELECT * FROM payments WHERE transaction_id=%s ORDER BY payment_date ASC",
                        conn, params=(tid,))
                    total_paid_so_far = float(df_pmts["amount"].sum()) if not df_pmts.empty else 0.0

                    if df_pmts.empty:
                        st.markdown('<div class="empty-state" style="padding:1rem">'
                                    '<div class="es-icon">💸</div>No payments logged yet.</div>',
                                    unsafe_allow_html=True)
                    else:
                        running = A
                        for _, p in df_pmts.iterrows():
                            pid_p   = int(p["payment_id"])
                            p_amt_v = float(p["amount"])
                            _bill_dt_tl = datetime.strptime(str(txn["date"]), "%Y-%m-%d").date()
                            d_from  = _days_30_360(_bill_dt_tl,
                                                   datetime.strptime(str(p["payment_date"]),"%Y-%m-%d").date())
                            edit_pmt_key = f"edit_pmt_{tid}_{pid_p}"
                            if edit_pmt_key not in st.session_state:
                                st.session_state[edit_pmt_key] = False

                            # Detect auto-allocated payment for badge + sync
                            _pmt_note        = str(p.get("note") or "")
                            _is_auto_alloc   = _pmt_note.startswith("Auto-allocated from passbook #")
                            _auto_alloc_eid  = None
                            if _is_auto_alloc:
                                try:
                                    _auto_alloc_eid = int(_pmt_note.split("#")[1].split()[0])
                                except (IndexError, ValueError):
                                    pass

                            # Build method display — append cheque details when applicable
                            _p_chq_extra = ""
                            if p["method"] == "Cheque" and p.get("cheque_number"):
                                _p_chq_extra = (
                                    f'<div style="font-size:0.72rem;color:#8a8070;margin-top:2px">'
                                    f'#{h(p["cheque_number"])} &nbsp;·&nbsp; '
                                    f'{fmt_date(p.get("cheque_date"))}</div>')

                            # Auto-allocated badge
                            _auto_badge_html = ""
                            if _is_auto_alloc and _auto_alloc_eid:
                                _auto_badge_html = (
                                    f'<span title="🔗 Linked to passbook entry #{_auto_alloc_eid} '
                                    f'— editing here syncs both" '
                                    f'style="font-size:0.62rem;background:#0d1e18;color:#4dc89a;'
                                    f'border:1px solid #1a4033;border-radius:3px;padding:1px 5px;'
                                    f'margin-left:6px;cursor:help">🔗 Auto</span>')

                            st.markdown(f'<div class="tl-node" style="padding-left:28px;position:relative;margin-bottom:0.3rem">'
                                        f'<div class="tl-dot paid" style="position:absolute;left:4px;top:8px"></div>'
                                        f'<div class="tl-card">'
                                        f'<div class="tl-date">💳 {fmt_date(p["payment_date"])} · #{pid_p}</div>'
                                        f'<div class="tl-row">'
                                        f'<div><div class="tl-label">Amount Paid</div>'
                                        f'<div class="tl-amount">{fmt_inr(p_amt_v)}{_auto_badge_html}</div></div>'
                                        f'<div><div class="tl-label">Days from Bill</div><div class="tl-days">{d_from} days</div></div>'
                                        f'<div><div class="tl-label">Method</div>'
                                        f'<div class="tl-label" style="color:#c8bfa8">{h(p["method"])}{_p_chq_extra}</div></div>'
                                        f'<div><div class="tl-label">Note</div><div class="tl-label" style="color:#c8bfa8">'
                                        f'{h(p["note"]) if p["note"] else "–"}</div></div>'
                                        f'</div></div></div>', unsafe_allow_html=True)

                            pb1, pb2, _ = st.columns([0.7, 0.7, 6])
                            with pb1:
                                if st.button("✏️ Edit", key=f"epb_{tid}_{pid_p}", use_container_width=True):
                                    st.session_state[edit_pmt_key] = True; st.rerun()
                            with pb2:
                                if st.button("🗑 Del", key=f"dpb_{tid}_{pid_p}", use_container_width=True):
                                    with conn:
                                        log_audit(conn, "payments", pid_p, "DELETE",
                                                  old_value={"amount": float(p["amount"]),
                                                             "payment_date": str(p["payment_date"])})
                                        # ── Passbook sync: delete linked cheque entry ──
                                        if p["method"] == "Cheque":
                                            conn.execute(
                                                "DELETE FROM passbook_entries "
                                                "WHERE source_type=%s AND source_id=%s",
                                                (SRC_CUST_CHQ_PMT, pid_p))
                                        # ── Passbook sync: revert auto-allocated passbook entry ──
                                        if _is_auto_alloc and _auto_alloc_eid:
                                            conn.execute(
                                                "UPDATE passbook_entries SET details='Suspense',"
                                                "source_type=%s,source_id=NULL "
                                                "WHERE entry_id=%s AND source_type=%s",
                                                (SRC_MANUAL, _auto_alloc_eid, SRC_ALLOCATION))
                                        # ── CASH IN HAND SYNC ──────────────────────────────
                                        if p["method"] == "Cash":
                                            try:
                                                conn.execute(
                                                    "DELETE FROM cash_in_hand_entries "
                                                    "WHERE source_type=%s AND source_id=%s",
                                                    (SRC_CUSTOMER_CASH, pid_p))
                                            except psycopg2.errors.UniqueViolation:
                                                pass
                                        conn.execute("DELETE FROM payments WHERE payment_id=%s", (pid_p,))
                                        rem_q = pg_read_sql(
                                            "SELECT COALESCE(SUM(amount),0) s FROM payments WHERE transaction_id=%s",
                                            conn, params=(tid,)).iloc[0]["s"]
                                        new_total = float(rem_q)
                                        new_st = ("Partial" if new_total > 0 else "Pending")
                                        conn.execute(
                                            "UPDATE customer_transactions SET payment_status=%s,"
                                            "calc_status='Pending',final_settlement=NULL,interest_amount=0 "
                                            "WHERE transaction_id=%s", (new_st, tid))
                                    st.rerun()

                            if st.session_state[edit_pmt_key]:
                                # Method + cheque fields outside form for dynamic visibility
                                st.markdown(f"**✏️ Edit Payment #{pid_p}**")
                                ep1, ep2 = st.columns(2)
                                with ep1:
                                    ep_date  = st.date_input("Payment Date",
                                        value=datetime.strptime(str(p["payment_date"]),"%Y-%m-%d").date(),
                                        key=f"epd_{tid}_{pid_p}")
                                    ep_amt_s = st.text_input("Amount (₹)",
                                        value=str(p_amt_v).rstrip("0").rstrip(".") if "." in str(p_amt_v) else str(int(p_amt_v)),
                                        key=f"epa_{tid}_{pid_p}")
                                with ep2:
                                    _mo = ["Cash","UPI","Bank Transfer","Cheque"]
                                    ep_method = st.selectbox("Method", _mo,
                                        index=_mo.index(p["method"]) if p["method"] in _mo else 0,
                                        key=f"epm_{tid}_{pid_p}")
                                    ep_note = st.text_input("Note", value=p["note"] or "",
                                                            key=f"epn_{tid}_{pid_p}")
                                if ep_method == "Cheque":
                                    epc1, epc2 = st.columns(2)
                                    with epc1:
                                        ep_chq_no = st.text_input("Cheque Number",
                                            value=str(p.get("cheque_number") or ""),
                                            key=f"epcn_{tid}_{pid_p}")
                                    with epc2:
                                        _ep_chq_default = (
                                            datetime.strptime(str(p["cheque_date"]),"%Y-%m-%d").date()
                                            if p.get("cheque_date") else ep_date)
                                        ep_chq_date = st.date_input("Cheque Date",
                                            value=_ep_chq_default, key=f"epcd_{tid}_{pid_p}")
                                    _ep_dep_default = str(p.get("deposit_firm") or FIRM_SP)
                                    ep_dep_firm = st.radio(
                                        "Deposit to", ["SP Spices", "Mukund Traders"],
                                        horizontal=True,
                                        index=0 if _ep_dep_default == FIRM_SP else 1,
                                        key=f"epdepf_{tid}_{pid_p}")
                                else:
                                    ep_chq_no, ep_chq_date, ep_dep_firm = None, None, None
                                with st.form(f"edit_pmt_form_{tid}_{pid_p}"):
                                    ef1, ef2 = st.columns(2)
                                    with ef1:
                                        if st.form_submit_button("💾 Save Changes", use_container_width=True):
                                            try:
                                                ep_amt = parse_slash_amount(ep_amt_s)
                                                if ep_amt <= 0:
                                                    st.error("Amount must be > 0.")
                                                elif ep_method == "Cheque" and not (ep_chq_no and ep_chq_no.strip()):
                                                    st.error("Cheque Number is required for Cheque payment.")
                                                else:
                                                    _bill_dt_ep   = datetime.strptime(str(txn["date"]), "%Y-%m-%d").date()
                                                    ep_days       = _days_30_360(_bill_dt_ep, ep_date)
                                                    ep_chq_no_sv  = ep_chq_no.strip() if ep_chq_no else None
                                                    ep_chq_dt_sv  = str(ep_chq_date) if ep_chq_date else None
                                                    with conn:
                                                        conn.execute(
                                                            "UPDATE payments SET payment_date=%s,amount=%s,method=%s,"
                                                            "note=%s,days_from_start=%s,cheque_number=%s,cheque_date=%s,"
                                                            "deposit_firm=%s "
                                                            "WHERE payment_id=%s",
                                                            (str(ep_date), ep_amt, ep_method,
                                                             ep_note.strip(), ep_days,
                                                             ep_chq_no_sv, ep_chq_dt_sv,
                                                             ep_dep_firm, pid_p))
                                                        new_tot_q = pg_read_sql(
                                                            "SELECT COALESCE(SUM(amount),0) s FROM payments WHERE transaction_id=%s",
                                                            conn, params=(tid,)).iloc[0]["s"]
                                                        new_tot_f = float(new_tot_q)
                                                        new_st = ("Partial" if new_tot_f > 0 else "Pending")
                                                        conn.execute(
                                                            "UPDATE customer_transactions SET payment_status=%s,"
                                                            "calc_status='Pending',final_settlement=NULL,interest_amount=0 "
                                                            "WHERE transaction_id=%s", (new_st, tid))
                                                        # ── Passbook sync: cheque method change ──
                                                        _old_m = str(p["method"] or "")
                                                        _brow3 = conn.execute(
                                                            "SELECT b.broker_name FROM brokers b "
                                                            "JOIN customer_transactions ct "
                                                            "ON b.broker_id=ct.broker_id "
                                                            "WHERE ct.transaction_id=%s", (tid,)).fetchone()
                                                        _bn3   = _brow3["broker_name"] if _brow3 else ""
                                                        _det3  = f"{txn['customer_name']} (via {_bn3})"
                                                        if _old_m == "Cheque" and ep_method == "Cheque":
                                                            conn.execute(
                                                                "UPDATE passbook_entries "
                                                                "SET firm=%s,entry_date=%s,details=%s,"
                                                                "amount=%s,cheque_number=%s "
                                                                "WHERE source_type=%s AND source_id=%s",
                                                                (ep_dep_firm,
                                                                 ep_chq_dt_sv or str(ep_date),
                                                                 _det3, round(ep_amt, 2),
                                                                 ep_chq_no_sv,
                                                                 SRC_CUST_CHQ_PMT, pid_p))
                                                        elif _old_m == "Cheque" and ep_method != "Cheque":
                                                            conn.execute(
                                                                "DELETE FROM passbook_entries "
                                                                "WHERE source_type=%s AND source_id=%s",
                                                                (SRC_CUST_CHQ_PMT, pid_p))
                                                        elif _old_m != "Cheque" and ep_method == "Cheque":
                                                            if ep_chq_no_sv and ep_dep_firm:
                                                                conn.execute(
                                                                    "INSERT INTO passbook_entries "
                                                                    "(firm,entry_date,details,amount,txn_type,"
                                                                    " cheque_number,cheque_status,"
                                                                    " source_type,source_id) "
                                                                    "VALUES (%s,%s,%s,%s,'Credit',%s,%s,%s,%s)",
                                                                    (ep_dep_firm,
                                                                     ep_chq_dt_sv or str(ep_date),
                                                                     _det3, round(ep_amt, 2),
                                                                     ep_chq_no_sv,
                                                                     CHQ_PENDING,
                                                                     SRC_CUST_CHQ_PMT, pid_p))
                                                        # ── Passbook sync: auto-allocated entry amount/date ──
                                                        if _is_auto_alloc and _auto_alloc_eid:
                                                            conn.execute(
                                                                "UPDATE passbook_entries "
                                                                "SET amount=%s,entry_date=%s "
                                                                "WHERE entry_id=%s "
                                                                "AND source_type=%s",
                                                                (round(ep_amt, 2), str(ep_date),
                                                                 _auto_alloc_eid, SRC_ALLOCATION))
                                                        # ── CASH IN HAND SYNC ──────────────────────────────
                                                        if _old_m == "Cash" and ep_method == "Cash":
                                                            conn.execute(
                                                                "UPDATE cash_in_hand_entries "
                                                                "SET entry_date=%s,amount=%s "
                                                                "WHERE source_type=%s AND source_id=%s",
                                                                (str(ep_date), round(ep_amt, 2),
                                                                 SRC_CUSTOMER_CASH, pid_p))
                                                        elif _old_m == "Cash" and ep_method != "Cash":
                                                            try:
                                                                conn.execute(
                                                                    "DELETE FROM cash_in_hand_entries "
                                                                    "WHERE source_type=%s AND source_id=%s",
                                                                    (SRC_CUSTOMER_CASH, pid_p))
                                                            except psycopg2.errors.UniqueViolation:
                                                                pass
                                                        elif _old_m != "Cash" and ep_method == "Cash":
                                                            conn.execute(
                                                                "INSERT INTO cash_in_hand_entries "
                                                                "(entry_date,details,amount,txn_type,"
                                                                " source_type,source_id) "
                                                                "VALUES (%s,%s,%s,%s,%s,%s)",
                                                                (str(ep_date),
                                                                 f"Cash from {bname} (Txn: {fmt_date(str(txn['date']))})",
                                                                 round(ep_amt, 2), 'Credit',
                                                                 SRC_CUSTOMER_CASH, pid_p))
                                                    st.session_state[edit_pmt_key] = False
                                                    st.success(f"Payment #{pid_p} updated. Please recalculate.")
                                                    st.rerun()
                                            except (ValueError, InvalidOperation) as e:
                                                st.error(f"Invalid amount: {e}")
                                    with ef2:
                                        if st.form_submit_button("Cancel", use_container_width=True):
                                            st.session_state[edit_pmt_key] = False; st.rerun()
                            running -= p_amt_v

                        if running > 0:
                            st.markdown(f'<div class="tl-node" style="padding-left:28px;position:relative;margin-top:0.3rem">'
                                        f'<div class="tl-dot remaining" style="position:absolute;left:4px;top:8px"></div>'
                                        f'<div class="tl-remaining"><div class="tl-remaining-label">Outstanding balance</div>'
                                        f'<div class="tl-amount" style="color:#6a9fd4">{fmt_inr(running)}</div>'
                                        f'</div></div>', unsafe_allow_html=True)

                    # ── Log Payment ───────────────────────────────
                    log_pmt_key = f"lpmt_{tid}"
                    if log_pmt_key not in st.session_state:
                        st.session_state[log_pmt_key] = False
                    if not st.session_state[log_pmt_key]:
                        if st.button("＋ Log Payment", key=f"lp_btn_{tid}"):
                            st.session_state[log_pmt_key] = True; st.rerun()
                    else:
                        st.markdown("**Log Intermediate Payment**")
                        pc1, pc2 = st.columns(2)
                        with pc1:
                            p_date  = st.date_input("Payment Date", value=date.today(), key=f"pd_{tid}")
                            p_amt_s = st.text_input("Amount (₹)", value="", placeholder="0.00",
                                                    key=f"pa_{tid}",
                                                    help="Use / as decimal")
                        with pc2:
                            p_method = st.selectbox("Method", ["Cash","UPI","Bank Transfer","Cheque"],
                                                    key=f"pm_{tid}")
                            p_note   = st.text_input("Note (optional)", key=f"pn_{tid}")
                        if p_method == "Cheque":
                            pcc1, pcc2 = st.columns(2)
                            with pcc1:
                                p_chq_no = st.text_input("Cheque Number", key=f"pchqn_{tid}")
                            with pcc2:
                                p_chq_date = st.date_input("Cheque Date", value=p_date,
                                                           key=f"pchqd_{tid}")
                            p_dep_firm = st.radio(
                                "Deposit to", ["SP Spices", "Mukund Traders"],
                                horizontal=True, index=0, key=f"pdepf_{tid}")
                        else:
                            p_chq_no, p_chq_date, p_dep_firm = None, None, None
                        st.markdown(f"*Outstanding before this payment: {fmt_inr(A - total_paid_so_far)}*")
                        with st.form(f"pmt_form_{tid}"):
                            pf1, pf2 = st.columns(2)
                            with pf1:
                                if st.form_submit_button("💾 Save Payment", use_container_width=True):
                                    try:
                                        p_amt = parse_slash_amount(p_amt_s)
                                        if p_amt <= 0:
                                            st.error("Amount must be > 0.")
                                        elif p_method == "Cheque" and not (p_chq_no and p_chq_no.strip()):
                                            st.error("Cheque Number is required for Cheque payment.")
                                        else:
                                            _bill_dt_lp   = datetime.strptime(str(txn["date"]), "%Y-%m-%d").date()
                                            d_fs          = _days_30_360(_bill_dt_lp, p_date)
                                            chq_no_save   = p_chq_no.strip() if p_chq_no else None
                                            chq_date_save = str(p_chq_date) if p_chq_date else None
                                            with conn:
                                                _pmt_cur = conn.execute(
                                                    "INSERT INTO payments "
                                                    "(transaction_id,payment_date,amount,method,note,"
                                                    "days_from_start,cheque_number,cheque_date,deposit_firm) "
                                                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) "
                                                    "RETURNING payment_id",
                                                    (tid, str(p_date), p_amt, p_method,
                                                     p_note.strip(), d_fs, chq_no_save,
                                                     chq_date_save, p_dep_firm))
                                                new_pmt_id = _pmt_cur.fetchone()["payment_id"]
                                                new_total_paid = total_paid_so_far + p_amt
                                                new_status = ("Partial" if new_total_paid > 0 else "Pending")
                                                conn.execute(
                                                    "UPDATE customer_transactions SET payment_status=%s,"
                                                    "calc_status='Pending',final_settlement=NULL,interest_amount=0 "
                                                    "WHERE transaction_id=%s",
                                                    (new_status, tid))
                                                log_audit(conn, "payments", new_pmt_id, "INSERT",
                                                          new_value={"amount": p_amt,
                                                                     "date": str(p_date),
                                                                     "transaction_id": tid})
                                                # ── Passbook sync: cheque intermediate payment ──
                                                if p_method == "Cheque" and chq_no_save and p_dep_firm:
                                                    _brow2 = conn.execute(
                                                        "SELECT b.broker_name FROM brokers b "
                                                        "JOIN customer_transactions ct "
                                                        "ON b.broker_id=ct.broker_id "
                                                        "WHERE ct.transaction_id=%s", (tid,)).fetchone()
                                                    _bn2 = _brow2["broker_name"] if _brow2 else ""
                                                    conn.execute(
                                                        "INSERT INTO passbook_entries "
                                                        "(firm,entry_date,details,amount,txn_type,"
                                                        " cheque_number,cheque_status,"
                                                        " source_type,source_id) "
                                                        "VALUES (%s,%s,%s,%s,'Credit',%s,%s,%s,%s)",
                                                        (p_dep_firm,
                                                         chq_date_save or str(p_date),
                                                         f"{txn['customer_name']} (via {_bn2})",
                                                         round(p_amt, 2),
                                                         chq_no_save,
                                                         CHQ_PENDING,
                                                         SRC_CUST_CHQ_PMT, new_pmt_id))
                                                # ── CASH IN HAND SYNC ──────────────────────────────
                                                if p_method == "Cash":
                                                    conn.execute(
                                                        "INSERT INTO cash_in_hand_entries "
                                                        "(entry_date,details,amount,txn_type,"
                                                        " source_type,source_id) "
                                                        "VALUES (%s,%s,%s,%s,%s,%s)",
                                                        (str(p_date),
                                                         f"Cash from {bname} (Txn: {fmt_date(str(txn['date']))})",
                                                         round(p_amt, 2), 'Credit',
                                                         SRC_CUSTOMER_CASH, new_pmt_id))
                                            st.session_state[log_pmt_key] = False
                                            st.success(f"Payment of {fmt_inr(p_amt)} saved!"); st.rerun()
                                    except (ValueError, InvalidOperation) as e:
                                        st.error(f"Invalid amount: {e}")
                            with pf2:
                                if st.form_submit_button("Cancel", use_container_width=True):
                                    st.session_state[log_pmt_key] = False; st.rerun()

                    # ── ACTION BUTTONS ────────────────────────────
                    st.markdown("<br>", unsafe_allow_html=True)
                    edit_key = f"edit_{tid}"
                    calc_key = f"calc_{tid}"
                    view_key = f"view_{tid}"
                    for _k in [edit_key, calc_key, view_key]:
                        if _k not in st.session_state:
                            st.session_state[_k] = False

                    if not st.session_state[edit_key] and not st.session_state[calc_key] and not st.session_state[view_key]:
                        b1, b2, b3, b4, b5 = st.columns([1.1, 1.1, 1.1, 1.5, 1.6])
                        with b1:
                            if st.button("✏️ Edit", key=f"eb_{tid}", use_container_width=True):
                                st.session_state[edit_key] = True; st.rerun()
                        with b2:
                            if st.button("🗑 Delete", key=f"db_{tid}", use_container_width=True):
                                # ── Passbook sync: revert CustomerAllocation entries ──
                                conn.execute(
                                    "UPDATE passbook_entries SET details='Suspense',"
                                    "source_type=%s,source_id=NULL "
                                    "WHERE source_type=%s AND source_id=%s",
                                    (SRC_MANUAL, SRC_ALLOCATION, tid))
                                # ── CASH IN HAND SYNC ──────────────────────────────
                                try:
                                    conn.execute(
                                        "DELETE FROM cash_in_hand_entries "
                                        "WHERE source_type=%s AND source_id IN ("
                                        "  SELECT payment_id FROM payments "
                                        "  WHERE transaction_id=%s AND method='Cash'"
                                        ")", (SRC_CUSTOMER_CASH, tid))
                                except Exception:
                                    pass
                                conn.execute("DELETE FROM payments WHERE transaction_id=%s", (tid,))
                                conn.execute("DELETE FROM transaction_items WHERE transaction_id=%s", (tid,))
                                conn.execute("DELETE FROM customer_transactions WHERE transaction_id=%s", (tid,))
                                conn.commit(); st.rerun()
                        with b3:
                            if has_settle:
                                if st.button("📄 View Bill", key=f"vb_{tid}", use_container_width=True):
                                    st.session_state[view_key] = True; st.rerun()
                        with b4:
                            clbl = "🧮 Recalculate" if has_settle else "🧮 Calculate Bill"
                            if st.button(clbl, key=f"cb_{tid}", use_container_width=True):
                                st.session_state[calc_key] = True
                                st.session_state[view_key] = False; st.rerun()
                        with b5:
                            bp_lbl = "✓ Brok. Paid" if brok_paid == "Unpaid" else "✗ Brok. Unpaid"
                            if st.button(bp_lbl, key=f"bp_{tid}", use_container_width=True):
                                conn.execute(
                                    "UPDATE customer_transactions SET brokerage_paid=%s WHERE transaction_id=%s",
                                    ("Paid" if brok_paid == "Unpaid" else "Unpaid", tid))
                                conn.commit(); st.rerun()

                    # ── VIEW BILL ─────────────────────────────────
                    if st.session_state.get(view_key) and has_settle:
                        st.markdown("---")
                        st.markdown("#### 📋 Settled Bill")
                        d_p      = float(txn.get("discount_pct", 0) or 0)
                        b_f      = bool(txn.get("brokerage_applied", 0))
                        disc_amt = float(txn.get("discount_amount", 0) or 0)
                        brok_amt = float(txn.get("brokerage_amount", 0) or 0)
                        int_amt  = float(txn.get("interest_amount", 0) or 0)
                        g_days_v = int(txn.get("grace_days", 0) or 0)
                        d_over_v = int(txn.get("days_overdue", 0) or 0)
                        p_rcvd   = float(df_pmts["amount"].sum()) if not df_pmts.empty else 0.0

                        if d_p == 0.0 and disc_amt > 0:
                            d_note = "(custom fixed amount)"
                        elif d_p > 0:
                            d_note = f"({d_p}% of A)"
                        else:
                            d_note = "(not applied)"
                        b_note = "(1% of A)" if b_f else "(not applied)"
                        i_note = ("(= ₹0 — Discount applied)" if disc_amt > 0
                                  else f"({INTEREST_RATE_PCT}% p.a. × {d_over_v} interest days ÷ 360)")

                        if settle_val < -0.01:
                            _sv_color = "#6a9fd4"
                            _sv_label = "= Amount to Return (Overpaid)"
                        elif abs(settle_val) < 0.01:
                            _sv_color = "#f0ebe0"
                            _sv_label = "= Final Balance Due"
                        else:
                            _sv_color = "#8dd87a"
                            _sv_label = "= Final Balance Due"
                        st.markdown(f'<div class="bill-breakdown">'
                                    f'<div class="bb-row"><span class="bb-label">Total Amount (A)</span>'
                                    f'<span class="bb-val">{fmt_inr(A)}</span></div>'
                                    f'<div class="bb-row bb-neg"><span class="bb-label">− Payment Received (P)</span>'
                                    f'<span class="bb-val">{fmt_inr(p_rcvd)}</span></div>'
                                    f'<div class="bb-row bb-neg"><span class="bb-label">− Discount &nbsp;<em style="color:#3a3628;font-size:.75rem">{d_note}</em></span>'
                                    f'<span class="bb-val">{fmt_inr(disc_amt)}</span></div>'
                                    f'<div class="bb-row bb-neg"><span class="bb-label">− Brokerage &nbsp;<em style="color:#3a3628;font-size:.75rem">{b_note}</em></span>'
                                    f'<span class="bb-val">{fmt_inr(brok_amt)}</span></div>'
                                    f'<div class="bb-row bb-pos"><span class="bb-label">+ Interest &nbsp;<em style="color:#3a3628;font-size:.75rem">{i_note}</em></span>'
                                    f'<span class="bb-val">{fmt_inr(int_amt)}</span></div>'
                                    f'<div class="bb-row bb-total"><span class="bb-label">{_sv_label}</span>'
                                    f'<span class="bb-val" style="color:{_sv_color}">{fmt_inr(settle_val)}</span></div></div>',
                                    unsafe_allow_html=True)

                        if st.button("✕  Close Bill View", key=f"close_view_{tid}"):
                            st.session_state[view_key] = False; st.rerun()

                    # ── CALCULATE BILL ────────────────────────────
                    if st.session_state[calc_key]:
                        st.markdown("---")
                        st.markdown("#### 🧮 Calculate Bill")
                        prev_dpct = float(txn.get("discount_pct", 0) or 0)
                        prev_brok = bool(txn.get("brokerage_applied", 0))

                        pmts_for_calc = float(pg_read_sql(
                            "SELECT COALESCE(SUM(amount),0) s FROM payments WHERE transaction_id=%s",
                            conn, params=(tid,)).iloc[0]["s"])

                        _SETT_METHODS = ["Cash","UPI","Bank Transfer","Cheque"]
                        sett_method = st.selectbox(
                            "Payment Method for Settlement", _SETT_METHODS,
                            key=f"sett_method_{tid}")
                        if sett_method == "Cheque":
                            scol1, scol2 = st.columns(2)
                            with scol1:
                                sett_chq_no = st.text_input(
                                    "Cheque Number", key=f"sett_chqno_{tid}")
                            with scol2:
                                sett_chq_date = st.date_input(
                                    "Cheque Date", value=date.today(),
                                    key=f"sett_chqdt_{tid}")
                            sett_dep_firm = st.radio(
                                "Deposit to", ["SP Spices", "Mukund Traders"],
                                horizontal=True, index=0,
                                key=f"sett_depf_{tid}")
                        else:
                            sett_chq_no, sett_chq_date, sett_dep_firm = None, None, None

                        # Discount section outside form so "Custom Amount" shows dynamically
                        _prev_disc_amt = float(txn.get("discount_amount", 0) or 0)
                        if prev_dpct == 0.5:
                            _disc_default = "0.5%";  _custom_disc_default = ""
                        elif prev_dpct == 1.0:
                            _disc_default = "1%";    _custom_disc_default = ""
                        elif prev_dpct == 0.0 and _prev_disc_amt == 0.0:
                            _disc_default = "None (0%)"; _custom_disc_default = ""
                        else:
                            _disc_default = "Custom Amount"
                            _custom_disc_default = (
                                str(_prev_disc_amt).rstrip("0").rstrip(".")
                                if _prev_disc_amt else "")
                        _disc_opts = ["None (0%)", "0.5%", "1%", "Custom Amount"]
                        disc_choice = st.radio(
                            "Discount", _disc_opts,
                            index=_disc_opts.index(_disc_default),
                            horizontal=True, key=f"disc_radio_{tid}")
                        if disc_choice == "Custom Amount":
                            custom_disc_amt_s = st.text_input(
                                "Discount Amount (₹)",
                                value=_custom_disc_default,
                                placeholder="e.g. 500 or 1500/50",
                                help="Enter exact rupee amount to deduct. "
                                     "Use / as decimal separator.",
                                key=f"custom_disc_{tid}")
                        else:
                            custom_disc_amt_s = ""

                        with st.form(f"calc_form_{tid}"):
                            st.markdown(f"**Base Total Amount (A) = {fmt_inr(A)}**")
                            st.markdown('<div class="rule-note">ℹ️ Discount and Interest are mutually exclusive — '
                                        'selecting a Discount % sets Interest to ₹0.</div>',
                                        unsafe_allow_html=True)
                            st.markdown(f'<div class="info-chip">📐 Interest rate: {INTEREST_RATE_PCT}% p.a. (fixed) '
                                        f'| Interest starts after grace period</div>', unsafe_allow_html=True)
                            cf2, cf3 = st.columns(2)
                            with cf2:
                                apply_brok        = st.radio("Brokerage (1% of Total)",["No","Yes"],
                                                             index=1 if prev_brok else 0, horizontal=True)
                                settle_date_input = st.date_input("Settlement Date", value=date.today())
                            with cf3:
                                P_s = st.text_input("Payment Already Received (₹)",
                                    value=str(pmts_for_calc).replace(".","/") if pmts_for_calc else "",
                                    placeholder="" if pmts_for_calc else "0.00",
                                    help="Pre-filled from logged payments. Use / as decimal.")
                                grace_days_s = st.text_input("Grace Days",
                                    value="",
                                    placeholder=str(DEFAULT_GRACE_DAYS),
                                    help="Days excluded from interest calculation")

                            if st.form_submit_button("🔍  Preview Bill", use_container_width=True):
                                if sett_method == "Cheque" and not (sett_chq_no and sett_chq_no.strip()):
                                    st.error("Cheque Number is required for Cheque payment.")
                                    st.stop()
                                try:
                                    P_val  = parse_slash_amount(P_s)
                                except Exception:
                                    P_val  = pmts_for_calc
                                try:
                                    g_days = max(int(parse_slash_amount(grace_days_s)), 0)
                                except Exception:
                                    g_days = DEFAULT_GRACE_DAYS

                                if disc_choice == "Custom Amount":
                                    try:
                                        D_amt = round(parse_slash_amount(custom_disc_amt_s), 2)
                                    except (ValueError, InvalidOperation):
                                        st.error("Please enter a valid discount amount.")
                                        st.stop()
                                    if D_amt < 0:
                                        st.error("Discount amount cannot be negative.")
                                        st.stop()
                                    if D_amt > A:
                                        st.error(
                                            f"Discount ({fmt_inr(D_amt)}) cannot exceed "
                                            f"Total Bill ({fmt_inr(A)}).")
                                        st.stop()
                                    d_pct = 0.0
                                else:
                                    disc_map = {"None (0%)": 0.0, "0.5%": 0.5, "1%": 1.0}
                                    d_pct    = disc_map[disc_choice]
                                    D_amt    = round(A * d_pct / 100, 2)
                                b_flag = (apply_brok == "Yes")

                                conn.execute(
                                    "UPDATE customer_transactions SET interest_rate_pct=%s,discount_pct=%s,"
                                    "discount_amount=%s,brokerage_applied=%s,brokerage_amount=%s "
                                    "WHERE transaction_id=%s",
                                    (INTEREST_RATE_PCT, d_pct, D_amt,
                                     1 if b_flag else 0,
                                     round(A*0.01,2) if b_flag else 0.0, tid))
                                conn.commit()

                                res = calculate_final_settlement(tid, settle_date_input, g_days, conn=conn)

                                if not res.get("payments"):
                                    _int_days  = max(_days_30_360(res["start_date"], settle_date_input) - g_days, 0)
                                    _rem_prin  = A - P_val
                                    _total_i   = 0.0 if D_amt > 0 else round(
                                        max(_rem_prin, 0) * (INTEREST_RATE_PCT/100) * (_int_days/360), 2)
                                    _final_bal = round(_rem_prin - D_amt -
                                                       (round(A*0.01,2) if b_flag else 0.0) + _total_i, 2)
                                    res.update({
                                        "total_interest": _total_i, "final_balance_due": _final_bal,
                                        "remaining_principal": _rem_prin, "remaining_interest": _total_i,
                                        "remaining_int_days": _int_days,
                                        "remaining_days": max((settle_date_input - res["start_date"]).days,0),
                                        "total_paid": P_val,
                                    })

                                # Custom fixed discount: override calculator which uses disc_pct from DB
                                if D_amt > 0 and d_pct == 0.0:
                                    res["discount_amount"]    = D_amt
                                    res["total_interest"]     = 0.0
                                    res["remaining_interest"] = 0.0
                                    for _rp in res.get("payments", []):
                                        _rp["interest"] = 0.0
                                    res["final_balance_due"] = round(
                                        res["remaining_principal"] - D_amt
                                        - res.get("brokerage_amount", 0.0), 2)

                                res["discount_pct"]    = d_pct
                                res["grace_days"]      = g_days
                                res["payment_entered"] = P_val
                                st.session_state[f"preview_{tid}"]       = res
                                st.session_state[f"preview_dpct_{tid}"]  = d_pct
                                st.session_state[f"preview_bflag_{tid}"] = b_flag
                                st.session_state[f"preview_P_{tid}"]     = P_val
                                st.session_state[f"preview_gd_{tid}"]    = g_days
                                st.rerun()

                        prev_res = st.session_state.get(f"preview_{tid}")
                        if prev_res:
                            r      = prev_res
                            d_p    = st.session_state.get(f"preview_dpct_{tid}", 0)
                            b_f    = st.session_state.get(f"preview_bflag_{tid}", False)
                            P_disp = st.session_state.get(f"preview_P_{tid}", 0)
                            gd_disp= st.session_state.get(f"preview_gd_{tid}", DEFAULT_GRACE_DAYS)

                            _preview_disc_amt = r.get("discount_amount", 0) or 0
                            i_note = ("(= ₹0 — Discount applied)" if _preview_disc_amt > 0
                                      else f"({INTEREST_RATE_PCT}% p.a. × "
                                           f"{r.get('remaining_int_days',r.get('remaining_days',0))} interest days ÷ 360)")
                            if d_p == 0.0 and _preview_disc_amt > 0:
                                d_note = "(custom fixed amount)"
                            elif d_p > 0:
                                d_note = f"({d_p}% of A)"
                            else:
                                d_note = "(not applied)"
                            b_note = "(1% of A)" if b_f else "(not applied)"

                            _fbd_r = r["final_balance_due"]
                            if _fbd_r < -0.01:
                                _fpc = "#6a9fd4"; _fpl = "= Amount to Return (Overpaid)"
                            elif abs(_fbd_r) < 0.01:
                                _fpc = "#f0ebe0"; _fpl = "= Final Balance Due"
                            else:
                                _fpc = "#8dd87a"; _fpl = "= Final Balance Due"
                            st.markdown(f'<div class="bill-breakdown">'
                                        f'<div class="bb-row"><span class="bb-label">Total Amount (A)</span>'
                                        f'<span class="bb-val">{fmt_inr(A)}</span></div>'
                                        f'<div class="bb-row bb-neg"><span class="bb-label">− Payment Received (P)</span>'
                                        f'<span class="bb-val">{fmt_inr(P_disp)}</span></div>'
                                        f'<div class="bb-row bb-neg"><span class="bb-label">− Discount &nbsp;<em style="color:#3a3628;font-size:.75rem">{d_note}</em></span>'
                                        f'<span class="bb-val">{fmt_inr(r["discount_amount"])}</span></div>'
                                        f'<div class="bb-row bb-neg"><span class="bb-label">− Brokerage &nbsp;<em style="color:#3a3628;font-size:.75rem">{b_note}</em></span>'
                                        f'<span class="bb-val">{fmt_inr(r["brokerage_amount"])}</span></div>'
                                        f'<div class="bb-row bb-pos"><span class="bb-label">+ Interest &nbsp;<em style="color:#3a3628;font-size:.75rem">{i_note}</em></span>'
                                        f'<span class="bb-val">{fmt_inr(r["total_interest"])}</span></div>'
                                        f'<div class="bb-row bb-total"><span class="bb-label">{_fpl}</span>'
                                        f'<span class="bb-val" style="color:{_fpc}">{fmt_inr(_fbd_r)}</span></div></div>',
                                        unsafe_allow_html=True)

                            if r.get("overpayment", 0) > 0:
                                st.warning(
                                    f"⚠️ Overpayment detected: customer paid "
                                    f"{fmt_inr(r['overpayment'])} more than the bill total. "
                                    f"Final balance shown in blue — this amount is owed back to the customer.")

                            sc1, sc2 = st.columns(2)
                            with sc1:
                                if st.button("💾  Save Settlement", key=f"commit_{tid}", use_container_width=True):
                                    if sett_method == "Cheque" and not (sett_chq_no and sett_chq_no.strip()):
                                        st.error("Cheque Number is required for Cheque payment.")
                                    else:
                                        _actual_paid = float(conn.execute(
                                            "SELECT COALESCE(SUM(amount),0) AS v FROM payments WHERE transaction_id=%s",
                                            (tid,)).fetchone()["v"])
                                        _p_to_save    = _actual_paid if _actual_paid > 0 else P_disp
                                        _days_overdue = r.get("remaining_int_days", 0)
                                        _sv_chq_no    = sett_chq_no.strip() if sett_chq_no else None
                                        _sv_chq_dt    = str(sett_chq_date) if sett_chq_date else None
                                        # Reads pulled before the atomic block
                                        _brow4 = conn.execute(
                                            "SELECT broker_name FROM brokers WHERE broker_id=%s",
                                            (bid,)).fetchone()
                                        _bn4  = _brow4["broker_name"] if _brow4 else ""
                                        _det4 = f"{txn['customer_name']} (via {_bn4})"
                                        _existing_pb = conn.execute(
                                            "SELECT entry_id FROM passbook_entries "
                                            "WHERE source_type=%s AND source_id=%s",
                                            (SRC_CUST_CHQ_TXN, tid)).fetchone()

                                        with conn:
                                            conn.execute(
                                                "UPDATE customer_transactions SET "
                                                "interest_rate_pct=%s,discount_pct=%s,discount_amount=%s,"
                                                "brokerage_applied=%s,brokerage_amount=%s,"
                                                "interest_amount=%s,final_settlement=%s,"
                                                "payment_received=%s,grace_days=%s,days_overdue=%s,"
                                                "payment_method=%s,cheque_number=%s,cheque_date=%s,"
                                                "deposit_firm=%s,calc_status='Calculated' "
                                                "WHERE transaction_id=%s",
                                                (INTEREST_RATE_PCT, d_p, r["discount_amount"],
                                                 1 if b_f else 0, r["brokerage_amount"],
                                                 r["total_interest"], r["final_balance_due"],
                                                 _p_to_save, gd_disp, _days_overdue,
                                                 sett_method, _sv_chq_no, _sv_chq_dt,
                                                 sett_dep_firm, tid))
                                            for pmt in r.get("payments", []):
                                                conn.execute(
                                                    "UPDATE payments SET interest_charged=%s,days_from_start=%s "
                                                    "WHERE payment_id=%s",
                                                    (pmt["interest"], pmt["days"], pmt["payment_id"]))
                                            log_audit(conn, "customer_transactions", tid, "SETTLEMENT",
                                                      new_value={"final_balance_due": r["final_balance_due"],
                                                                 "total_interest": r["total_interest"],
                                                                 "grace_days": gd_disp,
                                                                 "days_overdue": _days_overdue})
                                            # ── Passbook sync: settlement cheque ──
                                            if sett_method == "Cheque" and _sv_chq_no and sett_dep_firm:
                                                if _existing_pb:
                                                    conn.execute(
                                                        "UPDATE passbook_entries "
                                                        "SET firm=%s,entry_date=%s,details=%s,"
                                                        "amount=%s,cheque_number=%s "
                                                        "WHERE entry_id=%s",
                                                        (sett_dep_firm,
                                                         _sv_chq_dt or str(date.today()),
                                                         _det4,
                                                         round(r["final_balance_due"], 2),
                                                         _sv_chq_no, _existing_pb[0]))
                                                else:
                                                    conn.execute(
                                                        "INSERT INTO passbook_entries "
                                                        "(firm,entry_date,details,amount,txn_type,"
                                                        " cheque_number,cheque_status,"
                                                        " source_type,source_id) "
                                                        "VALUES (%s,%s,%s,%s,'Credit',%s,%s,%s,%s)",
                                                        (sett_dep_firm,
                                                         _sv_chq_dt or str(date.today()),
                                                         _det4,
                                                         round(r["final_balance_due"], 2),
                                                         _sv_chq_no,
                                                         CHQ_PENDING,
                                                         SRC_CUST_CHQ_TXN, tid))
                                            else:
                                                conn.execute(
                                                    "DELETE FROM passbook_entries "
                                                    "WHERE source_type=%s AND source_id=%s",
                                                    (SRC_CUST_CHQ_TXN, tid))
                                            # ── CASH IN HAND SYNC — Settlement path ──────────────────
                                            if sett_method == "Cash" and float(P_disp or 0) > 0:
                                                _existing_cih = conn.execute("""
                                                    SELECT COALESCE(SUM(c.amount), 0) AS v
                                                    FROM cash_in_hand_entries c
                                                    JOIN payments p
                                                      ON c.source_id = p.payment_id
                                                     AND c.source_type = %s
                                                    WHERE p.transaction_id = %s
                                                """, (SRC_CUSTOMER_CASH, tid)).fetchone()["v"]
                                                _existing_cih = round(float(_existing_cih or 0), 2)
                                                _sett_amount  = round(float(P_disp), 2)
                                                _gap          = round(_sett_amount - _existing_cih, 2)
                                                if _gap > 0.01:
                                                    conn.execute("""
                                                        INSERT INTO cash_in_hand_entries
                                                            (entry_date, details, amount, txn_type,
                                                             source_type, source_id)
                                                        VALUES (%s, %s, %s, 'Credit', %s, %s)
                                                    """, (
                                                        str(settle_date_input),
                                                        f"Cash from {_bn4} (Txn: {fmt_date(str(txn['date']))})",
                                                        _gap,
                                                        SRC_CUSTOMER_CASH,
                                                        tid
                                                    ))
                                            # ── END CASH IN HAND SYNC ────────────────────────────────
                                        st.session_state[calc_key] = False
                                        st.session_state.pop(f"preview_{tid}", None)
                                        if tid in st.session_state.get("sum_intercept", []):
                                            st.session_state.sum_intercept.remove(tid)
                                        st.success(f"✓ Settlement {fmt_inr(r['final_balance_due'])} saved.")
                                        st.rerun()
                            with sc2:
                                if st.button("Cancel", key=f"cancel_calc_{tid}", use_container_width=True):
                                    st.session_state[calc_key] = False
                                    st.session_state.pop(f"preview_{tid}", None); st.rerun()

                    # ── EDIT PANEL ────────────────────────────────
                    if st.session_state[edit_key]:
                        st.markdown("---")
                        with st.form(f"edit_form_{tid}"):
                            st.markdown("**Edit Transaction**")
                            u_name   = st.text_input("Customer Name", txn["customer_name"])
                            try:
                                _txn_date_val = datetime.strptime(
                                    str(txn["date"]), "%Y-%m-%d").date()
                            except Exception:
                                _txn_date_val = date.today()
                            u_date   = st.date_input("Bill Date", value=_txn_date_val)
                            u_status = st.selectbox("Payment Status", ["Pending","Paid","Partial"],
                                index=["Pending","Paid","Partial"].index(txn["payment_status"]))
                            # Bill Sent field — editable, optional
                            _prev_bs = float(txn.get("bill_sent") or 0)
                            _bs_default = (f"{_prev_bs:.2f}".rstrip("0").rstrip(".")
                                           if _prev_bs else "")
                            u_bill_sent_s = st.text_input(
                                "Bill Sent (₹) — optional",
                                value=_bs_default,
                                help="Official invoice amount. Leave blank to clear.")
                            fc1, fc2 = st.columns(2)
                            with fc1:
                                if st.form_submit_button("Update", use_container_width=True):
                                    _u_bs_save = None
                                    _u_bs_ok   = True
                                    if u_bill_sent_s.strip():
                                        try:
                                            _ubsf = parse_slash_amount(u_bill_sent_s)
                                            if _ubsf <= 0:
                                                st.error("Bill Sent must be > 0 if set.")
                                                _u_bs_ok = False
                                            elif _ubsf > A:
                                                st.error(f"Bill Sent cannot exceed Total Amount ({fmt_inr(A)}).")
                                                _u_bs_ok = False
                                            else:
                                                _u_bs_save = round(_ubsf, 2)
                                        except (ValueError, InvalidOperation):
                                            st.error("Invalid Bill Sent amount.")
                                            _u_bs_ok = False
                                    if _u_bs_ok:
                                        conn.execute(
                                            "UPDATE customer_transactions "
                                            "SET customer_name=%s,date=%s,payment_status=%s,"
                                            "bill_sent=%s WHERE transaction_id=%s",
                                            (u_name, str(u_date), u_status, _u_bs_save, tid))
                                        conn.commit()
                                        st.session_state[edit_key] = False; st.rerun()
                            with fc2:
                                if st.form_submit_button("Cancel", use_container_width=True):
                                    st.session_state[edit_key] = False; st.rerun()

                st.markdown(
                    '<div style="border-bottom:1px solid #1a1810;'
                    'margin:0.2rem 0 0.4rem 0"></div>',
                    unsafe_allow_html=True)

    finally:
        conn.close()
