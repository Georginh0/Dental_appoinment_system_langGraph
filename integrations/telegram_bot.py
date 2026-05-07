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
from contextlib import asynccontextmanager      # FIX 1: replaces on_event
from pathlib import Path
 
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
 
from dotenv import load_dotenv
load_dotenv()
 
from fastapi import FastAPI, Header, HTTPException, Request
from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)
 
from scripts.dental_agent import run_agent
 
log = logging.getLogger("dentai.telegram")
 
TOKEN          = os.environ.get("TELEGRAM_BOT_TOKEN", "")
WEBHOOK_SECRET = os.environ.get("TELEGRAM_WEBHOOK_SECRET", "")
 
if not TOKEN:
    raise EnvironmentError(
        "\n  TELEGRAM_BOT_TOKEN not set in .env\n"
        "  Get it: Telegram → @BotFather → /newbot\n"
    )
 
_sessions: dict[int, str] = {}
 
 
def _get_session(chat_id: int) -> str:
    if chat_id not in _sessions:
        import uuid
        _sessions[chat_id] = f"tg-{uuid.uuid4().hex[:12]}"
    return _sessions[chat_id]
 
 
def _format_for_telegram(text: str) -> str:
    if len(text) > 3900:
        text = text[:3900] + "\n\n_(message truncated — ask a follow-up)_"
    return text
 
 
# ── Command handlers ───────────────────────────────────────────────────────
 
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
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
    )
 
 
async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "<b>DentAI Pro — What I can do</b>\n\n"
        "<b>Booking:</b> 'Book a teeth cleaning next Tuesday'\n"
        "<b>Cancel:</b> 'Cancel my appointment DENT-0048-XY1234'\n"
        "<b>Reschedule:</b> 'Move my appointment to Friday'\n"
        "<b>Specialists:</b> 'Which orthodontists do you have?'\n"
        "<b>History:</b> 'Show my appointments' (need patient ID)\n"
        "<b>Emergency:</b> Just describe your symptoms\n\n"
        "Hours: Mon–Fri 8am–4:30pm | Sat 9am–2pm\n"
        "Emergency: (555) DENTIST",
        parse_mode=ParseMode.HTML,
    )
 
 
async def cmd_emergency(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "EMERGENCY\n\n"
        "Emergency dentists: Dr. Daniel Miller, Dr. Susan Davis\n"
        "Call now: (555) DENTIST\n\n"
        "Or describe your symptoms and I'll book an emergency slot now.",
    )
 
 
async def cmd_reset(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    _sessions.pop(update.effective_chat.id, None)
    await update.message.reply_text("Session reset. Fresh start! How can I help?")
 
 
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.message.text:
        return
 
    chat_id    = update.effective_chat.id
    session_id = _get_session(chat_id)
    user_text  = update.message.text.strip()
 
    log.info("Telegram | chat:%d | %s", chat_id, user_text[:60])
 
    await context.bot.send_chat_action(chat_id=chat_id, action="typing")
 
    result = run_agent(user_text, session_id=session_id, channel="telegram")
    reply  = _format_for_telegram(result["reply"])
 
    if result["is_emergency"]:
        reply += "\n\nEMERGENCY LINE: (555) DENTIST"
 
    await update.message.reply_text(reply)
 
 
# ── Application builder ────────────────────────────────────────────────────
 
def build_telegram_app() -> Application:
    """
    FIX 2: Added explicit timeout values to the Application builder.
    Default 5s timeouts cause ConnectTimeout on slow networks. 30s is safe.
    """
    return (
        Application.builder()
        .token(TOKEN)
        .connect_timeout(30.0)       # time to open TCP connection
        .read_timeout(30.0)          # time to wait for server response
        .write_timeout(30.0)         # time to send data
        .pool_timeout(30.0)          # time to wait for connection from pool
        .build()
    )
 
 
# ── FastAPI app with lifespan ──────────────────────────────────────────────
 
_tg_app: Application | None = None
 
 
# FIX 1: Replace @app.on_event("startup") / ("shutdown") with lifespan.
# The asynccontextmanager pattern is the FastAPI-recommended approach
# since v0.93. Code before yield = startup, code after yield = shutdown.
@asynccontextmanager
async def lifespan(app: FastAPI):
    global _tg_app
    _tg_app = build_telegram_app()
 
    # Add all handlers
    _tg_app.add_handler(CommandHandler("start",     cmd_start))
    _tg_app.add_handler(CommandHandler("help",      cmd_help))
    _tg_app.add_handler(CommandHandler("emergency", cmd_emergency))
    _tg_app.add_handler(CommandHandler("reset",     cmd_reset))
    _tg_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
 
    await _tg_app.initialize()
    await _tg_app.start()
    log.info("Telegram application initialised")
 
    yield   # ← app runs while we await here
 
    # Shutdown
    if _tg_app:
        await _tg_app.stop()
        await _tg_app.shutdown()
    log.info("Telegram application stopped")
 
 
app = FastAPI(
    title="DentAI Pro — Telegram Bot",
    version="1.0.0",
    lifespan=lifespan,          # FIX 1: pass lifespan here
)
 
 
# ── Webhook endpoint ───────────────────────────────────────────────────────
 
@app.post("/webhook/telegram")
async def telegram_webhook(
    request: Request,
    x_telegram_bot_api_secret_token: str = Header(default=""),
):
    if WEBHOOK_SECRET and x_telegram_bot_api_secret_token != WEBHOOK_SECRET:
        raise HTTPException(status_code=403, detail="Invalid secret token")
 
    data   = await request.json()
    update = Update.de_json(data, _tg_app.bot)
    await _tg_app.process_update(update)
    return {"ok": True}
 
 
@app.get("/health")
def health():
    return {"status": "ok", "service": "dentai-telegram"}
 
 
# ── Polling mode (local dev) ───────────────────────────────────────────────
 
def run_polling() -> None:
    """
    Long-polling mode for local development.
    No public URL or ngrok needed.
    FIX: Also applies the timeout fix in polling mode.
    """
    print("\nTelegram bot starting in POLLING mode...")
    print("Find your bot in Telegram and send /start")
    print("Press Ctrl+C to stop.\n")
 
    tg_app = build_telegram_app()
    tg_app.add_handler(CommandHandler("start",     cmd_start))
    tg_app.add_handler(CommandHandler("help",      cmd_help))
    tg_app.add_handler(CommandHandler("emergency", cmd_emergency))
    tg_app.add_handler(CommandHandler("reset",     cmd_reset))
    tg_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    tg_app.run_polling(allowed_updates=Update.ALL_TYPES)
 
 
# ── Entry point ────────────────────────────────────────────────────────────
 
if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--polling", action="store_true",
                        help="Run in polling mode (local dev, no ngrok needed)")
    args = parser.parse_args()
 
    if args.polling:
        run_polling()
    else:
        import uvicorn
        print("\nTelegram webhook server starting on http://localhost:8002")
        uvicorn.run("integrations.telegram_bot:app",
                    host="0.0.0.0", port=8002, reload=True)
 