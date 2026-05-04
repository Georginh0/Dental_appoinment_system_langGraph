"""
02_csv_to_supabase.py — CSV Importer for Supabase (PostgreSQL)
==============================================================
Reads doctor_availability.csv → Supabase PostgreSQL.

HOW TO RUN:
    conda activate dentai
    python scripts/02_csv_to_supabase.py

PREREQUISITES:
    1. DATABASE_URL in .env (Supabase connection string)
    2. Run 01_supabase_setup.sql in Supabase SQL Editor first
    3. CSV at: data/doctor_availability.csv
"""

from __future__ import annotations

import csv
import os
import random
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
load_dotenv()

from scripts.db_connection import DBManager, test_connection

# ── Config ─────────────────────────────────────────────────
CSV_PATH = Path(__file__).resolve().parent.parent / "data" / "doctor_availability.csv"

# Sentinel value used in the CSV to mean "slot is free — no patient assigned".
# Must be converted to NULL before inserting; 999999 does not exist in patients.
SENTINEL_PATIENT_ID = 999999

FIRST_NAMES = [
    "Amara", "James", "Fatima", "David", "Chioma", "Michael",
    "Ngozi", "Robert", "Adaeze", "William", "Kemi", "Joseph",
    "Blessing", "Charles", "Ifeoma", "Thomas", "Nneka", "Daniel",
    "Chiamaka", "Matthew", "Oluchi", "Anthony", "Ebele", "Mark",
]
LAST_NAMES = [
    "Okafor", "Smith", "Nwosu", "Johnson", "Eze", "Williams",
    "Obiora", "Brown", "Chukwu", "Jones", "Emeka", "Garcia",
    "Nnamdi", "Miller", "Onyeka", "Davis", "Chidi", "Rodriguez",
]
INSURANCES = [
    "BlueCross BlueShield", "Aetna", "Cigna", "Delta Dental",
    "MetLife", "Guardian", "United Healthcare", "No Insurance (Self-Pay)",
]
REASONS = [
    "Routine cleaning", "Filling", "Crown fitting", "Root canal",
    "Teeth whitening", "Braces consultation", "Emergency pain",
    "Extraction", "Implant checkup", "New patient exam", "Follow-up",
]


def patient_name(pid: int) -> tuple[str, str]:
    random.seed(pid)
    return (
        FIRST_NAMES[pid % len(FIRST_NAMES)],
        LAST_NAMES[(pid * 7 + 3) % len(LAST_NAMES)],
    )


def patient_email(first: str, last: str, pid: int) -> str:
    domains = ["gmail.com", "yahoo.com", "outlook.com", "hotmail.com"]
    return f"{first.lower()}.{last.lower()}{pid % 100}@{domains[pid % 4]}"


def patient_dob(pid: int) -> date:
    random.seed(pid + 42)
    return date.today() - timedelta(days=random.randint(18, 75) * 365)


def _parse_patient_id(raw: str) -> int | None:
    """Convert a raw CSV patient_to_attend value to int or None.

    Returns None for:
    - Empty / whitespace strings  (no patient assigned in source)
    - The sentinel value 999999   (placeholder meaning "free slot")

    Args:
        raw: Raw string value from the CSV cell.

    Returns:
        Integer patient ID, or None if the slot is free.
    """
    stripped = raw.strip()
    if not stripped:
        return None
    pid = int(stripped)
    return None if pid == SENTINEL_PATIENT_ID else pid


# ── Pass 1: read CSV and pre-seed patients ──────────────────
# Must run BEFORE import_availability because doctor_availability.patient_to_attend
# is a FK to patients. We cannot insert availability rows that reference patient IDs
# that don't exist yet.

