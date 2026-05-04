"""
streamlit_app.py — DentAI Pro Chat Interface
=============================================
Redesigned with a premium dark navy/teal theme.

RUN:
    conda activate dentai
    streamlit run streamlit_app.py
"""

from __future__ import annotations

import streamlit as st

# ── Page config — must be first Streamlit call ─────────────
st.set_page_config(
    page_title="DentAI Pro",
    page_icon="🦷",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── Design system ───────────────────────────────────────────
THEME_CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=DM+Sans:wght@300;400;500;600&family=Playfair+Display:wght@600&display=swap');

/* ── Reset & root ── */
html, body, [class*="css"] {
    font-family: 'DM Sans', sans-serif !important;
}

:root {
    --navy:   #0d1b2a;
    --navy2:  #162032;
    --navy3:  #1e2f45;
    --teal:   #1D9E75;
    --teal-l: #5DCAA5;
    --amber:  #EF9F27;
    --red:    #E24B4A;
    --text:   #e8edf2;
    --muted:  #8fa3ba;
    --soft:   #c2d2e2;
}

/* ── App background ── */
.stApp {
    background-color: var(--navy) !important;
}

/* ── Sidebar ── */
[data-testid="stSidebar"] {
    background-color: #162032 !important;
    border-right: 1px solid rgba(255,255,255,0.07) !important;
}
[data-testid="stSidebar"] .stMarkdown p,
[data-testid="stSidebar"] label {
    color: var(--soft) !important;
}

/* ── Sidebar logo ── */
.sidebar-logo {
    display: flex;
    align-items: center;
    gap: 12px;
    padding: 8px 0 20px;
    border-bottom: 1px solid rgba(255,255,255,0.07);
    margin-bottom: 16px;
}
.logo-badge {
    width: 40px; height: 40px;
    background: #1D9E75;
    border-radius: 10px;
    display: flex; align-items: center; justify-content: center;
    font-size: 22px; flex-shrink: 0;
}
.logo-name {
    font-family: 'Playfair Display', serif !important;
    font-size: 19px;
    color: #e8edf2;
    line-height: 1.1;
}
.logo-tagline {
    font-size: 10px;
    letter-spacing: 1.5px;
    text-transform: uppercase;
    color: #5DCAA5;
    margin-top: 2px;
}

/* ── Sidebar section labels ── */
.sidebar-label {
    font-size: 10px;
    letter-spacing: 1.4px;
    text-transform: uppercase;
    color: var(--muted);
    font-weight: 500;
    margin: 16px 0 8px;
}

/* ── Sidebar buttons ── */
[data-testid="stSidebar"] .stButton button {
    width: 100% !important;
    background: transparent !important;
    border: 1px solid rgba(255,255,255,0.08) !important;
    border-radius: 8px !important;
    color: var(--soft) !important;
    font-family: 'DM Sans', sans-serif !important;
    font-size: 13px !important;
    text-align: left !important;
    padding: 8px 12px !important;
    margin-bottom: 4px !important;
    transition: all 0.15s !important;
}
[data-testid="stSidebar"] .stButton button:hover {
    background: rgba(29,158,117,0.12) !important;
    border-color: rgba(29,158,117,0.35) !important;
    color: #9FE1CB !important;
}

/* ── Main area ── */
.block-container {
    padding: 0 !important;
    max-width: 100% !important;
}

/* ── Top bar ── */
.topbar {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 16px 28px;
    background: #162032;
    border-bottom: 1px solid rgba(255,255,255,0.07);
    position: sticky;
    top: 0;
    z-index: 100;
}
.topbar-title {
    font-family: 'Playfair Display', serif;
    font-size: 22px;
    color: #e8edf2;
}
.topbar-pills {
    display: flex;
    gap: 6px;
    margin-top: 6px;
}
.pill {
    font-size: 10px;
    padding: 3px 9px;
    border-radius: 20px;
    font-weight: 500;
    letter-spacing: 0.3px;
}
.pill-teal {
    background: rgba(29,158,117,0.18);
    color: #5DCAA5;
    border: 1px solid rgba(29,158,117,0.3);
}
.pill-amber {
    background: rgba(239,159,39,0.15);
    color: #FAC775;
    border: 1px solid rgba(239,159,39,0.25);
}
.status-dot {
    width: 8px; height: 8px;
    border-radius: 50%;
    background: #1D9E75;
    display: inline-block;
    margin-right: 6px;
    box-shadow: 0 0 6px rgba(29,158,117,0.6);
}

/* ── Emergency banner ── */
.emergency-banner {
    margin: 16px 24px 0;
    background: rgba(226,75,74,0.1);
    border: 1px solid rgba(226,75,74,0.28);
    border-left: 3px solid #E24B4A;
    border-radius: 8px;
    padding: 10px 16px;
    display: flex;
    align-items: center;
    gap: 10px;
    font-size: 13px;
    color: #F09595;
}
.emergency-banner strong { color: #F7C1C1; }

/* ── Chat messages ── */
.chat-wrapper {
    padding: 20px 28px;
    display: flex;
    flex-direction: column;
    gap: 16px;
}
.msg-row { display: flex; align-items: flex-start; gap: 12px; }
.msg-row.user { flex-direction: row-reverse; }

.avatar {
    width: 34px; height: 34px;
    border-radius: 50%;
    display: flex; align-items: center; justify-content: center;
    font-size: 15px;
    flex-shrink: 0;
    margin-top: 2px;
}
.avatar-ai  { background: #1D9E75; color: white; }
.avatar-usr {
    background: rgba(239,159,39,0.18);
    border: 1px solid rgba(239,159,39,0.32);
    color: #FAC775;
    font-size: 13px;
    font-weight: 600;
}

.bubble {
    max-width: 72%;
    padding: 12px 16px;
    border-radius: 14px;
    font-size: 14px;
    line-height: 1.65;
}
.bubble-ai {
    background: #1e2f45;
    border: 1px solid rgba(255,255,255,0.08);
    color: #c2d2e2;
    border-top-left-radius: 4px;
}
.bubble-user {
    background: rgba(29,158,117,0.16);
    border: 1px solid rgba(29,158,117,0.28);
    color: #9FE1CB;
    border-top-right-radius: 4px;
    text-align: right;
}

.bubble-meta {
    font-size: 11px;
    color: #8fa3ba;
    margin-top: 4px;
}

/* ── Info card inside bubble ── */
.info-card {
    background: rgba(29,158,117,0.09);
    border: 1px solid rgba(29,158,117,0.2);
    border-radius: 8px;
    padding: 10px 13px;
    margin-top: 10px;
    font-size: 13px;
    color: #8fa3ba;
    line-height: 1.7;
}
.info-card .ic-item { color: #5DCAA5; }

/* ── Chat input ── */
.stTextInput > div > div > input,
.stTextArea textarea {
    background: #1e2f45 !important;
    border: 1px solid rgba(255,255,255,0.1) !important;
    border-radius: 12px !important;
    color: #e8edf2 !important;
    font-family: 'DM Sans', sans-serif !important;
    font-size: 14px !important;
    padding: 12px 16px !important;
}
.stTextInput > div > div > input:focus,
.stTextArea textarea:focus {
    border-color: rgba(29,158,117,0.45) !important;
    box-shadow: none !important;
}
.stTextInput > div > div > input::placeholder,
.stTextArea textarea::placeholder {
    color: #8fa3ba !important;
}

/* ── Send button ── */
.main-send .stButton button {
    background: #1D9E75 !important;
    border: none !important;
    border-radius: 10px !important;
    color: white !important;
    font-weight: 500 !important;
    font-size: 14px !important;
    padding: 10px 22px !important;
    transition: background 0.15s !important;
}
.main-send .stButton button:hover {
    background: #0F6E56 !important;
}

/* ── Hint tags row ── */
.hint-row {
    display: flex;
    gap: 7px;
    flex-wrap: wrap;
    margin-top: 10px;
}
.hint-tag {
    font-size: 11.5px;
    padding: 4px 11px;
    border-radius: 20px;
    background: rgba(255,255,255,0.04);
    border: 1px solid rgba(255,255,255,0.09);
    color: #8fa3ba;
    cursor: pointer;
}

/* ── Timestamp divider ── */
.ts-divider {
    text-align: center;
    font-size: 11px;
    color: #8fa3ba;
    letter-spacing: 0.4px;
    margin: 4px 0;
}

/* ── Streamlit element overrides ── */
.stMarkdown, .element-container { color: var(--soft) !important; }
.stSpinner > div { color: var(--teal-l) !important; }
div[data-testid="stVerticalBlock"] > div { gap: 0 !important; }
hr { border-color: rgba(255,255,255,0.07) !important; }
</style>
"""

st.markdown(THEME_CSS, unsafe_allow_html=True)

# ── Session state ───────────────────────────────────────────
if "messages" not in st.session_state:
    st.session_state.messages: list[dict] = []
if "patient_id" not in st.session_state:
    st.session_state.patient_id: str | None = None


# ── Sidebar ─────────────────────────────────────────────────
with st.sidebar:
    st.markdown("""
    <div class="sidebar-logo">
        <div class="logo-badge">🦷</div>
        <div>
            <div class="logo-name">DentAI Pro</div>
            <div class="logo-tagline">Intelligent Assistant</div>
        </div>
    </div>
    <div class="sidebar-label">Quick Actions</div>
    """, unsafe_allow_html=True)

    QUICK_ACTIONS = [
        ("📅", "Book a teeth cleaning"),
        ("🦷", "I have severe tooth pain"),
        ("❌", "Cancel my appointment"),
        ("🔍", "List orthodontists"),
        ("💉", "What does a root canal involve?"),
        ("📋", "Show my appointment history"),
    ]
    for icon, label in QUICK_ACTIONS:
        if st.button(f"{icon}  {label}", key=f"quick_{label}"):
            st.session_state.messages.append({"role": "user", "content": label})

    st.markdown('<div class="sidebar-label">Specialties</div>', unsafe_allow_html=True)
    SPECIALTIES = ["General Dentistry", "Orthodontics", "Oral Surgery"]
    for spec in SPECIALTIES:
        if st.button(spec, key=f"spec_{spec}"):
            st.session_state.messages.append(
                {"role": "user", "content": f"Show available {spec} slots"}
            )

    st.markdown("<br>", unsafe_allow_html=True)
    if st.button("🗑  Clear conversation", key="clear"):
        st.session_state.messages = []
        st.session_state.patient_id = None
        st.rerun()


# ── Top bar ─────────────────────────────────────────────────
st.markdown("""
<div class="topbar">
    <div>
        <div class="topbar-title">🦷 DentAI Pro</div>
        <div class="topbar-pills">
            <span class="pill pill-teal">Book</span>
            <span class="pill pill-teal">Reschedule</span>
            <span class="pill pill-teal">Cancel</span>
            <span class="pill pill-amber">24 / 7</span>
        </div>
    </div>
    <div style="display:flex;align-items:center;gap:8px">
        <span class="status-dot"></span>
        <span style="font-size:12px;color:#5DCAA5">Online</span>
    </div>
</div>
""", unsafe_allow_html=True)

# ── Emergency banner ────────────────────────────────────────
st.markdown("""
<div class="emergency-banner">
    🚨 <div><strong>Dental Emergency?</strong>
    Call <strong>0800 DENTIST</strong> or visit your nearest A&amp;E immediately.</div>
</div>
""", unsafe_allow_html=True)

st.markdown("<br>", unsafe_allow_html=True)


# ── Helper: render a single message ─────────────────────────
def render_message(role: str, content: str, initials: str = "G") -> None:
    if role == "user":
        st.markdown(f"""
        <div class="msg-row user">
            <div class="avatar avatar-usr">{initials}</div>
            <div>
                <div class="bubble bubble-user">{content}</div>
                <div class="bubble-meta" style="text-align:right">Just now</div>
            </div>
        </div>""", unsafe_allow_html=True)
    else:
        st.markdown(f"""
        <div class="msg-row">
            <div class="avatar avatar-ai">🦷</div>
            <div>
                <div class="bubble bubble-ai">{content}</div>
                <div class="bubble-meta">DentAI · Just now</div>
            </div>
        </div>""", unsafe_allow_html=True)


# ── Chat history ─────────────────────────────────────────────
if not st.session_state.messages:
    st.markdown("""
    <div class="ts-divider">Start a conversation below</div>
    <div style="text-align:center;padding:32px 0 16px">
        <div style="font-size:40px;margin-bottom:12px">🦷</div>
        <div style="font-size:15px;color:#8fa3ba">Ask me to book, reschedule, or cancel an appointment.</div>
        <div style="font-size:13px;color:#5f7a8c;margin-top:6px">Or pick a quick action from the sidebar.</div>
    </div>
    """, unsafe_allow_html=True)
else:
    st.markdown('<div class="ts-divider">Today</div>', unsafe_allow_html=True)
    initials = (st.session_state.patient_id or "G")[:2].upper()
    for msg in st.session_state.messages:
        render_message(msg["role"], msg["content"], initials=initials)


# ── Input area ───────────────────────────────────────────────
st.markdown("<div style='height:16px'></div>", unsafe_allow_html=True)

col_input, col_send = st.columns([9, 1])
with col_input:
    user_input = st.text_input(
        label="",
        placeholder="How can we help you today?",
        label_visibility="collapsed",
        key="chat_input",
    )
with col_send:
    st.markdown('<div class="main-send">', unsafe_allow_html=True)
    send = st.button("Send ↗", key="send_btn")
    st.markdown("</div>", unsafe_allow_html=True)

# Hint tags
st.markdown("""
<div class="hint-row">
    <span class="hint-tag">Next available slot</span>
    <span class="hint-tag">Tooth pain</span>
    <span class="hint-tag">Cleaning</span>
    <span class="hint-tag">Check insurance</span>
</div>
""", unsafe_allow_html=True)


# ── Message handling ─────────────────────────────────────────
def handle_message(text: str) -> None:
    """Append user message and generate AI reply."""
    if not text.strip():
        return
    st.session_state.messages.append({"role": "user", "content": text})

    # ── Plug in your LangGraph agent here ────────────────────
    # from src.agents.dental_agent import run_agent
    # reply = run_agent(text, patient_id=st.session_state.patient_id)
    # ─────────────────────────────────────────────────────────

    # Placeholder echo until agent is wired up
    reply = (
        f"I received your message: <em>{text}</em>.<br><br>"
        "To proceed, please provide your <strong style='color:#5DCAA5'>patient ID</strong> "
        "and preferred appointment date."
    )
    st.session_state.messages.append({"role": "assistant", "content": reply})
    st.rerun()


if send and user_input:
    handle_message(user_input)