"""
Authentication gate — call require_login() at the top of every page.
Stores authenticated state in st.session_state for the session duration.
"""

import hashlib
import streamlit as st

_PASSWORD_HASH = hashlib.sha256("Mukund@2806".encode()).hexdigest()

_LOGIN_CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Playfair+Display:wght@600;700&family=DM+Sans:wght@300;400;500;600&display=swap');

section[data-testid="stMain"] > div,
section[data-testid="stMain"] {
    background: #0f0e0c !important;
}
[data-testid="stAppViewContainer"] {
    background: #0f0e0c !important;
}
#MainMenu, footer, header { visibility: hidden; }
[data-testid="stDecoration"] { display: none; }

/* Login card */
.auth-card {
    background: linear-gradient(160deg, #1c1a14 0%, #161410 100%);
    border: 1px solid #2e2b20;
    border-radius: 22px;
    padding: 3rem 2.4rem 2.4rem;
    text-align: center;
    box-shadow: 0 24px 80px rgba(0,0,0,0.55);
    margin-top: 6vh;
}
.auth-orb {
    font-size: 3rem;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    width: 84px; height: 84px;
    border-radius: 50%;
    background: radial-gradient(circle at 35% 35%, #2e2916, #181208);
    border: 1px solid #3e3520;
    box-shadow: 0 0 36px rgba(232,201,126,0.1), 0 6px 24px rgba(0,0,0,0.4);
    margin-bottom: 1.2rem;
}
.auth-brand {
    font-family: 'Playfair Display', serif;
    font-size: 2.1rem; font-weight: 700;
    color: #e8c97e; letter-spacing: 0.02em;
    margin-bottom: 0.2rem;
    line-height: 1.2;
}
.auth-sub {
    font-size: 0.67rem; color: #4a4438;
    letter-spacing: 0.2em; text-transform: uppercase;
    margin-bottom: 1.8rem;
}
.auth-divider {
    height: 1px;
    background: linear-gradient(to right, transparent, #2e2b20, transparent);
    margin-bottom: 1.6rem;
}
.auth-label {
    font-size: 0.72rem; color: #5a5048;
    letter-spacing: 0.14em; text-transform: uppercase;
    margin-bottom: 1.2rem;
}
.auth-footer {
    font-size: 0.62rem; color: #2a2820;
    letter-spacing: 0.14em; text-transform: uppercase;
    margin-top: 1.8rem;
}

/* Input */
[data-testid="stTextInput"] input {
    background: #0f0e0c !important;
    border: 1px solid #2e2b20 !important;
    border-radius: 11px !important;
    color: #f0ebe0 !important;
    padding: 0.75rem 1rem !important;
    font-size: 1rem !important;
    text-align: center !important;
    letter-spacing: 0.1em !important;
    font-family: 'DM Sans', sans-serif !important;
}
[data-testid="stTextInput"] input:focus {
    border-color: #e8c97e !important;
    box-shadow: 0 0 0 3px rgba(232,201,126,0.1) !important;
    outline: none !important;
}

/* Button */
.stButton > button {
    background: linear-gradient(135deg, #b08030 0%, #e8c97e 45%, #b08030 100%) !important;
    color: #0f0e0c !important;
    border: none !important;
    border-radius: 11px !important;
    font-family: 'DM Sans', sans-serif !important;
    font-weight: 700 !important;
    font-size: 0.85rem !important;
    letter-spacing: 0.1em !important;
    text-transform: uppercase !important;
    padding: 0.68rem 1.5rem !important;
    width: 100% !important;
    margin-top: 0.4rem !important;
    box-shadow: 0 4px 16px rgba(232,201,126,0.15) !important;
    transition: opacity 0.18s, transform 0.18s !important;
}
.stButton > button:hover {
    opacity: 0.88 !important;
    transform: translateY(-1px) !important;
    box-shadow: 0 8px 24px rgba(232,201,126,0.22) !important;
}
</style>
"""


def require_login() -> None:
    """
    Call immediately after st.set_page_config() on every page.
    Shows the login screen and calls st.stop() until the user authenticates.
    """
    if st.session_state.get("authenticated"):
        return

    st.markdown(_LOGIN_CSS, unsafe_allow_html=True)

    _, col, _ = st.columns([1, 1.1, 1])

    with col:
        # ── Card top ──────────────────────────────────────────────
        st.markdown("""
        <div class="auth-card">
          <div class="auth-orb">🌶️</div>
          <div class="auth-brand">S P Spices</div>
          <div class="auth-sub">Business Management Diary</div>
          <div class="auth-divider"></div>
          <div class="auth-label">🔒 &nbsp; Enter Password to Continue</div>
        </div>
        """, unsafe_allow_html=True)

        # ── Password input ────────────────────────────────────────
        pwd = st.text_input(
            "Password",
            type="password",
            placeholder="Password",
            label_visibility="collapsed",
            key="_auth_pwd",
        )

        # ── Sign in button ────────────────────────────────────────
        if st.button("Sign In →", use_container_width=True, key="_auth_btn"):
            if hashlib.sha256(pwd.encode()).hexdigest() == _PASSWORD_HASH:
                st.session_state.authenticated = True
                st.rerun()
            else:
                st.error("Incorrect password — please try again.")

        # ── Footer ────────────────────────────────────────────────
        st.markdown(
            '<div class="auth-footer">S P Spices &nbsp;·&nbsp; Internal Use Only</div>',
            unsafe_allow_html=True,
        )

    st.stop()
