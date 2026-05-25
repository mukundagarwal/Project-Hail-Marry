"""
Passbook — bank account ledgers for SP Spices and Mukund Traders.
Navigation: st.session_state.pb_page in ("home", "firm", "cash")
"""

import math
import streamlit as st
import psycopg2
import pandas as pd
from datetime import date, datetime

from utils.db import (
    pg_read_sql,
    get_conn, ensure_schema,
    FIRM_SP, FIRM_MT, FIRMS,
    SRC_MANUAL, SRC_VENDOR_RTGS,
    SRC_CUST_CHQ_TXN, SRC_CUST_CHQ_PMT,
    SRC_ALLOCATION, SRC_OPENING,
    SRC_CUSTOMER_CASH, SRC_VENDOR_UB, SRC_CIH_OPENING,
    CHQ_PENDING, CHQ_CLEARED,
    TXNS_PER_PAGE,
)
from utils.styles import APP_CSS, BRAND_BAR_HTML, get_light_mode_css
from utils.formatters import fmt_inr, fmt_date, h, days_between
from utils.passbook_helpers import (
    compute_passbook_view, compute_cash_view,
    _firm_summary, _cih_summary,
    allocate_to_customer, _unlink_allocation,
)
from utils.auth import require_login, render_logout_button

st.set_page_config(
    page_title="Passbook – S P Spices",
    page_icon="📒",
    layout="wide",
    initial_sidebar_state="collapsed",
)

require_login()
render_logout_button()
ensure_schema()

st.markdown(APP_CSS, unsafe_allow_html=True)
if st.session_state.get("light_mode"):
    st.markdown(get_light_mode_css(), unsafe_allow_html=True)
st.markdown(BRAND_BAR_HTML, unsafe_allow_html=True)

# ── Session state ──────────────────────────────────────────────
for _k, _v in {
    "pb_page":             "home",
    "pb_selected_firm":    FIRM_SP,
    "pb_filter_mode":      "All",
    "pb_filter_single":    date.today(),
    "pb_filter_start":     date.today(),
    "pb_filter_end":       date.today(),
    "pb_search":           "",
    "pb_show_add_form":    False,
    "pb_show_ob_form":     False,
    # Cash in Hand
    "pb_cih_filter_mode":   "All",
    "pb_cih_filter_single": date.today(),
    "pb_cih_filter_start":  date.today(),
    "pb_cih_filter_end":    date.today(),
    "pb_cih_search":        "",
    "pb_show_cih_add_form": False,
    "pb_show_cih_ob_form":  False,
    # Pagination
    "pb_firm_page": 0,
    "pb_cih_page":  0,
}.items():
    if _k not in st.session_state:
        st.session_state[_k] = _v


# ══════════════════════════════════════════════════════════════
#  HELPERS
# ══════════════════════════════════════════════════════════════

def bal_color(x: float) -> str:
    if abs(x) < 0.01:
        return "#f0ebe0"
    return "#8dd87a" if x > 0 else "#ff5555"


def source_badge(source_type: str) -> str:
    _MAP = {
        SRC_MANUAL:       ('<span style="font-size:0.6rem;background:#1e1c14;color:#5a5448;'
                           'border:1px solid #2a2820;border-radius:3px;padding:1px 6px">Manual</span>'),
        SRC_VENDOR_RTGS:  ('<span style="font-size:0.6rem;background:#0d1830;color:#6a9fd4;'
                           'border:1px solid #1a3060;border-radius:3px;padding:1px 6px">Vendor RTGS</span>'),
        SRC_CUST_CHQ_TXN: ('<span style="font-size:0.6rem;background:#1a0d30;color:#b88adc;'
                           'border:1px solid #3a1860;border-radius:3px;padding:1px 6px">Cust. Cheque</span>'),
        SRC_CUST_CHQ_PMT: ('<span style="font-size:0.6rem;background:#1a0d30;color:#b88adc;'
                           'border:1px solid #3a1860;border-radius:3px;padding:1px 6px">Cust. Cheque</span>'),
        SRC_ALLOCATION:   ('<span style="font-size:0.6rem;background:#0d1e18;color:#4dc89a;'
                           'border:1px solid #1a4033;border-radius:3px;padding:1px 6px">Auto-Match</span>'),
        SRC_OPENING:      ('<span style="font-size:0.6rem;background:#1e1808;color:#e8c97e;'
                           'border:1px solid #3a3010;border-radius:3px;padding:1px 6px">Opening</span>'),
    }
    return _MAP.get(source_type or SRC_MANUAL, h(source_type or ""))


# compute_cash_view, _cih_summary, compute_passbook_view, _firm_summary,
# allocate_to_customer, and _unlink_allocation are imported from
# utils.passbook_helpers at the top of this file.


def cih_source_badge(source_type: str) -> str:
    _MAP = {
        SRC_MANUAL:        ('<span style="font-size:0.6rem;background:#1e1c14;color:#5a5448;'
                            'border:1px solid #2a2820;border-radius:3px;padding:1px 6px">Manual</span>'),
        SRC_CUSTOMER_CASH: ('<span style="font-size:0.6rem;background:#1a0d30;color:#b88adc;'
                            'border:1px solid #3a1860;border-radius:3px;padding:1px 6px">Customer Cash</span>'),
        SRC_VENDOR_UB:     ('<span style="font-size:0.6rem;background:#0d1830;color:#6a9fd4;'
                            'border:1px solid #1a3060;border-radius:3px;padding:1px 6px">Vendor UB</span>'),
        SRC_CIH_OPENING:   ('<span style="font-size:0.6rem;background:#1e1808;color:#e8c97e;'
                            'border:1px solid #3a3010;border-radius:3px;padding:1px 6px">Opening</span>'),
    }
    return _MAP.get(source_type or SRC_MANUAL, h(source_type or ""))


def _get_all_customers(conn) -> list:
    """Return sorted list of distinct customer names from customer_transactions."""
    rows = conn.execute(
        "SELECT DISTINCT customer_name FROM customer_transactions ORDER BY customer_name"
    ).fetchall()
    return [r["customer_name"] for r in rows]




# ══════════════════════════════════════════════════════════════
#  PAGE: HOME  (Level 1)
# ══════════════════════════════════════════════════════════════

