"""
Stock Register — live inventory across Transport, Shop, and Anandpuri.
Navigation: st.session_state.stock_page in ("home", "category")
"""

import streamlit as st
import psycopg2
import pandas as pd
from datetime import date

from utils.db import get_conn, pg_read_sql, ensure_schema, log_stock_change, purge_old_stock_history
from utils.styles import APP_CSS, BRAND_BAR_HTML, get_light_mode_css
from utils.formatters import h, fmt_inr
from utils.auth import require_login, render_logout_button

LOCATIONS = ["Transport", "Shop", "Anandpuri"]

LOC_ACCENT = {
    "Transport": {"color": "#d4864a", "bg": "#1e1208", "border": "#4a2800"},
    "Shop":      {"color": "#8dd87a", "bg": "#0d1209", "border": "#1e3018"},
    "Anandpuri": {"color": "#6a9fd4", "bg": "#0d1118", "border": "#1e2840"},
}

# ── Page config ─────────────────────────────────────────────────
st.set_page_config(
    page_title="Stock Register – S P Spices",
    page_icon="📦",
    layout="wide",
    initial_sidebar_state="collapsed",
)

require_login()
render_logout_button()
st.markdown(APP_CSS, unsafe_allow_html=True)
if st.session_state.get("light_mode"):
    st.markdown(get_light_mode_css(), unsafe_allow_html=True)
st.markdown(BRAND_BAR_HTML, unsafe_allow_html=True)


# ── Schema & seeding ────────────────────────────────────────────
# Stock tables are created and seeded by utils.db.ensure_schema(),
# which is called here to guarantee tables exist if this page is
# opened directly (without going through app.py first).
ensure_schema()


@st.cache_resource
def _run_initial_purge():
    _pc = get_conn()
    try:
        with _pc:
            purge_old_stock_history(_pc)
    finally:
        _pc.close()


_run_initial_purge()

# ── Session state ───────────────────────────────────────────────
for _k, _v in {
    "stock_page": "home",
    "stock_selected_category_id": None,
    "stock_selected_category_name": "",
    "stock_transfer_loc": None,
    "stock_update_loc": None,
}.items():
    if _k not in st.session_state:
        st.session_state[_k] = _v


