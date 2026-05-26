"""
Vendor Payments — RTGS and UB ledger tracking per vendor.
Navigation: st.session_state.vp_page in ("home", "vendor", "rtgs", "ub")

Sign convention (from OUR perspective):
  Bill recorded    → amount stored NEGATIVE  (we owe vendor)
  Payment recorded → amount stored POSITIVE  (we paid)
  Running balance  = cumsum(amount) ordered by date asc
"""

import streamlit as st
import psycopg2
import pandas as pd
from datetime import date

from utils.db import (
    pg_read_sql, get_conn, FIRM_SP, FIRM_MT, SRC_VENDOR_RTGS, SRC_VENDOR_UB,
    log_stock_change, add_unidentified_stock, reverse_unidentified_stock,
    get_all_vendors_cached, invalidate_lookup_cache,
    ensure_good_at_all_locations, ensure_category_at_all_locations,
    _ensure_schema_once)
from utils.styles import APP_CSS, BRAND_BAR_HTML, get_light_mode_css
from utils.formatters import fmt_inr, fmt_date, h, parse_slash_amount
from utils.auth import require_login, render_logout_button

# ── Page config ─────────────────────────────────────────────────
st.set_page_config(
    page_title="Vendor Payments – S P Spices",
    page_icon="🏭",
    layout="wide",
    initial_sidebar_state="collapsed",
)
require_login()
render_logout_button()
st.markdown(APP_CSS, unsafe_allow_html=True)
if st.session_state.get("light_mode"):
    st.markdown(get_light_mode_css(), unsafe_allow_html=True)
st.markdown(BRAND_BAR_HTML, unsafe_allow_html=True)
_ensure_schema_once()


# ══════════════════════════════════════════════════════════════
#  HELPERS
# ══════════════════════════════════════════════════════════════

def bal_color(balance: float) -> str:
    """Red if we owe, green if credit, white if settled."""
    if abs(balance) < 0.01:
        return "#f0ebe0"
    return "#ff5555" if balance < 0 else "#8dd87a"


def compute_balances(df_entries: pd.DataFrame) -> pd.DataFrame:
    """Add running 'balance' (cumsum of amount) and sequential 's_no' columns.
    Balance and s_no are assigned chronologically (oldest first), then the
    dataframe is reversed so the newest entry renders at the top."""
    df = df_entries.sort_values(["entry_date", "entry_id"]).copy()
    running = 0.0
    balances = []
    for amt in df["amount"]:
        running = round(running + float(amt), 2)
        balances.append(running)
    df["balance"] = balances
    df["s_no"] = range(1, len(df) + 1)
    return df.iloc[::-1].reset_index(drop=True)


def reverse_bill_stock(conn, good_id: int, bags: int, kg: float):
    """
    Subtract bags/kg from Transport stock when reversing a vendor bill.
    Allows the result to go negative — Stock Register UI handles negative stock visually.
    Returns a warning string if stock went negative, None on clean reversal.
    """
    row = conn.execute(
        "SELECT bags, quantity_kg FROM stock_levels "
        "WHERE good_id=%s AND location='Transport'",
        (good_id,)).fetchone()
    if not row:
        return f"Stock row not found for good_id={good_id} — stock not reversed."
    new_bags = row["bags"] - bags
    new_kg   = row["quantity_kg"] - kg
    # Allow negative — Stock Register UI handles negative stock visually
    warning  = None
    if new_bags < 0 or new_kg < 0:
        warning = (f"Reversal produced negative stock "
                   f"(bags: {new_bags}, kg: {new_kg:.1f}).")
    conn.execute(
        "UPDATE stock_levels SET bags=%s, quantity_kg=%s "
        "WHERE good_id=%s AND location='Transport'",
        (new_bags, new_kg, good_id))
    return warning


# ── Session state ─────────────────────────────────────────────
for _k, _v in {
    "vp_page":        "home",
    "vp_vendor_id":   None,
    "vp_vendor_name": "",
    "vp_rtgs_firm":   FIRM_SP,
}.items():
    if _k not in st.session_state:
        st.session_state[_k] = _v


# ══════════════════════════════════════════════════════════════
#  BILL & PAYMENT POPOVERS  (shared by RTGS and UB)
# ══════════════════════════════════════════════════════════════

