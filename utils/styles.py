"""
Shared CSS and HTML brand constants injected by every page.
get_dashboard_css(light_mode) is used only by app.py to support the theme toggle.
All inner pages use APP_CSS directly (always dark).
"""

APP_CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Playfair+Display:wght@600;700&family=DM+Sans:wght@300;400;500;600&display=swap');
*,*::before,*::after{box-sizing:border-box}
html,body,[data-testid="stAppViewContainer"]{background:#0f0e0c!important;color:#f0ebe0!important;font-family:'DM Sans',sans-serif!important}
[data-testid="stAppViewContainer"]>.main{padding:0!important}
[data-testid="block-container"]{padding:2rem 2.5rem!important}
#MainMenu,footer,header{visibility:hidden}
[data-testid="stDecoration"]{display:none}
.brand-bar{display:flex;align-items:center;gap:12px;padding:0.6rem 0 1.4rem 0;border-bottom:1px solid #2a2820;margin-bottom:2rem}
.brand-icon{font-size:2rem;line-height:1}
.brand-name{font-family:'Playfair Display',serif;font-size:1.6rem;font-weight:700;color:#e8c97e;letter-spacing:0.02em}
.brand-sub{font-size:0.72rem;color:#7a7060;letter-spacing:0.12em;text-transform:uppercase;margin-top:2px}
.page-title{font-family:'Playfair Display',serif;font-size:1.9rem;font-weight:600;color:#e8c97e;margin-bottom:0.3rem}
.page-sub{color:#6b6454;font-size:0.82rem;letter-spacing:0.08em;text-transform:uppercase;margin-bottom:1.6rem}
.nav-card{background:#181610;border:1px solid #2a2820;border-radius:14px;padding:2rem 1.4rem;position:relative;overflow:hidden;transition:border-color 0.2s,transform 0.18s}
.nav-card:hover{border-color:#e8c97e;transform:translateY(-3px)}
.nav-card.disabled{opacity:0.35}
.nav-card .card-num{font-family:'Playfair Display',serif;font-size:2.8rem;color:#2e2b22;font-weight:700;position:absolute;top:12px;right:18px;line-height:1}
.nav-card .card-icon{font-size:2rem;margin-bottom:0.7rem}
.nav-card .card-label{font-size:1.05rem;font-weight:600;color:#e0d8c8;margin-bottom:0.3rem}
.nav-card .card-desc{font-size:0.78rem;color:#5a5448}
.stat-row{display:flex;gap:1rem;margin-bottom:1.6rem;flex-wrap:wrap}
.stat-pill{background:#181610;border:1px solid #2a2820;border-radius:10px;padding:0.7rem 1.3rem;display:flex;flex-direction:column;min-width:130px}
.stat-pill .sp-label{font-size:0.7rem;color:#5a5448;text-transform:uppercase;letter-spacing:0.1em}
.stat-pill .sp-value{font-size:1.3rem;font-weight:600;color:#e8c97e;margin-top:2px}
.stat-pill .sp-sub{font-size:0.72rem;color:#4a4438;margin-top:1px}
[data-testid="stTextInput"] input{background:#181610!important;border:1px solid #2a2820!important;border-radius:10px!important;color:#f0ebe0!important;padding:0.6rem 1rem!important}
[data-testid="stTextInput"] input:focus{border-color:#e8c97e!important;box-shadow:0 0 0 2px rgba(232,201,126,0.12)!important}
[data-testid="stTextInput"] label{color:#5a5448!important;font-size:0.78rem!important}
[data-testid="stDateInput"] input{background:#0f0e0c!important;border-color:#2a2820!important;color:#f0ebe0!important;border-radius:8px!important}
.stButton>button{background:#1e1c14!important;color:#c8bfa8!important;border:1px solid #2a2820!important;border-radius:9px!important;font-family:'DM Sans',sans-serif!important;font-weight:500!important;font-size:0.85rem!important;transition:all 0.15s!important;padding:0.45rem 1.1rem!important}
.stButton>button:hover{background:#e8c97e!important;color:#0f0e0c!important;border-color:#e8c97e!important}
.back-btn>button{background:transparent!important;color:#6b6454!important;border:none!important;padding:0!important;font-size:0.8rem!important}
.back-btn>button:hover{color:#e8c97e!important;background:transparent!important}
.filter-bar{background:#141210;border:1px solid #2a2820;border-radius:12px;padding:1rem 1.4rem;margin-bottom:1.2rem;display:flex;align-items:center;gap:1rem;flex-wrap:wrap}
.filter-label{font-size:0.72rem;color:#5a5448;text-transform:uppercase;letter-spacing:0.1em;white-space:nowrap}
.ledger-header{background:linear-gradient(135deg,#1a1810 0%,#201e14 100%);border:1px solid #2e2b1e;border-radius:14px;padding:1.2rem 1.6rem;margin-bottom:1.2rem;display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:1rem}
.lh-name{font-family:'Playfair Display',serif;font-size:1.4rem;color:#e8c97e}
.lh-id{font-size:0.75rem;color:#4a4438;margin-top:3px}
.cart-item{background:#0f0e0c;border:1px solid #2a2820;border-radius:9px;padding:0.7rem 1rem;margin-bottom:0.5rem}
.ci-header{display:flex;justify-content:space-between;align-items:center;margin-bottom:0.3rem}
.ci-name{font-size:0.88rem;font-weight:600;color:#e8c97e}
.ci-total{font-size:0.88rem;font-weight:600;color:#8dd87a}
.ci-detail{font-size:0.75rem;color:#5a5448;line-height:1.6}
.bill-total-box{background:#0d1209;border:1px solid #1e3018;border-radius:10px;padding:0.9rem 1.2rem;margin:0.8rem 0}
.bt-row{display:flex;justify-content:space-between;font-size:0.83rem;padding:0.2rem 0;color:#6a8060}
.bt-total{font-family:'Playfair Display',serif;font-size:1.3rem;color:#8dd87a;font-weight:700;margin-top:0.5rem}
.items-table{width:100%;border-collapse:collapse;font-size:0.8rem;margin:0.8rem 0}
.items-table th{text-align:left;padding:5px 8px;font-size:0.65rem;color:#3a3628;text-transform:uppercase;letter-spacing:0.1em;border-bottom:1px solid #252318}
.items-table td{padding:6px 8px;color:#c8bfa8;border-bottom:1px solid #1a1810}
.items-table tr:last-child td{border-bottom:none}
.items-table .td-goods{font-weight:600;color:#e0d8c8}
.items-table .td-total{font-weight:600;color:#8dd87a;text-align:right}
.items-table .td-num{text-align:right;color:#8a8070}
.items-footer{display:flex;justify-content:flex-end;padding:6px 8px;border-top:2px solid #2a3820;font-weight:700;color:#e8c97e;font-size:0.9rem}
.timeline-wrap{margin:1rem 0;position:relative;padding-left:28px}
.timeline-wrap::before{content:"";position:absolute;left:10px;top:0;bottom:0;width:2px;background:linear-gradient(to bottom,#2a3820,#1a2030)}
.tl-node{position:relative;margin-bottom:1.1rem}
.tl-dot{position:absolute;left:-24px;top:6px;width:12px;height:12px;border-radius:50%;border:2px solid #2a4020;background:#0d1209}
.tl-dot.paid{background:#6dbf67;border-color:#2a4a2a}
.tl-dot.remaining{background:#6a9fd4;border-color:#2a3460}
.tl-card{background:#181610;border:1px solid #252318;border-radius:10px;padding:0.8rem 1.1rem}
.tl-card:hover{border-color:#3a3628}
.tl-date{font-size:0.7rem;color:#5a5448;letter-spacing:0.05em;margin-bottom:4px}
.tl-row{display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:0.5rem}
.tl-label{font-size:0.8rem;color:#8a8070}
.tl-amount{font-size:1rem;font-weight:600;color:#e0d8c8}
.tl-interest{font-size:0.8rem;color:#6a9fd4}
.tl-days{font-size:0.75rem;color:#4a5460;background:#13141f;border-radius:5px;padding:2px 8px}
.tl-remaining{background:#0d1118;border:1px dashed #2a3460;border-radius:10px;padding:0.8rem 1.1rem;margin-top:0.5rem}
.tl-remaining-label{font-size:0.7rem;color:#3a4060;text-transform:uppercase;letter-spacing:0.1em;margin-bottom:4px}
.bb-row{display:flex;justify-content:space-between;padding:0.38rem 0;border-bottom:1px solid #141e10;font-size:0.88rem}
.bb-row:last-child{border-bottom:none}
.bb-row .bb-label{color:#6a8060}
.bb-row .bb-val{color:#c8bfa8;font-weight:500}
.bb-row.bb-neg .bb-val{color:#d4864a}
.bb-row.bb-pos .bb-val{color:#6a9fd4}
.bb-row.bb-total{margin-top:0.5rem;padding-top:0.65rem;border-top:2px solid #2a4020!important;border-bottom:none}
.bb-row.bb-total .bb-label{color:#8dd87a;font-weight:700;font-size:0.95rem}
.bb-row.bb-total .bb-val{color:#8dd87a;font-weight:700;font-size:1.15rem}
.bill-breakdown{background:#0d1209;border:1px solid #1a2a14;border-radius:12px;padding:1.2rem 1.5rem;margin:1rem 0}
.cs-calculated{display:inline-flex;align-items:center;gap:5px;font-size:0.7rem;font-weight:600;background:#0d1e0d;color:#8dd87a;border:1px solid #1e3e1e;border-radius:20px;padding:2px 9px;text-transform:uppercase}
.cs-pending{display:inline-flex;align-items:center;gap:5px;font-size:0.7rem;font-weight:600;background:#1e1808;color:#b89040;border:1px solid #3a2e10;border-radius:20px;padding:2px 9px;text-transform:uppercase}
.detail-grid{display:grid;grid-template-columns:repeat(4,1fr);gap:0.7rem;margin-top:0.8rem;padding:0 0.2rem}
.detail-cell{background:#0f0e0c;border-radius:8px;padding:0.6rem 0.9rem}
.dc-label{font-size:0.65rem;color:#3a3628;text-transform:uppercase;letter-spacing:0.1em}
.dc-value{font-size:0.92rem;color:#c8bfa8;font-weight:500;margin-top:3px}
.sum-panel{background:#0d0e1a;border:1px solid #1e2040;border-radius:14px;padding:1.4rem 1.6rem;margin-top:1rem}
.sum-panel-title{font-family:'Playfair Display',serif;font-size:1.2rem;color:#8a9fd4;margin-bottom:1rem}
.sum-grid{display:grid;grid-template-columns:repeat(4,1fr);gap:0.8rem;margin-bottom:1.2rem}
.sum-cell{background:#13141f;border:1px solid #252840;border-radius:10px;padding:0.8rem 1rem}
.sum-cell .sc-l{font-size:0.65rem;color:#3a4060;text-transform:uppercase;letter-spacing:0.1em}
.sum-cell .sc-v{font-size:1.2rem;font-weight:600;color:#8a9fd4;margin-top:4px}
.warn-box{background:#1e1000;border:1px solid #4a2800;border-radius:10px;padding:0.9rem 1.2rem;margin-bottom:1rem;font-size:0.85rem;color:#d4864a}
.warn-box strong{color:#e8a060}
.rule-note{background:#1e1808;border:1px solid #3a2e10;border-radius:8px;padding:0.55rem 0.9rem;font-size:0.78rem;color:#8a7840;margin:0.4rem 0 0.9rem 0}
.info-chip{background:#13141f;border:1px solid #252840;border-radius:6px;padding:3px 9px;font-size:0.72rem;color:#6a8ad4;display:inline-block;margin-bottom:0.5rem}
.txn-badge{font-size:0.7rem;font-weight:600;border-radius:20px;padding:3px 10px;letter-spacing:0.05em;text-transform:uppercase}
.badge-paid{background:#1a2e1a;color:#6dbf67;border:1px solid #2a4a2a}
.badge-pending{background:#2e2218;color:#d4864a;border:1px solid #4a3420}
.badge-partial{background:#1e2030;color:#6a8ad4;border:1px solid #2a3460}
.badge-bp-paid{background:#1a2e1a;color:#6dbf67;border:1px solid #2a4a2a;font-size:0.65rem}
.badge-bp-unpaid{background:#2e1818;color:#bf6767;border:1px solid #4a2a2a;font-size:0.65rem}
.txn-tbl-hdr{display:grid;grid-template-columns:28px 2fr 1.2fr 1fr 1fr 1.1fr 0.9fr;gap:0.5rem;padding:0.3rem 1rem 0.4rem 1rem}
.txn-tbl-hdr span{font-size:0.64rem;color:#3a3628;text-transform:uppercase;letter-spacing:0.1em}
[data-testid="stExpander"]{background:#181610!important;border:1px solid #252318!important;border-radius:11px!important;margin-bottom:0.5rem!important}
[data-testid="stExpander"] summary{font-size:0.88rem!important;color:#c8bfa8!important}
[data-testid="stForm"]{background:#181610!important;border:1px solid #2a2820!important;border-radius:12px!important;padding:1.2rem!important}
.stSelectbox>div>div,.stNumberInput>div>div>input{background:#0f0e0c!important;border-color:#2a2820!important;color:#f0ebe0!important;border-radius:8px!important}
hr{border-color:#1e1c14!important;margin:1rem 0!important}
.overdue-alert{display:flex;align-items:center;gap:10px;background:#2a0808;border:1px solid #7a1a1a;border-radius:10px;padding:0.55rem 1rem;margin-bottom:0.35rem;font-size:0.8rem;color:#ff8080;font-weight:500}
.overdue-alert .oa-icon{font-size:1.1rem;flex-shrink:0}
.overdue-alert .oa-days{font-weight:700;color:#ff6060}
::-webkit-scrollbar{width:5px;height:5px}
::-webkit-scrollbar-track{background:#0f0e0c}
::-webkit-scrollbar-thumb{background:#2a2820;border-radius:10px}
.empty-state{text-align:center;padding:3rem;color:#3a3628;font-size:0.88rem}
.empty-state .es-icon{font-size:2.5rem;margin-bottom:0.6rem}
@media print{.no-print{display:none!important}body{background:#fff!important;color:#000!important;font-family:serif}.print-section{padding:24px}}
</style>
"""

BRAND_BAR_HTML = """
<div class="brand-bar">
  <div class="brand-icon">🌶️</div>
  <div>
    <div class="brand-name">S P Spices</div>
    <div class="brand-sub">Business Management Diary</div>
  </div>
</div>
"""

_TOGGLE_WRAP_CSS = """
<style>
.theme-pill{background:#1a1814;border:1px solid #2a2820;border-radius:12px;padding:0.45rem 0.9rem;display:flex;align-items:center;gap:0.5rem;justify-content:flex-end}
div[data-testid="column"]:last-child{display:flex;flex-direction:column;justify-content:center}
</style>
"""


def get_light_mode_css() -> str:
    """
    Comprehensive warm-cream light mode override.
    Inject via st.markdown() AFTER APP_CSS on every page when light_mode is active.
    """
    return """
<style>
/* ── Core layout ── */
html,body,
[data-testid="stAppViewContainer"],
[data-testid="stAppViewContainer"]>.main,
[data-testid="stMainBlockContainer"],
[data-testid="block-container"],
section[data-testid="stMain"]>div,
section[data-testid="stMain"]{background:#F5F1E8!important;color:#2C2C2C!important}

/* ── Sidebar ── */
[data-testid="stSidebar"],[data-testid="stSidebar"]>div:first-child{background:#EFE9DC!important}
[data-testid="stSidebar"] p,[data-testid="stSidebar"] span,[data-testid="stSidebar"] div,
[data-testid="stSidebar"] label{color:#2C2C2C!important}
[data-testid="stSidebar"] hr{border-color:#D8D0C0!important}
[data-testid="stSidebar"] .stButton>button{background:#E6DFD4!important;color:#3A3020!important;border-color:#D8D0C0!important}
[data-testid="stSidebar"] .stButton>button:hover{background:#B07E28!important;color:#fff!important;border-color:#B07E28!important}

/* ── Brand bar ── */
.brand-bar{border-bottom-color:#D8D0C0!important}
.brand-name{color:#8A5E12!important}
.brand-sub{color:#8A7A60!important}

/* ── Page headings ── */
.page-title{color:#8A5E12!important}
.page-sub{color:#8A7A60!important}

/* ── Nav cards (dashboard) ── */
.nav-card{background:#FFFBF7!important;border-color:#D8D0C0!important;box-shadow:0 2px 8px rgba(0,0,0,0.06)!important}
.nav-card:hover{border-color:#B07E28!important;box-shadow:0 6px 20px rgba(176,126,40,0.12)!important;transform:translateY(-3px)!important}
.nav-card .card-num{color:#EDE8DF!important}
.nav-card .card-label{color:#2C2C2C!important}
.nav-card .card-desc{color:#8A7A60!important}

/* ── Stat pills ── */
.stat-pill{background:#FFFBF7!important;border-color:#D8D0C0!important}
.stat-pill .sp-label{color:#8A7A60!important}
.stat-pill .sp-value{color:#8A5E12!important}
.stat-pill .sp-sub{color:#A89880!important}

/* ── Buttons ── */
.stButton>button{background:#EDE8DC!important;color:#3A3020!important;border-color:#D8D0C0!important}
.stButton>button:hover{background:#B07E28!important;color:#fff!important;border-color:#B07E28!important}
.back-btn>button{color:#8A7A60!important;background:transparent!important;border:none!important}
.back-btn>button:hover{color:#B07E28!important;background:transparent!important}

/* ── Text inputs ── */
[data-testid="stTextInput"] input{background:#FFFDF9!important;border-color:#D8D0C0!important;color:#2C2C2C!important}
[data-testid="stTextInput"] input:focus{border-color:#B07E28!important;box-shadow:0 0 0 2px rgba(176,126,40,0.15)!important}
[data-testid="stTextInput"] input::placeholder{color:#B0A088!important}
[data-testid="stTextInput"] label{color:#8A7A60!important;font-size:0.78rem!important}

/* ── Date input ── */
[data-testid="stDateInput"] input,[data-testid="stDateInput"]>div>div{background:#FFFDF9!important;border-color:#D8D0C0!important;color:#2C2C2C!important}
[data-testid="stDateInput"] label{color:#8A7A60!important}

/* ── Number input ── */
.stNumberInput>div>div>input,[data-testid="stNumberInput"] input{background:#FFFDF9!important;border-color:#D8D0C0!important;color:#2C2C2C!important}
[data-testid="stNumberInput"] label{color:#8A7A60!important}
[data-testid="stNumberInput"] button{background:#EDE8DC!important;border-color:#D8D0C0!important;color:#3A3020!important}

/* ── Selectbox ── */
.stSelectbox>div>div,[data-testid="stSelectbox"]>div>div{background:#FFFDF9!important;border-color:#D8D0C0!important;color:#2C2C2C!important}
[data-testid="stSelectbox"] label{color:#8A7A60!important}
[data-baseweb="select"]>div{background:#FFFDF9!important;border-color:#D8D0C0!important}
[data-baseweb="select"] span{color:#2C2C2C!important}
[data-baseweb="popover"]>div{background:#FFFBF7!important;border-color:#D8D0C0!important}
li[role="option"]{background:#FFFBF7!important;color:#2C2C2C!important}
li[role="option"]:hover,li[role="option"][aria-selected="true"]{background:#F0EBE0!important}

/* ── Checkbox / Radio / Toggle ── */
[data-testid="stCheckbox"] label,[data-testid="stRadio"] label,[data-testid="stToggle"] label{color:#3A3020!important}
[data-testid="stRadio"] div{color:#3A3020!important}

/* ── Expander ── */
[data-testid="stExpander"]{background:#FFFBF7!important;border-color:#D8D0C0!important}
[data-testid="stExpander"] summary{color:#3A3020!important}
[data-testid="stExpander"] summary:hover{color:#B07E28!important}
[data-testid="stExpander"]>div{background:#FFFBF7!important}

/* ── Form ── */
[data-testid="stForm"]{background:#FFFBF7!important;border-color:#D8D0C0!important}
[data-testid="stForm"] label{color:#8A7A60!important}

/* ── Popover ── */
[data-testid="stPopover"]>div,[data-testid="stPopoverBody"]{background:#FFFBF7!important;border-color:#D8D0C0!important}
[data-testid="stPopoverBody"] *{color:#2C2C2C!important}
[data-testid="stPopoverBody"] .stButton>button{background:#EDE8DC!important;color:#3A3020!important;border-color:#D8D0C0!important}
[data-testid="stPopoverBody"] .stButton>button:hover{background:#B07E28!important;color:#fff!important}

/* ── Filter bar ── */
.filter-bar{background:#F0EBE0!important;border-color:#D8D0C0!important}
.filter-label{color:#8A7A60!important}

/* ── Ledger header ── */
.ledger-header{background:linear-gradient(135deg,#F0EBE0,#EDE6D8)!important;border-color:#D8D0C0!important}
.lh-name{color:#8A5E12!important}
.lh-id{color:#8A7A60!important}

/* ── Cart / bill items ── */
.cart-item{background:#FFFBF7!important;border-color:#D8D0C0!important}
.ci-name{color:#8A5E12!important}
.ci-detail{color:#8A7A60!important}
.ci-total{color:#4A9444!important}

/* ── Bill total box ── */
.bill-total-box{background:#F0F5EE!important;border-color:#C8DCC4!important}
.bt-row{color:#5A7A54!important}

/* ── Items table ── */
.items-table th{color:#8A7A60!important;border-bottom-color:#D8D0C0!important}
.items-table td{color:#3A3020!important;border-bottom-color:#EDE8DC!important}
.items-table .td-goods{color:#2C2C2C!important}
.items-table .td-num{color:#8A7A60!important}
.items-footer{border-top-color:#C8DCC4!important;color:#8A5E12!important}

/* ── Timeline ── */
.tl-card{background:#FFFBF7!important;border-color:#D8D0C0!important}
.tl-card:hover{border-color:#B07E28!important}
.tl-date{color:#8A7A60!important}
.tl-label{color:#6A5A44!important}
.tl-amount{color:#2C2C2C!important}
.tl-days{color:#5A7A98!important;background:#E8F0FA!important}
.tl-remaining{background:#EEF2FA!important;border-color:#C0CCDC!important}
.tl-remaining-label{color:#7A8AAA!important}
.tl-interest{color:#4A7AAA!important}

/* ── Bill breakdown ── */
.bill-breakdown{background:#F0F5EE!important;border-color:#C8DCC4!important}
.bb-row{border-bottom-color:#D8EDD4!important}
.bb-row .bb-label{color:#5A7A54!important}
.bb-row .bb-val{color:#3A3020!important}
.bb-row.bb-total{border-top-color:#4A9444!important}
.bb-row.bb-total .bb-label,.bb-row.bb-total .bb-val{color:#4A9444!important}

/* ── Status badges ── */
.cs-calculated{background:#E8F5E8!important;color:#4A9444!important;border-color:#B0D8B0!important}
.cs-pending{background:#FFF0E0!important;color:#C05A00!important;border-color:#E8C080!important}
.badge-paid{background:#E8F5E8!important;color:#3A8034!important;border-color:#B0D8B0!important}
.badge-pending{background:#FFF0E8!important;color:#C04800!important;border-color:#E8B080!important}
.badge-partial{background:#E8EEF8!important;color:#3A5898!important;border-color:#B0C4E8!important}
.badge-bp-paid{background:#E8F5E8!important;color:#3A8034!important;border-color:#B0D8B0!important}
.badge-bp-unpaid{background:#FAE8E8!important;color:#983A3A!important;border-color:#E8B0B0!important}

/* ── Detail grid ── */
.detail-cell{background:#F5F1E8!important;border:1px solid #E0D8C8!important}
.dc-label{color:#8A7A60!important}
.dc-value{color:#3A3020!important}

/* ── Summary panel ── */
.sum-panel{background:#EEF0FA!important;border-color:#C4C8E0!important}
.sum-panel-title{color:#4A5AA0!important}
.sum-cell{background:#F5F6FC!important;border-color:#D0D4EC!important}
.sum-cell .sc-l{color:#7A80B0!important}
.sum-cell .sc-v{color:#4A5AA0!important}

/* ── Warn / rule / info boxes ── */
.warn-box{background:#FFF4E8!important;border-color:#E8B060!important;color:#8A4400!important}
.warn-box strong{color:#B05800!important}
.rule-note{background:#FFF8E8!important;border-color:#E8D080!important;color:#7A6020!important}
.info-chip{background:#EEF0FA!important;border-color:#C4C8E0!important;color:#4A5AA0!important}

/* ── Overdue alert ── */
.overdue-alert{background:#FAE8E8!important;border-color:#E8A0A0!important;color:#C02020!important}
.overdue-alert .oa-days{color:#A00000!important}

/* ── Dividers & scrollbar ── */
hr{border-color:#D8D0C0!important}
::-webkit-scrollbar-track{background:#F0EBE0!important}
::-webkit-scrollbar-thumb{background:#D8D0C0!important}

/* ── Empty state ── */
.empty-state{color:#8A7A60!important}

/* ── Generic markdown text ── */
[data-testid="stMarkdownContainer"] p,[data-testid="stMarkdownContainer"] li{color:#2C2C2C!important}
[data-testid="stCaption"]{color:#8A7A60!important}

/* ── Streamlit alerts ── */
[data-testid="stAlert"]{background:#FFFBF7!important}

/* ── Theme toggle pill ── */
.theme-pill{background:#FFFBF7!important;border-color:#D8D0C0!important;box-shadow:0 1px 4px rgba(0,0,0,0.06)!important}
</style>
"""


def get_dashboard_css(light_mode: bool = False) -> str:
    """Full CSS for the dashboard page (includes theme toggle pill styles)."""
    base = APP_CSS + _TOGGLE_WRAP_CSS
    if light_mode:
        return base + get_light_mode_css()
    return base