# ══════════════════════════════════════════════════════════════
#  LEVEL 1 — HOME
# ══════════════════════════════════════════════════════════════
if st.session_state.stock_page == "home":

    bcol, _ = st.columns([1, 8])
    with bcol:
        st.markdown('<div class="back-btn">', unsafe_allow_html=True)
        if st.button("← Home"):
            for _ck in ["stock_transfer_loc", "stock_update_loc",
                        "stock_selected_category_id", "stock_selected_category_name"]:
                st.session_state.pop(_ck, None)
            st.switch_page("app.py")
        st.markdown('</div>', unsafe_allow_html=True)

    st.markdown('<div class="page-title">Stock Register</div>', unsafe_allow_html=True)
    st.markdown(
        '<div class="page-sub">Live inventory across all locations</div>',
        unsafe_allow_html=True)

    conn = get_conn()
    try:
        n_cats   = conn.execute("SELECT COUNT(*) AS cnt FROM stock_categories").fetchone()["cnt"]
        n_goods  = conn.execute("SELECT COUNT(*) AS cnt FROM stock_goods").fetchone()["cnt"]
        _unid_bags = float(conn.execute(
            "SELECT COALESCE(SUM(bags),0) AS v FROM unidentified_stock").fetchone()["v"])
        _unid_kg   = float(conn.execute(
            "SELECT COALESCE(SUM(quantity_kg),0) AS v FROM unidentified_stock").fetchone()["v"])
        tot_bags = float(conn.execute(
            "SELECT COALESCE(SUM(bags),0) AS v FROM stock_levels").fetchone()["v"]) + _unid_bags
        tot_kg   = float(conn.execute(
            "SELECT COALESCE(SUM(quantity_kg),0) AS v FROM stock_levels").fetchone()["v"]) + _unid_kg

        st.markdown(
            f'<div class="stat-row">'
            f'<div class="stat-pill"><span class="sp-label">Categories</span>'
            f'<span class="sp-value">{int(n_cats)}</span></div>'
            f'<div class="stat-pill"><span class="sp-label">Good Types</span>'
            f'<span class="sp-value">{int(n_goods)}</span></div>'
            f'<div class="stat-pill"><span class="sp-label">Total Bags</span>'
            f'<span class="sp-value" style="color:#8dd87a">{tot_bags:,.0f}</span></div>'
            f'<div class="stat-pill"><span class="sp-label">Total Kgs</span>'
            f'<span class="sp-value" style="color:#6a9fd4">{tot_kg:,.1f} Kg</span></div>'
            f'</div>',
            unsafe_allow_html=True)

        # ── Negative stock alert ─────────────────────────────────
        neg_rows = conn.execute("""
            SELECT sg.good_name, sc.category_name, sl.location,
                   sl.bags, sl.quantity_kg
            FROM stock_levels sl
            JOIN stock_goods sg ON sg.good_id = sl.good_id
            JOIN stock_categories sc ON sc.category_id = sg.category_id
            WHERE sl.bags < 0 OR sl.quantity_kg < 0
            ORDER BY sc.category_name, sg.good_name""").fetchall()
        if neg_rows:
            neg_list_html = "".join(
                f'<div style="display:flex;justify-content:space-between;'
                f'padding:4px 0;border-bottom:1px solid #4a1212">'
                f'<span style="color:#ffaaaa">{h(r["good_name"])}'
                f'<span style="color:#7a3030;font-size:0.78rem"> — {h(r["category_name"])}, {h(r["location"])}</span></span>'
                f'<span style="color:#ff6060;font-weight:700;font-size:0.85rem">'
                f'{int(r["bags"])} bags &nbsp;/&nbsp; {float(r["quantity_kg"]):.1f} Kg</span>'
                f'</div>'
                for r in neg_rows
            )
            st.markdown(
                f'<div style="background:#2a0808;border:1px solid #8a1a1a;border-radius:10px;'
                f'padding:1rem 1.3rem;margin-bottom:1.2rem">'
                f'<div style="font-size:0.88rem;color:#ff8080;font-weight:700;margin-bottom:0.7rem">'
                f'⚠ {len(neg_rows)} item(s) with negative stock — immediate attention needed'
                f'</div>{neg_list_html}</div>',
                unsafe_allow_html=True)

        # Add Category button
        _, add_col = st.columns([6, 1.2])
        with add_col:
            with st.popover("＋ Add Category", use_container_width=True):
                new_cat = st.text_input("Category Name", key="new_cat_name")
                if st.button("Save Category", key="save_cat_btn"):
                    name = new_cat.strip()
                    if not name:
                        st.error("Name cannot be empty.")
                    else:
                        try:
                            c2 = get_conn()
                            _c2_row = c2.execute(
                                "INSERT INTO stock_categories (category_name) VALUES (%s) "
                                "RETURNING category_id", (name,))
                            _c2_id = _c2_row.fetchone()["category_id"]
                            for _loc in ["Transport", "Shop", "Anandpuri"]:
                                c2.execute(
                                    "INSERT INTO unidentified_stock "
                                    "(category_id, location, bags, quantity_kg) VALUES (%s,%s,0,0)",
                                    (_c2_id, _loc))
                            c2.commit()
                            c2.close()
                            st.success(f"Added '{name}'")
                            st.rerun()
                        except psycopg2.errors.UniqueViolation:
                            st.error(f"'{name}' already exists.")

        # Category cards
        cats = conn.execute(
            "SELECT category_id, category_name FROM stock_categories "
            "ORDER BY category_name").fetchall()

        for cat_row in cats:
            cat_id   = cat_row["category_id"]
            cat_name = cat_row["category_name"]
            totals = conn.execute("""
                SELECT COALESCE(SUM(sl.bags),0) AS total_bags,
                       COALESCE(SUM(sl.quantity_kg),0) AS total_kg
                FROM stock_goods sg
                LEFT JOIN stock_levels sl ON sl.good_id = sg.good_id
                WHERE sg.category_id = %s""", (cat_id,)).fetchone()
            _unid_cat = conn.execute(
                "SELECT COALESCE(SUM(bags),0) AS total_bags, "
                "       COALESCE(SUM(quantity_kg),0) AS total_kg "
                "FROM unidentified_stock WHERE category_id=%s",
                (cat_id,)).fetchone()
            total_bags_cat = float(totals["total_bags"]) + float(_unid_cat["total_bags"])
            total_kg_cat   = float(totals["total_kg"])   + float(_unid_cat["total_kg"])

            loc_rows = conn.execute("""
                SELECT sl.location,
                       COALESCE(SUM(sl.bags),0) AS loc_bags,
                       COALESCE(SUM(sl.quantity_kg),0) AS loc_kg
                FROM stock_goods sg
                LEFT JOIN stock_levels sl ON sl.good_id = sg.good_id
                WHERE sg.category_id = %s
                GROUP BY sl.location""", (cat_id,)).fetchall()
            loc_map = {r["location"]: (float(r["loc_bags"]), float(r["loc_kg"])) for r in loc_rows}

            # Add unidentified stock into per-location map
            _unid_loc_rows = conn.execute(
                "SELECT location, bags, quantity_kg "
                "FROM unidentified_stock WHERE category_id=%s",
                (cat_id,)).fetchall()
            for _ur in _unid_loc_rows:
                _ul = _ur["location"]
                _ub = float(_ur["bags"] or 0)
                _uk = float(_ur["quantity_kg"] or 0)
                if _ul in loc_map:
                    loc_map[_ul] = (loc_map[_ul][0] + _ub, loc_map[_ul][1] + _uk)
                else:
                    loc_map[_ul] = (_ub, _uk)

            n_goods_cat = conn.execute(
                "SELECT COUNT(*) AS cnt FROM stock_goods WHERE category_id=%s",
                (cat_id,)).fetchone()["cnt"]

            loc_html = "".join(
                f'<div style="flex:1;background:{LOC_ACCENT[loc]["bg"]};'
                f'border:1px solid {LOC_ACCENT[loc]["border"]};'
                f'border-radius:8px;padding:0.6rem 0.9rem">'
                f'<div style="font-size:0.65rem;color:{LOC_ACCENT[loc]["color"]};'
                f'text-transform:uppercase;letter-spacing:0.1em;margin-bottom:4px">{loc}</div>'
                f'<div style="font-size:0.9rem;font-weight:600;color:#e0d8c8">'
                f'{loc_map.get(loc,(0,0.0))[0]:,.0f} bags</div>'
                f'<div style="font-size:0.75rem;color:#6a6050">'
                f'{loc_map.get(loc,(0,0.0))[1]:,.1f} Kg</div>'
                f'</div>'
                for loc in LOCATIONS
            )

            st.markdown(
                f'<div style="background:#181610;border:1px solid #2a2820;border-radius:14px;'
                f'padding:1.4rem 1.6rem;margin-bottom:0.5rem">'
                f'<div style="display:flex;justify-content:space-between;'
                f'align-items:flex-start;margin-bottom:1rem">'
                f'<div>'
                f'<div style="font-family:\'Playfair Display\',serif;font-size:1.25rem;'
                f'color:#e8c97e;font-weight:700">{h(cat_name)}</div>'
                f'<div style="font-size:0.7rem;color:#5a5448;margin-top:3px">'
                f'{n_goods_cat} goods tracked</div>'
                f'</div>'
                f'<div style="text-align:right">'
                f'<div style="font-size:1.5rem;font-weight:700;color:#8dd87a">'
                f'{total_bags_cat:,.0f} bags</div>'
                f'<div style="font-size:0.85rem;color:#6a9fd4">'
                f'{total_kg_cat:,.1f} Kg</div>'
                f'</div></div>'
                f'<div style="display:flex;gap:0.7rem;margin-bottom:1rem">{loc_html}</div>'
                f'</div>',
                unsafe_allow_html=True)

            btn_col, _ = st.columns([1.6, 6.4])
            with btn_col:
                if st.button("View Details →", key=f"cat_btn_{cat_id}",
                             use_container_width=True):
                    st.session_state.update({
                        "stock_page": "category",
                        "stock_selected_category_id": cat_id,
                        "stock_selected_category_name": cat_name,
                        "stock_transfer_loc": None,
                        "stock_update_loc": None,
                    })
                    st.rerun()
    finally:
        conn.close()


