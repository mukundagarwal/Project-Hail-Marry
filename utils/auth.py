"""
Authentication gate — call require_login() at the top of every page.
Call render_logout_button() in the sidebar to show a sign-out control.

Password is read from st.secrets["APP_PASSWORD_HASH"] (a bcrypt hash).
Generate a new hash with: python -c "import bcrypt; print(bcrypt.hashpw(b'yourpassword', bcrypt.gensalt()).decode())"
"""

import time
import bcrypt
import streamlit as st
from utils.db import record_login_attempt, check_lockout, purge_old_login_attempts

# ── Brute-force lockout config ──────────────────────────────────
_MAX_ATTEMPTS  = 5
_LOCKOUT_SECS  = 15 * 60   # 15 minutes

# ── Session expiry ──────────────────────────────────────────────
_SESSION_TTL   = 8 * 60 * 60  # 8 hours

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


def _get_client_ip() -> str:
    """Best-effort client IP extraction. Falls back to 'unknown' if unavailable."""
    try:
        headers = st.context.headers
        ip = headers.get("X-Forwarded-For", "").split(",")[0].strip()
        if not ip:
            ip = headers.get("X-Real-Ip", "").strip()
        return ip or "unknown"
    except Exception:
        return "unknown"


def _get_password_hash() -> bytes:
    """Read bcrypt hash from st.secrets or APP_PASSWORD_HASH env var."""
    import os
    try:
        val = st.secrets.get("APP_PASSWORD_HASH")
        if val:
            return val.encode() if isinstance(val, str) else val
    except Exception:
        pass
    val = os.environ.get("APP_PASSWORD_HASH", "")
    if not val:
        raise RuntimeError(
            "APP_PASSWORD_HASH is not set. Add it to .streamlit/secrets.toml or .env.\n"
            "Generate one with: python -c \"import bcrypt; "
            "print(bcrypt.hashpw(b'yourpassword', bcrypt.gensalt()).decode())\""
        )
    return val.encode()


def _is_locked_out() -> tuple[bool, int]:
    """Returns (is_locked, seconds_remaining)."""
    attempts  = st.session_state.get("_auth_attempts", 0)
    locked_at = st.session_state.get("_auth_locked_at", 0)
    if attempts >= _MAX_ATTEMPTS and locked_at:
        elapsed   = time.time() - locked_at
        remaining = int(_LOCKOUT_SECS - elapsed)
        if remaining > 0:
            return True, remaining
        # lockout expired — reset
        st.session_state._auth_attempts  = 0
        st.session_state._auth_locked_at = 0
    return False, 0


def _check_session_expiry() -> None:
    """Clear auth if the session has been idle too long."""
    if not st.session_state.get("authenticated"):
        return
    login_time = st.session_state.get("_auth_login_time", 0)
    if time.time() - login_time > _SESSION_TTL:
        st.session_state.authenticated  = False
        st.session_state._auth_login_time = 0
        st.info("Your session has expired. Please sign in again.")
        st.rerun()


def require_login() -> None:
    """
    Call immediately after st.set_page_config() on every page.
    Shows the login screen and calls st.stop() until the user authenticates.
    """
    _check_session_expiry()

    if st.session_state.get("authenticated"):
        return

    st.markdown(_LOGIN_CSS, unsafe_allow_html=True)

    _, col, _ = st.columns([1, 1.1, 1])

    with col:
        st.markdown("""
        <div class="auth-card">
          <div class="auth-orb">🌶️</div>
          <div class="auth-brand">S P Spices</div>
          <div class="auth-sub">Business Management Diary</div>
          <div class="auth-divider"></div>
          <div class="auth-label">🔒 &nbsp; Enter Password to Continue</div>
        </div>
        """, unsafe_allow_html=True)

        # ── DB-backed lockout check (cross-session protection) ──
        _ip = _get_client_ip()
        try:
            _db_locked, _db_fails = check_lockout(_ip)
            if _db_locked:
                st.error(
                    f"Too many failed attempts from your connection. "
                    f"Please try again in {_LOCKOUT_SECS // 60} minutes.",
                    icon="🔒",
                )
                st.stop()
        except Exception:
            pass  # DB unreachable — fall back to session-only protection

        locked, remaining = _is_locked_out()
        if locked:
            mins = remaining // 60
            secs = remaining % 60
            st.error(
                f"Too many failed attempts. Try again in {mins}m {secs}s.",
                icon="🔒",
            )
            st.stop()

        with st.form("login_form"):
            pwd = st.text_input(
                "Password",
                type="password",
                placeholder="Password",
                label_visibility="collapsed",
                key="_auth_pwd",
            )
            submitted = st.form_submit_button("Sign In →", use_container_width=True)

        if submitted:
            try:
                stored_hash = _get_password_hash()
                if bcrypt.checkpw(pwd.encode(), stored_hash):
                    st.session_state.authenticated    = True
                    st.session_state._auth_attempts   = 0
                    st.session_state._auth_locked_at  = 0
                    st.session_state._auth_login_time = time.time()
                    try:
                        record_login_attempt(_ip, success=True)
                        purge_old_login_attempts()
                    except Exception:
                        pass
                    st.rerun()
                else:
                    attempts = st.session_state.get("_auth_attempts", 0) + 1
                    st.session_state._auth_attempts = attempts
                    try:
                        record_login_attempt(_ip, success=False)
                    except Exception:
                        pass
                    if attempts >= _MAX_ATTEMPTS:
                        st.session_state._auth_locked_at = time.time()
                        st.error(
                            f"Too many failed attempts. Locked for {_LOCKOUT_SECS // 60} minutes.",
                            icon="🔒",
                        )
                    else:
                        remaining_attempts = _MAX_ATTEMPTS - attempts
                        st.error(
                            f"Incorrect password. {remaining_attempts} attempt(s) remaining."
                        )
            except RuntimeError as e:
                st.error(str(e))

        st.markdown(
            '<div class="auth-footer">S P Spices &nbsp;·&nbsp; Internal Use Only</div>',
            unsafe_allow_html=True,
        )

    st.stop()


def render_logout_button() -> None:
    """Render a logout button in the sidebar. Call after require_login()."""
    with st.sidebar:
        st.markdown("---")
        if st.button("Sign Out", key="_logout_btn", use_container_width=True):
            for key in ("authenticated", "_auth_login_time", "_auth_attempts", "_auth_locked_at"):
                st.session_state.pop(key, None)
            st.rerun()