def _bill_popover(vendor_id: int, ledger_type: str, firm, conn):
    """Record Bill popover. firm=None for UB entries."""
    _k = f"{vendor_id}_{ledger_type}_{firm or 'ub'}"
    with st.popover("💰 Record Bill", use_container_width=True):
        b_date = st.date_input("Bill Date", value=date.today(), key=f"bfd_{_k}")

        # Goods category dropdown
        cats = conn.execute(
            "SELECT category_id, category_name "
            "FROM stock_categories ORDER BY category_name").fetchall()
        cat_names = [r["category_name"] for r in cats]
        cat_ids   = [r["category_id"] for r in cats]

        if cat_names:
            sel_ci = st.selectbox(
                "Goods Category", range(len(cat_names)),
                format_func=lambda i: cat_names[i], key=f"bfc_{_k}")
            sel_cat_id   = cat_ids[sel_ci]
            sel_cat_name = cat_names[sel_ci]
        else:
            st.warning("No categories yet — add one below.")
            sel_cat_id, sel_cat_name = None, None

        with st.expander("＋ Add new category"):
            nc = st.text_input("Category Name", key=f"bfnc_{_k}")
            if st.button("Save Category", key=f"bfncs_{_k}"):
                nm = nc.strip()
                if nm:
                    try:
                        _nc_row = conn.execute(
                            "INSERT INTO stock_categories (category_name) VALUES (%s) "
                            "RETURNING category_id", (nm,))
                        _nc_id = _nc_row.fetchone()["category_id"]
                        ensure_category_at_all_locations(conn, _nc_id)
                        conn.commit()
                        st.success(f"Added '{nm}'")
                        st.rerun()
                    except psycopg2.errors.UniqueViolation:
                        st.error("Category already exists.")

        # Goods name — optional; "— Not specified —" means category-only or no goods info
        _goods_raw = (conn.execute(
            "SELECT good_id, good_name FROM stock_goods "
            "WHERE category_id=%s ORDER BY good_name", (sel_cat_id,)).fetchall()
            if sel_cat_id else [])
        _good_opt_labels = ["— Not specified —"] + [r["good_name"] for r in _goods_raw]
        _good_opt_ids    = [None] + [r["good_id"] for r in _goods_raw]

        sel_gi      = st.selectbox(
            "Goods Name (optional)", range(len(_good_opt_labels)),
            format_func=lambda i: _good_opt_labels[i], key=f"bfg_{_k}")
        sel_good_id   = _good_opt_ids[sel_gi]
        good_specified = sel_good_id is not None

        with st.expander("＋ Add new good"):
            ng = st.text_input("Good Name", key=f"bfng_{_k}")
            if st.button("Save Good", key=f"bfngs_{_k}") and sel_cat_id:
                nm = ng.strip()
                if nm:
                    try:
                        with conn:
                            cur = conn.execute(
                                "INSERT INTO stock_goods (category_id, good_name) VALUES (%s,%s) "
                                "RETURNING good_id",
                                (sel_cat_id, nm))
                            gid = cur.fetchone()["good_id"]
                            ensure_good_at_all_locations(conn, gid)
                        invalidate_lookup_cache()
                        st.success(f"Added '{nm}'")
                        st.rerun()
                    except psycopg2.errors.UniqueViolation:
                        st.error("Good already exists.")

        bc1, bc2 = st.columns(2)
        with bc1:
            bags_s = st.text_input("No. of Bags (optional)",
                                   placeholder="leave blank if unknown", key=f"bfb_{_k}")
        with bc2:
            kg_s = st.text_input("Weight / Kg (optional)",
                                 placeholder="leave blank if unknown", key=f"bfk_{_k}")
        amt = st.number_input("Bill Amount (₹)", min_value=0.0, step=100.0,
                              format="%.2f", key=f"bfa_{_k}")
        note_s = st.text_input(
            "Note (optional)",
            placeholder="e.g. freight included, advance bill, etc.",
            key=f"bfnt_{_k}")

        if st.button("💾 Save Bill", key=f"bfsv_{_k}", use_container_width=True):
            _bags_filled = bags_s.strip() != ""
            _kg_filled   = kg_s.strip() != ""

            # MODE A: good name + bags + kg all provided (fully identified)
            # MODE B: no good name but bags + kg provided (unidentified with quantities)
            # MODE C: no good name, no bags, no kg (category only)
            # anything else: partial error
            if good_specified and _bags_filled and _kg_filled:
                _bill_mode = "A"
            elif not good_specified and _bags_filled and _kg_filled:
                _bill_mode = "B"
            elif not good_specified and not _bags_filled and not _kg_filled:
                _bill_mode = "C"
            else:
                st.error("Fill Goods Name + Bags + Weight together, "
                         "or just Bags + Weight without a name, "
                         "or leave all three blank.")
                st.stop()
                _bill_mode = None  # unreachable, satisfies linter

            if amt <= 0:
                st.error("Bill Amount must be > 0.")
            elif not sel_cat_id:
                st.error("Please select a category.")
            else:
                _vn_row = conn.execute(
                    "SELECT vendor_name FROM vendors WHERE vendor_id=%s",
                    (vendor_id,)).fetchone()
                _vn_str = _vn_row["vendor_name"] if _vn_row else f"Vendor #{vendor_id}"

                if _bill_mode == "A":
                    try:
                        _bags_val = parse_slash_amount(bags_s)
                        _kg_val   = parse_slash_amount(kg_s)
                    except ValueError as _pe:
                        st.error(f"Invalid number: {_pe}")
                        st.stop()
                    if _bags_val <= 0 or _kg_val <= 0:
                        st.error("Bags and Kg must be > 0 for an identified bill.")
                    else:
                        _vh_row = conn.execute("""
                            SELECT sl.bags, sl.quantity_kg,
                                   sg.good_name, sc.category_name
                            FROM stock_levels sl
                            JOIN stock_goods sg ON sl.good_id = sg.good_id
                            JOIN stock_categories sc ON sg.category_id = sc.category_id
                            WHERE sl.good_id = %s AND sl.location = 'Transport'
                        """, (sel_good_id,)).fetchone()
                        _vh_b_bags = float(_vh_row["bags"] or 0) if _vh_row else 0
                        _vh_b_kg   = float(_vh_row["quantity_kg"] or 0) if _vh_row else 0.0
                        _vh_cat    = _vh_row["category_name"] if _vh_row else sel_cat_name
                        _vh_good   = _vh_row["good_name"] if _vh_row else ""
                        with conn:
                            conn.execute(
                                """INSERT INTO vendor_entries
                                   (vendor_id,entry_date,ledger_type,firm,entry_kind,
                                    particulars,amount,good_id,bags,quantity_kg,note)
                                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                                (vendor_id, str(b_date), ledger_type, firm,
                                 "Bill", sel_cat_name, -amt,
                                 sel_good_id, round(float(_bags_val), 2),
                                 round(float(_kg_val), 2), note_s.strip()))
                            conn.execute(
                                "UPDATE stock_levels SET bags=bags+%s, quantity_kg=quantity_kg+%s "
                                "WHERE good_id=%s AND location='Transport'",
                                (round(float(_bags_val), 2), round(float(_kg_val), 2),
                                 sel_good_id))
                            try:
                                log_stock_change(
                                    conn, _vh_cat, _vh_good, "Transport", "Vendor Bill",
                                    _vh_b_bags, _vh_b_bags + float(_bags_val),
                                    _vh_b_kg,   _vh_b_kg   + float(_kg_val),
                                    source=f"Vendor: {_vn_str}")
                            except Exception as _e:
                                import logging
                                logging.getLogger(__name__).warning(
                                    "stock_history log failed: %s", _e)
                        st.success(f"Bill of {fmt_inr(amt)} saved. Transport stock updated.")
                        st.rerun()

                elif _bill_mode == "B":
                    try:
                        _bags_val = parse_slash_amount(bags_s)
                        _kg_val   = parse_slash_amount(kg_s)
                    except ValueError as _pe:
                        st.error(f"Invalid number: {_pe}")
                        st.stop()
                    with conn:
                        conn.execute(
                            """INSERT INTO vendor_entries
                               (vendor_id,entry_date,ledger_type,firm,entry_kind,
                                particulars,amount,good_id,bags,quantity_kg,note)
                               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                            (vendor_id, str(b_date), ledger_type, firm,
                             "Bill", sel_cat_name, -amt,
                             None, round(float(_bags_val), 2), round(float(_kg_val), 2),
                             note_s.strip()))
                        try:
                            add_unidentified_stock(conn, sel_cat_id, "Transport",
                                                   _bags_val, _kg_val)
                            log_stock_change(conn, sel_cat_name, "Unidentified",
                                             "Transport", "Vendor Bill",
                                             0, _bags_val, 0, _kg_val,
                                             source=f"Vendor: {_vn_str}")
                        except Exception as _e:
                            import logging
                            logging.getLogger(__name__).warning(
                                "Unidentified stock update failed: %s", _e)
                    st.success(f"Bill of {fmt_inr(amt)} saved. Unidentified stock updated.")
                    st.rerun()

                else:  # MODE C — no stock update
                    with conn:
                        conn.execute(
                            """INSERT INTO vendor_entries
                               (vendor_id,entry_date,ledger_type,firm,entry_kind,
                                particulars,amount,good_id,bags,quantity_kg,note)
                               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                            (vendor_id, str(b_date), ledger_type, firm,
                             "Bill", sel_cat_name, -amt,
                             None, 0, 0.0, note_s.strip()))
                    st.success(f"Bill of {fmt_inr(amt)} saved.")
                    st.rerun()


def _payment_popover(vendor_id: int, ledger_type: str, firm, conn):
    """Record Payment popover. firm=None for UB entries."""
    _k          = f"{vendor_id}_{ledger_type}_{firm or 'ub'}"
    particulars = "RTGS Payment" if ledger_type == "RTGS" else "UB Payment"
    with st.popover("💳 Record Payment", use_container_width=True):
        with st.form(f"payment_form_{_k}"):
            p_date = st.date_input("Payment Date", value=date.today(), key=f"pfd_{_k}")
            p_amt  = st.number_input("Payment Amount (₹)", min_value=0.0, step=100.0,
                                     format="%.2f", key=f"pfa_{_k}")
            note_s = st.text_input(
                "Note (optional)",
                placeholder="e.g. part payment, reference no., etc.",
                key=f"pfnt_{_k}")
            if st.form_submit_button("💾 Save Payment", use_container_width=True):
                if p_amt <= 0:
                    st.error("Amount must be > 0.")
                else:
                    # Positive amount = payment (we reduce our debt)
                    with conn:
                        cur = conn.execute(
                            """INSERT INTO vendor_entries
                               (vendor_id,entry_date,ledger_type,firm,entry_kind,
                                particulars,amount,good_id,bags,quantity_kg,note)
                               VALUES (%s,%s,%s,%s,%s,%s,%s,NULL,0,0,%s)
                               RETURNING entry_id""",
                            (vendor_id, str(p_date), ledger_type, firm,
                             "Payment", particulars, p_amt, note_s.strip()))
                        new_eid = cur.fetchone()["entry_id"]
                        _vrow  = conn.execute(
                            "SELECT vendor_name FROM vendors WHERE vendor_id=%s",
                            (vendor_id,)).fetchone()
                        _vname = _vrow["vendor_name"] if _vrow else f"Vendor #{vendor_id}"
                        # ── Passbook sync: RTGS payments debit the bank account ──
                        if ledger_type == "RTGS" and firm:
                            conn.execute(
                                "INSERT INTO passbook_entries "
                                "(firm,entry_date,details,amount,txn_type,"
                                " source_type,source_id,cheque_status) "
                                "VALUES (%s,%s,%s,%s,'Debit',%s,%s,NULL)",
                                (firm, str(p_date), _vname, p_amt, SRC_VENDOR_RTGS, new_eid))
                        # ── CASH IN HAND SYNC ──────────────────────────────
                        if ledger_type == "UB":
                            conn.execute(
                                "INSERT INTO cash_in_hand_entries "
                                "(entry_date,details,amount,txn_type,source_type,source_id) "
                                "VALUES (%s,%s,%s,%s,%s,%s)",
                                (str(p_date), _vname, p_amt, 'Debit', SRC_VENDOR_UB, new_eid))
                    st.toast(f"Payment of {fmt_inr(p_amt)} recorded.", icon="✅")
                    st.rerun()


# ══════════════════════════════════════════════════════════════
#  EDIT FORM  (inline, per entry)
# ══════════════════════════════════════════════════════════════

def _edit_form(entry_row, conn):
    entry_id     = int(entry_row["entry_id"])
    ek           = entry_row["entry_kind"]
    _confirm_key = f"vp_bill_neg_{entry_id}"

    # ── Negative-stock confirmation (shown instead of the edit form) ──────
    if st.session_state.get(_confirm_key):
        _pd = st.session_state[_confirm_key]
        st.warning(
            f"⚠️ This edit will result in negative Transport stock for "
            f"{_pd['good_name']}: {_pd['preview_bags']} bags / "
            f"{_pd['preview_kg']:.1f} kg. "
            f"This usually means goods were sold after the bill was recorded. "
            f"Save anyway%s")
        _sa1, _sa2, _ = st.columns([1.2, 1, 5.8])
        with _sa1:
            if st.button("💾 Save Anyway", key=f"vp_sa_{entry_id}",
                         use_container_width=True):
                _p = st.session_state.pop(_confirm_key)
                # Old bill always had identified good (confirmation only triggered for MODE A)
                _gid_old = int(entry_row["good_id"])
                with conn:
                    warn = reverse_bill_stock(
                        conn, _gid_old,
                        int(entry_row["bags"]), float(entry_row["quantity_kg"]))
                    _sa_mode = _p.get("new_mode", "A")
                    if _sa_mode == "A":
                        conn.execute(
                            "UPDATE stock_levels SET bags=bags+%s, quantity_kg=quantity_kg+%s "
                            "WHERE good_id=%s AND location='Transport'",
                            (_p["new_bags"], _p["new_kg"], _p["new_good_id"]))
                    elif _sa_mode == "B":
                        _nc_id = _p.get("new_cat_id")
                        if _nc_id:
                            add_unidentified_stock(
                                conn, _nc_id, "Transport",
                                float(_p["new_bags"]), float(_p["new_kg"]))
                    # MODE C: no stock update
                    conn.execute(
                        "UPDATE vendor_entries "
                        "SET entry_date=%s,particulars=%s,amount=%s,"
                        "good_id=%s,bags=%s,quantity_kg=%s,firm=%s,note=%s WHERE entry_id=%s",
                        (_p["new_date"], _p["new_cat_name"], _p["new_amt_neg"],
                         _p["new_good_id"], _p["new_bags"], _p["new_kg"],
                         _p["new_firm"], _p.get("new_note", ""), entry_id))
                st.session_state[f"vp_edit_{entry_id}"] = False
                if warn:
                    st.warning(warn)
                st.success("Bill updated (stock may be negative).")
                st.rerun()
        with _sa2:
            if st.button("✕ Cancel", key=f"vp_sc_{entry_id}",
                         use_container_width=True):
                st.session_state.pop(_confirm_key, None)
                st.session_state[f"vp_edit_{entry_id}"] = False
                st.rerun()
        return

    with st.form(f"vp_ef_{entry_id}"):
        st.markdown(
            f'<div style="font-size:0.85rem;color:#e8c97e;margin-bottom:0.4rem">'
            f'✏️ Edit {ek} #{entry_id}</div>', unsafe_allow_html=True)

        if ek == "Bill":
            # Show all goods with "Category — Good" label to avoid
            # dependent-dropdown issue inside st.form (no reruns inside forms)
            all_goods = conn.execute("""
                SELECT sg.good_id, sg.good_name, sc.category_name
                FROM stock_goods sg
                JOIN stock_categories sc ON sc.category_id = sg.category_id
                ORDER BY sc.category_name, sg.good_name""").fetchall()
            # Index 0 = "— Not specified —" (unidentified)
            g_labels   = ["— Not specified —"] + [f"{g['category_name']} — {g['good_name']}" for g in all_goods]
            g_ids_all  = [None] + [g["good_id"] for g in all_goods]
            g_cat_all  = [None] + [g["category_name"] for g in all_goods]

            cur_idx = 0  # default: "— Not specified —"
            _old_good_id = entry_row.get("good_id")
            if _old_good_id is not None:
                try:
                    _ogi = int(_old_good_id)
                    if _ogi in g_ids_all:
                        cur_idx = g_ids_all.index(_ogi)
                except (TypeError, ValueError):
                    pass

            ef1, ef2 = st.columns(2)
            with ef1:
                new_date  = st.date_input("Bill Date",
                    value=date.fromisoformat(str(entry_row["entry_date"])),
                    key=f"efd_{entry_id}")
                new_g_idx = st.selectbox(
                    "Good (Category — Name)", range(len(g_labels)),
                    format_func=lambda i: g_labels[i],
                    index=cur_idx, key=f"efg_{entry_id}")
            with ef2:
                new_bags = st.number_input("Bags", min_value=0,
                    value=int(entry_row["bags"]), step=1, key=f"efb_{entry_id}")
                new_kg   = st.number_input("Kg", min_value=0.0,
                    value=float(entry_row["quantity_kg"]), step=0.1,
                    format="%.2f", key=f"efk_{entry_id}")
            new_amt = st.number_input("Amount (₹)", min_value=0.0,
                value=abs(float(entry_row["amount"])), step=100.0,
                format="%.2f", key=f"efa_{entry_id}")
            # Firm radio for RTGS bills (FIX 3)
            if entry_row.get("ledger_type") == "RTGS":
                _firm_idx = 0 if str(entry_row.get("firm", "")) == FIRM_SP else 1
                new_firm = st.radio("Firm", ["SP Spices", "Mukund Traders"],
                                    index=_firm_idx, horizontal=True,
                                    key=f"efirm_{entry_id}")
            else:
                new_firm = entry_row.get("firm")
        else:
            ef1, ef2 = st.columns(2)
            with ef1:
                new_date = st.date_input("Payment Date",
                    value=date.fromisoformat(str(entry_row["entry_date"])),
                    key=f"efd_{entry_id}")
            with ef2:
                new_amt = st.number_input("Amount (₹)", min_value=0.0,
                    value=abs(float(entry_row["amount"])), step=100.0,
                    format="%.2f", key=f"efa_{entry_id}")
            # Firm radio for RTGS payments (FIX 3)
            if entry_row.get("ledger_type") == "RTGS":
                _firm_idx = 0 if str(entry_row.get("firm", "")) == FIRM_SP else 1
                new_firm = st.radio("Firm", ["SP Spices", "Mukund Traders"],
                                    index=_firm_idx, horizontal=True,
                                    key=f"efirm_{entry_id}")
            else:
                new_firm = entry_row.get("firm")

        edit_note = st.text_input(
            "Note (optional)",
            value=str(entry_row.get("note") or ""),
            key=f"vp_edit_note_{entry_id}")

        sc1, sc2 = st.columns(2)
        with sc1:
            save_e   = st.form_submit_button("💾 Save Changes", use_container_width=True)
        with sc2:
            cancel_e = st.form_submit_button("✕ Cancel", use_container_width=True)

        if cancel_e:
            st.session_state[f"vp_edit_{entry_id}"] = False
            st.rerun()

        if save_e:
            new_good_id  = g_ids_all[new_g_idx] if ek == "Bill" else None
            new_cat_name = g_cat_all[new_g_idx]  if ek == "Bill" else None
            _new_specified = new_good_id is not None

            # Determine new mode for bill edits
            if ek == "Bill":
                if _new_specified and new_bags > 0 and new_kg > 0:
                    _edit_new_mode = "A"
                elif not _new_specified and new_bags > 0:
                    _edit_new_mode = "B"
                elif not _new_specified:
                    _edit_new_mode = "C"
                else:
                    _edit_new_mode = "err"
            else:
                _edit_new_mode = None

            if new_amt <= 0:
                st.error("Amount must be > 0.")
            elif ek == "Bill" and _edit_new_mode == "err":
                st.error("Bags and Kg must be > 0 when a specific good is selected.")
            else:
                if ek == "Bill":
                    _old_good_id_val = entry_row.get("good_id")
                    _old_bags        = int(entry_row["bags"])
                    _old_kg          = float(entry_row["quantity_kg"])
                    _old_particulars = str(entry_row["particulars"])

                    # Negative-stock confirmation only when old bill had an identified good
                    _do_confirm = False
                    if _old_good_id_val is not None:
                        _gid_old = int(_old_good_id_val)
                        _sl_row = conn.execute(
                            "SELECT bags, quantity_kg FROM stock_levels "
                            "WHERE good_id=%s AND location='Transport'",
                            (_gid_old,)).fetchone()
                        if _sl_row:
                            _same_good    = (new_good_id == _gid_old)
                            _preview_bags = (int(_sl_row["bags"]) - _old_bags
                                             + (int(new_bags) if _same_good else 0))
                            _preview_kg   = (float(_sl_row["quantity_kg"]) - _old_kg
                                             + (float(new_kg) if _same_good else 0.0))
                            if _preview_bags < 0 or _preview_kg < 0:
                                _gname_row = conn.execute(
                                    "SELECT good_name FROM stock_goods WHERE good_id=%s",
                                    (_gid_old,)).fetchone()
                                _gname = (_gname_row["good_name"] if _gname_row
                                          else f"good_id={_gid_old}")
                                _new_part = new_cat_name or _old_particulars
                                _ncat_r = conn.execute(
                                    "SELECT category_id FROM stock_categories "
                                    "WHERE category_name=%s",
                                    (_new_part,)).fetchone()
                                st.session_state[_confirm_key] = {
                                    "good_name":      _gname,
                                    "preview_bags":   _preview_bags,
                                    "preview_kg":     _preview_kg,
                                    "new_mode":       _edit_new_mode,
                                    "new_good_id":    new_good_id,
                                    "new_cat_name":   _new_part,
                                    "new_cat_id":     _ncat_r["category_id"] if _ncat_r else None,
                                    "new_date":       str(new_date),
                                    "new_bags":       int(new_bags),
                                    "new_kg":         float(new_kg),
                                    "new_amt_neg":    -new_amt,
                                    "new_firm":       new_firm,
                                    "new_note":       edit_note.strip(),
                                }
                                _do_confirm = True
                                st.rerun()

                    if not _do_confirm:
                        _new_part = new_cat_name or _old_particulars

                        with conn:
                            # Reverse old stock effect
                            if _old_good_id_val is not None:
                                warn = reverse_bill_stock(
                                    conn, int(_old_good_id_val), _old_bags, _old_kg)
                            elif _old_bags > 0:
                                # Old bill was MODE B — reverse unidentified stock
                                _oc_row = conn.execute(
                                    "SELECT category_id FROM stock_categories "
                                    "WHERE category_name=%s",
                                    (_old_particulars,)).fetchone()
                                if _oc_row:
                                    reverse_unidentified_stock(
                                        conn, _oc_row["category_id"], "Transport",
                                        float(_old_bags), _old_kg)
                                warn = None
                            else:
                                warn = None

                            # Apply new stock effect
                            if _edit_new_mode == "A":
                                conn.execute(
                                    "UPDATE stock_levels "
                                    "SET bags=bags+%s, quantity_kg=quantity_kg+%s "
                                    "WHERE good_id=%s AND location='Transport'",
                                    (int(new_bags), float(new_kg), new_good_id))
                            elif _edit_new_mode == "B":
                                _nc_row = conn.execute(
                                    "SELECT category_id FROM stock_categories "
                                    "WHERE category_name=%s",
                                    (_new_part,)).fetchone()
                                if _nc_row:
                                    add_unidentified_stock(
                                        conn, _nc_row["category_id"], "Transport",
                                        float(new_bags), float(new_kg))

                            conn.execute(
                                "UPDATE vendor_entries "
                                "SET entry_date=%s,particulars=%s,amount=%s,"
                                "good_id=%s,bags=%s,quantity_kg=%s,firm=%s,note=%s WHERE entry_id=%s",
                                (str(new_date), _new_part, -new_amt,
                                 new_good_id, int(new_bags), float(new_kg),
                                 new_firm, edit_note.strip(), entry_id))
                        st.session_state[f"vp_edit_{entry_id}"] = False
                        if warn:
                            st.warning(warn)
                        st.success("Bill updated.")
                        st.rerun()
                else:
                    with conn:
                        conn.execute(
                            "UPDATE vendor_entries SET entry_date=%s,amount=%s,firm=%s,note=%s "
                            "WHERE entry_id=%s",
                            (str(new_date), new_amt, new_firm, edit_note.strip(), entry_id))
                        # ── Passbook sync: update linked RTGS passbook entry ──
                        if entry_row.get("ledger_type") == "RTGS" and entry_row.get("firm"):
                            conn.execute(
                                "UPDATE passbook_entries "
                                "SET entry_date=%s,amount=%s,firm=%s "
                                "WHERE source_type=%s AND source_id=%s",
                                (str(new_date), new_amt, new_firm, SRC_VENDOR_RTGS, entry_id))
                        # ── CASH IN HAND SYNC ──────────────────────────────
                        if entry_row.get("ledger_type") == "UB":
                            conn.execute(
                                "UPDATE cash_in_hand_entries SET entry_date=%s,amount=%s "
                                "WHERE source_type=%s AND source_id=%s",
                                (str(new_date), new_amt, SRC_VENDOR_UB, entry_id))
                    st.session_state[f"vp_edit_{entry_id}"] = False
                    st.success("Payment updated.")
                    st.rerun()


# ══════════════════════════════════════════════════════════════
#  DELETE CONFIRM  (inline, per entry)
# ══════════════════════════════════════════════════════════════

def _delete_confirm(entry_row, conn):
    entry_id = int(entry_row["entry_id"])
    ek       = entry_row["entry_kind"]
    note     = " Stock will be reversed." if ek == "Bill" else ""
    st.markdown(
        f'<div style="background:#1e0808;border:1px solid #6a1a1a;border-radius:8px;'
        f'padding:0.6rem 1rem;margin:0.2rem 0">'
        f'<span style="color:#ff8080;font-size:0.83rem">'
        f'⚠️ Delete {ek} #{entry_id}%s{note}</span></div>',
        unsafe_allow_html=True)
    dc1, dc2, _ = st.columns([0.9, 0.9, 6])
    with dc1:
        if st.button("✔ Delete", key=f"vp_cfd_{entry_id}", use_container_width=True):
            with conn:
                if ek == "Bill":
                    _del_gid  = entry_row.get("good_id")
                    _del_bags = int(entry_row["bags"])
                    _del_kg   = float(entry_row["quantity_kg"])
                    if _del_gid is not None:
                        # MODE A: reverse identified stock
                        warn = reverse_bill_stock(conn, int(_del_gid), _del_bags, _del_kg)
                        if warn:
                            st.warning(warn)
                    elif _del_bags > 0:
                        # MODE B: reverse unidentified stock
                        _dc_row = conn.execute(
                            "SELECT category_id FROM stock_categories "
                            "WHERE category_name=%s",
                            (str(entry_row["particulars"]),)).fetchone()
                        if _dc_row:
                            reverse_unidentified_stock(
                                conn, _dc_row["category_id"], "Transport",
                                float(_del_bags), _del_kg)
                    # MODE C: no stock reversal
                # ── Passbook sync: remove linked RTGS passbook entry ──
                if ek == "Payment" and entry_row.get("ledger_type") == "RTGS":
                    conn.execute(
                        "DELETE FROM passbook_entries "
                        "WHERE source_type=%s AND source_id=%s",
                        (SRC_VENDOR_RTGS, entry_id))
                # ── CASH IN HAND SYNC ──────────────────────────────
                if ek == "Payment" and entry_row.get("ledger_type") == "UB":
                    conn.execute(
                        "DELETE FROM cash_in_hand_entries "
                        "WHERE source_type=%s AND source_id=%s",
                        (SRC_VENDOR_UB, entry_id))
                conn.execute("DELETE FROM vendor_entries WHERE entry_id=%s", (entry_id,))
            st.session_state[f"vp_del_{entry_id}"] = False
            st.rerun()
    with dc2:
        if st.button("✕ Cancel", key=f"vp_cfc_{entry_id}", use_container_width=True):
            st.session_state[f"vp_del_{entry_id}"] = False
            st.rerun()


# ══════════════════════════════════════════════════════════════
#  LEDGER TABLE RENDERER
# ══════════════════════════════════════════════════════════════

_HDR_STYLE = ("font-size:0.62rem;color:#3a3628;text-transform:uppercase;"
              "letter-spacing:0.1em;padding:3px 4px;border-bottom:1px solid #252318")

_CELL_STYLE = "padding:5px 4px;font-size:0.8rem"


def _col_header(label: str, align: str = "left"):
    ta = f"text-align:{align};display:block" if align != "left" else ""
    st.markdown(f'<div style="{_HDR_STYLE};{ta}">{label}</div>', unsafe_allow_html=True)


def _cell(text, color="#c8bfa8", align="left", bold=False):
    fw = "font-weight:700;" if bold else ""
    ta = f"text-align:{align};" if align != "left" else ""
    st.markdown(
        f'<div style="{_CELL_STYLE};color:{color};{ta}{fw}">{text}</div>',
        unsafe_allow_html=True)


def _render_entries(df: pd.DataFrame, conn=None, show_ledger_col: bool = False):
    """
    Render a ledger table.
    show_ledger_col=True  → Total Ledger (read-only, adds Ledger column)
    show_ledger_col=False → RTGS/UB ledger (with ✏️ 🗑 per row)
    """
    if df.empty:
        st.markdown(
            '<div class="empty-state" style="padding:1.5rem">'
            '<div class="es-icon">📑</div>No entries yet.</div>',
            unsafe_allow_html=True)
        return

    df_b = compute_balances(df)

    # Column layout
    if show_ledger_col:
        widths = [0.35, 0.9, 2.2, 1.0, 1.3, 1.3]
        headers = ["No.", "Date", "Particulars", "Ledger", "Amount", "Balance"]
    else:
        widths  = [0.35, 0.9, 2.5, 1.3, 1.3, 0.55, 0.55]
        headers = ["No.", "Date", "Particulars", "Amount", "Balance", "", ""]

    # Header row
    hcols = st.columns(widths)
    aligns = (["left"] * 3 + ["left"] + ["right", "right"]) if show_ledger_col else \
             (["left"] * 3 + ["right", "right", "left", "left"])
    for hc, lbl, al in zip(hcols, headers, aligns):
        with hc:
            ta = f"text-align:{al};display:block;" if al != "left" else ""
            st.markdown(f'<div style="{_HDR_STYLE};{ta}">{lbl}</div>',
                        unsafe_allow_html=True)

    for _, row in df_b.iterrows():
        entry_id  = int(row["entry_id"])
        amt       = float(row["amount"])
        bal       = float(row["balance"])
        amt_color = "#ff5555" if amt < 0 else "#8dd87a"
        b_color   = bal_color(bal)
        kind_tag  = (
            '<span style="font-size:0.6rem;background:#13141f;color:#6a9fd4;'
            'border-radius:3px;padding:1px 5px;margin-right:4px;border:1px solid #252840">Bill</span>'
            if row["entry_kind"] == "Bill" else
            '<span style="font-size:0.6rem;background:#0d1e0d;color:#8dd87a;'
            'border-radius:3px;padding:1px 5px;margin-right:4px;border:1px solid #1e3e1e">Paid</span>'
        )

        if show_ledger_col:
            lt = row.get("ledger_type", "")
            if lt == "RTGS":
                firm_lbl = row.get("firm") or ""
                ledger_lbl = f"RTGS ({firm_lbl})"
            else:
                ledger_lbl = "UB"
            c_no, c_dt, c_pt, c_ld, c_am, c_bl = st.columns(widths)
        else:
            c_no, c_dt, c_pt, c_am, c_bl, c_ed, c_dl = st.columns(widths)

        with c_no:
            _cell(str(int(row["s_no"])), "#5a5448")
        with c_dt:
            _cell(fmt_date(row["entry_date"]))
        with c_pt:
            _note = str(row.get("note") or "").strip()
            _bags_info = ("" if row["entry_kind"] == "Payment"
                          else (" · " + str(int(row["bags"])) + " bags / "
                                + str(float(row["quantity_kg"])) + " Kg"
                                if row.get("bags") else ""))
            _note_html = (
                f'<br><span style="font-size:.72rem;color:#5a5448;font-style:italic">'
                f'{h(_note)}</span>' if _note else "")
            st.markdown(
                f'<div style="{_CELL_STYLE};color:#c8bfa8">'
                f'{kind_tag}{h(row["particulars"])}{_bags_info}{_note_html}'
                f'</div>', unsafe_allow_html=True)
        if show_ledger_col:
            with c_ld:
                _cell(ledger_lbl, "#8a8070")
        with c_am:
            _cell(fmt_inr(amt), amt_color, align="right", bold=True)
        with c_bl:
            _cell(fmt_inr(bal), b_color, align="right", bold=True)

        if not show_ledger_col:
            with c_ed:
                if st.button("✏️", key=f"vp_eb_{entry_id}", use_container_width=True):
                    # Close all other open edit panels
                    for k in [x for x in st.session_state if x.startswith("vp_edit_")]:
                        st.session_state[k] = False
                    st.session_state[f"vp_edit_{entry_id}"] = \
                        not st.session_state.get(f"vp_edit_{entry_id}", False)
                    st.session_state[f"vp_del_{entry_id}"] = False
                    st.rerun()
            with c_dl:
                if st.button("🗑", key=f"vp_db_{entry_id}", use_container_width=True):
                    st.session_state[f"vp_del_{entry_id}"] = \
                        not st.session_state.get(f"vp_del_{entry_id}", False)
                    st.session_state[f"vp_edit_{entry_id}"] = False
                    st.rerun()

            if st.session_state.get(f"vp_edit_{entry_id}"):
                _edit_form(row, conn)
            if st.session_state.get(f"vp_del_{entry_id}"):
                _delete_confirm(row, conn)

        st.markdown(
            '<div style="border-bottom:1px solid #181610;margin-bottom:1px"></div>',
            unsafe_allow_html=True)


# ══════════════════════════════════════════════════════════════
#  PAGE: HOME  (Level 1)
# ══════════════════════════════════════════════════════════════

if st.session_state.vp_page == "home":

    bcol, _ = st.columns([1, 8])
    with bcol:
        st.markdown('<div class="back-btn">', unsafe_allow_html=True)
        if st.button("← Home"):
            st.switch_page("app.py")
        st.markdown('</div>', unsafe_allow_html=True)

    st.markdown('<div class="page-title">Vendor Payments</div>', unsafe_allow_html=True)
    st.markdown('<div class="page-sub">Vendor directory</div>', unsafe_allow_html=True)

    conn = get_conn()
    # row_factory not needed with psycopg2 RealDictCursor

    try:
        _vs = conn.execute("""
            SELECT
                (SELECT COUNT(*) FROM vendors)                              AS n_vendors,
                COALESCE(SUM(amount) FILTER (WHERE amount < 0), 0)         AS tot_bills,
                COALESCE(SUM(amount) FILTER (WHERE amount > 0), 0)         AS tot_pmts,
                COALESCE(SUM(amount), 0)                                   AS net_bal
            FROM vendor_entries
        """).fetchone()
        n_vendors = int(_vs["n_vendors"])
        tot_bills = float(_vs["tot_bills"])
        tot_pmts  = float(_vs["tot_pmts"])
        net_bal   = round(float(_vs["net_bal"]), 2)

        st.markdown(
            f'<div class="stat-row">'
            f'<div class="stat-pill"><span class="sp-label">Vendors</span>'
            f'<span class="sp-value">{int(n_vendors)}</span></div>'
            f'<div class="stat-pill"><span class="sp-label">Total Bills</span>'
            f'<span class="sp-value" style="color:#ff5555">{fmt_inr(-tot_bills)}</span></div>'
            f'<div class="stat-pill"><span class="sp-label">Total Payments</span>'
            f'<span class="sp-value" style="color:#8dd87a">{fmt_inr(tot_pmts)}</span></div>'
            f'<div class="stat-pill"><span class="sp-label">Net Balance</span>'
            f'<span class="sp-value" style="color:{bal_color(net_bal)}">'
            f'{fmt_inr(net_bal)}</span></div></div>',
            unsafe_allow_html=True)

        # Search bar + Add Vendor popover
        sv1, sv2 = st.columns([4, 1.2])
        with sv1:
            search = st.text_input("", placeholder="🔍  Search vendor...",
                                   label_visibility="collapsed", key="vp_search")
        with sv2:
            with st.popover("＋ Add Vendor", use_container_width=True):
                with st.form("add_vendor_form"):
                    new_name = st.text_input("Vendor Name", key="vp_new_vendor_name")
                    if st.form_submit_button("Save Vendor", use_container_width=True):
                        nm = new_name.strip()
                        if not nm:
                            st.error("Name cannot be empty.")
                        else:
                            try:
                                max_id = conn.execute(
                                    "SELECT COALESCE(MAX(vendor_id),100) AS v FROM vendors"
                                ).fetchone()["v"]
                                conn.execute(
                                    "INSERT INTO vendors (vendor_id, vendor_name) VALUES (%s,%s)",
                                    (int(max_id) + 1, nm))
                                conn.commit()
                                invalidate_lookup_cache()
                                st.toast(f"Vendor '{nm}' added.", icon="✅")
                                st.rerun()
                            except psycopg2.errors.UniqueViolation:
                                st.error(f"'{nm}' already exists.")

        st.markdown("<hr>", unsafe_allow_html=True)

        df_v = pd.DataFrame(get_all_vendors_cached())
        if search:
            df_v = df_v[df_v["vendor_name"].str.contains(search, case=False, na=False)]

        if df_v.empty:
            st.markdown(
                '<div class="empty-state"><div class="es-icon">🏭</div>'
                'No vendors yet. Add one to get started.</div>',
                unsafe_allow_html=True)
        else:
            df_bal_q = pg_read_sql(
                "SELECT vendor_id, ROUND(SUM(amount)::numeric,2) bal "
                "FROM vendor_entries GROUP BY vendor_id", conn)
            bal_map = dict(zip(df_bal_q["vendor_id"].astype(int),
                               df_bal_q["bal"].astype(float)))

            for chunk in [df_v.iloc[i:i+2] for i in range(0, len(df_v), 2)]:
                g1, g2 = st.columns(2, gap="medium")
                for col_w, (_, row) in zip([g1, g2], chunk.iterrows()):
                    with col_w:
                        vid   = int(row["vendor_id"])
                        vname = row["vendor_name"]
                        bal   = round(float(bal_map.get(vid, 0.0)), 2)
                        bc    = bal_color(bal)

                        vc1, vc2 = st.columns([5, 1])
                        with vc1:
                            st.markdown(
                                f'<div style="background:#181610;border:1px solid #2a2820;'
                                f'border-radius:10px;padding:0.85rem 1.1rem;margin-bottom:0.2rem">'
                                f'<div style="font-size:0.75rem;color:#5a5448">#{vid}</div>'
                                f'<div style="font-size:1rem;font-weight:600;color:#e0d8c8">'
                                f'{h(vname)}</div>'
                                f'<div style="font-size:0.95rem;font-weight:700;color:{bc};'
                                f'margin-top:4px">{fmt_inr(bal)}</div>'
                                f'</div>', unsafe_allow_html=True)
                            if st.button("Open Ledger →", key=f"vp_open_{vid}",
                                         use_container_width=True):
                                st.session_state.update({
                                    "vp_page": "vendor",
                                    "vp_vendor_id": vid,
                                    "vp_vendor_name": vname,
                                })
                                st.rerun()
                        with vc2:
                            if st.button("🗑", key=f"vp_dv_{vid}",
                                         use_container_width=True):
                                st.session_state[f"vp_del_vendor_{vid}"] = \
                                    not st.session_state.get(f"vp_del_vendor_{vid}", False)
                                st.rerun()

                        if st.session_state.get(f"vp_del_vendor_{vid}"):
                            st.markdown(
                                f'<div style="background:#1e0808;border:1px solid #6a1a1a;'
                                f'border-radius:8px;padding:0.7rem 1rem;margin-bottom:0.4rem">'
                                f'<div style="color:#ff8080;font-size:0.83rem;font-weight:600">'
                                f'Delete {h(vname)}%s</div>'
                                f'<div style="color:#8a5050;font-size:0.75rem;margin-top:3px">'
                                f'All entries deleted. Bill stock reversed.</div></div>',
                                unsafe_allow_html=True)
                            dc1, dc2 = st.columns(2)
                            with dc1:
                                if st.button("✔ Confirm", key=f"vp_cfv_{vid}",
                                             use_container_width=True):
                                    with conn:
                                        # Reverse identified (MODE A) bills
                                        bills = conn.execute(
                                            "SELECT good_id, bags, quantity_kg "
                                            "FROM vendor_entries "
                                            "WHERE vendor_id=%s AND entry_kind='Bill' "
                                            "AND good_id IS NOT NULL", (vid,)).fetchall()
                                        for b in bills:
                                            reverse_bill_stock(conn, b["good_id"], b["bags"], b["quantity_kg"])
                                        # Reverse unidentified (MODE B) bills
                                        unid_bills = conn.execute(
                                            "SELECT particulars, bags, quantity_kg "
                                            "FROM vendor_entries "
                                            "WHERE vendor_id=%s AND entry_kind='Bill' "
                                            "AND good_id IS NULL AND bags > 0", (vid,)).fetchall()
                                        for ub in unid_bills:
                                            _uc_row = conn.execute(
                                                "SELECT category_id FROM stock_categories "
                                                "WHERE category_name=%s",
                                                (str(ub["particulars"]),)).fetchone()
                                            if _uc_row:
                                                reverse_unidentified_stock(
                                                    conn, _uc_row["category_id"], "Transport",
                                                    float(ub["bags"]), float(ub["quantity_kg"]))
                                        conn.execute(
                                            "DELETE FROM passbook_entries "
                                            "WHERE source_type=%s AND source_id IN ("
                                            "  SELECT entry_id FROM vendor_entries "
                                            "  WHERE vendor_id=%s AND entry_kind='Payment'"
                                            ")", (SRC_VENDOR_RTGS, vid))
                                        # ── CASH IN HAND SYNC ──────────────────────────────
                                        conn.execute(
                                            "DELETE FROM cash_in_hand_entries "
                                            "WHERE source_type=%s AND source_id IN ("
                                            "  SELECT entry_id FROM vendor_entries "
                                            "  WHERE vendor_id=%s AND ledger_type='UB'"
                                            "  AND entry_kind='Payment'"
                                            ")", (SRC_VENDOR_UB, vid))
                                        conn.execute(
                                            "DELETE FROM vendor_entries WHERE vendor_id=%s", (vid,))
                                        conn.execute(
                                            "DELETE FROM vendors WHERE vendor_id=%s", (vid,))
                                    invalidate_lookup_cache()
                                    st.session_state.pop(f"vp_del_vendor_{vid}", None)
                                    st.rerun()
                            with dc2:
                                if st.button("✕ Cancel", key=f"vp_cav_{vid}",
                                             use_container_width=True):
                                    st.session_state[f"vp_del_vendor_{vid}"] = False
                                    st.rerun()
    finally:
        conn.close()


# ══════════════════════════════════════════════════════════════
#  PAGE: VENDOR  (Level 2 — Ledger Hub)
# ══════════════════════════════════════════════════════════════

elif st.session_state.vp_page == "vendor":

    vid   = st.session_state.vp_vendor_id
    vname = st.session_state.vp_vendor_name

    bcol, _ = st.columns([1, 8])
    with bcol:
        st.markdown('<div class="back-btn">', unsafe_allow_html=True)
        if st.button("← Vendors"):
            st.session_state.vp_page = "home"
            st.rerun()
        st.markdown('</div>', unsafe_allow_html=True)

    conn = get_conn()
    # row_factory not needed with psycopg2 RealDictCursor

    try:
        row = conn.execute(
            "SELECT COALESCE(SUM(CASE WHEN amount<0 THEN amount ELSE 0 END),0) bills,"
            "COALESCE(SUM(CASE WHEN amount>0 THEN amount ELSE 0 END),0) pmts,"
            "COALESCE(SUM(amount),0) net, COUNT(*) cnt "
            "FROM vendor_entries WHERE vendor_id=%s", (vid,)).fetchone()
        bills_val = float(row["bills"])
        pmts_val  = float(row["pmts"])
        net_val   = round(float(row["net"]), 2)
        n_entries = int(row["cnt"])

        # Vendor header card
        st.markdown(
            f'<div style="background:linear-gradient(135deg,#1a1810,#201e14);'
            f'border:1px solid #2e2b1e;border-radius:14px;padding:1.2rem 1.6rem;'
            f'margin-bottom:1.2rem;display:flex;justify-content:space-between;'
            f'align-items:center;flex-wrap:wrap;gap:1rem">'
            f'<div>'
            f'<div style="font-family:\'Playfair Display\',serif;font-size:1.4rem;'
            f'color:#e8c97e">{h(vname)}</div>'
            f'<div style="font-size:0.75rem;color:#4a4438;margin-top:3px">'
            f'Vendor #{vid}</div>'
            f'</div>'
            f'<div style="display:flex;gap:1.8rem;text-align:right;flex-wrap:wrap">'
            f'<div><div style="font-size:.65rem;color:#3a3628;text-transform:uppercase;'
            f'letter-spacing:.1em">Total Bills</div>'
            f'<div style="font-size:1.1rem;font-weight:600;color:#ff5555">'
            f'{fmt_inr(-bills_val)}</div></div>'
            f'<div><div style="font-size:.65rem;color:#3a3628;text-transform:uppercase;'
            f'letter-spacing:.1em">Payments</div>'
            f'<div style="font-size:1.1rem;font-weight:600;color:#8dd87a">'
            f'{fmt_inr(pmts_val)}</div></div>'
            f'<div><div style="font-size:.65rem;color:#3a3628;text-transform:uppercase;'
            f'letter-spacing:.1em">Net Balance</div>'
            f'<div style="font-size:1.1rem;font-weight:600;color:{bal_color(net_val)}">'
            f'{fmt_inr(net_val)}</div></div>'
            f'<div><div style="font-size:.65rem;color:#3a3628;text-transform:uppercase;'
            f'letter-spacing:.1em">Entries</div>'
            f'<div style="font-size:1.1rem;font-weight:600;color:#c8bfa8">'
            f'{n_entries}</div></div>'
            f'</div></div>',
            unsafe_allow_html=True)

        # RTGS / UB navigation buttons
        nb1, nb2, _ = st.columns([1.2, 1.2, 5.6], gap="small")
        with nb1:
            if st.button("📘 RTGS Ledger", use_container_width=True, key="vp_goto_rtgs"):
                st.session_state.vp_page = "rtgs"
                st.rerun()
        with nb2:
            if st.button("📕 UB Ledger", use_container_width=True, key="vp_goto_ub"):
                st.session_state.vp_page = "ub"
                st.rerun()

        st.markdown("<hr>", unsafe_allow_html=True)

        # Total Ledger — always visible, read-only combined view
        st.markdown(
            '<div style="font-family:\'Playfair Display\',serif;font-size:1.1rem;'
            'color:#e8c97e;margin-bottom:0.8rem">'
            'Total Ledger — Combined view</div>',
            unsafe_allow_html=True)

        df_all = pg_read_sql(
            "SELECT * FROM vendor_entries WHERE vendor_id=%s "
            "ORDER BY entry_date ASC, entry_id ASC",
            conn, params=(vid,))

        _render_entries(df_all, show_ledger_col=True)
    finally:
        conn.close()


# ══════════════════════════════════════════════════════════════
#  PAGE: RTGS  (Level 3A)
# ══════════════════════════════════════════════════════════════

elif st.session_state.vp_page == "rtgs":

    vid   = st.session_state.vp_vendor_id
    vname = st.session_state.vp_vendor_name

    bcol, _ = st.columns([1.5, 8])
    with bcol:
        st.markdown('<div class="back-btn">', unsafe_allow_html=True)
        if st.button(f"← {vname}"):
            st.session_state.vp_page = "vendor"
            st.rerun()
        st.markdown('</div>', unsafe_allow_html=True)

    st.markdown(
        f'<div class="page-title">{h(vname)} — RTGS Ledger</div>',
        unsafe_allow_html=True)

    # Firm toggle
    firm = st.radio(
        "Firm", ["SP Spices", "Mukund Traders"],
        index=0 if st.session_state.vp_rtgs_firm == FIRM_SP else 1,
        horizontal=True, key="vp_rtgs_firm_radio",
        label_visibility="collapsed")
    st.session_state.vp_rtgs_firm = firm

    # Map UI label to DB value
    firm_db = FIRM_SP if firm == FIRM_SP else FIRM_MT

    conn = get_conn()
    # row_factory not needed with psycopg2 RealDictCursor

    try:
        # Action buttons
        ab1, ab2, _ = st.columns([1.2, 1.4, 5.4], gap="small")
        with ab1:
            _bill_popover(vid, "RTGS", firm_db, conn)
        with ab2:
            _payment_popover(vid, "RTGS", firm_db, conn)

        st.markdown("<hr>", unsafe_allow_html=True)

        # Filtered ledger for this firm
        st.markdown(
            f'<div style="font-size:0.75rem;color:#5a5448;margin-bottom:0.6rem">'
            f'Showing: RTGS · {firm_db}</div>',
            unsafe_allow_html=True)

        df_rtgs = pg_read_sql(
            "SELECT * FROM vendor_entries "
            "WHERE vendor_id=%s AND ledger_type='RTGS' AND firm=%s "
            "ORDER BY entry_date ASC, entry_id ASC",
            conn, params=(vid, firm_db))

        _render_entries(df_rtgs, conn=conn, show_ledger_col=False)
    finally:
        conn.close()


# ══════════════════════════════════════════════════════════════
#  PAGE: UB  (Level 3B)
# ══════════════════════════════════════════════════════════════

elif st.session_state.vp_page == "ub":

    vid   = st.session_state.vp_vendor_id
    vname = st.session_state.vp_vendor_name

    bcol, _ = st.columns([1.5, 8])
    with bcol:
        st.markdown('<div class="back-btn">', unsafe_allow_html=True)
        if st.button(f"← {vname}"):
            st.session_state.vp_page = "vendor"
            st.rerun()
        st.markdown('</div>', unsafe_allow_html=True)

    st.markdown(
        f'<div class="page-title">{h(vname)} — UB Ledger</div>',
        unsafe_allow_html=True)

    conn = get_conn()
    # row_factory not needed with psycopg2 RealDictCursor

    try:
        ab1, ab2, _ = st.columns([1.2, 1.4, 5.4], gap="small")
        with ab1:
            _bill_popover(vid, "UB", None, conn)
        with ab2:
            _payment_popover(vid, "UB", None, conn)

        st.markdown("<hr>", unsafe_allow_html=True)

        df_ub = pg_read_sql(
            "SELECT * FROM vendor_entries "
            "WHERE vendor_id=%s AND ledger_type='UB' "
            "ORDER BY entry_date ASC, entry_id ASC",
            conn, params=(vid,))

        _render_entries(df_ub, conn=conn, show_ledger_col=False)
    finally:
        conn.close()
