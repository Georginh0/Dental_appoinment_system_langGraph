"""
integrations/telegram_bot.py — Telegram Chatbot
=================================================
Uses python-telegram-bot (v20+) in webhook mode (production)
with asyncio event loop. Polling mode also supported for local dev.

SETUP (5 minutes):
    1. Open Telegram → search @BotFather → /newbot
    2. Name it "DentAI Pro" → username: "dentai_pro_bot"
    3. Copy the token BotFather gives you
    4. Add to .env:
           TELEGRAM_BOT_TOKEN=7123456789:AAFxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
           TELEGRAM_WEBHOOK_SECRET=any_random_string_32_chars

WEBHOOK MODE (Render.com deployment):
    Set webhook after deploy:
        curl -X POST "https://api.telegram.org/bot{TOKEN}/setWebhook" \
             -d "url=https://YOUR-APP.onrender.com/webhook/telegram" \
             -d "secret_token={TELEGRAM_WEBHOOK_SECRET}"

POLLING MODE (local dev — no ngrok needed):
    python integrations/telegram_bot.py --polling

RUN (production webhook):
    uvicorn integrations.telegram_bot:app --host 0.0.0.0 --port 8002
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI, Header, HTTPException, Request
from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import (
    Application, CommandHandler, ContextTypes, MessageHandler, filters,
)

from scripts.dental_agent import run_agent

log = logging.getLogger("dentai.telegram")

# ── Config ─────────────────────────────────────────────────
TOKEN          = os.environ.get("TELEGRAM_BOT_TOKEN", "")
WEBHOOK_SECRET = os.environ.get("TELEGRAM_WEBHOOK_SECRET", "")

if not TOKEN:
    raise EnvironmentError(
        "\n  TELEGRAM_BOT_TOKEN not set in .env\n"
        "  Get it: Telegram → @BotFather → /newbot\n"
    )

# ── Session store (in-memory — use Redis in prod) ──────────
_sessions: dict[int, str] = {}   # chat_id → session_id


def _get_session(chat_id: int) -> str:
    if chat_id not in _sessions:
        import uuid
        _sessions[chat_id] = f"tg-{uuid.uuid4().hex[:12]}"
    return _sessions[chat_id]


# ── Message formatting ─────────────────────────────────────

def _md_escape(text: str) -> str:
    """Escape Telegram MarkdownV2 special characters."""
    for ch in r"_*[]()~`>#+-=|{}.!\\":
        text = text.replace(ch, f"\\{ch}")
    return text


def _format_for_telegram(text: str) -> str:
    """Trim to Telegram's 4096-char limit."""
    if len(text) > 3900:
        text = text[:3900] + "\n\n_(message truncated — ask a follow-up)_"
    return text


# ──────────────────────────────────────────────────────────
# COMMAND HANDLERS
# ──────────────────────────────────────────────────────────

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /start command."""
    name = update.effective_user.first_name or "there"
    await update.message.reply_text(
        f"Hi {name}! I'm the DentAI Pro assistant.\n\n"
        "I can help you:\n"
        "• Book, cancel, or reschedule appointments\n"
        "• Find the right dental specialist\n"
        "• Answer questions about dental procedures\n"
        "• Handle dental emergencies\n\n"
        "Just type your question or request!\n\n"
        "Emergency: Call (555) DENTIST",
        parse_mode=ParseMode.HTML,
    )


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /help command."""
    await update.message.reply_text(
        "<b>DentAI Pro — What I can do</b>\n\n"
        "<b>Booking:</b> 'Book a teeth cleaning next Tuesday'\n"
        "<b>Cancel:</b> 'Cancel my appointment DENT-0048-XY1234'\n"
        "<b>Reschedule:</b> 'Move my appointment to Friday'\n"
        "<b>Specialists:</b> 'Which orthodontists do you have?'\n"
        "<b>History:</b> 'Show my appointments' (need your patient ID)\n"
        "<b>Emergency:</b> Just describe your symptoms\n\n"
        "Clinic hours: Mon–Fri 8am–4:30pm | Sat 9am–2pm\n"
        "Emergency line: (555) DENTIST",
        parse_mode=ParseMode.HTML,
    )


