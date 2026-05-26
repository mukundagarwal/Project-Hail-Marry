"""
Stock Register — live inventory across Transport, Shop, and Anandpuri.
Navigation: st.session_state.stock_page in ("home", "category")
"""

import streamlit as st
import psycopg2
import pandas as pd
from datetime import date

from utils.db import (
    get_conn, pg_read_sql, _ensure_schema_once, log_stock_change, purge_old_stock_history,
    invalidate_lookup_cache, ensure_good_at_all_locations, ensure_category_at_all_locations,
    STOCK_LOCATIONS,
)
from utils.styles import APP_CSS, BRAND_BAR_HTML, get_light_mode_css
from utils.formatters import h, fmt_inr
from utils.auth import require_login, render_logout_button

LOCATIONS = list(STOCK_LOCATIONS)

LOC_ACCENT = {
    "Transport": {"color": "#d4864a", "bg": "#1e1208", "border": "#4a2800"},
    "Shop":      {"color": "#8dd87a", "bg": "#0d1209", "border": "#1e3018"},
    "Anandpuri": {"color": "#6a9fd4", "bg": "#0d1118", "border": "#1e2840"},
    "Cold":      {"color": "#7dd4e8", "bg": "#080e14", "border": "#1a3040"},
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
_ensure_schema_once()


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
        _meta = conn.execute("""
            SELECT
                (SELECT COUNT(*) FROM stock_categories) AS n_cats,
                (SELECT COUNT(*) FROM stock_goods)      AS n_goods,
                COALESCE((SELECT SUM(bags)        FROM stock_levels),      0) +
                COALESCE((SELECT SUM(bags)        FROM unidentified_stock),0) AS tot_bags,
                COALESCE((SELECT SUM(quantity_kg) FROM stock_levels),      0) +
                COALESCE((SELECT SUM(quantity_kg) FROM unidentified_stock),0) AS tot_kg
        """).fetchone()
        n_cats   = int(_meta["n_cats"])
        n_goods  = int(_meta["n_goods"])
        tot_bags = float(_meta["tot_bags"])
        tot_kg   = float(_meta["tot_kg"])

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
                with st.form("add_category_form"):
                    new_cat = st.text_input("Category Name", key="new_cat_name")
                    if st.form_submit_button("Save Category", use_container_width=True):
                        name = new_cat.strip()
                        if not name:
                            st.error("Name cannot be empty.")
                        else:
                            try:
                                c2 = get_conn()
                                with c2:
                                    _c2_row = c2.execute(
                                        "INSERT INTO stock_categories (category_name) VALUES (%s) "
                                        "RETURNING category_id", (name,))
                                    _c2_id = _c2_row.fetchone()["category_id"]
                                    ensure_category_at_all_locations(c2, _c2_id)
                                c2.close()
                                invalidate_lookup_cache()
                                st.toast(f"Category '{name}' added.", icon="✅")
                                st.rerun()
                            except psycopg2.errors.UniqueViolation:
                                st.error(f"'{name}' already exists.")

        # Category cards — fetch all data in 4 queries instead of 5×N
        cats = conn.execute(
            "SELECT category_id, category_name FROM stock_categories "
            "ORDER BY category_name").fetchall()

        # Batch 1: stock_levels totals + good count per category
        _sl_map = {}
        for r in conn.execute("""
            SELECT sg.category_id,
                   COALESCE(SUM(sl.bags), 0)        AS total_bags,
                   COALESCE(SUM(sl.quantity_kg), 0) AS total_kg,
                   COUNT(DISTINCT sg.good_id)       AS n_goods_cat
            FROM stock_goods sg
            LEFT JOIN stock_levels sl ON sl.good_id = sg.good_id
            GROUP BY sg.category_id
        """).fetchall():
            _sl_map[r["category_id"]] = (float(r["total_bags"]), float(r["total_kg"]), int(r["n_goods_cat"]))

        # Batch 2: all unidentified_stock rows — build both category-total and per-location dicts
        _unid_cat_map = {}   # {cat_id: [tot_bags, tot_kg]}
        _unid_loc_map = {}   # {cat_id: {loc: [bags, kg]}}
        for _ur in conn.execute(
            "SELECT category_id, location, bags, quantity_kg FROM unidentified_stock"
        ).fetchall():
            _cid = _ur["category_id"]
            _ul  = _ur["location"]
            _ub  = float(_ur["bags"] or 0)
            _uk  = float(_ur["quantity_kg"] or 0)
            _unid_cat_map.setdefault(_cid, [0.0, 0.0])
            _unid_cat_map[_cid][0] += _ub
            _unid_cat_map[_cid][1] += _uk
            _unid_loc_map.setdefault(_cid, {}).setdefault(_ul, [0.0, 0.0])
            _unid_loc_map[_cid][_ul][0] += _ub
            _unid_loc_map[_cid][_ul][1] += _uk

        # Batch 3: per-location stock_levels totals for all categories
        _loc_map_all = {}   # {cat_id: {loc: (bags, kg)}}
        for r in conn.execute("""
            SELECT sg.category_id, sl.location,
                   COALESCE(SUM(sl.bags), 0)        AS loc_bags,
                   COALESCE(SUM(sl.quantity_kg), 0) AS loc_kg
            FROM stock_goods sg
            LEFT JOIN stock_levels sl ON sl.good_id = sg.good_id
            WHERE sl.location IS NOT NULL
            GROUP BY sg.category_id, sl.location
        """).fetchall():
            _loc_map_all.setdefault(r["category_id"], {})[r["location"]] = (
                float(r["loc_bags"]), float(r["loc_kg"]))

        # Merge unidentified per-location into loc_map_all
        for _cid, _lm in _unid_loc_map.items():
            _loc_map_all.setdefault(_cid, {})
            for _ul, (_ub, _uk) in {k: v for k, v in _lm.items()}.items():
                if _ul in _loc_map_all[_cid]:
                    _loc_map_all[_cid][_ul] = (
                        _loc_map_all[_cid][_ul][0] + _ub,
                        _loc_map_all[_cid][_ul][1] + _uk)
                else:
                    _loc_map_all[_cid][_ul] = (_ub, _uk)

        # Render loop — zero DB calls per category
        for cat_row in cats:
            cat_id   = cat_row["category_id"]
            cat_name = cat_row["category_name"]

            _sl            = _sl_map.get(cat_id, (0.0, 0.0, 0))
            _unid          = _unid_cat_map.get(cat_id, [0.0, 0.0])
            total_bags_cat = _sl[0] + _unid[0]
            total_kg_cat   = _sl[1] + _unid[1]
            n_goods_cat    = _sl[2]
            loc_map        = _loc_map_all.get(cat_id, {})

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
                   sl.batch_label, sl.batch_notes,
                   sl.bags, sl.quantity_kg
            FROM stock_goods sg
            JOIN stock_levels sl ON sl.good_id = sg.good_id
            WHERE sg.category_id = %s
            ORDER BY sg.good_name, sl.location, sl.batch_label""",
            conn, params=(cat_id,))

        # ── Negative stock alert for this category ───────────────
        neg_cat = df_levels[(df_levels["bags"] < 0) | (df_levels["quantity_kg"] < 0)]
        if not neg_cat.empty:
            neg_items = "".join(
                f'<span style="display:inline-block;background:#3a0a0a;border:1px solid #7a1a1a;'
                f'border-radius:5px;padding:2px 10px;margin:2px 4px 2px 0;'
                f'font-size:0.8rem;color:#ff8080">'
                f'{h(r["good_name"])} @ {h(r["location"])} '
                f'{("[" + h(str(r["batch_label"])) + "] ") if r["batch_label"] else ""}'
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
        lc1, lc2, lc3, lc4 = st.columns(4, gap="medium")

        for col_widget, loc in zip([lc1, lc2, lc3, lc4], LOCATIONS):
            acc    = LOC_ACCENT[loc]
            df_loc = df_levels[df_levels["location"] == loc]
            total_bags_loc = int(df_loc["bags"].sum())
            total_kg_loc   = float(df_loc["quantity_kg"].sum())

            # Unidentified stock for this location
            _unid_row = conn.execute("""
                SELECT bags, quantity_kg, notes
                FROM unidentified_stock
                WHERE category_id = %s AND location = %s
            """, (cat_id, loc)).fetchone()
            _unid_bags  = float(_unid_row["bags"] or 0) if _unid_row else 0.0
            _unid_kg    = float(_unid_row["quantity_kg"] or 0) if _unid_row else 0.0
            _unid_notes = str(_unid_row["notes"] or '') if _unid_row else ''

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
                    _unid_notes_html = (
                        f'<div style="font-size:.72rem;color:#8a7040;'
                        f'font-style:italic;margin-top:4px">{h(_unid_notes)}</div>'
                        if _unid_notes else ''
                    )
                    st.markdown(
                        f'<div style="background:#1a1208;border:1px solid #4a3010;'
                        f'border-radius:10px;padding:0.7rem 1rem;margin-bottom:0.8rem">'
                        f'<div style="font-size:.7rem;color:#b89040;text-transform:uppercase;'
                        f'letter-spacing:.1em;margin-bottom:4px">⚠ Unidentified Stock</div>'
                        f'<div style="font-size:.88rem;color:#e0d0a0">'
                        f'<b>{_unid_bags:,.0f}</b> bags &nbsp;·&nbsp; '
                        f'<b>{_unid_kg:,.2f} kg</b></div>'
                        f'<div style="font-size:.72rem;color:#6a5a30;margin-top:3px">'
                        f'Category known · Specific good not yet identified</div>'
                        f'{_unid_notes_html}</div>',
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
                        _new_unotes = st.text_input(
                            "Notes (optional)", value=_unid_notes,
                            placeholder="e.g. Arrived from Kerala, mixed grades",
                            key=f"unid_nn_{cat_id}_{loc}")
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
                                except Exception as _slh_e:
                                    import logging
                                    logging.getLogger(__name__).warning(
                                        "stock_history log failed for unidentified stock update: %s",
                                        _slh_e)
                                conn.execute("""
                                    UPDATE unidentified_stock
                                       SET bags = %s, quantity_kg = %s, notes = %s
                                     WHERE category_id = %s AND location = %s
                                """, (round(float(_new_ubags), 2),
                                      round(float(_new_ukg), 2),
                                      _new_unotes.strip() or None,
                                      cat_id, loc))
                            st.session_state[_unid_edit_key] = False
                            st.rerun()

                # Goods table — group by good, show batch sub-rows
                _any_stock = False
                rows_html  = ""
                for _gname in sorted(df_loc['good_name'].unique()):
                    _grp    = df_loc[df_loc['good_name'] == _gname]
                    _named  = _grp[_grp['batch_label'] != '']
                    _agg_b  = _grp['bags'].sum()
                    _agg_k  = _grp['quantity_kg'].sum()
                    if _agg_b == 0 and _agg_k == 0:
                        continue
                    _any_stock = True

                    if _named.empty:
                        # No batches — single row (existing style)
                        _dr      = _grp.iloc[0]
                        _is_neg  = int(_dr["bags"]) < 0 or float(_dr["quantity_kg"]) < 0
                        _row_bg  = 'background:#2a0808;' if _is_neg else ''
                        _nc      = '#ff8080' if _is_neg else '#c8bfa8'
                        _bc      = '#ff6060' if _is_neg else acc["color"]
                        _kc      = '#ff6060' if _is_neg else '#8a8070'
                        _alert   = (' <span style="font-size:0.6rem;background:#7a1a1a;color:#ffaaaa;'
                                    'border-radius:3px;padding:1px 5px;vertical-align:middle">NEG</span>'
                                    ) if _is_neg else ''
                        rows_html += (
                            f'<tr style="{_row_bg}">'
                            f'<td style="padding:5px 8px;color:{_nc};font-size:0.8rem">'
                            f'{h(_gname)}{_alert}</td>'
                            f'<td style="padding:5px 8px;text-align:right;color:{_bc};'
                            f'font-size:0.8rem;font-weight:600">{int(_dr["bags"]):,}</td>'
                            f'<td style="padding:5px 8px;text-align:right;color:{_kc};'
                            f'font-size:0.8rem">{float(_dr["quantity_kg"]):,.1f}</td>'
                            f'</tr>'
                        )
                    else:
                        # Aggregate row
                        _is_neg_agg = _agg_b < 0 or _agg_k < 0
                        _agg_nc = '#ff8080' if _is_neg_agg else '#e8c97e'
                        _agg_bc = '#ff6060' if _is_neg_agg else acc["color"]
                        _nb     = len(_named)
                        rows_html += (
                            f'<tr style="background:#1a1812;">'
                            f'<td style="padding:5px 8px;color:{_agg_nc};'
                            f'font-size:0.8rem;font-weight:700">'
                            f'{h(_gname)} '
                            f'<span style="font-size:0.62rem;background:#2a2418;color:#6a5a38;'
                            f'border-radius:3px;padding:1px 6px">'
                            f'{_nb} batch{"es" if _nb != 1 else ""}</span></td>'
                            f'<td style="padding:5px 8px;text-align:right;color:{_agg_bc};'
                            f'font-size:0.8rem;font-weight:700">{int(_agg_b):,}</td>'
                            f'<td style="padding:5px 8px;text-align:right;color:#6a9fd4;'
                            f'font-size:0.8rem;font-weight:700">{float(_agg_k):,.1f}</td>'
                            f'</tr>'
                        )
                        # Sub-rows (default '' row + named batches, skip zeros)
                        for _, _br in _grp.iterrows():
                            _bl     = str(_br['batch_label'])
                            if int(_br["bags"]) == 0 and float(_br["quantity_kg"]) == 0:
                                continue
                            _bn     = str(_br['batch_notes'] or '')
                            _dlbl   = _bl if _bl else 'Default'
                            _is_neg = int(_br["bags"]) < 0 or float(_br["quantity_kg"]) < 0
                            _nc     = '#ff8080' if _is_neg else '#9a9080'
                            _bc     = '#ff6060' if _is_neg else acc["color"]
                            _kc     = '#ff6060' if _is_neg else '#6a6050'
                            _alert  = (' <span style="font-size:0.6rem;background:#7a1a1a;'
                                       'color:#ffaaaa;border-radius:3px;padding:1px 4px">NEG</span>'
                                       ) if _is_neg else ''
                            _notes  = (f'<div style="font-size:0.68rem;color:#4a4438;'
                                       f'font-style:italic;padding-left:0.8rem">{h(_bn)}</div>'
                                       ) if _bn else ''
                            rows_html += (
                                f'<tr>'
                                f'<td style="padding:3px 8px 3px 20px;color:{_nc};font-size:0.76rem">'
                                f'↳ {h(_dlbl)}{_alert}{_notes}</td>'
                                f'<td style="padding:3px 8px;text-align:right;color:{_bc};'
                                f'font-size:0.76rem">{int(_br["bags"]):,}</td>'
                                f'<td style="padding:3px 8px;text-align:right;color:{_kc};'
                                f'font-size:0.76rem">{float(_br["quantity_kg"]):,.1f}</td>'
                                f'</tr>'
                            )

                if not _any_stock:
                    st.markdown(
                        '<div style="padding:1rem;color:#3a3628;'
                        'font-size:.85rem;text-align:center">'
                        '📦 No stock at this location.</div>',
                        unsafe_allow_html=True)
                else:
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
                with st.expander("📋 Stock History", expanded=False):
                    from datetime import datetime as _dt, timedelta as _td
                    _hk = f"{cat_name}_{loc}"
                    _hcols = st.columns([2, 2])
                    with _hcols[0]:
                        _hist_days = st.selectbox(
                            "Period", options=[7, 30, 60, 90, 0],
                            format_func=lambda d: f"Last {d} days" if d > 0 else "All time",
                            index=1, key=f"hist_days_{_hk}")
                    with _hcols[1]:
                        _hist_limit = st.selectbox(
                            "Show", options=[50, 100, 200, 500],
                            index=1, key=f"hist_limit_{_hk}")
                    _hist_cutoff = (
                        (_dt.now() - _td(days=_hist_days)).strftime("%Y-%m-%d %H:%M:%S")
                        if _hist_days > 0 else None
                    )
                    if _hist_cutoff:
                        df_hist = pg_read_sql("""
                            SELECT recorded_at, good_name, change_type,
                                   bags_before, bags_after, bags_change,
                                   kg_before, kg_after, kg_change, source
                            FROM stock_history
                            WHERE category_name = %s AND location = %s
                              AND recorded_at >= %s
                            ORDER BY recorded_at DESC LIMIT %s
                        """, conn, params=(cat_name, loc, _hist_cutoff, _hist_limit))
                        _total_cnt = int((conn.execute(
                            "SELECT COUNT(*) AS cnt FROM stock_history "
                            "WHERE category_name=%s AND location=%s AND recorded_at>=%s",
                            (cat_name, loc, _hist_cutoff)).fetchone() or {"cnt": 0})["cnt"])
                    else:
                        df_hist = pg_read_sql("""
                            SELECT recorded_at, good_name, change_type,
                                   bags_before, bags_after, bags_change,
                                   kg_before, kg_after, kg_change, source
                            FROM stock_history
                            WHERE category_name = %s AND location = %s
                            ORDER BY recorded_at DESC LIMIT %s
                        """, conn, params=(cat_name, loc, _hist_limit))
                        _total_cnt = int((conn.execute(
                            "SELECT COUNT(*) AS cnt FROM stock_history "
                            "WHERE category_name=%s AND location=%s",
                            (cat_name, loc)).fetchone() or {"cnt": 0})["cnt"])
                    if df_hist.empty:
                        st.markdown(
                            '<div class="empty-state">No stock changes in this period.</div>',
                            unsafe_allow_html=True)
                    else:
                        st.markdown(
                            f'<div style="font-size:0.72rem;color:#5a5448;'
                            f'margin-bottom:0.5rem">'
                            f'Showing {len(df_hist)} of {_total_cnt} changes'
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

        # ── MANAGE GOODS (delete) ────────────────────────────────
        with st.expander("🗂 Manage Goods — Delete", expanded=False):
            if not goods:
                st.info("No goods in this category yet.")
            else:
                st.markdown(
                    '<div style="font-size:0.78rem;color:#5a5448;margin-bottom:0.8rem">'
                    'Permanently removes a good and all its stock across every location '
                    'and batch. Existing vendor and customer payment records are '
                    'unaffected.</div>',
                    unsafe_allow_html=True)
                _mg_n   = min(len(goods), 3)
                _mg_cols = st.columns(_mg_n)
                for _mg_i, _mg_g in enumerate(goods):
                    _mg_gid   = _mg_g["good_id"]
                    _mg_gname = _mg_g["good_name"]
                    _mg_rows  = df_levels[df_levels['good_id'] == _mg_gid]
                    _mg_bags  = float(_mg_rows['bags'].sum())
                    _mg_kg    = float(_mg_rows['quantity_kg'].sum())
                    _mg_has_stock = _mg_bags != 0 or _mg_kg != 0
                    with _mg_cols[_mg_i % _mg_n]:
                        with st.popover(f"🗑 {_mg_gname}", use_container_width=True):
                            st.markdown(
                                f'<div style="font-size:0.82rem;color:#c8bfa8;'
                                f'margin-bottom:0.6rem">'
                                f'<b>{h(_mg_gname)}</b><br>'
                                f'<span style="color:#5a5448">Total across all '
                                f'locations &amp; batches:</span><br>'
                                f'<b style="color:#d4864a">{_mg_bags:,.0f} bags'
                                f'</b> &nbsp;·&nbsp; '
                                f'<b style="color:#6a9fd4">{_mg_kg:,.1f} Kg</b>'
                                f'</div>',
                                unsafe_allow_html=True)
                            if _mg_has_stock:
                                st.warning(
                                    f"This good still has stock "
                                    f"({_mg_bags:,.0f} bags / {_mg_kg:,.1f} Kg "
                                    f"across all locations). All stock data will "
                                    f"be permanently deleted.")
                            if st.button(
                                    "Confirm Delete",
                                    key=f"del_good_{cat_id}_{_mg_gid}",
                                    use_container_width=True,
                                    type="primary"):
                                with conn:
                                    conn.execute(
                                        "DELETE FROM stock_levels "
                                        "WHERE good_id = %s", (_mg_gid,))
                                    conn.execute(
                                        "DELETE FROM stock_goods "
                                        "WHERE good_id = %s", (_mg_gid,))
                                st.success(
                                    f"✓ '{_mg_gname}' deleted from {cat_name}.")
                                st.rerun()

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
                tf1, tf2, tf3, tf4 = st.columns(4)
                with tf1:
                    sel_good = st.selectbox(
                        "Good", good_names,
                        key=f"tf_good_{from_loc}")
                with tf2:
                    to_loc = st.selectbox(
                        "To Location", other_locs,
                        key=f"tf_to_{from_loc}")
                with tf3:
                    tf_batch = st.text_input(
                        "Batch (blank = unassigned)",
                        placeholder="e.g. Batch #001",
                        key=f"tf_batch_{from_loc}")
                with tf4:
                    transfer_date = st.date_input(
                        "Transfer Date", value=date.today(),
                        key=f"tf_date_{from_loc}")

                tf5, tf6, tf7 = st.columns(3)
                with tf5:
                    bags_mv = st.number_input(
                        "Bags to Transfer", min_value=0, step=1,
                        key=f"tf_bags_{from_loc}")
                with tf6:
                    kg_mv = st.number_input(
                        "Kg to Transfer", min_value=0.0, step=0.1,
                        format="%.2f", key=f"tf_kg_{from_loc}")
                with tf7:
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
                        gid          = good_ids[good_names.index(sel_good)]
                        batch_label  = tf_batch.strip()
                        _src_row = conn.execute("""
                            SELECT sl.bags, sl.quantity_kg,
                                   sg.good_name, sc.category_name
                            FROM stock_levels sl
                            JOIN stock_goods sg ON sl.good_id = sg.good_id
                            JOIN stock_categories sc ON sg.category_id = sc.category_id
                            WHERE sl.good_id = %s AND sl.location = %s
                              AND sl.batch_label = %s
                        """, (gid, from_loc, batch_label)).fetchone()
                        _dst_row = conn.execute(
                            "SELECT bags, quantity_kg FROM stock_levels "
                            "WHERE good_id=%s AND location=%s AND batch_label=%s",
                            (gid, to_loc, batch_label)).fetchone()
                        cur_bags = int(_src_row["bags"] or 0) if _src_row else 0
                        cur_kg   = float(_src_row["quantity_kg"] or 0) if _src_row else 0.0
                        _src_bags_before = cur_bags
                        _src_kg_before   = cur_kg
                        _dst_bags_before = float(_dst_row["bags"] or 0) if _dst_row else 0.0
                        _dst_kg_before   = float(_dst_row["quantity_kg"] or 0) if _dst_row else 0.0
                        _cat_name        = _src_row["category_name"] if _src_row else ""
                        _good_name       = _src_row["good_name"] if _src_row else ""

                        errs = []
                        if from_loc == to_loc:
                            errs.append("Source and destination locations must be different.")
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
                            _log_sfx = f" [Batch: {batch_label}]" if batch_label else ""
                            with conn:
                                conn.execute(
                                    "UPDATE stock_levels "
                                    "SET bags=bags-%s, quantity_kg=quantity_kg-%s "
                                    "WHERE good_id=%s AND location=%s AND batch_label=%s",
                                    (bags_mv, kg_mv, gid, from_loc, batch_label))
                                conn.execute("""
                                    INSERT INTO stock_levels
                                        (good_id, location, batch_label, bags, quantity_kg)
                                    VALUES (%s, %s, %s, %s, %s)
                                    ON CONFLICT (good_id, location, batch_label) DO UPDATE
                                        SET bags        = stock_levels.bags        + excluded.bags,
                                            quantity_kg = stock_levels.quantity_kg + excluded.quantity_kg
                                """, (gid, to_loc, batch_label, bags_mv, kg_mv))
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
                                        source=f"Transferred to {to_loc}{_log_sfx}"
                                    )
                                    log_stock_change(
                                        conn, _cat_name, _good_name, to_loc,
                                        "Transfer In",
                                        _dst_bags_before, _dst_bags_before + bags_mv,
                                        _dst_kg_before,   _dst_kg_before   + kg_mv,
                                        source=f"Transferred from {from_loc}{_log_sfx}"
                                    )
                                except Exception as _e:
                                    import logging
                                    logging.getLogger(__name__).warning(
                                        "stock_history log failed: %s", _e)
                            st.session_state.stock_transfer_loc = None
                            _batch_note = f" (Batch: {batch_label})" if batch_label else ""
                            st.success(
                                f"✓ Transferred {bags_mv:,} bags & {kg_mv:,.1f} Kg of "
                                f"**{sel_good}**{_batch_note} from {from_loc} → {to_loc}.")
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

            _upd_search = st.text_input(
                "🔍 Search goods",
                placeholder="Type to filter...",
                key=f"upd_search_{upd_loc}",
                label_visibility="collapsed")

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
                    for (gid, gname), _grp in df_upd.groupby(
                            ['good_id', 'good_name'], sort=False):
                        if _upd_search and _upd_search.lower() not in str(gname).lower():
                            continue
                        gid    = int(gid)
                        _named = _grp[_grp['batch_label'] != '']

                        if _named.empty:
                            # No batches — single row (existing style)
                            row        = _grp.iloc[0]
                            is_neg_row = int(row["bags"]) < 0 or float(row["quantity_kg"]) < 0
                            name_color = "#ff8080" if is_neg_row else "#c8bfa8"
                            rc1, rc2, rc3 = st.columns([4, 2, 2])
                            with rc1:
                                st.markdown(
                                    f'<div style="padding:0.45rem 0;font-size:0.88rem;'
                                    f'color:{name_color}">{h(gname)}</div>',
                                    unsafe_allow_html=True)
                            with rc2:
                                new_bags = st.number_input(
                                    "", value=int(row["bags"]), step=1,
                                    key=f"upd_bags_{upd_loc}_{gid}_",
                                    label_visibility="collapsed")
                            with rc3:
                                new_kg = st.number_input(
                                    "", value=float(row["quantity_kg"]),
                                    step=0.1, format="%.2f",
                                    key=f"upd_kg_{upd_loc}_{gid}_",
                                    label_visibility="collapsed")
                            upd_vals[(gid, '')] = (new_bags, new_kg)
                        else:
                            # Group header
                            _agg_b     = _grp['bags'].sum()
                            _is_hdr_neg = _agg_b < 0 or _grp['quantity_kg'].sum() < 0
                            _hdr_color  = "#ff8080" if _is_hdr_neg else "#e8c97e"
                            st.markdown(
                                f'<div style="padding:0.35rem 0 0.1rem 0;font-size:0.88rem;'
                                f'font-weight:700;color:{_hdr_color}">{h(gname)}</div>',
                                unsafe_allow_html=True)
                            for _, brow in _grp.iterrows():
                                blabel  = str(brow['batch_label'])
                                bnotes  = str(brow['batch_notes'] or '')
                                dlabel  = blabel if blabel else 'Default'
                                is_neg_row = int(brow["bags"]) < 0 or float(brow["quantity_kg"]) < 0
                                name_color = "#ff8080" if is_neg_row else "#9a9080"
                                rc1, rc2, rc3 = st.columns([4, 2, 2])
                                with rc1:
                                    _lhtml = (
                                        f'<div style="padding:0.3rem 0 0 1rem;'
                                        f'font-size:0.82rem;color:{name_color}">'
                                        f'↳ {h(dlabel)}</div>'
                                    )
                                    if bnotes:
                                        _lhtml += (
                                            f'<div style="padding:0 0 0.3rem 1.6rem;'
                                            f'font-size:0.7rem;color:#4a4438;'
                                            f'font-style:italic">{h(bnotes)}</div>'
                                        )
                                    st.markdown(_lhtml, unsafe_allow_html=True)
                                _sk = (blabel.replace(" ", "_").replace("#", "n")
                                       if blabel else "default")
                                with rc2:
                                    new_bags = st.number_input(
                                        "", value=int(brow["bags"]), step=1,
                                        key=f"upd_bags_{upd_loc}_{gid}_{_sk}",
                                        label_visibility="collapsed")
                                with rc3:
                                    new_kg = st.number_input(
                                        "", value=float(brow["quantity_kg"]),
                                        step=0.1, format="%.2f",
                                        key=f"upd_kg_{upd_loc}_{gid}_{_sk}",
                                        label_visibility="collapsed")
                                upd_vals[(gid, blabel)] = (new_bags, new_kg)

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
                            for (gid, blabel), (nb, nk) in upd_vals.items():
                                _before_row = conn.execute("""
                                    SELECT sl.bags, sl.quantity_kg,
                                           sg.good_name, sc.category_name
                                    FROM stock_levels sl
                                    JOIN stock_goods sg ON sl.good_id = sg.good_id
                                    JOIN stock_categories sc ON sg.category_id = sc.category_id
                                    WHERE sl.good_id = %s AND sl.location = %s
                                      AND sl.batch_label = %s
                                """, (gid, upd_loc, blabel)).fetchone()
                                _b_bags = float(_before_row["bags"] or 0) if _before_row else 0
                                _b_kg   = float(_before_row["quantity_kg"] or 0) if _before_row else 0.0
                                _a_bags = float(nb)
                                _a_kg   = float(nk)
                                conn.execute(
                                    "UPDATE stock_levels SET bags=%s, quantity_kg=%s "
                                    "WHERE good_id=%s AND location=%s AND batch_label=%s",
                                    (nb, nk, gid, upd_loc, blabel))
                                if abs(_b_bags - _a_bags) > 0.001 or abs(_b_kg - _a_kg) > 0.001:
                                    try:
                                        _log_src = (f"Manual update [Batch: {blabel}]"
                                                    if blabel else "Manual update")
                                        log_stock_change(
                                            conn,
                                            _before_row["category_name"] if _before_row else "",
                                            _before_row["good_name"] if _before_row else "",
                                            upd_loc, "Update",
                                            _b_bags, _a_bags, _b_kg, _a_kg,
                                            source=_log_src
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
                    try:
                        conn.execute(
                            "UPDATE stock_levels "
                            "SET bags        = CASE WHEN bags        < 0 THEN 0 ELSE bags        END, "
                            "    quantity_kg = CASE WHEN quantity_kg < 0 THEN 0 ELSE quantity_kg END "
                            "WHERE location = %s",
                            (upd_loc,))
                        conn.commit()
                        st.success(f"✓ All negative values at {upd_loc} reset to 0.")
                        st.rerun()
                    except Exception as e:
                        conn.rollback()
                        st.error(f"Reset failed: {e}")

            # ── Add Batch to existing good ────────────────────────
            if goods:
                st.markdown(
                    f'<div style="font-size:0.82rem;color:#5a5448;font-weight:600;'
                    f'margin:1rem 0 0.5rem 0;text-transform:uppercase;letter-spacing:0.08em">'
                    f'＋ Add Batch to Existing Good at {h(upd_loc)}</div>',
                    unsafe_allow_html=True)
                _ab_search = st.text_input(
                    "🔍 Search goods",
                    placeholder="Type to filter...",
                    key=f"ab_search_{upd_loc}",
                    label_visibility="collapsed")
                _ab_goods = [g for g in goods
                             if not _ab_search
                             or _ab_search.lower() in g["good_name"].lower()]
                _ab_cols = st.columns(min(len(_ab_goods), 3)) if _ab_goods else []
                for _ab_i, _ab_g in enumerate(_ab_goods):
                    _ab_gid   = _ab_g["good_id"]
                    _ab_gname = _ab_g["good_name"]
                    with _ab_cols[_ab_i % len(_ab_cols)]:
                        with st.popover(f"＋ {_ab_gname}", use_container_width=True):
                            with st.form(f"add_batch_{upd_loc}_{_ab_gid}"):
                                ab_label = st.text_input(
                                    "Batch Label *",
                                    placeholder="e.g. Batch #001 or Oct-24 Kerala",
                                    key=f"ab_label_{upd_loc}_{_ab_gid}")
                                ab_notes = st.text_input(
                                    "Notes (optional)",
                                    placeholder="e.g. High moisture, store separately",
                                    key=f"ab_notes_{upd_loc}_{_ab_gid}")
                                ab_bags = st.number_input(
                                    "Bags", min_value=0, step=1,
                                    key=f"ab_bags_{upd_loc}_{_ab_gid}")
                                ab_kg = st.number_input(
                                    "Weight (Kg)", min_value=0.0, step=0.1,
                                    format="%.2f",
                                    key=f"ab_kg_{upd_loc}_{_ab_gid}")
                                if st.form_submit_button(
                                        "＋ Add Batch", use_container_width=True):
                                    _blabel = ab_label.strip()
                                    if not _blabel:
                                        st.error("Batch label is required.")
                                    else:
                                        try:
                                            conn.execute(
                                                "INSERT INTO stock_levels "
                                                "(good_id, location, batch_label, "
                                                " batch_notes, bags, quantity_kg) "
                                                "VALUES (%s,%s,%s,%s,%s,%s)",
                                                (_ab_gid, upd_loc, _blabel,
                                                 ab_notes.strip() or None,
                                                 int(ab_bags), float(ab_kg)))
                                            conn.commit()
                                            st.success(f"✓ Batch '{_blabel}' added.")
                                            st.rerun()
                                        except psycopg2.errors.UniqueViolation:
                                            st.error(
                                                f"'{_blabel}' already exists for "
                                                f"this good at {upd_loc}.")

            # ── Delete named batches ──────────────────────────────
            _named_at_loc = df_upd[df_upd['batch_label'] != '']
            if not _named_at_loc.empty:
                st.markdown(
                    f'<div style="font-size:0.82rem;color:#7a3030;font-weight:600;'
                    f'margin:1rem 0 0.5rem 0;text-transform:uppercase;letter-spacing:0.08em">'
                    f'🗑 Delete Batch at {h(upd_loc)}</div>',
                    unsafe_allow_html=True)
                _del_cols = st.columns(min(len(_named_at_loc), 3))
                for _di, (_, _dbr) in enumerate(_named_at_loc.iterrows()):
                    _dgid    = int(_dbr['good_id'])
                    _dgname  = str(_dbr['good_name'])
                    _dblabel = str(_dbr['batch_label'])
                    _dbags   = int(_dbr['bags'])
                    _dkg     = float(_dbr['quantity_kg'])
                    with _del_cols[_di % min(len(_named_at_loc), 3)]:
                        with st.popover(
                                f"🗑 {_dgname} — {_dblabel}",
                                use_container_width=True):
                            st.markdown(
                                f'<div style="font-size:0.8rem;color:#c8bfa8;margin-bottom:0.5rem">'
                                f'<b>{_dgname}</b><br>'
                                f'Batch: <b>{h(_dblabel)}</b><br>'
                                f'Stock: <b>{_dbags:,} bags &nbsp;·&nbsp; {_dkg:,.1f} Kg</b>'
                                f'</div>',
                                unsafe_allow_html=True)
                            if _dbags != 0 or _dkg != 0:
                                st.warning(
                                    f"This batch has {_dbags:,} bags and "
                                    f"{_dkg:,.1f} Kg. Deleting it will "
                                    f"permanently remove this stock.")
                            if st.button(
                                    "Confirm Delete",
                                    key=f"del_batch_{upd_loc}_{_dgid}_{_dblabel}",
                                    use_container_width=True,
                                    type="primary"):
                                try:
                                    conn.execute(
                                        "DELETE FROM stock_levels "
                                        "WHERE good_id=%s AND location=%s "
                                        "AND batch_label=%s",
                                        (_dgid, upd_loc, _dblabel))
                                    conn.commit()
                                    st.success(f"✓ Batch '{_dblabel}' deleted.")
                                    st.rerun()
                                except Exception as e:
                                    conn.rollback()
                                    st.error(f"Delete failed: {e}")

            # ── Add New Good sub-form ─────────────────────────────
            st.markdown(
                f'<div style="font-size:0.82rem;color:#5a5448;font-weight:600;'
                f'margin:1rem 0 0.5rem 0;text-transform:uppercase;letter-spacing:0.08em">'
                f'＋ Add New Good to {h(cat_name)}</div>',
                unsafe_allow_html=True)

            with st.form(f"add_good_form_{upd_loc}"):
                ag1, ag2, ag3, ag4, ag5 = st.columns([3, 1.5, 1.5, 1.5, 1.5])
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
                with ag5:
                    ag_bags_c = st.number_input(
                        "Cold Bags", min_value=0, step=1,
                        key=f"ag_bc_{upd_loc}")

                ag6, ag7, ag8, ag9, _ = st.columns([1.5, 1.5, 1.5, 1.5, 1.5])
                with ag6:
                    ag_kg_t = st.number_input(
                        "Transport Kg", min_value=0.0, step=0.1,
                        format="%.2f", key=f"ag_kt_{upd_loc}")
                with ag7:
                    ag_kg_s = st.number_input(
                        "Shop Kg", min_value=0.0, step=0.1,
                        format="%.2f", key=f"ag_ks_{upd_loc}")
                with ag8:
                    ag_kg_a = st.number_input(
                        "Anandpuri Kg", min_value=0.0, step=0.1,
                        format="%.2f", key=f"ag_ka_{upd_loc}")
                with ag9:
                    ag_kg_c = st.number_input(
                        "Cold Kg", min_value=0.0, step=0.1,
                        format="%.2f", key=f"ag_kc_{upd_loc}")

                if st.form_submit_button("＋ Add Good", use_container_width=False):
                    gname = new_good_name.strip()
                    if not gname:
                        st.error("Good name cannot be empty.")
                    else:
                        try:
                            with conn:
                                cur = conn.execute(
                                    "INSERT INTO stock_goods (category_id, good_name) VALUES (%s,%s) "
                                    "RETURNING good_id",
                                    (cat_id, gname))
                                new_gid = cur.fetchone()["good_id"]
                                loc_init = {
                                    "Transport": (ag_bags_t, ag_kg_t),
                                    "Shop":      (ag_bags_s, ag_kg_s),
                                    "Anandpuri": (ag_bags_a, ag_kg_a),
                                    "Cold":      (ag_bags_c, ag_kg_c),
                                }
                                for loc_n, (b, k) in loc_init.items():
                                    conn.execute(
                                        "INSERT INTO stock_levels "
                                        "(good_id, location, batch_label, bags, quantity_kg) "
                                        "VALUES (%s,%s,'',%s,%s)",
                                        (new_gid, loc_n, b, k))
                            invalidate_lookup_cache()
                            st.success(f"✓ Added '{gname}' to {cat_name}.")
                            st.rerun()
                        except psycopg2.errors.UniqueViolation:
                            st.error(f"'{gname}' already exists in {cat_name}.")

    finally:
        conn.close()
