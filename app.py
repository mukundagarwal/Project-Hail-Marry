"""
S P Spices – Business Management Diary
Entry point: home dashboard only.
All feature logic lives in pages/ and shared utils live in utils/.
"""

import streamlit as st
from utils.db import ensure_schema
from utils.styles import get_dashboard_css, BRAND_BAR_HTML
from utils.auth import require_login, render_logout_button

st.set_page_config(
    page_title="S P Spices – Business Diary",
    page_icon="🌶️",
    layout="wide",
    initial_sidebar_state="collapsed",
)

require_login()
render_logout_button()
ensure_schema()

if "light_mode" not in st.session_state:
    st.session_state.light_mode = False

st.markdown(get_dashboard_css(st.session_state.light_mode), unsafe_allow_html=True)

# ── Header row: brand bar left, theme toggle right ────────────
hdr_col, toggle_col = st.columns([7, 1.4])
with hdr_col:
    st.markdown(BRAND_BAR_HTML, unsafe_allow_html=True)
with toggle_col:
    st.markdown('<div class="theme-pill">', unsafe_allow_html=True)
    icon = "☀️" if not st.session_state.light_mode else "🌙"
    label = f"{icon} {'Light' if not st.session_state.light_mode else 'Dark'}"
    new_mode = st.toggle(label, value=st.session_state.light_mode, key="theme_toggle")
    st.markdown('</div>', unsafe_allow_html=True)
    if new_mode != st.session_state.light_mode:
        st.session_state.light_mode = new_mode
        st.rerun()

st.markdown('<div class="page-title">Dashboard</div>', unsafe_allow_html=True)
st.markdown('<div class="page-sub">Select a module to continue</div>', unsafe_allow_html=True)

# Row 1: Customer Payments · Vendor Payments · Stock Register
c1, c2, c3 = st.columns(3, gap="medium")

with c1:
    st.markdown('<div class="nav-card"><div class="card-num">01</div>'
                '<div class="card-icon">💰</div><div class="card-label">Customer Payments</div>'
                '<div class="card-desc">Broker ledgers, billing &amp; settlements</div></div>',
                unsafe_allow_html=True)
    if st.button("Open →", key="go_cust", use_container_width=True):
        st.switch_page("pages/1_Customer_Payments.py")

with c2:
    st.markdown('<div class="nav-card"><div class="card-num">02</div>'
                '<div class="card-icon">🏭</div><div class="card-label">Vendor Payments</div>'
                '<div class="card-desc">Track supplier invoices &amp; dues</div></div>',
                unsafe_allow_html=True)
    if st.button("Open →", key="go_vendor", use_container_width=True):
        st.switch_page("pages/2_Vendor_Payments.py")

with c3:
    st.markdown('<div class="nav-card"><div class="card-num">03</div>'
                '<div class="card-icon">📦</div><div class="card-label">Stock Register</div>'
                '<div class="card-desc">Inventory levels &amp; movements</div></div>',
                unsafe_allow_html=True)
    if st.button("Open →", key="go_stock", use_container_width=True):
        st.switch_page("pages/3_Stock_Register.py")

# Row 2: Passbook sits directly below Customer Payments (first column)
r2c1, r2c2, r2c3 = st.columns(3, gap="medium")

with r2c1:
    st.markdown('<div class="nav-card"><div class="card-num">04</div>'
                '<div class="card-icon">📒</div><div class="card-label">Passbook</div>'
                '<div class="card-desc">Bank ledgers for both firms</div></div>',
                unsafe_allow_html=True)
    if st.button("Open →", key="go_passbook", use_container_width=True):
        st.switch_page("pages/4_Passbook.py")