if st.session_state.pb_page == "home":

    bcol, _ = st.columns([1, 8])
    with bcol:
        st.markdown('<div class="back-btn">', unsafe_allow_html=True)
        if st.button("← Home"):
            st.switch_page("app.py")
        st.markdown('</div>', unsafe_allow_html=True)

    st.markdown('<div class="page-title">Passbook</div>', unsafe_allow_html=True)
    st.markdown('<div class="page-sub">Bank account ledgers for both firms</div>',
                unsafe_allow_html=True)

    _home_conn = get_conn()
    try:
        stats_sp  = _firm_summary(FIRM_SP, conn=_home_conn)
        stats_mt  = _firm_summary(FIRM_MT, conn=_home_conn)
        stats_cih = _cih_summary(conn=_home_conn)
    finally:
        _home_conn.close()

    fc1, fc2 = st.columns(2, gap="medium")
    for col_w, firm_name, stats in [
        (fc1, FIRM_SP, stats_sp),
        (fc2, FIRM_MT, stats_mt),
    ]:
        with col_w:
            bc       = bal_color(stats["balance"])
            last_str = fmt_date(stats["last_date"]) if stats["last_date"] else "—"
            st.markdown(
                f'<div style="background:#181610;border:1px solid #2a2820;border-radius:14px;'
                f'padding:1.2rem 1.4rem;margin-bottom:0.6rem">'
                f'<div style="font-family:\'Playfair Display\',serif;font-size:1.2rem;'
                f'color:#e8c97e;margin-bottom:0.5rem">{h(firm_name)}</div>'
                f'<div style="font-size:1.6rem;font-weight:700;color:{bc};margin-bottom:0.4rem">'
                f'{fmt_inr(stats["balance"])}</div>'
                f'<div style="display:flex;gap:1.4rem;font-size:0.75rem;color:#5a5448">'
                f'<span>{stats["entry_count"]} entries</span>'
                f'<span>Last: {last_str}</span>'
                f'</div></div>', unsafe_allow_html=True)
            if st.button("Open Passbook →",
                         key=f"pb_open_{firm_name.replace(' ','_')}",
                         use_container_width=True):
                st.session_state["pb_selected_firm"] = firm_name
                st.session_state["pb_page"]          = "firm"
                st.session_state["pb_show_add_form"] = False
                st.session_state["pb_show_ob_form"]  = False
                st.rerun()

    # ── Cash in Hand card ──────────────────────────────────────
    cih_bc       = bal_color(stats_cih["balance"])
    cih_last_str = fmt_date(stats_cih["last_date"]) if stats_cih["last_date"] else "—"
    st.markdown(
        f'<div style="background:#181610;border:1px solid #1e3018;border-radius:14px;'
        f'padding:1.2rem 1.4rem;margin-top:0.4rem;margin-bottom:0.6rem">'
        f'<div style="font-family:\'Playfair Display\',serif;font-size:1.2rem;'
        f'color:#8dd87a;margin-bottom:0.5rem">💵 Cash in Hand</div>'
        f'<div style="font-size:1.6rem;font-weight:700;color:{cih_bc};margin-bottom:0.4rem">'
        f'{fmt_inr(stats_cih["balance"])}</div>'
        f'<div style="display:flex;gap:1.4rem;font-size:0.75rem;color:#5a5448">'
        f'<span>{stats_cih["entry_count"]} entries</span>'
        f'<span>Last: {cih_last_str}</span>'
        f'</div></div>', unsafe_allow_html=True)
    if st.button("Open Cash Register →", key="pb_open_cash", use_container_width=False):
        st.session_state["pb_page"]             = "cash"
        st.session_state["pb_show_cih_add_form"] = False
        st.session_state["pb_show_cih_ob_form"]  = False
        st.rerun()

    # ── Combined Net Balance pill (Part F) ─────────────────────
    bank_bal  = round(stats_sp["balance"] + stats_mt["balance"], 2)
    cih_bal   = stats_cih["balance"]
    total_bal = round(bank_bal + cih_bal, 2)
    st.markdown(
        f'<div style="display:inline-block;margin-top:0.8rem;padding:0.5rem 1.4rem;'
        f'background:#1e1c14;border:1px solid #2a2820;border-radius:20px;'
        f'font-size:0.85rem;color:#8a8070">'
        f'Combined Net Balance'
        f'&nbsp;&nbsp;·&nbsp;&nbsp;Bank: <span style="color:{bal_color(bank_bal)}">{fmt_inr(bank_bal)}</span>'
        f'&nbsp;&nbsp;·&nbsp;&nbsp;Cash: <span style="color:{bal_color(cih_bal)}">{fmt_inr(cih_bal)}</span>'
        f'&nbsp;&nbsp;·&nbsp;&nbsp;Total: <span style="font-weight:700;color:{bal_color(total_bal)}">'
        f'{fmt_inr(total_bal)}</span></div>', unsafe_allow_html=True)


# ══════════════════════════════════════════════════════════════
#  PAGE: FIRM PASSBOOK  (Level 2)
# ══════════════════════════════════════════════════════════════