# ══════════════════════════════════════════════════════════════
#  LEVEL 2 — CATEGORY + LOCATION VIEW
# ══════════════════════════════════════════════════════════════
elif st.session_state.stock_page == "category":

    cat_id   = st.session_state.stock_selected_category_id
    cat_name = st.session_state.stock_selected_category_name

    bcol, _ = st.columns([1, 8])
    with bcol:
        st.markdown('<div class="back-btn">', unsafe_allow_html=True)
        if st.button("← Back"):
            st.session_state["stock_page"] = "home"
            for _ck in ["stock_transfer_loc", "stock_update_loc",
                        "stock_selected_category_id", "stock_selected_category_name"]:
                st.session_state.pop(_ck, None)
            st.rerun()
        st.markdown('</div>', unsafe_allow_html=True)

    st.markdown(
        f'<div class="page-title">{h(cat_name)} — Stock Breakdown</div>',
        unsafe_allow_html=True)
    st.markdown(
        '<div class="page-sub">Location-wise inventory details</div>',
        unsafe_allow_html=True)

    conn = get_conn()
    try:
        goods = conn.execute(
            "SELECT good_id, good_name FROM stock_goods "
            "WHERE category_id=%s ORDER BY good_name",
            (cat_id,)).fetchall()
        good_ids   = [g["good_id"] for g in goods]
        good_names = [g["good_name"] for g in goods]

        df_levels = pg_read_sql("""
            SELECT sg.good_id, sg.good_name, sl.location,
                   sl.bags, sl.quantity_kg
            FROM stock_goods sg
            JOIN stock_levels sl ON sl.good_id = sg.good_id
            WHERE sg.category_id = %s
            ORDER BY sg.good_name, sl.location""",
            conn, params=(cat_id,))

        # ── Negative stock alert for this category ───────────────
        neg_cat = df_levels[(df_levels["bags"] < 0) | (df_levels["quantity_kg"] < 0)]
        if not neg_cat.empty:
            neg_items = "".join(
                f'<span style="display:inline-block;background:#3a0a0a;border:1px solid #7a1a1a;'
                f'border-radius:5px;padding:2px 10px;margin:2px 4px 2px 0;'
                f'font-size:0.8rem;color:#ff8080">'
                f'{h(r["good_name"])} @ {h(r["location"])} '
                f'({int(r["bags"])} bags / {float(r["quantity_kg"]):.1f} Kg)'
                f'</span>'
                for _, r in neg_cat.iterrows()
            )
            st.markdown(
                f'<div style="background:#2a0808;border:1px solid #8a1a1a;border-radius:10px;'
                f'padding:0.9rem 1.2rem;margin-bottom:1.2rem">'
                f'<div style="font-size:0.85rem;color:#ff8080;font-weight:700;margin-bottom:0.5rem">'
                f'⚠ Negative stock in {h(cat_name)} — update or transfer to correct</div>'
                f'{neg_items}</div>',
                unsafe_allow_html=True)

        # ── 3 location panels ────────────────────────────────────
        lc1, lc2, lc3 = st.columns(3, gap="medium")

        for col_widget, loc in zip([lc1, lc2, lc3], LOCATIONS):
            acc    = LOC_ACCENT[loc]
            df_loc = df_levels[df_levels["location"] == loc]
            total_bags_loc = int(df_loc["bags"].sum())
            total_kg_loc   = float(df_loc["quantity_kg"].sum())

            # Unidentified stock for this location
            _unid_row = conn.execute("""
                SELECT bags, quantity_kg
                FROM unidentified_stock
                WHERE category_id = %s AND location = %s
            """, (cat_id, loc)).fetchone()
            _unid_bags = float(_unid_row["bags"] or 0) if _unid_row else 0.0
            _unid_kg   = float(_unid_row["quantity_kg"] or 0) if _unid_row else 0.0

            with col_widget:
                # Panel header (total includes unidentified)
                _panel_bags = total_bags_loc + _unid_bags
                _panel_kg   = total_kg_loc   + _unid_kg
                st.markdown(
                    f'<div style="background:{acc["bg"]};border:1px solid {acc["border"]};'
                    f'border-radius:12px;padding:1rem 1.2rem;margin-bottom:0.8rem">'
                    f'<div style="font-family:\'Playfair Display\',serif;font-size:1.05rem;'
                    f'color:{acc["color"]};font-weight:700;margin-bottom:2px">{loc}</div>'
                    f'<div style="font-size:0.7rem;color:#4a4438">'
                    f'{_panel_bags:,.0f} bags &nbsp;·&nbsp; {_panel_kg:,.1f} Kg total'
                    f'</div></div>',
                    unsafe_allow_html=True)

                # Unidentified stock card — hidden when both values are zero
                if _unid_bags != 0 or _unid_kg != 0:
                    st.markdown(
                        f'<div style="background:#1a1208;border:1px solid #4a3010;'
                        f'border-radius:10px;padding:0.7rem 1rem;margin-bottom:0.8rem">'
                        f'<div style="font-size:.7rem;color:#b89040;text-transform:uppercase;'
                        f'letter-spacing:.1em;margin-bottom:4px">⚠ Unidentified Stock</div>'
                        f'<div style="font-size:.88rem;color:#e0d0a0">'
                        f'<b>{_unid_bags:,.0f}</b> bags &nbsp;·&nbsp; '
                        f'<b>{_unid_kg:,.2f} kg</b></div>'
                        f'<div style="font-size:.72rem;color:#6a5a30;margin-top:3px">'
                        f'Category known · Specific good not yet identified</div></div>',
                        unsafe_allow_html=True)

                # Edit button + form for unidentified stock
                _unid_edit_key = f"unid_edit_{cat_id}_{loc}"
                if _unid_edit_key not in st.session_state:
                    st.session_state[_unid_edit_key] = False

                if not st.session_state[_unid_edit_key]:
                    if st.button("✏️ Edit Unidentified",
                                 key=f"unid_btn_{cat_id}_{loc}",
                                 use_container_width=True):
                        st.session_state[_unid_edit_key] = True
                        st.rerun()
                else:
                    with st.form(f"unid_form_{cat_id}_{loc}"):
                        st.markdown("**Edit Unidentified Stock**")
                        _new_ubags = st.number_input(
                            "Bags", value=float(_unid_bags), step=1.0,
                            key=f"unid_nb_{cat_id}_{loc}")
                        _new_ukg   = st.number_input(
                            "Weight (Kg)", value=float(_unid_kg), step=0.1,
                            key=f"unid_nk_{cat_id}_{loc}")
                        _uc1, _uc2 = st.columns(2)
                        with _uc1:
                            _usave = st.form_submit_button(
                                "💾 Save", use_container_width=True)
                        with _uc2:
                            _ucancel = st.form_submit_button(
                                "Cancel", use_container_width=True)
                        if _ucancel:
                            st.session_state[_unid_edit_key] = False
                            st.rerun()
                        if _usave:
                            with conn:
                                try:
                                    log_stock_change(
                                        conn, cat_name, "Unidentified",
                                        loc, "Update",
                                        _unid_bags, float(_new_ubags),
                                        _unid_kg,   float(_new_ukg),
                                        source="Manual update")
                                except Exception:
                                    pass
                                conn.execute("""
                                    UPDATE unidentified_stock
                                       SET bags = %s, quantity_kg = %s
                                     WHERE category_id = %s AND location = %s
                                """, (round(float(_new_ubags), 2),
                                      round(float(_new_ukg), 2),
                                      cat_id, loc))
                            st.session_state[_unid_edit_key] = False
                            st.rerun()

                # Goods table — hide zero-stock rows for display only
                df_display = df_loc[
                    (df_loc["bags"] != 0) | (df_loc["quantity_kg"] != 0)
                ].copy()

                if df_display.empty:
                    st.markdown(
                        '<div style="padding:1rem;color:#3a3628;'
                        'font-size:.85rem;text-align:center">'
                        '📦 No stock at this location.</div>',
                        unsafe_allow_html=True)
                else:
                    rows_html = ""
                    for _, row in df_display.iterrows():
                        is_neg     = int(row["bags"]) < 0 or float(row["quantity_kg"]) < 0
                        row_style  = 'background:#2a0808;' if is_neg else ''
                        name_col   = '#ff8080'  if is_neg else '#c8bfa8'
                        bags_col   = '#ff6060'  if is_neg else acc["color"]
                        kg_col     = '#ff6060'  if is_neg else '#8a8070'
                        alert_tag  = (
                            ' <span style="font-size:0.6rem;background:#7a1a1a;color:#ffaaaa;'
                            'border-radius:3px;padding:1px 5px;vertical-align:middle">NEG</span>'
                        ) if is_neg else ''
                        rows_html += (
                            f'<tr style="{row_style}">'
                            f'<td style="padding:5px 8px;color:{name_col};font-size:0.8rem">'
                            f'{h(row["good_name"])}{alert_tag}</td>'
                            f'<td style="padding:5px 8px;text-align:right;color:{bags_col};'
                            f'font-size:0.8rem;font-weight:600">{int(row["bags"]):,}</td>'
                            f'<td style="padding:5px 8px;text-align:right;color:{kg_col};'
                            f'font-size:0.8rem">{float(row["quantity_kg"]):,.1f}</td>'
                            f'</tr>'
                        )
                    st.markdown(
                        f'<table style="width:100%;border-collapse:collapse;margin-bottom:0.8rem">'
                        f'<thead><tr style="border-bottom:1px solid #252318">'
                        f'<th style="text-align:left;padding:4px 8px;font-size:0.63rem;'
                        f'color:#3a3628;text-transform:uppercase;letter-spacing:0.1em">Good</th>'
                        f'<th style="text-align:right;padding:4px 8px;font-size:0.63rem;'
                        f'color:#3a3628;text-transform:uppercase;letter-spacing:0.1em">Bags</th>'
                        f'<th style="text-align:right;padding:4px 8px;font-size:0.63rem;'
                        f'color:#3a3628;text-transform:uppercase;letter-spacing:0.1em">Kg</th>'
                        f'</tr></thead>'
                        f'<tbody>{rows_html}</tbody>'
                        f'<tfoot><tr style="border-top:1px solid #2a2820">'
                        f'<td style="padding:6px 8px;font-size:0.8rem;font-weight:700;'
                        f'color:#e8c97e">Total</td>'
                        f'<td style="padding:6px 8px;text-align:right;font-size:0.8rem;'
                        f'font-weight:700;color:{acc["color"]}">{total_bags_loc:,}</td>'
                        f'<td style="padding:6px 8px;text-align:right;font-size:0.8rem;'
                        f'font-weight:700;color:#6a9fd4">{total_kg_loc:,.1f}</td>'
                        f'</tr></tfoot></table>',
                        unsafe_allow_html=True)

                # Action buttons — toggle on click, close when same is clicked again
                btn1, btn2 = st.columns(2)
                with btn1:
                    t_active = st.session_state.stock_transfer_loc == loc
                    if st.button(
                        "📦 Transfer ✓" if t_active else "📦 Transfer",
                        key=f"transfer_btn_{loc}", use_container_width=True
                    ):
                        st.session_state.stock_transfer_loc = None if t_active else loc
                        st.session_state.stock_update_loc   = None
                        st.rerun()
                with btn2:
                    u_active = st.session_state.stock_update_loc == loc
                    if st.button(
                        "✏️ Update ✓" if u_active else "✏️ Update",
                        key=f"update_btn_{loc}", use_container_width=True
                    ):
                        st.session_state.stock_update_loc   = None if u_active else loc
                        st.session_state.stock_transfer_loc = None
                        st.rerun()

                # ── Stock history expander ────────────────────────
                with st.expander("📋 Stock History — last 30 days", expanded=False):
                    df_hist = pg_read_sql("""
                        SELECT recorded_at, good_name, change_type,
                               bags_before, bags_after, bags_change,
                               kg_before, kg_after, kg_change, source
                        FROM stock_history
                        WHERE category_name = %s
                          AND location      = %s
                          AND recorded_at  >= to_char(NOW() - INTERVAL '30 days', 'YYYY-MM-DD HH24:MI:SS')
                        ORDER BY recorded_at DESC
                    """, conn, params=(cat_name, loc))
                    if df_hist.empty:
                        st.markdown(
                            '<div class="empty-state">No stock changes in the last 30 days.</div>',
                            unsafe_allow_html=True)
                    else:
                        st.markdown(
                            f'<div style="font-size:0.72rem;color:#5a5448;'
                            f'margin-bottom:0.5rem">'
                            f'{len(df_hist)} changes recorded in the last 30 days'
                            f'</div>',
                            unsafe_allow_html=True)
                        _BADGE_COLORS = {
                            "Transfer Out":  "#ff5555",
                            "Transfer In":   "#8dd87a",
                            "Update":        "#d4864a",
                            "Vendor Bill":   "#6a9fd4",
                            "Customer Sale": "#b88adc",
                        }
                        hist_rows_html = ""
                        for _, hr in df_hist.iterrows():
                            try:
                                _dt_parts = str(hr["recorded_at"]).split(" ")
                                _d, _t = _dt_parts[0], _dt_parts[1][:5]
                                _y, _m, _day = _d.split("-")
                                _fmt_dt = f"{_day}/{_m}/{_y} {_t}"
                            except Exception:
                                _fmt_dt = str(hr["recorded_at"])
                            _ct    = str(hr["change_type"])
                            _bc    = _BADGE_COLORS.get(_ct, "#8a8070")
                            _badge = (
                                f'<span style="background:{_bc}26;'
                                f'border:1px solid {_bc}66;'
                                f'border-radius:4px;padding:2px 8px;'
                                f'font-size:0.72rem;font-weight:600;'
                                f'text-transform:uppercase;color:{_bc}">'
                                f'{h(_ct)}</span>'
                            )
                            _bc_val = float(hr["bags_change"])
                            if abs(_bc_val) < 0.001:
                                _bags_chg = '<span style="color:#3a3628">—</span>'
                            elif _bc_val > 0:
                                _bags_chg = (
                                    f'<span style="color:#8dd87a;font-weight:600">'
                                    f'+{_bc_val:.2f}</span>'
                                )
                            else:
                                _bags_chg = (
                                    f'<span style="color:#ff5555;font-weight:600">'
                                    f'{_bc_val:.2f}</span>'
                                )
                            _kc_val = float(hr["kg_change"])
                            if abs(_kc_val) < 0.001:
                                _kg_chg = '<span style="color:#3a3628">—</span>'
                            elif _kc_val > 0:
                                _kg_chg = (
                                    f'<span style="color:#8dd87a;font-weight:600">'
                                    f'+{_kc_val:.2f}</span>'
                                )
                            else:
                                _kg_chg = (
                                    f'<span style="color:#ff5555;font-weight:600">'
                                    f'{_kc_val:.2f}</span>'
                                )
                            hist_rows_html += (
                                f'<tr>'
                                f'<td style="padding:4px 6px;font-size:0.72rem;'
                                f'color:#6a6050;white-space:nowrap">{_fmt_dt}</td>'
                                f'<td style="padding:4px 6px;font-size:0.75rem;'
                                f'color:#c8bfa8">{h(str(hr["good_name"]))}</td>'
                                f'<td style="padding:4px 6px">{_badge}</td>'
                                f'<td style="padding:4px 6px;text-align:right">'
                                f'{_bags_chg}</td>'
                                f'<td style="padding:4px 6px;text-align:right">'
                                f'{_kg_chg}</td>'
                                f'<td style="padding:4px 6px;font-size:0.72rem;'
                                f'color:#5a5448">{h(str(hr["source"]))}</td>'
                                f'</tr>'
                            )
                        st.markdown(
                            f'<table style="width:100%;border-collapse:collapse">'
                            f'<thead><tr style="border-bottom:1px solid #252318">'
                            f'<th style="text-align:left;padding:3px 6px;font-size:0.62rem;'
                            f'color:#3a3628;text-transform:uppercase;letter-spacing:0.1em">'
                            f'Date &amp; Time</th>'
                            f'<th style="text-align:left;padding:3px 6px;font-size:0.62rem;'
                            f'color:#3a3628;text-transform:uppercase;letter-spacing:0.1em">'
                            f'Good</th>'
                            f'<th style="text-align:left;padding:3px 6px;font-size:0.62rem;'
                            f'color:#3a3628;text-transform:uppercase;letter-spacing:0.1em">'
                            f'Change Type</th>'
                            f'<th style="text-align:right;padding:3px 6px;font-size:0.62rem;'
                            f'color:#3a3628;text-transform:uppercase;letter-spacing:0.1em">'
                            f'Bags (&plusmn;)</th>'
                            f'<th style="text-align:right;padding:3px 6px;font-size:0.62rem;'
                            f'color:#3a3628;text-transform:uppercase;letter-spacing:0.1em">'
                            f'Kg (&plusmn;)</th>'
                            f'<th style="text-align:left;padding:3px 6px;font-size:0.62rem;'
                            f'color:#3a3628;text-transform:uppercase;letter-spacing:0.1em">'
                            f'Source</th>'
                            f'</tr></thead>'
                            f'<tbody>{hist_rows_html}</tbody>'
                            f'</table>',
                            unsafe_allow_html=True)

        # ── LEVEL 3A: TRANSFER FORM ──────────────────────────────
        if st.session_state.stock_transfer_loc:
            from_loc = st.session_state.stock_transfer_loc
            acc = LOC_ACCENT[from_loc]

            st.markdown("<hr>", unsafe_allow_html=True)
            st.markdown(
                f'<div style="font-family:\'Playfair Display\',serif;font-size:1.1rem;'
                f'color:{acc["color"]};margin-bottom:0.8rem">'
                f'📦 Transfer Stock from {from_loc}</div>',
                unsafe_allow_html=True)

            other_locs = [l for l in LOCATIONS if l != from_loc]

            with st.form(f"transfer_form_{from_loc}", clear_on_submit=True):
                tf1, tf2, tf3 = st.columns(3)
                with tf1:
                    sel_good = st.selectbox(
                        "Good", good_names,
                        key=f"tf_good_{from_loc}")
                with tf2:
                    to_loc = st.selectbox(
                        "To Location", other_locs,
                        key=f"tf_to_{from_loc}")
                with tf3:
                    transfer_date = st.date_input(
                        "Transfer Date", value=date.today(),
                        key=f"tf_date_{from_loc}")

                tf4, tf5, tf6 = st.columns(3)
                with tf4:
                    bags_mv = st.number_input(
                        "Bags to Transfer", min_value=0, step=1,
                        key=f"tf_bags_{from_loc}")
                with tf5:
                    kg_mv = st.number_input(
                        "Kg to Transfer", min_value=0.0, step=0.1,
                        format="%.2f", key=f"tf_kg_{from_loc}")
                with tf6:
                    tf_note = st.text_input(
                        "Note (optional)", key=f"tf_note_{from_loc}")

                sub_col, can_col = st.columns(2)
                with sub_col:
                    submitted = st.form_submit_button(
                        "✔ Confirm Transfer", use_container_width=True)
                with can_col:
                    cancelled = st.form_submit_button(
                        "✕ Cancel", use_container_width=True)

                if cancelled:
                    st.session_state.stock_transfer_loc = None
                    st.rerun()

                if submitted:
                    if bags_mv == 0 and kg_mv == 0.0:
                        st.error("Enter at least bags or Kg to transfer.")
                    else:
                        gid = good_ids[good_names.index(sel_good)]
                        _src_row = conn.execute("""
                            SELECT sl.bags, sl.quantity_kg,
                                   sg.good_name, sc.category_name
                            FROM stock_levels sl
                            JOIN stock_goods sg ON sl.good_id = sg.good_id
                            JOIN stock_categories sc ON sg.category_id = sc.category_id
                            WHERE sl.good_id = %s AND sl.location = %s
                        """, (gid, from_loc)).fetchone()
                        _dst_row = conn.execute(
                            "SELECT bags, quantity_kg FROM stock_levels "
                            "WHERE good_id=%s AND location=%s",
                            (gid, to_loc)).fetchone()
                        cur_bags = int(_src_row["bags"] or 0) if _src_row else 0
                        cur_kg   = float(_src_row["quantity_kg"] or 0) if _src_row else 0.0
                        _src_bags_before = cur_bags
                        _src_kg_before   = cur_kg
                        _dst_bags_before = float(_dst_row["bags"] or 0) if _dst_row else 0.0
                        _dst_kg_before   = float(_dst_row["quantity_kg"] or 0) if _dst_row else 0.0
                        _cat_name        = _src_row["category_name"] if _src_row else ""
                        _good_name       = _src_row["good_name"] if _src_row else ""

                        errs = []
                        if bags_mv > cur_bags:
                            errs.append(
                                f"Not enough bags at {from_loc} "
                                f"(have {cur_bags:,}, want {bags_mv:,}).")
                        if kg_mv > cur_kg:
                            errs.append(
                                f"Not enough Kg at {from_loc} "
                                f"(have {cur_kg:,.1f}, want {kg_mv:,.1f}).")

                        if errs:
                            for e in errs:
                                st.error(e)
                        else:
                            with conn:
                                conn.execute(
                                    "UPDATE stock_levels "
                                    "SET bags=bags-%s, quantity_kg=quantity_kg-%s "
                                    "WHERE good_id=%s AND location=%s",
                                    (bags_mv, kg_mv, gid, from_loc))
                                conn.execute(
                                    "UPDATE stock_levels "
                                    "SET bags=bags+%s, quantity_kg=quantity_kg+%s "
                                    "WHERE good_id=%s AND location=%s",
                                    (bags_mv, kg_mv, gid, to_loc))
                                conn.execute(
                                    "INSERT INTO stock_transfers "
                                    "(transfer_date,good_id,from_location,to_location,"
                                    "bags_moved,kg_moved,note) VALUES (%s,%s,%s,%s,%s,%s,%s)",
                                    (str(transfer_date), gid, from_loc, to_loc,
                                     bags_mv, kg_mv, tf_note.strip()))
                                try:
                                    log_stock_change(
                                        conn, _cat_name, _good_name, from_loc,
                                        "Transfer Out",
                                        _src_bags_before, _src_bags_before - bags_mv,
                                        _src_kg_before,   _src_kg_before   - kg_mv,
                                        source=f"Transferred to {to_loc}"
                                    )
                                    log_stock_change(
                                        conn, _cat_name, _good_name, to_loc,
                                        "Transfer In",
                                        _dst_bags_before, _dst_bags_before + bags_mv,
                                        _dst_kg_before,   _dst_kg_before   + kg_mv,
                                        source=f"Transferred from {from_loc}"
                                    )
                                except Exception as _e:
                                    import logging
                                    logging.getLogger(__name__).warning(
                                        "stock_history log failed: %s", _e)
                            st.session_state.stock_transfer_loc = None
                            st.success(
                                f"✓ Transferred {bags_mv:,} bags & {kg_mv:,.1f} Kg of "
                                f"**{sel_good}** from {from_loc} → {to_loc}.")
                            st.rerun()

        # ── LEVEL 3B: UPDATE FORM ────────────────────────────────
        if st.session_state.stock_update_loc:
            upd_loc = st.session_state.stock_update_loc
            acc = LOC_ACCENT[upd_loc]

            st.markdown("<hr>", unsafe_allow_html=True)
            st.markdown(
                f'<div style="font-family:\'Playfair Display\',serif;font-size:1.1rem;'
                f'color:{acc["color"]};margin-bottom:0.8rem">'
                f'✏️ Update Stock at {upd_loc}</div>',
                unsafe_allow_html=True)

            df_upd = df_levels[df_levels["location"] == upd_loc].copy()

            # Advisory banner when negative stock exists at this location
            neg_upd = df_upd[(df_upd["bags"] < 0) | (df_upd["quantity_kg"] < 0)]
            if not neg_upd.empty:
                st.markdown(
                    f'<div style="background:#2a0808;border:1px solid #8a1a1a;'
                    f'border-radius:8px;padding:0.7rem 1rem;margin-bottom:0.8rem">'
                    f'<div style="color:#ff8080;font-size:0.83rem;font-weight:600">'
                    f'⚠️ {len(neg_upd)} item(s) at {upd_loc} have negative stock. '
                    f'Edit the values below to 0 or any valid number and save.</div>'
                    f'</div>',
                    unsafe_allow_html=True)

            try:
                with st.form(f"update_form_{upd_loc}"):
                    # Column headers
                    h1, h2, h3 = st.columns([4, 2, 2])
                    with h1:
                        st.markdown(
                            '<span style="font-size:0.7rem;color:#5a5448;'
                            'text-transform:uppercase;letter-spacing:0.1em">Good</span>',
                            unsafe_allow_html=True)
                    with h2:
                        st.markdown(
                            '<span style="font-size:0.7rem;color:#5a5448;'
                            'text-transform:uppercase;letter-spacing:0.1em">Bags</span>',
                            unsafe_allow_html=True)
                    with h3:
                        st.markdown(
                            '<span style="font-size:0.7rem;color:#5a5448;'
                            'text-transform:uppercase;letter-spacing:0.1em">Kg</span>',
                            unsafe_allow_html=True)

                    upd_vals = {}
                    for _, row in df_upd.iterrows():
                        gid = int(row["good_id"])
                        is_neg_row = int(row["bags"]) < 0 or float(row["quantity_kg"]) < 0
                        name_color = "#ff8080" if is_neg_row else "#c8bfa8"
                        rc1, rc2, rc3 = st.columns([4, 2, 2])
                        with rc1:
                            st.markdown(
                                f'<div style="padding:0.45rem 0;font-size:0.88rem;'
                                f'color:{name_color}">{h(row["good_name"])}</div>',
                                unsafe_allow_html=True)
                        with rc2:
                            # No min_value so pre-filled negative values render without error
                            new_bags = st.number_input(
                                "", value=int(row["bags"]), step=1,
                                key=f"upd_bags_{upd_loc}_{gid}",
                                label_visibility="collapsed")
                        with rc3:
                            new_kg = st.number_input(
                                "", value=float(row["quantity_kg"]),
                                step=0.1, format="%.2f",
                                key=f"upd_kg_{upd_loc}_{gid}",
                                label_visibility="collapsed")
                        upd_vals[gid] = (new_bags, new_kg)

                    st.markdown("<br>", unsafe_allow_html=True)
                    sv1, sv2 = st.columns(2)
                    with sv1:
                        save_clicked = st.form_submit_button(
                            "💾 Save All Changes", use_container_width=True)
                    with sv2:
                        cancel_upd = st.form_submit_button(
                            "✕ Cancel", use_container_width=True)

                    if cancel_upd:
                        st.session_state.stock_update_loc = None
                        st.rerun()

                    if save_clicked:
                        with conn:
                            for gid, (nb, nk) in upd_vals.items():
                                _before_row = conn.execute("""
                                    SELECT sl.bags, sl.quantity_kg,
                                           sg.good_name, sc.category_name
                                    FROM stock_levels sl
                                    JOIN stock_goods sg ON sl.good_id = sg.good_id
                                    JOIN stock_categories sc ON sg.category_id = sc.category_id
                                    WHERE sl.good_id = %s AND sl.location = %s
                                """, (gid, upd_loc)).fetchone()
                                _b_bags = float(_before_row["bags"] or 0) if _before_row else 0
                                _b_kg   = float(_before_row["quantity_kg"] or 0) if _before_row else 0.0
                                _a_bags = float(nb)
                                _a_kg   = float(nk)
                                conn.execute(
                                    "UPDATE stock_levels SET bags=%s, quantity_kg=%s "
                                    "WHERE good_id=%s AND location=%s",
                                    (nb, nk, gid, upd_loc))
                                if abs(_b_bags - _a_bags) > 0.001 or abs(_b_kg - _a_kg) > 0.001:
                                    try:
                                        log_stock_change(
                                            conn,
                                            _before_row["category_name"] if _before_row else "",
                                            _before_row["good_name"] if _before_row else "",
                                            upd_loc, "Update",
                                            _b_bags, _a_bags, _b_kg, _a_kg,
                                            source="Manual update"
                                        )
                                    except Exception as _e:
                                        import logging
                                        logging.getLogger(__name__).warning(
                                            "stock_history log failed: %s", _e)
                        st.session_state.stock_update_loc = None
                        st.success(f"✓ Stock at {upd_loc} updated successfully.")
                        st.rerun()

            except Exception as _upd_err:
                st.error(f"Could not render Update Stock form: {_upd_err}")
                st.info(
                    "Tip: there may be invalid (negative) stock values in the database. "
                    "Use the 'Reset negative to 0' button below.")

            # Escape-hatch: one-click reset of negative values at this location
            if not neg_upd.empty:
                if st.button(
                    f"🔧 Reset negative values to 0 at {upd_loc}",
                    key=f"reset_neg_{upd_loc}"
                ):
                    conn.execute(
                        "UPDATE stock_levels "
                        "SET bags        = CASE WHEN bags        < 0 THEN 0 ELSE bags        END, "
                        "    quantity_kg = CASE WHEN quantity_kg < 0 THEN 0 ELSE quantity_kg END "
                        "WHERE location = %s",
                        (upd_loc,))
                    conn.commit()
                    st.success(f"✓ All negative values at {upd_loc} reset to 0.")
                    st.rerun()

            # ── Add New Good sub-form ─────────────────────────────
            st.markdown(
                f'<div style="font-size:0.82rem;color:#5a5448;font-weight:600;'
                f'margin:1rem 0 0.5rem 0;text-transform:uppercase;letter-spacing:0.08em">'
                f'＋ Add New Good to {h(cat_name)}</div>',
                unsafe_allow_html=True)

            with st.form(f"add_good_form_{upd_loc}"):
                ag1, ag2, ag3, ag4 = st.columns([3, 1.5, 1.5, 1.5])
                with ag1:
                    new_good_name = st.text_input(
                        "Good Name", key=f"ag_name_{upd_loc}")
                with ag2:
                    ag_bags_t = st.number_input(
                        "Transport Bags", min_value=0, step=1,
                        key=f"ag_bt_{upd_loc}")
                with ag3:
                    ag_bags_s = st.number_input(
                        "Shop Bags", min_value=0, step=1,
                        key=f"ag_bs_{upd_loc}")
                with ag4:
                    ag_bags_a = st.number_input(
                        "Anandpuri Bags", min_value=0, step=1,
                        key=f"ag_ba_{upd_loc}")

                ag5, ag6, ag7, _ = st.columns([1.5, 1.5, 1.5, 3])
                with ag5:
                    ag_kg_t = st.number_input(
                        "Transport Kg", min_value=0.0, step=0.1,
                        format="%.2f", key=f"ag_kt_{upd_loc}")
                with ag6:
                    ag_kg_s = st.number_input(
                        "Shop Kg", min_value=0.0, step=0.1,
                        format="%.2f", key=f"ag_ks_{upd_loc}")
                with ag7:
                    ag_kg_a = st.number_input(
                        "Anandpuri Kg", min_value=0.0, step=0.1,
                        format="%.2f", key=f"ag_ka_{upd_loc}")

                if st.form_submit_button("＋ Add Good", use_container_width=False):
                    gname = new_good_name.strip()
                    if not gname:
                        st.error("Good name cannot be empty.")
                    else:
                        try:
                            cur = conn.execute(
                                "INSERT INTO stock_goods (category_id, good_name) VALUES (%s,%s) "
                                "RETURNING good_id",
                                (cat_id, gname))
                            new_gid = cur.fetchone()["good_id"]
                            loc_init = {
                                "Transport": (ag_bags_t, ag_kg_t),
                                "Shop":      (ag_bags_s, ag_kg_s),
                                "Anandpuri": (ag_bags_a, ag_kg_a),
                            }
                            for loc_n, (b, k) in loc_init.items():
                                conn.execute(
                                    "INSERT INTO stock_levels "
                                    "(good_id, location, bags, quantity_kg) VALUES (%s,%s,%s,%s)",
                                    (new_gid, loc_n, b, k))
                            conn.commit()
                            st.success(f"✓ Added '{gname}' to {cat_name}.")
                            st.rerun()
                        except psycopg2.errors.UniqueViolation:
                            st.error(f"'{gname}' already exists in {cat_name}.")

    finally:
        conn.close()