def seed_patients_from_csv() -> int:
    """Read the CSV, collect unique real patient IDs, insert into patients.

    Args: None — reads directly from CSV_PATH.

    Returns:
        Number of patient rows inserted.
    """
    if not CSV_PATH.exists():
        return 0

    unique_pids: set[int] = set()
    with open(CSV_PATH, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            pid = _parse_patient_id(row["patient_to_attend"])
            if pid is not None:
                unique_pids.add(pid)

    print(f"  Found {len(unique_pids)} unique patient IDs in CSV")

    sql = """
        INSERT INTO patients
            (patient_id, first_name, last_name, email, phone, date_of_birth, insurance)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (patient_id) DO NOTHING
    """
    batch = []
    for pid in sorted(unique_pids):
        first, last = patient_name(pid)
        batch.append((
            pid, first, last,
            patient_email(first, last, pid),
            f"+1-555-{pid % 900 + 100:03d}-{pid % 9000 + 1000:04d}",
            patient_dob(pid).isoformat(),
            INSURANCES[pid % len(INSURANCES)],
        ))

    with DBManager() as db:
        db.executemany(sql, batch)

    return len(batch)


# ── Pass 2: import availability CSV ────────────────────────
# Patients now exist → FK constraint will not fire.

def import_availability() -> tuple[int, int]:
    """Import doctor_availability CSV into Supabase.

    Sentinel patient IDs (999999) and empty values are stored as NULL,
    representing free / unbooked slots.

    Returns:
        Tuple of (rows_inserted, rows_skipped).
    """
    if not CSV_PATH.exists():
        print(f"  CSV not found: {CSV_PATH}")
        print("  Place doctor_availability.csv inside the data/ folder.")
        return 0, 0

    sql = """
        INSERT INTO doctor_availability
            (date_slot, specialization, doctor_name, is_available, patient_to_attend)
        VALUES (%s, %s, %s, %s, %s)
        ON CONFLICT (doctor_name, date_slot) DO NOTHING
    """
    inserted = skipped = 0
    batch: list[tuple] = []

    with open(CSV_PATH, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            # FIX: use _parse_patient_id — converts 999999 and "" to None
            pid = _parse_patient_id(row["patient_to_attend"])
            batch.append((
                row["date_slot"].strip(),
                row["specialization"].strip(),
                row["doctor_name"].strip(),
                row["is_available"].strip().upper() == "TRUE",
                pid,  # None for free slots, real int for booked slots
            ))

            if len(batch) >= 500:
                with DBManager() as db:
                    db.executemany(sql, batch)
                inserted += len(batch)
                print(f"  Inserted {inserted} rows...")
                batch = []

    if batch:
        with DBManager() as db:
            db.executemany(sql, batch)
        inserted += len(batch)

    return inserted, skipped


# ── Seed patients (post-import fallback — kept for compatibility) ───────

def seed_patients() -> int:
    """Read unique patient IDs from doctor_availability and upsert into patients.

    This is a fallback / top-up pass. The primary seeding is done by
    seed_patients_from_csv() before availability rows are imported.

    Returns:
        Number of patient rows inserted.
    """
    with DBManager() as db:
        rows = db.query(
            "SELECT DISTINCT patient_to_attend AS pid "
            "FROM doctor_availability WHERE patient_to_attend IS NOT NULL "
            "ORDER BY patient_to_attend"
        )
    pids = [r["pid"] for r in rows]
    print(f"  Found {len(pids)} unique patient IDs in DB")

    sql = """
        INSERT INTO patients (patient_id, first_name, last_name, email, phone, date_of_birth, insurance)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (patient_id) DO NOTHING
    """
    batch = []
    for pid in pids:
        first, last = patient_name(pid)
        batch.append((
            pid, first, last,
            patient_email(first, last, pid),
            f"+1-555-{pid % 900 + 100:03d}-{pid % 9000 + 1000:04d}",
            patient_dob(pid).isoformat(),
            INSURANCES[pid % len(INSURANCES)],
        ))

    with DBManager() as db:
        db.executemany(sql, batch)

    return len(batch)


# ── Seed appointments from booked slots ─────────────────────

def seed_appointments() -> int:
    """Create appointment records from booked availability slots.

    Returns:
        Number of appointment rows inserted.
    """
    with DBManager() as db:
        booked = db.query(
            "SELECT date_slot, doctor_name, specialization, patient_to_attend "
            "FROM doctor_availability "
            "WHERE is_available = FALSE AND patient_to_attend IS NOT NULL "
            "ORDER BY date_slot"
        )

    sql = """
        INSERT INTO appointments
            (patient_id, doctor_name, specialization, appointment_dt, status, reason, confirmation_code)
        VALUES (%s, %s, %s, %s, 'scheduled', %s, %s)
        ON CONFLICT (confirmation_code) DO NOTHING
    """
    batch = []
    for slot in booked:
        pid    = slot["patient_to_attend"]
        reason = REASONS[pid % len(REASONS)]
        ts     = str(slot["date_slot"])
        code   = f"D{pid:07d}-{ts[5:7]}{ts[8:10]}{ts[11:13]}{ts[14:16]}"
        batch.append((pid, slot["doctor_name"], slot["specialization"], slot["date_slot"], reason, code))

    if batch:
        with DBManager() as db:
            db.executemany(sql, batch)

    return len(batch)


# ── Verification ────────────────────────────────────────────

def verify() -> None:
    print("\n" + "=" * 52)
    print("  SUPABASE IMPORT SUMMARY")
    print("=" * 52)
    queries = {
        "Total availability rows": "SELECT COUNT(*) AS n FROM doctor_availability",
        "Available slots":         "SELECT COUNT(*) AS n FROM doctor_availability WHERE is_available = TRUE",
        "Booked slots":            "SELECT COUNT(*) AS n FROM doctor_availability WHERE is_available = FALSE",
        "Total patients":          "SELECT COUNT(*) AS n FROM patients",
        "Total appointments":      "SELECT COUNT(*) AS n FROM appointments",
        "Total doctors":           "SELECT COUNT(*) AS n FROM doctors",
    }
    with DBManager() as db:
        for label, q in queries.items():
            row = db.query_one(q)
            print(f"  {label:<26} {row['n']:>8,}")
        print("\n  Slots by specialization:")
        specs = db.query(
            "SELECT specialization, COUNT(*) AS total, "
            "SUM(CASE WHEN is_available THEN 1 ELSE 0 END) AS avail "
            "FROM doctor_availability GROUP BY specialization ORDER BY specialization"
        )
        for s in specs:
            print(f"    {s['specialization']:<22} {s['total']:>5} total | {s['avail']:>5} available")
    print("=" * 52)


# ── Entry point ─────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 52)
    print("  DentAI Pro — CSV → Supabase Importer")
    print("=" * 52)

    print("\nTesting connection...")
    if not test_connection():
        sys.exit(1)

    # ── FIXED ORDER ─────────────────────────────────────────
    # Old order (broken): availability → patients → appointments
    #   ↳ Fails: availability FK to patients fires before patients exist
    #
    # New order (correct): patients (from CSV) → availability → appointments
    #   ↳ Patients seeded first via CSV read; FK constraint satisfied on insert

    print("\nSeeding patient profiles from CSV...")
    n = seed_patients_from_csv()
    print(f"  Created {n} patients")

    print("\nImporting availability CSV...")
    ins, _ = import_availability()
    print(f"  Done: {ins:,} rows inserted")

    print("\nCreating appointment records...")
    n = seed_appointments()
    print(f"  Created {n} appointments")

    verify()
    print("\nAll done! Run: streamlit run streamlit_app.py")