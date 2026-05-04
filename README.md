# 🦷 DentAI Pro — Production-Grade Dental Appointment System

> Multi-agent AI that runs your dental clinic's front desk — 24/7, across Web, WhatsApp, and Telegram.

[![Python 3.11](https://img.shields.io/badge/python-3.11-blue.svg)](https://python.org)
[![LangGraph](https://img.shields.io/badge/LangGraph-0.2+-green.svg)](https://github.com/langchain-ai/langgraph)
[![Groq](https://img.shields.io/badge/LLM-Groq%20(free)-orange.svg)](https://console.groq.com)
[![Supabase](https://img.shields.io/badge/DB-Supabase-3ecf8e.svg)](https://supabase.com)
[![Deploy on Render](https://img.shields.io/badge/deploy-Render.com-46e3b7.svg)](https://render.com)

---

## What It Does

DentAI Pro handles the 70% of dental clinic interactions that don't require a human: scheduling, cancellations, rescheduling, specialist discovery, and emergency triage — through **three channels simultaneously**.

| Channel | How |
|---------|-----|
| **Web** | Streamlit UI, deployed on Render.com |
| **WhatsApp** | Twilio Business API, FastAPI webhook |
| **Telegram** | BotFather bot, FastAPI webhook |

All three channels share the same **7 LangGraph tools** and **Supabase database**. A patient can book on WhatsApp and their appointment immediately shows up in the web dashboard.

---

## Architecture

```
Patient (Web / WhatsApp / Telegram)
             │
   ┌─────────▼─────────┐
   │   run_agent()     │  ← single entry point for all channels
   │   dental_agent.py │
   └─────────┬─────────┘
             │
   ┌─────────▼─────────────────────────────────┐
   │              LangGraph Graph               │
   │                                           │
   │  [Triage] → [Emergency]                   │
   │           → [Booking]    → [Tools] ⟳      │
   │           → [Cancel]     → [Tools] ⟳      │
   │           → [Reschedule] → [Tools] ⟳      │
   │           → [History]    → [Tools] ⟳      │
   │           → [Doctor Info]→ [Tools] ⟳      │
   │           → [General]   → END             │
   └─────────┬─────────────────────────────────┘
             │
   ┌─────────▼─────────┐
   │  Supabase (PostgreSQL)  │  ← all 7 tools query here
   └───────────────────┘
```

### 7 Tools Connected to Supabase

| Tool | What it does |
|------|-------------|
| `get_availability` | Open slots by date / specialization / doctor |
| `get_patient_appointments` | Full patient history + profile |
| `check_slot_available` | Single slot check before booking |
| `list_doctors_by_specialization` | Doctors with 30-day open counts |
| `booking_agent` | Atomic booking + confirmation code |
| `cancellation_agent` | Cancel + free slot (identity-verified) |
| `rescheduling_agent` | Atomic old→new slot swap |

---

## Tech Stack

| Component | Choice | Why |
|-----------|--------|-----|
| LLM | Groq `llama-3.3-70b-versatile` | Free, ~300 tok/s, tool-calling capable |
| Agent framework | LangGraph 0.2 | Stateful multi-agent graphs |
| Database | Supabase (PostgreSQL) | Free tier, serverless, global |
| Web UI | Streamlit | Fast to build, easy to deploy |
| WhatsApp | Twilio sandbox | Free for dev, full WhatsApp Business for prod |
| Telegram | python-telegram-bot | Clean async API, webhook support |
| Deployment | Render.com | Free tier, GitHub-integrated CI/CD |

---

## Setup in 15 Minutes

### Step 1 — Clone and environment

```bash
git clone https://github.com/Georginh0/Dental_appoinment_system_langGraph.git
cd Dental_appoinment_system_langGraph

conda create -n dentai python=3.11 -y
conda activate dentai
pip install -r requirements.txt
```

### Step 2 — Get your free API keys

| Service | Where to get it | Time |
|---------|----------------|------|
| **Groq** (LLM) | [console.groq.com](https://console.groq.com) → API Keys | 2 min |
| **Supabase** (DB) | [supabase.com](https://supabase.com) → New Project | 3 min |
| **Twilio** (WhatsApp) | [twilio.com](https://twilio.com) → Console | 3 min |
| **Telegram** (Bot) | Telegram → @BotFather → /newbot | 2 min |

### Step 3 — Configure environment

```bash
cp .env.example .env
# Edit .env with your keys
```

### Step 4 — Set up Supabase database

1. Go to [supabase.com](https://supabase.com) → your project → SQL Editor
2. Paste the contents of `scripts/01_supabase_setup.sql`
3. Click Run All
4. Get your connection string: Project Settings → Database → Connection string (Transaction mode, port 6543)
5. Paste it as `DATABASE_URL` in `.env`

### Step 5 — Import your data

```bash
# Place your CSV at: data/doctor_availability.csv
python scripts/02_csv_to_supabase.py
```

Expected output:
```
Testing connection...
  Connected | PostgreSQL 15.x | DB: postgres
  Tables: appointments, conversation_sessions, doctor_availability, doctors, patients

Importing availability CSV...
  Inserted 4,280 rows...

Seeding patient profiles...
  Created 107 patients

Creating appointment records...
  Created 1,570 appointments

Total availability rows:          4,280
Available slots:                  2,710
Booked slots:                     1,570
```

### Step 6 — Run locally

```bash
# Web UI
streamlit run streamlit_app.py

# WhatsApp webhook (expose with ngrok in another terminal)
ngrok http 8001
uvicorn integrations.whatsapp_bot:app --port 8001 --reload

# Telegram (polling mode — no ngrok needed locally)
python integrations/telegram_bot.py --polling

# Core agent CLI (for testing)
python scripts/dental_agent.py
```

---

## Deploy to Render.com

### One-click blueprint deployment

1. Push your repo to GitHub (with `.env` in `.gitignore`)
2. Go to [render.com](https://render.com) → **New** → **Blueprint**
3. Connect your GitHub repository
4. Render reads `render.yaml` and creates all 3 services automatically
5. For each service, add your environment variables in the Render dashboard

### Set Telegram webhook after deploy

```bash
curl -X POST "https://api.telegram.org/bot{YOUR_TOKEN}/setWebhook" \
     -d "url=https://dentai-telegram.onrender.com/webhook/telegram" \
     -d "secret_token={YOUR_TELEGRAM_WEBHOOK_SECRET}"
```

### Set WhatsApp webhook in Twilio console

Go to: Twilio Console → Messaging → WhatsApp → Sandbox → Webhook URL
```
https://dentai-whatsapp.onrender.com/webhook/whatsapp
```

---

## Project Structure

```
.
├── streamlit_app.py            ← Web UI entry point (Render deploys this)
├── render.yaml                 ← Render.com blueprint (3 services)
├── requirements.txt
├── .env.example                ← Copy to .env, fill in keys
├── .gitignore
│
├── scripts/
│   ├── dental_agent.py         ← Core agent: 7 tools + LangGraph graph
│   ├── db_connection.py        ← Supabase/PostgreSQL connection manager
│   ├── 01_supabase_setup.sql   ← Run once in Supabase SQL Editor
│   └── 02_csv_to_supabase.py   ← CSV importer
│
├── integrations/
│   ├── whatsapp_bot.py         ← Twilio WhatsApp webhook (FastAPI)
│   └── telegram_bot.py         ← Telegram bot (FastAPI + python-telegram-bot)
│
├── data/
│   └── doctor_availability.csv ← Your scheduling data
│
└── docs/
    └── GUIDE.md                ← Full learning guide
```

---

## Testing the Agent

After setup, test each intent:

```bash
# In your CLI (python scripts/dental_agent.py)

You: book a cleaning next tuesday
You: i have severe tooth pain and my jaw is swelling
You: cancel my appointment DENT-0048-AB1234
You: which orthodontists do you have?
You: show my appointment history, my ID is 1000048
You: reschedule my appointment to friday
```

---

## Upgrade Path

| Feature | Current | Next |
|---------|---------|------|
| LLM cost | Free (Groq) | Same — no change needed |
| Database | Supabase free | Supabase Pro ($25/mo) for > 500MB |
| Deployment | Render free | Render Starter ($7/mo, always-on) |
| WhatsApp | Twilio sandbox | WhatsApp Business API ($0.005/msg) |
| Observability | Log file | LangSmith (free tier available) |
| HIPAA | Not certified | BAA with Groq + encryption audit |

---

## License

MIT — use it, modify it, deploy it. Attribution appreciated.

---

*Built with [LangGraph](https://github.com/langchain-ai/langgraph) · [Groq](https://console.groq.com) · [Supabase](https://supabase.com)*