elif st.session_state.pb_page == "firm":

    firm = st.session_state.pb_selected_firm

    bcol, _ = st.columns([1, 8])
    with bcol:
        st.markdown('<div class="back-btn">', unsafe_allow_html=True)
        if st.button("← Passbook"):
            for _key in list(st.session_state.keys()):
                if _key.startswith(("pb_edit_", "pb_del_")):
                    del st.session_state[_key]
            st.session_state.pb_page          = "home"
            st.session_state.pb_firm_page     = 0
            st.session_state.pb_show_add_form = False
            st.session_state.pb_show_ob_form  = False
            st.rerun()
        st.markdown('</div>', unsafe_allow_html=True)

    conn = get_conn()
    try:
        # ── Stats ──────────────────────────────────────────────
        df_all   = compute_passbook_view(firm, conn=conn)
        non_pend = df_all[~df_all["balance_is_pending"]] if not df_all.empty else pd.DataFrame()
        balance  = float(non_pend.iloc[0]["balance"]) if not non_pend.empty else 0.0
        pend_sum = float(df_all.loc[df_all["balance_is_pending"], "amount"].sum()) if not df_all.empty else 0.0
        tot_cr   = float(df_all.loc[df_all["txn_type"] == "Credit", "amount"].sum()) if not df_all.empty else 0.0
        tot_db   = float(df_all.loc[df_all["txn_type"] == "Debit",  "amount"].sum()) if not df_all.empty else 0.0
        n_ent    = len(df_all)
        bc       = bal_color(balance)

        st.markdown(
            f'<div style="background:linear-gradient(135deg,#1a1810,#201e14);'
            f'border:1px solid #2e2b1e;border-radius:14px;padding:1.2rem 1.6rem;'
            f'margin-bottom:1.2rem">'
            f'<div style="font-family:\'Playfair Display\',serif;font-size:1.5rem;'
            f'color:#e8c97e;margin-bottom:0.8rem">{h(firm)}</div>'
            f'<div style="display:flex;gap:2rem;flex-wrap:wrap">'
            f'<div><div style="font-size:.6rem;color:#3a3628;text-transform:uppercase;letter-spacing:.1em">Balance</div>'
            f'<div style="font-size:1.2rem;font-weight:700;color:{bc}">{fmt_inr(balance)}</div></div>'
            f'<div><div style="font-size:.6rem;color:#3a3628;text-transform:uppercase;letter-spacing:.1em">Pending Cheques</div>'
            f'<div style="font-size:1.1rem;font-weight:600;color:#d4864a">{fmt_inr(pend_sum)}</div></div>'
            f'<div><div style="font-size:.6rem;color:#3a3628;text-transform:uppercase;letter-spacing:.1em">Total Credits</div>'
            f'<div style="font-size:1.1rem;font-weight:600;color:#8dd87a">{fmt_inr(tot_cr)}</div></div>'
            f'<div><div style="font-size:.6rem;color:#3a3628;text-transform:uppercase;letter-spacing:.1em">Total Debits</div>'
            f'<div style="font-size:1.1rem;font-weight:600;color:#ff5555">{fmt_inr(tot_db)}</div></div>'
            f'<div><div style="font-size:.6rem;color:#3a3628;text-transform:uppercase;letter-spacing:.1em">Entries</div>'
            f'<div style="font-size:1.1rem;font-weight:600;color:#c8bfa8">{n_ent}</div></div>'
            f'</div></div>', unsafe_allow_html=True)

        # ── Action row ─────────────────────────────────────────
        ab1, ab2, ab3, ab4 = st.columns([1.2, 1.6, 2.5, 2.0], gap="small")
        with ab1:
            if st.button("＋  Add Transaction", key="pb_btn_add", use_container_width=True):
                st.session_state.pb_show_add_form = not st.session_state.pb_show_add_form
                st.session_state.pb_show_ob_form  = False
                st.rerun()
        with ab2:
            if st.button("⚙  Set Opening Balance", key="pb_btn_ob", use_container_width=True):
                st.session_state.pb_show_ob_form  = not st.session_state.pb_show_ob_form
                st.session_state.pb_show_add_form = False
                st.rerun()
        with ab3:
            pb_search = st.text_input("", placeholder="🔍  Search details...",
                                      label_visibility="collapsed", key="pb_search_inp")
        with ab4:
            _fopts   = ["All", "Single Date", "Date Range"]
            pb_fmode = st.selectbox("", _fopts, label_visibility="collapsed",
                                    key="pb_fmode_sel",
                                    index=_fopts.index(st.session_state.pb_filter_mode)
                                    if st.session_state.pb_filter_mode in _fopts else 0)
            st.session_state.pb_filter_mode = pb_fmode

        if pb_fmode == "Single Date":
            _dc1, _dc2, _ = st.columns([1.5, 1.5, 5])
            with _dc1:
                st.session_state.pb_filter_single = st.date_input(
                    "Date", value=st.session_state.pb_filter_single,
                    key="pb_fd_s", label_visibility="collapsed")
        elif pb_fmode == "Date Range":
            _dc1, _dc2, _ = st.columns([1.5, 1.5, 5])
            with _dc1:
                st.session_state.pb_filter_start = st.date_input(
                    "From", value=st.session_state.pb_filter_start,
                    key="pb_fd_r1", label_visibility="collapsed")
            with _dc2:
                st.session_state.pb_filter_end = st.date_input(
                    "To", value=st.session_state.pb_filter_end,
                    key="pb_fd_r2", label_visibility="collapsed")

        # ══════════════════════════════════════════════════════
        #  ADD TRANSACTION FORM
        # ══════════════════════════════════════════════════════
        if st.session_state.pb_show_add_form:
            st.markdown(
                '<div style="background:#181610;border:1px solid #2a2820;'
                'border-radius:10px;padding:1rem 1.2rem;margin-bottom:0.8rem">',
                unsafe_allow_html=True)
            st.markdown("**＋ Add Transaction**")
            af1, af2 = st.columns(2)
            with af1:
                a_date   = st.date_input("Date", value=date.today(), key="pb_af_date")
                a_amount = st.number_input("Amount (₹)", min_value=0.01, step=100.0,
                                           format="%.2f", key="pb_af_amount")
            with af2:
                a_type = st.radio("Type", ["Credit", "Debit"], horizontal=True,
                                  key="pb_af_type")

            # For Credits: searchable selectbox with customer names + Suspense default.
            # For Debits: plain text input.
            if a_type == "Credit":
                _all_custs    = _get_all_customers(conn)
                _detail_opts  = ["Suspense", "Type new name..."] + _all_custs
                a_details_sel = st.selectbox(
                    "Customer / Details",
                    _detail_opts, index=0, key="pb_af_details_sel",
                    help="Type to search customers. Select 'Suspense' if unknown.")
                if a_details_sel == "Type new name...":
                    a_details_raw = st.text_input("Enter custom name", key="pb_af_details_raw")
                    a_details     = a_details_raw.strip()
                else:
                    a_details = a_details_sel
            else:
                a_details = st.text_input("Details (required)", key="pb_af_details_debit")

            a_chq_no = st.text_input("Cheque Number (optional)", key="pb_af_chqno")
            a_chq_st = None
            if a_chq_no.strip():
                a_chq_st = st.radio("Cheque Status", ["Pending", "Cleared"],
                                    horizontal=True, key="pb_af_chqst")

            af_s1, af_s2, _ = st.columns([1, 1, 4])
            with af_s1:
                if st.button("💾 Save", key="pb_af_save", use_container_width=True):
                    _det = a_details.strip() if a_details else ""
                    if not _det:
                        st.error("Details / customer name required.")
                    elif a_amount <= 0:
                        st.error("Amount must be > 0.")
                    elif a_chq_no.strip() and not a_chq_st:
                        st.error("Select Cheque Status.")
                    else:
                        _chq_no = a_chq_no.strip() or None
                        _chq_st = a_chq_st if _chq_no else None

                        # Insert passbook entry + optionally cascade allocation
                        with conn:
                            _cur = conn.execute(
                                "INSERT INTO passbook_entries "
                                "(firm,entry_date,details,amount,txn_type,"
                                " cheque_number,cheque_status,source_type) "
                                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s) "
                                "RETURNING entry_id",
                                (firm, str(a_date), _det,
                                 round(a_amount, 2), a_type,
                                 _chq_no, _chq_st, SRC_MANUAL))
                            new_eid = _cur.fetchone()["entry_id"]

                            # If user chose a specific customer (not Suspense / new name),
                            # trigger the allocation cascade immediately.
                            _alloc_result = {"success": False, "message": ""}
                            if (a_type == "Credit"
                                    and _det not in ("Suspense", "")
                                    and a_details_sel not in ("Suspense",
                                                              "Type new name...")):
                                # Caller handles commit via `with conn:` above
                                _alloc_result = allocate_to_customer(conn, new_eid, _det)

                        st.session_state.pb_show_add_form = False

                        if (a_type == "Credit"
                                and _det not in ("Suspense", "")
                                and a_details_sel not in ("Suspense", "Type new name...")):
                            if _alloc_result["success"]:
                                st.success(f"Credit saved and allocated to '{_det}'.")
                            else:
                                st.info(f"Credit saved as '{_det}'. {_alloc_result['message']}")
                        else:
                            st.success("Transaction saved.")
                        st.rerun()
            with af_s2:
                if st.button("✕ Cancel", key="pb_af_cancel", use_container_width=True):
                    st.session_state.pb_show_add_form = False
                    st.rerun()
            st.markdown('</div>', unsafe_allow_html=True)

        # ══════════════════════════════════════════════════════
        #  SET OPENING BALANCE FORM
        # ══════════════════════════════════════════════════════
        if st.session_state.pb_show_ob_form:
            ob_row = conn.execute(
                "SELECT opening_amount, opening_date, notes "
                "FROM passbook_opening_balance WHERE firm=%s", (firm,)).fetchone()
            _prev_amt  = float(ob_row["opening_amount"]) if ob_row else 0.0
            _prev_date = (date.fromisoformat(str(ob_row["opening_date"]))
                          if ob_row else date.today())
            _prev_note = ob_row["notes"] if ob_row else ""

            st.markdown(
                '<div style="background:#181610;border:1px solid #2a2820;'
                'border-radius:10px;padding:1rem 1.2rem;margin-bottom:0.8rem">',
                unsafe_allow_html=True)
            st.markdown("**⚙ Set Opening Balance**")
            with st.form("pb_ob_form"):
                ob1, ob2 = st.columns(2)
                with ob1:
                    ob_amt  = st.number_input("Opening Amount (₹, can be negative)",
                                              value=_prev_amt, step=100.0,
                                              format="%.2f", key="pb_ob_amt")
                    ob_date = st.date_input("Opening Date", value=_prev_date, key="pb_ob_date")
                with ob2:
                    ob_note = st.text_area("Notes (optional)", value=_prev_note,
                                           key="pb_ob_note", height=80)
                obs1, obs2, _ = st.columns([1, 1, 4])
                with obs1:
                    if st.form_submit_button("💾 Save", use_container_width=True):
                        _oa    = round(ob_amt, 2)
                        _otype = 'Credit' if _oa >= 0 else 'Debit'
                        with conn:
                            conn.execute(
                                "INSERT INTO passbook_opening_balance "
                                "(firm,opening_amount,opening_date,notes) VALUES (%s,%s,%s,%s) "
                                "ON CONFLICT(firm) DO UPDATE SET "
                                "opening_amount=excluded.opening_amount,"
                                "opening_date=excluded.opening_date,"
                                "notes=excluded.notes",
                                (firm, _oa, str(ob_date), ob_note.strip()))
                            conn.execute("""
                                INSERT INTO passbook_entries
                                    (firm, entry_date, details, amount, txn_type, source_type)
                                VALUES (%s, %s, 'Opening Balance', %s, %s, %s)
                                ON CONFLICT (firm) WHERE source_type = 'Opening'
                                DO UPDATE SET
                                    amount     = EXCLUDED.amount,
                                    txn_type   = EXCLUDED.txn_type,
                                    entry_date = EXCLUDED.entry_date
                            """, (firm, str(ob_date), abs(_oa), _otype, SRC_OPENING))
                        st.session_state.pb_show_ob_form = False
                        st.toast("Opening balance updated.", icon="✓")
                        st.rerun()
                with obs2:
                    if st.form_submit_button("✕ Cancel", use_container_width=True):
                        st.session_state.pb_show_ob_form = False
                        st.rerun()
            st.markdown('</div>', unsafe_allow_html=True)

        st.markdown("<hr>", unsafe_allow_html=True)

        # ── Build and filter view ──────────────────────────────
        df_view = compute_passbook_view(firm, conn=conn)

        if pb_search:
            df_view = df_view[
                df_view["details"].str.contains(pb_search, case=False, na=False)]
        if pb_fmode == "Single Date":
            _sd = str(st.session_state.pb_filter_single)
            df_view = df_view[df_view["entry_date"] == _sd]
        elif pb_fmode == "Date Range":
            _rs = str(st.session_state.pb_filter_start)
            _re = str(st.session_state.pb_filter_end)
            df_view = df_view[(df_view["entry_date"] >= _rs) &
                              (df_view["entry_date"] <= _re)]

        # ── Pagination (Fix 4) ────────────────────────────────
        _pb_sig = (firm, pb_search, pb_fmode,
                   str(st.session_state.pb_filter_single),
                   str(st.session_state.pb_filter_start),
                   str(st.session_state.pb_filter_end))
        if st.session_state.get("_pb_last_sig") != _pb_sig:
            st.session_state["pb_firm_page"]   = 0
            st.session_state["_pb_last_sig"]   = _pb_sig
        _pb_total = len(df_view)
        _pb_pages = max(1, math.ceil(_pb_total / TXNS_PER_PAGE))
        _pb_page  = min(st.session_state.get("pb_firm_page", 0), _pb_pages - 1)
        st.session_state["pb_firm_page"] = _pb_page
        df_page   = df_view.iloc[_pb_page * TXNS_PER_PAGE : (_pb_page + 1) * TXNS_PER_PAGE]

        if df_view.empty:
            st.markdown(
                '<div class="empty-state"><div class="es-icon">📒</div>'
                'No entries yet. Add one to get started.</div>',
                unsafe_allow_html=True)
        else:
            if _pb_pages > 1:
                _ppg1, _ppg2, _ppg3, _ = st.columns([0.8, 0.8, 2, 5], gap="small")
                with _ppg1:
                    if st.button("← Prev", key="pb_pg_prev",
                                 disabled=(_pb_page == 0), use_container_width=True):
                        st.session_state["pb_firm_page"] -= 1; st.rerun()
                with _ppg2:
                    if st.button("Next →", key="pb_pg_next",
                                 disabled=(_pb_page >= _pb_pages - 1),
                                 use_container_width=True):
                        st.session_state["pb_firm_page"] += 1; st.rerun()
                with _ppg3:
                    st.markdown(
                        f'<div style="padding:.4rem .8rem;background:#1e1c14;border:1px solid #2a2820;'
                        f'border-radius:8px;font-size:.82rem;color:#c8bfa8;text-align:center">'
                        f'Page <b>{_pb_page+1}</b> of <b>{_pb_pages}</b> '
                        f'&nbsp;·&nbsp; {_pb_total} total</div>',
                        unsafe_allow_html=True)

            # ── Table header ────────────────────────────────
            _HDR  = ("font-size:0.6rem;color:#3a3628;text-transform:uppercase;"
                     "letter-spacing:0.1em;padding:3px 4px;border-bottom:1px solid #252318")
            _CELL = "padding:5px 4px;font-size:0.78rem"

            widths  = [0.35, 0.85, 2.2, 1.1, 1.1, 0.85, 0.9, 0.9, 0.9]
            headers = ["No.", "Date", "Details", "Amount", "Balance",
                       "Cheque", "Status", "Source", "Actions"]
            aligns  = ["left","left","left","right","right","left","left","left","center"]

            hcols = st.columns(widths)
            for hc, lbl, al in zip(hcols, headers, aligns):
                with hc:
                    ta = f"text-align:{al};display:block;" if al != "left" else ""
                    st.markdown(f'<div style="{_HDR};{ta}">{lbl}</div>',
                                unsafe_allow_html=True)

            # ── Table rows ───────────────────────────────────
            for _, row in df_page.iterrows():
                eid        = int(row["entry_id"])
                is_pending = bool(row["balance_is_pending"])
                src        = str(row.get("source_type") or SRC_MANUAL)
                is_manual  = (src == SRC_MANUAL)
                is_alloc   = (src == SRC_ALLOCATION)
                # Suspense: Manual Credit with details='Suspense'
                is_suspense = (is_manual
                               and str(row.get("txn_type", "")) == "Credit"
                               and str(row.get("details", "")) == "Suspense")

                amt    = float(row["amount"])
                bal    = float(row["balance"])
                signed = float(row["signed_amount"])

                # Row background
                if is_pending:
                    row_bg = "background:#141210;"
                elif is_suspense:
                    row_bg = "background:#1e1808;"  # amber tint for Suspense
                else:
                    row_bg = ""

                amt_color = "#8dd87a" if signed > 0 else "#ff5555"
                amt_sign  = "+" if signed > 0 else "−"
                bal_disp  = (f'<span style="color:#5a5448">({fmt_inr(bal)})</span>'
                             if is_pending else
                             f'<span style="color:{bal_color(bal)}">{fmt_inr(bal)}</span>')

                chq_no = str(row.get("cheque_number") or "—")
                chq_st = row.get("cheque_status")

                rcols = st.columns(widths)
                with rcols[0]:
                    st.markdown(f'<div style="{_CELL};{row_bg}color:#5a5448">'
                                f'{int(row["s_no"])}</div>', unsafe_allow_html=True)
                with rcols[1]:
                    st.markdown(f'<div style="{_CELL};{row_bg}color:#c8bfa8">'
                                f'{fmt_date(row["entry_date"])}</div>', unsafe_allow_html=True)
                with rcols[2]:
                    st.markdown(f'<div style="{_CELL};{row_bg}color:#c8bfa8">'
                                f'{h(str(row["details"]))}</div>', unsafe_allow_html=True)
                with rcols[3]:
                    st.markdown(
                        f'<div style="{_CELL};{row_bg}color:{amt_color};'
                        f'text-align:right;font-weight:700">'
                        f'{amt_sign} {fmt_inr(amt)}</div>', unsafe_allow_html=True)
                with rcols[4]:
                    st.markdown(
                        f'<div style="{_CELL};{row_bg}text-align:right;font-weight:700">'
                        f'{bal_disp}</div>', unsafe_allow_html=True)
                with rcols[5]:
                    st.markdown(f'<div style="{_CELL};{row_bg}color:#8a8070">'
                                f'{h(chq_no)}</div>', unsafe_allow_html=True)

                # Status toggle
                with rcols[6]:
                    if chq_st == CHQ_PENDING:
                        if st.button("⏳ Pending", key=f"pb_st_{eid}",
                                     use_container_width=True,
                                     help="Click to mark Cleared"):
                            conn.execute(
                                "UPDATE passbook_entries SET cheque_status=%s "
                                "WHERE entry_id=%s", (CHQ_CLEARED, eid))
                            conn.commit(); st.rerun()
                    elif chq_st == CHQ_CLEARED:
                        if st.button("✓ Cleared", key=f"pb_st_{eid}",
                                     use_container_width=True,
                                     help="Click to mark Pending"):
                            conn.execute(
                                "UPDATE passbook_entries SET cheque_status=%s "
                                "WHERE entry_id=%s", (CHQ_PENDING, eid))
                            conn.commit(); st.rerun()
                    else:
                        st.markdown(f'<div style="{_CELL};color:#3a3628">—</div>',
                                    unsafe_allow_html=True)

                with rcols[7]:
                    st.markdown(f'<div style="{_CELL}">{source_badge(src)}</div>',
                                unsafe_allow_html=True)

                # Actions: Edit + Delete for manual/alloc entries
                with rcols[8]:
                    if is_manual or is_alloc:
                        ac1, ac2 = st.columns(2)
                        with ac1:
                            if st.button("✏️", key=f"pb_ed_{eid}",
                                         use_container_width=True):
                                for _k2 in [x for x in st.session_state
                                            if x.startswith("pb_edit_")]:
                                    st.session_state[_k2] = False
                                st.session_state[f"pb_edit_{eid}"] = \
                                    not st.session_state.get(f"pb_edit_{eid}", False)
                                st.session_state[f"pb_del_{eid}"]  = False
                                st.rerun()
                        with ac2:
                            if st.button("🗑", key=f"pb_dl_{eid}",
                                         use_container_width=True):
                                st.session_state[f"pb_del_{eid}"] = \
                                    not st.session_state.get(f"pb_del_{eid}", False)
                                st.session_state[f"pb_edit_{eid}"] = False
                                st.rerun()

                st.markdown(
                    '<div style="border-bottom:1px solid #181610;margin-bottom:1px"></div>',
                    unsafe_allow_html=True)

                # ── Suspense Match button (below row) ─────────
                if is_suspense:
                    _match_key = f"pb_match_open_{eid}"
                    _match_lbl = ("▲ Close" if st.session_state.get(_match_key)
                                  else "🔍 Match to Customer")
                    if st.button(_match_lbl, key=f"pb_match_btn_{eid}",
                                 use_container_width=False,
                                 help="Find a customer transaction matching this credit amount"):
                        st.session_state[_match_key] = not st.session_state.get(_match_key, False)
                        st.rerun()

                    if st.session_state.get(_match_key):
                        pb_amount = round(float(row["amount"]), 2)
                        st.markdown(
                            f'<div style="background:#1a1408;border:1px solid #3a2c10;'
                            f'border-radius:8px;padding:0.8rem 1rem;margin:0.3rem 0 0.5rem">'
                            f'<div style="font-size:0.8rem;color:#d4864a;font-weight:600;'
                            f'margin-bottom:0.6rem">'
                            f'Match Credit of {fmt_inr(pb_amount)} dated '
                            f'{fmt_date(row["entry_date"])} to a customer</div>',
                            unsafe_allow_html=True)

                        # Candidate transactions: bill_sent ≈ pb_amount, not Paid/Calculated
                        candidates = conn.execute("""
                            SELECT ct.transaction_id, ct.customer_name,
                                   COALESCE(b.broker_name, '—') AS broker_name,
                                   ct.date AS bill_date,
                                   ct.total_amount,
                                   ct.bill_sent,
                                   COALESCE(SUM(p.amount), 0) AS total_paid
                            FROM   customer_transactions ct
                            LEFT JOIN brokers b  ON b.broker_id  = ct.broker_id
                            LEFT JOIN payments p ON p.transaction_id = ct.transaction_id
                            WHERE  ct.calc_status   != 'Calculated'
                              AND  ct.payment_status != 'Paid'
                              AND  ct.bill_sent IS NOT NULL
                              AND  ABS(ct.bill_sent - %s) < 0.01
                            GROUP  BY ct.transaction_id
                            ORDER  BY ct.date ASC
                        """, (pb_amount,)).fetchall()

                        if not candidates:
                            st.markdown(
                                f'<div style="font-size:0.78rem;color:#5a5448;padding:0.4rem 0">'
                                f'No unpaid transactions found with Bill Sent of '
                                f'{fmt_inr(pb_amount)}. Either none exist, or all matching '
                                f'transactions are already Paid or Calculated.</div>',
                                unsafe_allow_html=True)
                        else:
                            # Candidate table header
                            _ch = [0.14, 1.5, 1.0, 0.85, 1.0, 1.0, 1.0, 0.8, 0.85]
                            _cht = ["#", "Customer", "Broker", "Bill Date",
                                    "Total Bill", "Bill Sent", "Outstanding",
                                    "Days Old", ""]
                            _hcols = st.columns(_ch)
                            for _hc, _hl in zip(_hcols, _cht):
                                with _hc:
                                    st.markdown(
                                        f'<div style="font-size:0.58rem;color:#3a3628;'
                                        f'text-transform:uppercase;letter-spacing:.08em;'
                                        f'padding:2px 3px;border-bottom:1px solid #252318">'
                                        f'{_hl}</div>', unsafe_allow_html=True)

                            for _c in candidates:
                                _c_tid  = int(_c["transaction_id"])
                                _c_out  = round(float(_c["total_amount"])
                                                - float(_c["total_paid"]), 2)
                                _c_days = days_between(str(_c["bill_date"]))
                                _cv     = st.columns(_ch)
                                with _cv[0]:
                                    st.markdown(f'<div style="font-size:0.72rem;color:#5a5448;padding:4px 3px">#{_c_tid}</div>', unsafe_allow_html=True)
                                with _cv[1]:
                                    st.markdown(f'<div style="font-size:0.72rem;color:#c8bfa8;padding:4px 3px">{h(str(_c["customer_name"]))}</div>', unsafe_allow_html=True)
                                with _cv[2]:
                                    st.markdown(f'<div style="font-size:0.72rem;color:#8a8070;padding:4px 3px">{h(str(_c["broker_name"]))}</div>', unsafe_allow_html=True)
                                with _cv[3]:
                                    st.markdown(f'<div style="font-size:0.72rem;color:#8a8070;padding:4px 3px">{fmt_date(str(_c["bill_date"]))}</div>', unsafe_allow_html=True)
                                with _cv[4]:
                                    st.markdown(f'<div style="font-size:0.72rem;color:#c8bfa8;text-align:right;padding:4px 3px">{fmt_inr(float(_c["total_amount"]))}</div>', unsafe_allow_html=True)
                                with _cv[5]:
                                    st.markdown(f'<div style="font-size:0.72rem;color:#d4864a;text-align:right;padding:4px 3px">{fmt_inr(float(_c["bill_sent"]))}</div>', unsafe_allow_html=True)
                                with _cv[6]:
                                    st.markdown(f'<div style="font-size:0.72rem;color:#6a9fd4;text-align:right;padding:4px 3px">{fmt_inr(_c_out)}</div>', unsafe_allow_html=True)
                                with _cv[7]:
                                    st.markdown(f'<div style="font-size:0.72rem;color:#5a5448;padding:4px 3px">{_c_days}d</div>', unsafe_allow_html=True)
                                with _cv[8]:
                                    if st.button("Select →", key=f"pb_sel_{eid}_{_c_tid}",
                                                 use_container_width=True):
                                        with conn:
                                            # Caller handles commit via `with conn:` above
                                            _res = allocate_to_customer(
                                                conn, eid, str(_c["customer_name"]),
                                                target_txn_id=int(_c["transaction_id"]))
                                        if _res["success"]:
                                            st.session_state[_match_key] = False
                                            st.success(f"Allocated to '{_c['customer_name']}'.")
                                        else:
                                            st.warning(_res["message"])
                                        st.rerun()

                        st.markdown('</div>', unsafe_allow_html=True)

                # ── Unlink button for CustomerAllocation rows ─
                if is_alloc:
                    if st.button("↩ Unlink", key=f"pb_ul_{eid}",
                                 help="Revert to Suspense and delete the auto-allocated payment"):
                        with conn:
                            # Caller handles commit via `with conn:` above
                            _ul_res = _unlink_allocation(conn, eid)
                        st.success("Entry unlinked — payment reversed.")
                        st.rerun()

                # ── Inline Edit form ──────────────────────────
                if (is_manual or is_alloc) and st.session_state.get(f"pb_edit_{eid}"):
                    st.markdown(
                        '<div style="background:#1a1810;border:1px solid #2a2820;'
                        'border-radius:8px;padding:0.8rem 1rem;margin-bottom:0.4rem">',
                        unsafe_allow_html=True)

                    if is_alloc:
                        # Allocated entry: edit date + amount only (syncs linked payment)
                        st.markdown(f"**✏️ Edit Allocated Entry #{eid}** "
                                    f'<span style="font-size:0.72rem;color:#5a5448">'
                                    f'(use ↩ Unlink to re-match)</span>',
                                    unsafe_allow_html=True)
                        ef1, ef2 = st.columns(2)
                        with ef1:
                            ne_date = st.date_input(
                                "Date",
                                value=date.fromisoformat(str(row["entry_date"])),
                                key=f"pb_efd_{eid}")
                        with ef2:
                            ne_amt = st.number_input(
                                "Amount (₹)", min_value=0.01,
                                value=float(row["amount"]), step=100.0,
                                format="%.2f", key=f"pb_efa_{eid}")
                        efs1, efs2, _ = st.columns([1, 1, 4])
                        with efs1:
                            if st.button("💾 Save", key=f"pb_efsv_{eid}",
                                         use_container_width=True):
                                _a_tid = row.get("source_id")
                                with conn:
                                    conn.execute(
                                        "UPDATE passbook_entries "
                                        "SET entry_date=%s,amount=%s WHERE entry_id=%s",
                                        (str(ne_date), round(ne_amt, 2), eid))
                                    if _a_tid:
                                        _a_tid = int(_a_tid)
                                        _pmt = conn.execute(
                                            "SELECT payment_id FROM payments "
                                            "WHERE passbook_entry_id=%s",
                                            (eid,)
                                        ).fetchone()
                                        if _pmt:
                                            _bill_d = conn.execute(
                                                "SELECT date FROM customer_transactions "
                                                "WHERE transaction_id=%s",
                                                (_a_tid,)).fetchone()
                                            _df = days_between(
                                                str(_bill_d[0]),
                                                ne_date) if _bill_d else 0
                                            conn.execute(
                                                "UPDATE payments "
                                                "SET payment_date=%s,amount=%s,days_from_start=%s "
                                                "WHERE payment_id=%s",
                                                (str(ne_date), round(ne_amt, 2),
                                                 _df, _pmt["payment_id"]))
                                        # Recompute payment_status
                                        _ta = float(conn.execute(
                                            "SELECT total_amount FROM customer_transactions "
                                            "WHERE transaction_id=%s",
                                            (_a_tid,)).fetchone()["total_amount"])
                                        _np = round(float(conn.execute(
                                            "SELECT COALESCE(SUM(amount),0) AS v FROM payments "
                                            "WHERE transaction_id=%s",
                                            (_a_tid,)).fetchone()["v"]), 2)
                                        _ns = "Partial" if _np > 0 else "Pending"
                                        conn.execute(
                                            "UPDATE customer_transactions "
                                            "SET payment_status=%s,payment_received=%s,"
                                            "calc_status='Pending',"
                                            "final_settlement=NULL,interest_amount=0 "
                                            "WHERE transaction_id=%s",
                                            (_ns, _np, _a_tid))
                                st.session_state[f"pb_edit_{eid}"] = False
                                st.success("Entry and linked payment updated.")
                                st.rerun()
                        with efs2:
                            if st.button("✕ Cancel", key=f"pb_efcx_{eid}",
                                         use_container_width=True):
                                st.session_state[f"pb_edit_{eid}"] = False; st.rerun()

                    else:
                        # Regular manual entry edit
                        st.markdown(f"**✏️ Edit Entry #{eid}**")
                        ef1, ef2 = st.columns(2)
                        with ef1:
                            ne_date = st.date_input(
                                "Date",
                                value=date.fromisoformat(str(row["entry_date"])),
                                key=f"pb_efd_{eid}")
                            ne_det  = st.text_input(
                                "Details", value=str(row["details"]),
                                key=f"pb_efdet_{eid}")
                        with ef2:
                            ne_amt  = st.number_input(
                                "Amount (₹)", min_value=0.01,
                                value=float(row["amount"]), step=100.0,
                                format="%.2f", key=f"pb_efa_{eid}")
                            ne_type = st.radio(
                                "Type", ["Credit", "Debit"], horizontal=True,
                                index=0 if row["txn_type"] == "Credit" else 1,
                                key=f"pb_eft_{eid}")
                        _old_chq  = str(row.get("cheque_number") or "")
                        ne_chq    = st.text_input("Cheque Number", value=_old_chq,
                                                  key=f"pb_efcn_{eid}")
                        ne_chq_st = None
                        if ne_chq.strip():
                            _ci = 0 if row.get("cheque_status") == CHQ_PENDING else 1
                            ne_chq_st = st.radio(
                                "Cheque Status", ["Pending", "Cleared"],
                                horizontal=True, index=_ci, key=f"pb_efcs_{eid}")
                        efs1, efs2, _ = st.columns([1, 1, 4])
                        with efs1:
                            if st.button("💾 Save", key=f"pb_efsv_{eid}",
                                         use_container_width=True):
                                _det2 = ne_det.strip()
                                if not _det2:
                                    st.error("Details required.")
                                elif ne_amt <= 0:
                                    st.error("Amount must be > 0.")
                                else:
                                    _chq2   = ne_chq.strip() or None
                                    _chqst2 = ne_chq_st if _chq2 else None
                                    conn.execute(
                                        "UPDATE passbook_entries SET "
                                        "entry_date=%s,details=%s,amount=%s,txn_type=%s,"
                                        "cheque_number=%s,cheque_status=%s "
                                        "WHERE entry_id=%s",
                                        (str(ne_date), _det2, round(ne_amt, 2),
                                         ne_type, _chq2, _chqst2, eid))
                                    conn.commit()
                                    st.session_state[f"pb_edit_{eid}"] = False
                                    st.success("Entry updated.")
                                    st.rerun()
                        with efs2:
                            if st.button("✕ Cancel", key=f"pb_efcx_{eid}",
                                         use_container_width=True):
                                st.session_state[f"pb_edit_{eid}"] = False; st.rerun()

                    st.markdown('</div>', unsafe_allow_html=True)

                # ── Delete confirm ────────────────────────────
                if (is_manual or is_alloc) and st.session_state.get(f"pb_del_{eid}"):
                    _del_note = " (linked payment will also be deleted)" if is_alloc else ""
                    st.markdown(
                        f'<div style="background:#1e0808;border:1px solid #6a1a1a;'
                        f'border-radius:8px;padding:0.5rem 1rem;margin-bottom:0.3rem">'
                        f'<span style="color:#ff8080;font-size:0.82rem">'
                        f'⚠️ Delete entry #{eid}%s{h(_del_note)}</span></div>',
                        unsafe_allow_html=True)
                    dd1, dd2, _ = st.columns([0.8, 0.8, 6])
                    with dd1:
                        if st.button("✔ Delete", key=f"pb_cfd_{eid}",
                                     use_container_width=True):
                            with conn:
                                if is_alloc:
                                    # Delete linked payment + recompute transaction
                                    _d_tid = row.get("source_id")
                                    if _d_tid:
                                        _d_tid = int(_d_tid)
                                        _d_pmt = conn.execute(
                                            "SELECT payment_id FROM payments "
                                            "WHERE passbook_entry_id=%s",
                                            (eid,)
                                        ).fetchone()
                                        if _d_pmt:
                                            conn.execute(
                                                "DELETE FROM payments WHERE payment_id=%s",
                                                (_d_pmt["payment_id"],))
                                        _d_np = round(float(conn.execute(
                                            "SELECT COALESCE(SUM(amount),0) AS v FROM payments "
                                            "WHERE transaction_id=%s",
                                            (_d_tid,)).fetchone()["v"]), 2)
                                        _d_ns = "Partial" if _d_np > 0 else "Pending"
                                        conn.execute(
                                            "UPDATE customer_transactions "
                                            "SET payment_status=%s,payment_received=%s,"
                                            "calc_status='Pending',"
                                            "final_settlement=NULL,interest_amount=0 "
                                            "WHERE transaction_id=%s",
                                            (_d_ns, _d_np, _d_tid))
                                conn.execute(
                                    "DELETE FROM passbook_entries WHERE entry_id=%s",
                                    (eid,))
                            st.session_state.pop(f"pb_del_{eid}", None)
                            st.rerun()
                    with dd2:
                        if st.button("✕ Cancel", key=f"pb_cfc_{eid}",
                                     use_container_width=True):
                            st.session_state[f"pb_del_{eid}"] = False
                            st.rerun()

    finally:
        conn.close()


