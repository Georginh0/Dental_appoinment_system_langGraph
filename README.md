# 🦷 DentAI Pro v2 — Intelligent Dental Appointment System

> **Production-grade multi-agent AI system for dental clinics**  
> Built with LangGraph · GPT-4o · MySQL · Streamlit  
> Real data: 4,280 slots · 10 doctors · 7 specializations · 107 patients


---

## What Is DentAI Pro?

A 24/7 conversational AI agent that acts as the complete front desk of a dental clinic — booking appointments, handling cancellations, rescheduling, routing patients to the right specialist, and responding to emergencies. All powered by real MySQL data imported from your clinic's scheduling CSV.

---

## Architecture

```
Patient Message
      ↓
 [TRIAGE NODE]  ← classifies intent, detects emergency
      │
      ├── emergency  →  [Emergency Node]  → [Tools] ⟳
      ├── booking    →  [Booking Node]    → [Tools] ⟳
      ├── cancel     →  [Cancel Node]     → [Tools] ⟳
      ├── reschedule →  [Reschedule Node] → [Tools] ⟳
      ├── history    →  [Patient History] → [Tools] ⟳
      ├── doctor_info→  [Doctor Info]     → [Tools] ⟳
      └── general    →  [General Node]    → END
                                ↓
                         [MySQL Database]
```

### The 7 Tools (Connected to MySQL)

| Tool | What it Does | DB Operation |
|------|-------------|--------------|
| `get_availability` | Available slots by date/spec/doctor | SELECT from `doctor_availability` |
| `get_patient_appointments` | Patient history and profile | SELECT from `appointments` + `patients` |
| `check_slot_available` | Single slot availability check | SELECT with row-lock |
| `list_doctors_by_specialization` | Doctors list with open slot counts | JOIN `doctors` + `doctor_availability` |
| `booking_agent` | Full atomic appointment booking | UPDATE + INSERT with transaction |
| `cancellation_agent` | Cancel with slot release | UPDATE appointments + availability |
| `rescheduling_agent` | Atomic old→new slot swap | 2× UPDATE in single transaction |

---

## Your Data at a Glance

| Metric | Value |
|--------|-------|
| Total schedule rows | 4,280 |
| Available slots | 2,710 (63%) |
| Booked slots | 1,570 (37%) |
| Specializations | 7 |
| Doctors | 10 |
| Unique patients | 107 |

**Specializations:** general dentist · cosmetic dentist · orthodontist · pediatric dentist · prosthodontist · oral surgeon · emergency dentist

**Doctors:** John Doe · Emily Johnson · Jane Smith · Lisa Brown · Kevin Anderson · Sarah Wilson · Michael Green · Robert Martinez · Daniel Miller · Susan Davis

---

## Setup — Step by Step (VS Code + Conda)
```

### Step 1: Create Conda Environment
```bash
# In VS Code terminal (Ctrl+`)
conda create -n dentai python=3.11 -y
conda activate dentai
pip install -r requirements.txt
```

### Step 2: Select Python Interpreter in VS Code
`Ctrl+Shift+P` → "Python: Select Interpreter" → choose `dentai`

### Step 3: Set Up MySQL
```bash
# Install MySQL if not already installed:
# Windows: Download MySQL Installer from mysql.com
# Mac:     brew install mysql && brew services start mysql
# Ubuntu:  sudo apt install mysql-server && sudo service mysql start

# Create the database:
mysql -u root -p < scripts/01_mysql_setup.sql
```

### Step 4: Configure Environment Variables
Create a `.env` file in the project root:
```env
OPENAI_API_KEY=sk-your-openai-key-here
MYSQL_HOST=localhost
MYSQL_PORT=3306
MYSQL_USER=root
MYSQL_PASSWORD=your_mysql_password
MYSQL_DATABASE=dentai_pro
```

### Step 5: Import Your CSV Data
```bash
# Copy your CSV to the data folder first:
mkdir data
cp /path/to/doctor_availability.csv data/

# Run the importer:
python scripts/02_csv_to_mysql.py
```

Expected output:
```
✅ Connected to MySQL: localhost:3306/dentai_pro
✔ Imported 4,280 rows...
✔ Created 107 patient records
✔ Created 1,570 appointment records
Total availability rows:     4,280
Available slots:             2,710
Booked slots:                1,570
Total patients:                107
Total appointments:          1,570
Total doctors:                  10
```

### Step 6: Run the Agent

```bash
# CLI mode (for testing and development)
python scripts/03_dental_agent.py

# Streamlit web app (for demo and production)
streamlit run scripts/03_dental_agent.py
```

---

## Project Structure

```
dentai-pro-v2/
├── data/
│   └── doctor_availability.csv      # Your scheduling data
├── scripts/
│   ├── 01_mysql_setup.sql           # Database schema + views + stored procedure
│   ├── 02_csv_to_mysql.py           # CSV importer + patient seeder
│   ├── 03_dental_agent.py           # Main LangGraph agent (all 7 tools)
│   └── db_connection.py             # MySQL connection manager
├── docs/
│   ├── GUIDE.md                     # Feynman-style learning guide
│   ├── MEDIUM_BLOG.md               # Medium article
│   └── STAKEHOLDER_REPORT.md        # Business case for investors/clinic owners
├── tests/
│   └── test_tools.py                # Unit tests for all 7 tools
├── .env                             # Your secrets (never commit this!)
├── .gitignore
├── requirements.txt
└── README.md
```

---

## MySQL Schema

```sql
dentai_pro
├── doctors              # 10 doctor profiles + specializations
├── patients             # 107 patient profiles
├── doctor_availability  # 4,280 schedule slots (your CSV)
├── appointments         # Booking records (derived from availability)
└── conversation_sessions # AI chat session analytics

Views:
├── v_available_slots    # All future open slots
├── v_doctor_schedule    # Doctor timetable with patient names
└── v_patient_appointments # Patient history view

Stored Procedure:
└── sp_book_appointment  # Atomic booking with race condition protection
```

---

## Example Conversations

### Booking
```
Patient: I need to get my braces checked — I'm free next week
DentAI:  Great! Braces are handled by our orthodontist. We have Kevin Anderson 
         available — 15 years of experience with Invisalign and traditional braces.
         What date works best for you next week?
```

### Emergency
```
Patient: I have severe pain and my jaw is swelling
DentAI:  I can hear that you're in real pain and I'm concerned about you. Jaw 
         swelling with severe pain can indicate an abscess — this needs to be seen 
         TODAY. Our emergency dentist Dr. Daniel Miller has openings this morning.
         Shall I book you in for 9:00am?
```

### Reschedule
```
Patient: I need to move my appointment, my confirmation is DENT-0048-AB3X7K
DentAI:  I found your appointment with Dr. John Doe on July 8th at 9am. 
         What date would you like to move it to?
```

---

## Testing

```bash
pytest tests/test_tools.py -v
```

---

## Production Upgrade Path

| Component | Now | Production |
|-----------|-----|------------|
| Database | MySQL local | MySQL on RDS / PlanetScale |
| Knowledge base | Inline prompts | ChromaDB + embeddings |
| API | None | FastAPI REST endpoints |
| Auth | None | OAuth2 + JWT |
| Notifications | None | Twilio SMS |
| Monitoring | Log file | LangSmith traces |
| HIPAA | Not certified | BAA + encryption + audit |

---

*Built with domain expertise in dental operations and production AI engineering.*
