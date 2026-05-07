""""
streamlit_app.py — DentAI Pro
================
"""

from __future__ import annotations
import sys
from datetime import datetime
from pathlib import Path

import streamlit as st
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parent))
load_dotenv()

from scripts.dental_agent import SPECIALIZATIONS, run_agent

# ── Page config — before every other st.* call ─────────────
st.set_page_config(
    page_title="DentAI Pro",
    page_icon="🦷",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── Theme ───────────────────────────────────────────────────
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=DM+Sans:wght@300;400;500;600&family=Playfair+Display:wght@600&display=swap');

html, body, [class*="css"] {
    font-family: 'DM Sans', sans-serif !important;
}

:root {
    --navy:  #0d1b2a;
    --navy2: #162032;
    --navy3: #1e2f45;
    --teal:  #1D9E75;
    --teal-l:#5DCAA5;
    --amber: #EF9F27;
    --red:   #E24B4A;
    --text:  #e8edf2;
    --muted: #8fa3ba;
    --soft:  #c2d2e2;
}

/* ── Global background ── */
.stApp { background-color: var(--navy) !important; }

/* ── Sidebar ── */
[data-testid="stSidebar"] {
    background-color: var(--navy2) !important;
    border-right: 1px solid rgba(255,255,255,0.07) !important;
}
[data-testid="stSidebar"] p,
[data-testid="stSidebar"] span,
[data-testid="stSidebar"] label { color: var(--soft) !important; }

[data-testid="stSidebar"] .stButton button {
    width: 100% !important;
    background: transparent !important;
    border: 1px solid rgba(255,255,255,0.09) !important;
    border-radius: 8px !important;
    color: var(--soft) !important;
    font-size: 13px !important;
    text-align: left !important;
    padding: 8px 12px !important;
    margin-bottom: 3px !important;
    transition: all 0.15s !important;
    font-family: 'DM Sans', sans-serif !important;
}
[data-testid="stSidebar"] .stButton button:hover {
    background: rgba(29,158,117,0.13) !important;
    border-color: rgba(29,158,117,0.38) !important;
    color: #9FE1CB !important;
}

/* ── Chat messages ── */
[data-testid="stChatMessage"] {
    background-color: var(--navy3) !important;
    border: 1px solid rgba(255,255,255,0.08) !important;
    border-radius: 14px !important;
    margin-bottom: 10px !important;
}

/* ── Chat input ── */
[data-testid="stChatInput"] {
    background: var(--navy3) !important;
    border: 1px solid rgba(255,255,255,0.1) !important;
    border-radius: 12px !important;
}
[data-testid="stChatInput"]:focus-within {
    border-color: rgba(29,158,117,0.45) !important;
    box-shadow: 0 0 0 1px rgba(29,158,117,0.2) !important;
}
[data-testid="stChatInput"] textarea {
    color: var(--text) !important;
    font-family: 'DM Sans', sans-serif !important;
}
[data-testid="stChatInput"] textarea::placeholder { color: var(--muted) !important; }

/* ── Spinner ── */
.stSpinner > div { color: var(--teal-l) !important; }

/* ── Text defaults ── */
p, .stMarkdown { color: var(--soft) !important; }
h1, h2, h3     { color: var(--text)  !important; }
hr { border-color: rgba(255,255,255,0.07) !important; }
</style>
""", unsafe_allow_html=True)


# ── Session state ───────────────────────────────────────────
_DEFAULTS: dict = {
    "messages":        [],      # list[dict[role, content]]
    "patient_id":      None,    # int | None — resolved by agent
    "sid":             datetime.now().strftime("%Y%m%d%H%M%S"),
    "emergency_shown": False,
    # KEY FIX 2: sidebar buttons write here instead of calling rerun directly.
    # _dispatch() reads and clears this at the TOP of the render cycle.
    "pending_input":   None,    # str | None
}
for _k, _v in _DEFAULTS.items():
    if _k not in st.session_state:
        st.session_state[_k] = _v


# ── Single dispatch function ─────────────────────────────────
# All channels (web chat_input, sidebar buttons, Telegram webhook, WhatsApp)
# should funnel through this one function.  Never call run_agent() directly
# from UI code — always go through _dispatch().
def _dispatch(user_text: str) -> str:
    """Call run_agent and persist both user message and reply to session state.

    Args:
        user_text: Raw user input string from any channel.

    Returns:
        Agent reply string (also stored in session_state.messages).
    """
    # Append user turn
    st.session_state.messages.append({"role": "user", "content": user_text})

    with st.spinner("DentAI is thinking..."):
        try:
            result: dict = run_agent(
                user_text,
                session_id=st.session_state.sid,
                patient_id=st.session_state.patient_id,
                channel="web",
            )

            reply: str = result.get("reply") or "I'm sorry, I couldn't process that request."

            # Persist patient ID if agent resolved one this turn
            if result.get("patient_id"):
                st.session_state.patient_id = result["patient_id"]

            # Latch emergency flag once so banner only shows once per session
            if result.get("is_emergency") and not st.session_state.emergency_shown:
                st.session_state.emergency_shown = True

        except Exception as exc:
            # Show real error in UI (dev) + print full traceback to terminal
            reply = f"⚠️ Agent error: {exc}"
            st.error(reply)
            raise   # prints full traceback in terminal — remove before prod

    st.session_state.messages.append({"role": "assistant", "content": reply})
    return reply


# ── KEY FIX 2: process pending sidebar input BEFORE any UI renders ──────
# Execution order: sidebar sets pending_input → Streamlit reruns →
# this block fires first → _dispatch() calls agent → history now contains
# both the user message AND the reply → chat history renders correctly.
if st.session_state.pending_input:
    _dispatch(st.session_state.pending_input)
    st.session_state.pending_input = None
    # No explicit st.rerun() here — fall through and render the updated history


# ── Sidebar ─────────────────────────────────────────────────
with st.sidebar:
    # Logo block
    st.markdown("""
    <div style="display:flex;align-items:center;gap:12px;padding:6px 0 18px;
                border-bottom:1px solid rgba(255,255,255,0.07);margin-bottom:14px">
        <div style="width:44px;height:44px;background:#1D9E75;border-radius:10px;
                    display:flex;align-items:center;justify-content:center;font-size:26px">🦷</div>
        <div>
            <div style="font-family:'Playfair Display',serif;font-size:20px;color:#e8edf2;line-height:1.2">
                DentAI Pro</div>
            <div style="font-size:10px;letter-spacing:1.5px;color:#5DCAA5;text-transform:uppercase">
                Intelligent Assistant</div>
        </div>
    </div>
    <div style="font-size:10px;letter-spacing:1.4px;text-transform:uppercase;
                color:#8fa3ba;font-weight:500;margin-bottom:8px">Quick Actions</div>
    """, unsafe_allow_html=True)

    QUICK_ACTIONS: list[tuple[str, str]] = [
        ("📅", "Book a teeth cleaning"),
        ("🦷", "I have severe tooth pain"),
        ("❌", "Cancel my appointment"),
        ("🔍", "List orthodontists near me"),
        ("💉", "What does a root canal involve?"),
        ("📋", "Show my appointment history"),
    ]
    for icon, label in QUICK_ACTIONS:
        # FIXED: set pending_input, NOT append+rerun.
        # On the next render the pending_input block (above) calls _dispatch().
        if st.button(f"{icon}  {label}", key=f"qa_{label}", use_container_width=True):
            st.session_state.pending_input = label

    st.markdown("""
    <div style="font-size:10px;letter-spacing:1.4px;text-transform:uppercase;
                color:#8fa3ba;font-weight:500;margin:14px 0 8px">Specialties</div>
    """, unsafe_allow_html=True)

    for spec in SPECIALIZATIONS:
        display_name = spec.replace("_", " ").title()
        if st.button(display_name, key=f"spec_{spec}", use_container_width=True):
            st.session_state.pending_input = f"Show available {display_name} slots"

    st.markdown("<br>", unsafe_allow_html=True)
    if st.button("🗑  Clear conversation", use_container_width=True):
        st.session_state.messages        = []
        st.session_state.sid             = datetime.now().strftime("%Y%m%d%H%M%S")
        st.session_state.emergency_shown = False
        st.session_state.pending_input   = None
        st.rerun()


# ── Top bar ─────────────────────────────────────────────────
st.markdown("""
<div style="background:#162032;padding:14px 28px;
            border-bottom:1px solid rgba(255,255,255,0.07);
            display:flex;align-items:center;justify-content:space-between;
            margin-bottom:10px">
    <div>
        <span style="font-family:'Playfair Display',serif;font-size:22px;color:#e8edf2">
            🦷 DentAI Pro
        </span>
        <span style="margin-left:12px;font-size:12px;
                     background:rgba(29,158,117,0.18);color:#5DCAA5;
                     padding:3px 10px;border-radius:20px;
                     border:1px solid rgba(29,158,117,0.3)">24/7 Online</span>
    </div>
    <div style="display:flex;gap:6px">
        <span style="font-size:11px;padding:3px 9px;border-radius:20px;
                     background:rgba(29,158,117,0.18);color:#5DCAA5;
                     border:1px solid rgba(29,158,117,0.3)">Book</span>
        <span style="font-size:11px;padding:3px 9px;border-radius:20px;
                     background:rgba(29,158,117,0.18);color:#5DCAA5;
                     border:1px solid rgba(29,158,117,0.3)">Reschedule</span>
        <span style="font-size:11px;padding:3px 9px;border-radius:20px;
                     background:rgba(239,159,39,0.18);color:#FAC775;
                     border:1px solid rgba(239,159,39,0.3)">Cancel</span>
    </div>
</div>
""", unsafe_allow_html=True)

# ── Emergency banner ────────────────────────────────────────
st.markdown("""
<div style="background:rgba(226,75,74,0.1);border:1px solid rgba(226,75,74,0.28);
            border-left:3px solid #E24B4A;border-radius:8px;padding:10px 16px;
            margin-bottom:16px;display:flex;align-items:center;gap:10px;
            font-size:13px;color:#F09595">
    🚨
    <div><strong style="color:#F7C1C1">Dental Emergency?</strong>
    Call <strong style="color:#F7C1C1">0800 DENTIST</strong>
    or visit your nearest A&amp;E immediately.</div>
</div>
""", unsafe_allow_html=True)

# Emergency alert when agent detects one
if st.session_state.emergency_shown:
    st.error("⚠️ **Emergency detected** — please call 0800 DENTIST immediately.")


# ── Chat history ─────────────────────────────────────────────
if not st.session_state.messages:
    st.markdown("""
    <div style="text-align:center;padding:60px 20px">
        <div style="font-size:64px;margin-bottom:16px">🦷</div>
        <div style="font-family:'Playfair Display',serif;font-size:22px;
                    color:#e8edf2;margin-bottom:10px">How can I help you today?</div>
        <div style="color:#8fa3ba;font-size:14px">
            Ask me to book, reschedule, cancel, or answer any dental question.
        </div>
        <div style="color:#5f7a8c;font-size:12px;margin-top:8px">
            Or pick a quick action from the sidebar.
        </div>
    </div>
    """, unsafe_allow_html=True)
else:
    for msg in st.session_state.messages:
        avatar = "🦷" if msg["role"] == "assistant" else None
        with st.chat_message(msg["role"], avatar=avatar):
            st.markdown(msg["content"], unsafe_allow_html=True)


# ── Chat input — typed messages ──────────────────────────────
# st.chat_input() is Streamlit's dedicated chat widget.
# It returns the submitted text on the frame it is submitted,
# then None on every subsequent frame until submitted again.
if prompt := st.chat_input("How can we help you today?"):
    # Render user bubble immediately so there's no visual delay
    with st.chat_message("user"):
        st.markdown(prompt)

    # Render assistant bubble — spinner shows while agent runs
    with st.chat_message("assistant", avatar="🦷"):
        reply = _dispatch(prompt)
        st.markdown(reply, unsafe_allow_html=True)

    # Rerun to sync full history from session_state (removes any duplicates)
    st.rerun()