# ══════════════════════════════════════════════════════════════
#  PAGE: CASH IN HAND  (Level 2)
# ══════════════════════════════════════════════════════════════

elif st.session_state.pb_page == "cash":

    bcol, _ = st.columns([1, 8])
    with bcol:
        st.markdown('<div class="back-btn">', unsafe_allow_html=True)
        if st.button("← Passbook"):
            for _key in list(st.session_state.keys()):
                if _key.startswith(("cih_edit_", "cih_del_")):
                    del st.session_state[_key]
            st.session_state.pb_page              = "home"
            st.session_state.pb_cih_page          = 0
            st.session_state.pb_show_cih_add_form = False
            st.session_state.pb_show_cih_ob_form  = False
            st.rerun()
        st.markdown('</div>', unsafe_allow_html=True)

    st.markdown('<div class="page-title">Cash in Hand</div>', unsafe_allow_html=True)
    st.markdown('<div class="page-sub">Physical cash flow — combined across both firms</div>',
                unsafe_allow_html=True)

    conn = get_conn()
    try:
        # Single compute_cash_view call reused for both stat pills and filtered table
        df_cih_full = compute_cash_view(conn=conn)

        # ── Stat row ───────────────────────────────────────────
        cih_bal = float(df_cih_full.iloc[0]["balance"]) if not df_cih_full.empty else 0.0
        cih_cr  = float(df_cih_full.loc[df_cih_full["txn_type"] == "Credit", "amount"].sum()) \
                  if not df_cih_full.empty else 0.0
        cih_db  = float(df_cih_full.loc[df_cih_full["txn_type"] == "Debit",  "amount"].sum()) \
                  if not df_cih_full.empty else 0.0
        cih_n   = len(df_cih_full)
        cih_bc  = bal_color(cih_bal)

        st.markdown(
            f'<div style="background:linear-gradient(135deg,#1a1810,#201e14);'
            f'border:1px solid #1e3018;border-radius:14px;padding:1.2rem 1.6rem;'
            f'margin-bottom:1.2rem">'
            f'<div style="display:flex;gap:2rem;flex-wrap:wrap">'
            f'<div><div style="font-size:.6rem;color:#3a3628;text-transform:uppercase;letter-spacing:.1em">Current Balance</div>'
            f'<div style="font-size:1.2rem;font-weight:700;color:{cih_bc}">{fmt_inr(cih_bal)}</div></div>'
            f'<div><div style="font-size:.6rem;color:#3a3628;text-transform:uppercase;letter-spacing:.1em">Total Credits</div>'
            f'<div style="font-size:1.1rem;font-weight:600;color:#8dd87a">{fmt_inr(cih_cr)}</div></div>'
            f'<div><div style="font-size:.6rem;color:#3a3628;text-transform:uppercase;letter-spacing:.1em">Total Debits</div>'
            f'<div style="font-size:1.1rem;font-weight:600;color:#ff5555">{fmt_inr(cih_db)}</div></div>'
            f'<div><div style="font-size:.6rem;color:#3a3628;text-transform:uppercase;letter-spacing:.1em">Entries</div>'
            f'<div style="font-size:1.1rem;font-weight:600;color:#c8bfa8">{cih_n}</div></div>'
            f'</div></div>', unsafe_allow_html=True)

        # ── Action row ─────────────────────────────────────────
        ca1, ca2, ca3, ca4 = st.columns([1.2, 1.6, 2.5, 2.0], gap="small")
        with ca1:
            if st.button("＋  Add Transaction", key="cih_btn_add", use_container_width=True):
                st.session_state.pb_show_cih_add_form = not st.session_state.pb_show_cih_add_form
                st.session_state.pb_show_cih_ob_form  = False
                st.rerun()
        with ca2:
            if st.button("⚙  Set Opening Balance", key="cih_btn_ob", use_container_width=True):
                st.session_state.pb_show_cih_ob_form  = not st.session_state.pb_show_cih_ob_form
                st.session_state.pb_show_cih_add_form = False
                st.rerun()
        with ca3:
            cih_search = st.text_input("", placeholder="🔍  Search details...",
                                       label_visibility="collapsed", key="cih_search_inp")
        with ca4:
            _cfopts   = ["All", "Single Date", "Date Range"]
            cih_fmode = st.selectbox("", _cfopts, label_visibility="collapsed",
                                     key="cih_fmode_sel",
                                     index=_cfopts.index(st.session_state.pb_cih_filter_mode)
                                     if st.session_state.pb_cih_filter_mode in _cfopts else 0)
            st.session_state.pb_cih_filter_mode = cih_fmode

        if cih_fmode == "Single Date":
            _cd1, _cd2, _ = st.columns([1.5, 1.5, 5])
            with _cd1:
                st.session_state.pb_cih_filter_single = st.date_input(
                    "Date", value=st.session_state.pb_cih_filter_single,
                    key="cih_fd_s", label_visibility="collapsed")
        elif cih_fmode == "Date Range":
            _cd1, _cd2, _ = st.columns([1.5, 1.5, 5])
            with _cd1:
                st.session_state.pb_cih_filter_start = st.date_input(
                    "From", value=st.session_state.pb_cih_filter_start,
                    key="cih_fd_r1", label_visibility="collapsed")
            with _cd2:
                st.session_state.pb_cih_filter_end = st.date_input(
                    "To", value=st.session_state.pb_cih_filter_end,
                    key="cih_fd_r2", label_visibility="collapsed")

        # ══════════════════════════════════════════════════════
        #  ADD TRANSACTION FORM (Manual)
        # ══════════════════════════════════════════════════════
        if st.session_state.pb_show_cih_add_form:
            st.markdown(
                '<div style="background:#181610;border:1px solid #2a2820;'
                'border-radius:10px;padding:1rem 1.2rem;margin-bottom:0.8rem">',
                unsafe_allow_html=True)
            st.markdown("**＋ Add Transaction**")
            with st.form("cih_add_form"):
                caf1, caf2 = st.columns(2)
                with caf1:
                    ca_date   = st.date_input("Date", value=date.today(), key="cih_af_date")
                    ca_amount = st.number_input("Amount (₹)", min_value=0.01, step=100.0,
                                                format="%.2f", key="cih_af_amount")
                with caf2:
                    ca_type    = st.radio("Type", ["Credit", "Debit"], horizontal=True,
                                          key="cih_af_type")
                    ca_details = st.text_input("Details (required)", key="cih_af_details")

                cafs1, cafs2, _ = st.columns([1, 1, 4])
                with cafs1:
                    _cih_save = st.form_submit_button("💾 Save", use_container_width=True)
                with cafs2:
                    _cih_cancel = st.form_submit_button("✕ Cancel", use_container_width=True)

            if _cih_save:
                _cdet = ca_details.strip()
                if not _cdet:
                    st.error("Details required.")
                elif ca_amount <= 0:
                    st.error("Amount must be > 0.")
                else:
                    with conn:
                        conn.execute(
                            "INSERT INTO cash_in_hand_entries "
                            "(entry_date,details,amount,txn_type,source_type) "
                            "VALUES (%s,%s,%s,%s,%s)",
                            (str(ca_date), _cdet, round(ca_amount, 2),
                             ca_type, SRC_MANUAL))
                    st.session_state.pb_show_cih_add_form = False
                    st.toast("Transaction saved.", icon="✓")
                    st.rerun()
            if _cih_cancel:
                st.session_state.pb_show_cih_add_form = False
                st.rerun()
            st.markdown('</div>', unsafe_allow_html=True)

        # ══════════════════════════════════════════════════════
        #  SET OPENING BALANCE FORM
        # ══════════════════════════════════════════════════════
        if st.session_state.pb_show_cih_ob_form:
            ob_row     = conn.execute(
                "SELECT opening_amount, opening_date, notes "
                "FROM cash_in_hand_opening WHERE id=1").fetchone()
            _prev_amt  = float(ob_row["opening_amount"]) if ob_row else 0.0
            _prev_date = (date.fromisoformat(str(ob_row["opening_date"]))
                          if ob_row else date.today())
            _prev_note = ob_row["notes"] if ob_row else ""

            st.markdown(
                '<div style="background:#181610;border:1px solid #2a2820;'
                'border-radius:10px;padding:1rem 1.2rem;margin-bottom:0.8rem">',
                unsafe_allow_html=True)
            st.markdown("**⚙ Set Opening Balance**")
            with st.form("cih_ob_form"):
                cob1, cob2 = st.columns(2)
                with cob1:
                    cob_amt  = st.number_input("Opening Amount (₹, can be negative)",
                                               value=_prev_amt, step=100.0,
                                               format="%.2f", key="cih_ob_amt")
                    cob_date = st.date_input("Opening Date", value=_prev_date,
                                             key="cih_ob_date")
                with cob2:
                    cob_note = st.text_area("Notes (optional)", value=_prev_note,
                                            key="cih_ob_note", height=80)
                cobs1, cobs2, _ = st.columns([1, 1, 4])
                with cobs1:
                    _cob_save = st.form_submit_button("💾 Save", use_container_width=True)
                with cobs2:
                    _cob_cancel = st.form_submit_button("✕ Cancel", use_container_width=True)

            if _cob_save:
                _coa    = round(cob_amt, 2)
                _cotype = 'Credit' if _coa >= 0 else 'Debit'
                with conn:
                    conn.execute(
                        "INSERT INTO cash_in_hand_opening "
                        "(id,opening_amount,opening_date,notes) VALUES (1,%s,%s,%s) "
                        "ON CONFLICT(id) DO UPDATE SET "
                        "opening_amount=excluded.opening_amount,"
                        "opening_date=excluded.opening_date,"
                        "notes=excluded.notes",
                        (_coa, str(cob_date), cob_note.strip()))
                    conn.execute("""
                        INSERT INTO cash_in_hand_entries
                            (entry_date, details, amount, txn_type, source_type)
                        VALUES (%s, %s, %s, %s, %s)
                        ON CONFLICT (source_type) WHERE source_type = 'CIHOpening'
                        DO UPDATE SET
                            amount     = EXCLUDED.amount,
                            txn_type   = EXCLUDED.txn_type,
                            entry_date = EXCLUDED.entry_date
                    """, (str(cob_date), 'Opening Balance',
                          abs(_coa), _cotype, SRC_CIH_OPENING))
                st.session_state.pb_show_cih_ob_form = False
                st.toast("Opening balance updated.", icon="✓")
                st.rerun()
            if _cob_cancel:
                st.session_state.pb_show_cih_ob_form = False
                st.rerun()
            st.markdown('</div>', unsafe_allow_html=True)

        st.markdown("<hr>", unsafe_allow_html=True)

        # ── Build and filter view (uses df_cih_full — no second DB call) ──
        df_cih_display = df_cih_full.copy()

        if cih_search:
            df_cih_display = df_cih_display[
                df_cih_display["details"].str.contains(cih_search, case=False, na=False)]
        if cih_fmode == "Single Date":
            _csd = str(st.session_state.pb_cih_filter_single)
            df_cih_display = df_cih_display[df_cih_display["entry_date"] == _csd]
        elif cih_fmode == "Date Range":
            _crs = str(st.session_state.pb_cih_filter_start)
            _cre = str(st.session_state.pb_cih_filter_end)
            df_cih_display = df_cih_display[(df_cih_display["entry_date"] >= _crs) &
                                            (df_cih_display["entry_date"] <= _cre)]

        # ── Pagination (Fix 4) ────────────────────────────────
        _cih_sig = (cih_fmode,
                    str(st.session_state.pb_cih_filter_single),
                    str(st.session_state.pb_cih_filter_start),
                    str(st.session_state.pb_cih_filter_end),
                    cih_search)
        if st.session_state.get("_cih_last_sig") != _cih_sig:
            st.session_state["pb_cih_page"]   = 0
            st.session_state["_cih_last_sig"] = _cih_sig
        _cih_total = len(df_cih_display)
        _cih_pages = max(1, math.ceil(_cih_total / TXNS_PER_PAGE))
        _cih_page  = min(st.session_state.get("pb_cih_page", 0), _cih_pages - 1)
        st.session_state["pb_cih_page"] = _cih_page
        df_cih_page = df_cih_display.iloc[
            _cih_page * TXNS_PER_PAGE : (_cih_page + 1) * TXNS_PER_PAGE
        ]

        if df_cih_display.empty:
            st.markdown(
                '<div class="empty-state"><div class="es-icon">💵</div>'
                "Use '⚙ Set Opening Balance' to set your starting cash amount, "
                "then use '＋ Add Transaction' to record cash movements.</div>",
                unsafe_allow_html=True)
        else:
            if _cih_pages > 1:
                _cpg1, _cpg2, _cpg3, _ = st.columns([0.8, 0.8, 2, 5], gap="small")
                with _cpg1:
                    if st.button("← Prev", key="cih_pg_prev",
                                 disabled=(_cih_page == 0), use_container_width=True):
                        st.session_state["pb_cih_page"] -= 1; st.rerun()
                with _cpg2:
                    if st.button("Next →", key="cih_pg_next",
                                 disabled=(_cih_page >= _cih_pages - 1),
                                 use_container_width=True):
                        st.session_state["pb_cih_page"] += 1; st.rerun()
                with _cpg3:
                    st.markdown(
                        f'<div style="padding:.4rem .8rem;background:#1e1c14;border:1px solid #2a2820;'
                        f'border-radius:8px;font-size:.82rem;color:#c8bfa8;text-align:center">'
                        f'Page <b>{_cih_page+1}</b> of <b>{_cih_pages}</b> '
                        f'&nbsp;·&nbsp; {_cih_total} total</div>',
                        unsafe_allow_html=True)

            # ── Table header ────────────────────────────────
            _HDR  = ("font-size:0.6rem;color:#3a3628;text-transform:uppercase;"
                     "letter-spacing:0.1em;padding:3px 4px;border-bottom:1px solid #252318")
            _CELL = "padding:5px 4px;font-size:0.78rem"

            widths  = [0.35, 0.85, 2.2, 1.1, 1.1, 0.85, 0.9]
            headers = ["No.", "Date", "Details", "Amount", "Balance", "Source", "Actions"]
            aligns  = ["left","left","left","right","right","left","center"]

            hcols = st.columns(widths)
            for hc, lbl, al in zip(hcols, headers, aligns):
                with hc:
                    ta = f"text-align:{al};display:block;" if al != "left" else ""
                    st.markdown(f'<div style="{_HDR};{ta}">{lbl}</div>',
                                unsafe_allow_html=True)

            # ── Table rows ───────────────────────────────────
            for _, crow in df_cih_page.iterrows():
                ceid      = int(crow["entry_id"])
                src       = str(crow.get("source_type") or SRC_MANUAL)
                is_manual = (src == SRC_MANUAL)

                amt    = float(crow["amount"])
                bal    = float(crow["balance"])
                signed = float(crow["signed_amount"])

                amt_color = "#8dd87a" if signed > 0 else "#ff5555"
                amt_sign  = "+" if signed > 0 else "−"

                rcols = st.columns(widths)
                with rcols[0]:
                    st.markdown(f'<div style="{_CELL};color:#5a5448">'
                                f'{int(crow["s_no"])}</div>', unsafe_allow_html=True)
                with rcols[1]:
                    st.markdown(f'<div style="{_CELL};color:#c8bfa8">'
                                f'{fmt_date(crow["entry_date"])}</div>', unsafe_allow_html=True)
                with rcols[2]:
                    st.markdown(f'<div style="{_CELL};color:#c8bfa8">'
                                f'{h(str(crow["details"]))}</div>', unsafe_allow_html=True)
                with rcols[3]:
                    st.markdown(
                        f'<div style="{_CELL};color:{amt_color};'
                        f'text-align:right;font-weight:700">'
                        f'{amt_sign} {fmt_inr(amt)}</div>', unsafe_allow_html=True)
                with rcols[4]:
                    st.markdown(
                        f'<div style="{_CELL};text-align:right;font-weight:700;'
                        f'color:{bal_color(bal)}">{fmt_inr(bal)}</div>',
                        unsafe_allow_html=True)
                with rcols[5]:
                    st.markdown(f'<div style="{_CELL}">{cih_source_badge(src)}</div>',
                                unsafe_allow_html=True)

                with rcols[6]:
                    if is_manual:
                        ac1, ac2 = st.columns(2)
                        with ac1:
                            if st.button("✏️", key=f"cih_ed_{ceid}",
                                         use_container_width=True):
                                for _k2 in [x for x in st.session_state
                                            if x.startswith("cih_edit_")]:
                                    st.session_state[_k2] = False
                                st.session_state[f"cih_edit_{ceid}"] = \
                                    not st.session_state.get(f"cih_edit_{ceid}", False)
                                st.session_state[f"cih_del_{ceid}"] = False
                                st.rerun()
                        with ac2:
                            if st.button("🗑", key=f"cih_dl_{ceid}",
                                         use_container_width=True):
                                st.session_state[f"cih_del_{ceid}"] = \
                                    not st.session_state.get(f"cih_del_{ceid}", False)
                                st.session_state[f"cih_edit_{ceid}"] = False
                                st.rerun()

                st.markdown(
                    '<div style="border-bottom:1px solid #181610;margin-bottom:1px"></div>',
                    unsafe_allow_html=True)

                # ── Inline Edit form ──────────────────────────
                if is_manual and st.session_state.get(f"cih_edit_{ceid}"):
                    st.markdown(
                        '<div style="background:#1a1810;border:1px solid #2a2820;'
                        'border-radius:8px;padding:0.8rem 1rem;margin-bottom:0.4rem">',
                        unsafe_allow_html=True)
                    st.markdown(f"**✏️ Edit Entry #{ceid}**")
                    cef1, cef2 = st.columns(2)
                    with cef1:
                        cne_date = st.date_input(
                            "Date",
                            value=date.fromisoformat(str(crow["entry_date"])),
                            key=f"cih_efd_{ceid}")
                        cne_det  = st.text_input(
                            "Details", value=str(crow["details"]),
                            key=f"cih_efdet_{ceid}")
                    with cef2:
                        cne_amt  = st.number_input(
                            "Amount (₹)", min_value=0.01,
                            value=float(crow["amount"]), step=100.0,
                            format="%.2f", key=f"cih_efa_{ceid}")
                        cne_type = st.radio(
                            "Type", ["Credit", "Debit"], horizontal=True,
                            index=0 if crow["txn_type"] == "Credit" else 1,
                            key=f"cih_eft_{ceid}")
                    cefs1, cefs2, _ = st.columns([1, 1, 4])
                    with cefs1:
                        if st.button("💾 Save", key=f"cih_efsv_{ceid}",
                                     use_container_width=True):
                            _cdet2 = cne_det.strip()
                            if not _cdet2:
                                st.error("Details required.")
                            elif cne_amt <= 0:
                                st.error("Amount must be > 0.")
                            else:
                                with conn:
                                    conn.execute(
                                        "UPDATE cash_in_hand_entries "
                                        "SET entry_date=%s,details=%s,amount=%s,txn_type=%s "
                                        "WHERE entry_id=%s",
                                        (str(cne_date), _cdet2,
                                         round(cne_amt, 2), cne_type, ceid))
                                st.session_state[f"cih_edit_{ceid}"] = False
                                st.success("Entry updated.")
                                st.rerun()
                    with cefs2:
                        if st.button("✕ Cancel", key=f"cih_efcx_{ceid}",
                                     use_container_width=True):
                            st.session_state[f"cih_edit_{ceid}"] = False
                            st.rerun()
                    st.markdown('</div>', unsafe_allow_html=True)

                # ── Delete confirm ────────────────────────────
                if is_manual and st.session_state.get(f"cih_del_{ceid}"):
                    st.markdown(
                        f'<div style="background:#1e0808;border:1px solid #6a1a1a;'
                        f'border-radius:8px;padding:0.5rem 1rem;margin-bottom:0.3rem">'
                        f'<span style="color:#ff8080;font-size:0.82rem">'
                        f'⚠️ Delete entry #{ceid}%s</span></div>',
                        unsafe_allow_html=True)
                    cdd1, cdd2, _ = st.columns([0.8, 0.8, 6])
                    with cdd1:
                        if st.button("✔ Delete", key=f"cih_cfd_{ceid}",
                                     use_container_width=True):
                            with conn:
                                conn.execute(
                                    "DELETE FROM cash_in_hand_entries WHERE entry_id=%s",
                                    (ceid,))
                            st.session_state.pop(f"cih_del_{ceid}", None)
                            st.rerun()
                    with cdd2:
                        if st.button("✕ Cancel", key=f"cih_cfc_{ceid}",
                                     use_container_width=True):
                            st.session_state[f"cih_del_{ceid}"] = False
                            st.rerun()

    finally:
        conn.close()