async def cmd_emergency(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /emergency command."""
    await update.message.reply_text(
        "EMERGENCY CONTACTS\n\n"
        "Emergency dentists:\n"
        "• Dr. Daniel Miller\n"
        "• Dr. Susan Davis\n\n"
        "Call us now: (555) DENTIST\n\n"
        "Or describe your symptoms and I'll help you book an emergency slot right now.",
    )


async def cmd_cancel_session(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Reset conversation session."""
    chat_id = update.effective_chat.id
    _sessions.pop(chat_id, None)
    await update.message.reply_text("Session reset. Fresh start! How can I help?")


# ──────────────────────────────────────────────────────────
# MESSAGE HANDLER
# ──────────────────────────────────────────────────────────

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Route all text messages through the dental agent."""
    if not update.message or not update.message.text:
        return

    chat_id    = update.effective_chat.id
    session_id = _get_session(chat_id)
    user_text  = update.message.text.strip()

    log.info("Telegram | chat:%d | %s", chat_id, user_text[:60])

    # Typing indicator while agent processes
    await context.bot.send_chat_action(chat_id=chat_id, action="typing")

    result = run_agent(user_text, session_id=session_id, channel="telegram")
    reply  = _format_for_telegram(result["reply"])

    if result["is_emergency"]:
        reply += "\n\nEMERGENCY LINE: (555) DENTIST"

    await update.message.reply_text(reply)


# ──────────────────────────────────────────────────────────
# BUILD APPLICATION
# ──────────────────────────────────────────────────────────

def build_telegram_app() -> Application:
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start",   cmd_start))
    app.add_handler(CommandHandler("help",    cmd_help))
    app.add_handler(CommandHandler("emergency", cmd_emergency))
    app.add_handler(CommandHandler("reset",   cmd_cancel_session))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    return app


# ──────────────────────────────────────────────────────────
# FASTAPI WEBHOOK (production)
# ──────────────────────────────────────────────────────────

app = FastAPI(title="DentAI Pro — Telegram Bot", version="1.0.0")
_tg_app: Application | None = None


@app.on_event("startup")
async def startup():
    global _tg_app
    _tg_app = build_telegram_app()
    await _tg_app.initialize()
    await _tg_app.start()
    log.info("Telegram application initialised")


@app.on_event("shutdown")
async def shutdown():
    if _tg_app:
        await _tg_app.stop()
        await _tg_app.shutdown()


@app.post("/webhook/telegram")
async def telegram_webhook(
    request: Request,
    x_telegram_bot_api_secret_token: str = Header(default=""),
):
    """Telegram sends all updates here."""
    if WEBHOOK_SECRET and x_telegram_bot_api_secret_token != WEBHOOK_SECRET:
        raise HTTPException(status_code=403, detail="Invalid secret token")

    data   = await request.json()
    update = Update.de_json(data, _tg_app.bot)
    await _tg_app.process_update(update)
    return {"ok": True}


@app.get("/health")
def health():
    return {"status": "ok", "service": "dentai-telegram"}


# ──────────────────────────────────────────────────────────
# POLLING MODE (local dev — no ngrok required)
# ──────────────────────────────────────────────────────────

def run_polling() -> None:
    """
    Long-polling mode. Use for local development only.
    Telegram will push updates directly without needing a public URL.
    """
    import asyncio

    print("\nTelegram bot starting in POLLING mode...")
    print("Find your bot: @dentai_pro_bot")
    print("Press Ctrl+C to stop.\n")

    tg_app = build_telegram_app()
    tg_app.run_polling(allowed_updates=Update.ALL_TYPES)


# ──────────────────────────────────────────────────────────
# ENTRY POINT
# ──────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--polling", action="store_true", help="Run in polling mode (local dev)")
    args = parser.parse_args()

    if args.polling:
        run_polling()
    else:
        import uvicorn
        print("\nTelegram webhook server starting on http://localhost:8002")
        print("Remember to set webhook URL after deploy:")
        print('  curl -X POST "https://api.telegram.org/bot{TOKEN}/setWebhook" -d "url=https://YOUR.onrender.com/webhook/telegram"')
        uvicorn.run("integrations.telegram_bot:app", host="0.0.0.0", port=8002, reload=True)
