"""
integrations/whatsapp_bot.py — WhatsApp Integration
=====================================================
Uses Twilio's WhatsApp Business API + FastAPI webhook.
The same dental_agent.run_agent() call powers this channel.

SETUP (free Twilio sandbox):
    1. Sign up at twilio.com — free account, no credit card
    2. Go to Messaging → Try it out → Send a WhatsApp message
    3. Follow sandbox join instructions (text "join <word>" to +1 415 523 8886)
    4. Set Webhook URL to: https://YOUR-APP.onrender.com/webhook/whatsapp
    5. Add to .env:
           TWILIO_ACCOUNT_SID=ACxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
           TWILIO_AUTH_TOKEN=your_auth_token
           TWILIO_WHATSAPP_NUMBER=whatsapp:+14155238886

RUN LOCALLY (with ngrok for webhook):
    pip install fastapi uvicorn twilio
    ngrok http 8000
    # set ngrok URL as webhook in Twilio console
    uvicorn integrations.whatsapp_bot:app --reload

DEPLOY ON RENDER:
    Add a second web service pointing to this file (see render.yaml)
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI, Form, HTTPException, Request, Response
from fastapi.responses import PlainTextResponse
from twilio.rest import Client as TwilioClient
from twilio.twiml.messaging_response import MessagingResponse

from scripts.dental_agent import run_agent

log = logging.getLogger("dentai.whatsapp")

# ── Twilio config ──────────────────────────────────────────
ACCOUNT_SID    = os.environ.get("TWILIO_ACCOUNT_SID", "")
AUTH_TOKEN     = os.environ.get("TWILIO_AUTH_TOKEN", "")
WA_NUMBER      = os.environ.get("TWILIO_WHATSAPP_NUMBER", "whatsapp:+14155238886")
twilio_client  = TwilioClient(ACCOUNT_SID, AUTH_TOKEN) if ACCOUNT_SID else None

# ── FastAPI app ────────────────────────────────────────────
app = FastAPI(title="DentAI Pro — WhatsApp Bot", version="1.0.0")


# ── Signature validation ───────────────────────────────────

def _validate_twilio_signature(request_url: str, post_data: dict, signature: str) -> bool:
    """
    Verify the request genuinely comes from Twilio.
    Prevents spoofed webhook calls.
    https://www.twilio.com/docs/usage/webhooks/webhooks-security
    """
    if not AUTH_TOKEN:
        return True  # Skip in dev mode

    s = request_url + "".join(f"{k}{v}" for k, v in sorted(post_data.items()))
    expected = hmac.new(AUTH_TOKEN.encode(), s.encode(), hashlib.sha1).digest()
    import base64
    return hmac.compare_digest(base64.b64encode(expected).decode(), signature)


# ── Session store (in-memory — use Redis in prod) ──────────
# Maps phone_number → session_id for per-user conversation tracking
_sessions: dict[str, str] = {}


def _get_session(phone: str) -> str:
    if phone not in _sessions:
        import uuid
        _sessions[phone] = f"wa-{uuid.uuid4().hex[:12]}"
    return _sessions[phone]


# ── Helpers ────────────────────────────────────────────────

def _format_for_whatsapp(text: str) -> str:
    """
    WhatsApp supports basic markdown:
      *bold*  _italic_  ~strikethrough~  ```code```
    Trim overly long responses so they fit comfortably.
    """
    # Keep under WhatsApp's 4096-char limit
    if len(text) > 3800:
        text = text[:3800] + "\n\n_(Message truncated. Please ask a follow-up question.)_"
    return text


# ── Webhook endpoint ───────────────────────────────────────

@app.post("/webhook/whatsapp")
async def whatsapp_webhook(
    request: Request,
    From: str = Form(...),          # whatsapp:+2348012345678
    Body: str = Form(...),          # Patient's message
    MessageSid: str = Form(default=""),
):
    """
    Twilio sends a POST here whenever a patient messages your WhatsApp number.
    Responds with TwiML (XML) that Twilio converts back to a WhatsApp message.
    """
    # Signature validation in production
    sig = request.headers.get("X-Twilio-Signature", "")
    form_data = dict(await request.form())
    url = str(request.url)
    if ACCOUNT_SID and not _validate_twilio_signature(url, form_data, sig):
        raise HTTPException(status_code=403, detail="Invalid Twilio signature")

    phone      = From.replace("whatsapp:", "")
    session_id = _get_session(phone)
    message    = Body.strip()

    log.info("WhatsApp | %s | %s", phone[-4:], message[:60])

    # Run through the dental agent
    result = run_agent(message, session_id=session_id, channel="whatsapp")
    reply  = _format_for_whatsapp(result["reply"])

    if result["is_emergency"]:
        reply += "\n\n*EMERGENCY LINE: (555) DENTIST*"

    # Return TwiML response
    twiml = MessagingResponse()
    twiml.message(reply)
    return Response(content=str(twiml), media_type="application/xml")


# ── Health check ───────────────────────────────────────────

@app.get("/health")
def health():
    return {"status": "ok", "service": "dentai-whatsapp"}


# ── Proactive message (optional) ───────────────────────────

def send_whatsapp_message(to_phone: str, message: str) -> str:
    """
    Send a proactive WhatsApp message (e.g., appointment reminder).
    Requires an approved Twilio message template for production.

    Usage:
        from integrations.whatsapp_bot import send_whatsapp_message
        send_whatsapp_message("+2348012345678", "Reminder: your appointment is tomorrow at 9am.")
    """
    if not twilio_client:
        raise EnvironmentError("Twilio not configured — check TWILIO_* env vars")

    msg = twilio_client.messages.create(
        from_=WA_NUMBER,
        to=f"whatsapp:{to_phone}",
        body=message,
    )
    log.info("Proactive WhatsApp sent to %s | SID: %s", to_phone[-4:], msg.sid)
    return msg.sid


# ── Local dev entry point ──────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    print("\nWhatsApp bot starting on http://localhost:8001")
    print("Expose with: ngrok http 8001")
    print("Then set webhook URL in Twilio console.\n")
    uvicorn.run("integrations.whatsapp_bot:app", host="0.0.0.0", port=8001, reload=True)
