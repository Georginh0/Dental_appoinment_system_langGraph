"""
============================================================
  Script 02 — CSV to MySQL Importer
  DentAI Pro | VS Code + Conda

  WHAT THIS DOES:
  Reads your doctor_availability.csv and loads it into
  the MySQL dentai_pro database.

  HOW TO RUN:
    conda activate dentai
    python scripts/02_csv_to_mysql.py

  PREREQUISITES:
  1. MySQL running locally (XAMPP, MySQL Workbench, or standalone)
  2. Run scripts/01_mysql_setup.sql first
  3. Set your MySQL password below (or use .env file)
============================================================
"""

import os
import csv
import sys
import random
import string
from datetime import datetime, date, timedelta
from pathlib import Path
import mysql.connector
from mysql.connector import Error
from dotenv import load_dotenv

load_dotenv()

# ── MySQL Connection Config ────────────────────────────────
# Either set these in your .env file or edit directly here
DB_CONFIG = {
    "host": os.getenv("MYSQL_HOST", "localhost"),
    "port": int(os.getenv("MYSQL_PORT", "3306")),
    "user": os.getenv("MYSQL_USER", "root"),
    "password": os.getenv("MYSQL_PASSWORD", ""),  # ← Set your MySQL password here
    "database": os.getenv("MYSQL_DATABASE", "dentai_pro"),
    "charset": "utf8mb4",
}

# Path to your CSV file — update if needed
CSV_PATH = Path(__file__).parent.parent / "data" / "doctor_availability.csv"


# ── Helper functions ───────────────────────────────────────


def get_connection() -> mysql.connector.MySQLConnection:
    """Create and return a MySQL connection."""
    try:
        conn = mysql.connector.connect(**DB_CONFIG)
        print(
            f"✅ Connected to MySQL: {DB_CONFIG['host']}:{DB_CONFIG['port']}/{DB_CONFIG['database']}"
        )
        return conn
    except Error as e:
        print(f"❌ MySQL connection failed: {e}")
        print("\n📋 Troubleshooting checklist:")
        print("  1. Is MySQL running? (Check XAMPP / MySQL Workbench / Services)")
        print("  2. Is your password correct in .env or in DB_CONFIG above?")
        print("  3. Did you run scripts/01_mysql_setup.sql first?")
        sys.exit(1)


def parse_bool(val: str) -> bool:
    """Convert 'TRUE'/'FALSE' strings to Python bool."""
    return val.strip().upper() == "TRUE"


def parse_patient_id(val: str) -> int | None:
    """Parse patient ID, return None if empty."""
    v = val.strip()
    if not v:
        return None
    try:
        return int(v)
    except ValueError:
        return None


def generate_patient_name(patient_id: int) -> tuple[str, str]:
    """
    Generate consistent synthetic names for patient IDs.
    In production, this would come from your patient registration system.
    """
    first_names = [
        "Amara",
        "James",
        "Fatima",
        "David",
        "Chioma",
        "Michael",
        "Ngozi",
        "Robert",
        "Adaeze",
        "William",
        "Kemi",
        "Joseph",
        "Blessing",
        "Charles",
        "Ifeoma",
        "Thomas",
        "Nneka",
        "Daniel",
        "Chiamaka",
        "Matthew",
        "Oluchi",
        "Anthony",
        "Ebele",
        "Mark",
        "Obiageli",
        "Donald",
        "Chinyere",
        "Paul",
        "Adaobi",
        "George",
    ]
    last_names = [
        "Okafor",
        "Smith",
        "Nwosu",
        "Johnson",
        "Eze",
        "Williams",
        "Obiora",
        "Brown",
        "Chukwu",
        "Jones",
        "Emeka",
        "Garcia",
        "Nnamdi",
        "Miller",
        "Onyeka",
        "Davis",
        "Chidi",
        "Rodriguez",
        "Uchendu",
        "Martinez",
        "Obi",
        "Hernandez",
        "Adeyemi",
        "Lopez",
        "Ikenna",
        "Gonzalez",
        "Okonkwo",
        "Wilson",
        "Umeh",
        "Anderson",
    ]
    random.seed(patient_id)  # Same seed = same name every time
    first = first_names[patient_id % len(first_names)]
    last = last_names[(patient_id * 7 + 3) % len(last_names)]
    return first, last


def generate_email(first: str, last: str, patient_id: int) -> str:
    """Generate a synthetic email address."""
    domains = ["gmail.com", "yahoo.com", "outlook.com", "hotmail.com"]
    domain = domains[patient_id % len(domains)]
    return f"{first.lower()}.{last.lower()}{patient_id % 100}@{domain}"


def generate_dob(patient_id: int) -> date:
    """Generate synthetic date of birth (18–75 years old)."""
    random.seed(patient_id + 42)
    years_ago = random.randint(18, 75)
    return date.today() - timedelta(days=years_ago * 365)


# ── Main import functions ──────────────────────────────────


def import_availability(conn, csv_path: Path) -> tuple[int, int]:
    """
    Load doctor_availability.csv into MySQL.
    Returns (rows_inserted, rows_failed).
    """
    cursor = conn.cursor()
    inserted, failed = 0, 0

    print(f"\n📂 Reading CSV: {csv_path}")

    if not csv_path.exists():
        # Try the uploads directory (when running from VS Code)
        alt_path = Path("/mnt/user-data/uploads/doctor_availability.csv")
        if alt_path.exists():
            csv_path = alt_path
        else:
            print(f"❌ CSV not found at {csv_path}")
            print("   Copy doctor_availability.csv into the data/ folder.")
            return 0, 0

    sql = """
        INSERT IGNORE INTO doctor_availability
            (date_slot, specialization, doctor_name, is_available, patient_to_attend)
        VALUES (%s, %s, %s, %s, %s)
    """

    batch = []
    batch_size = 500

    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for i, row in enumerate(reader, 1):
            try:
                batch.append(
                    (
                        row["date_slot"].strip(),
                        row["specialization"].strip(),
                        row["doctor_name"].strip(),
                        parse_bool(row["is_available"]),
                        parse_patient_id(row["patient_to_attend"]),
                    )
                )
            except KeyError as e:
                print(f"   ⚠️  Row {i}: missing column {e}, skipping")
                failed += 1
                continue

            # Batch insert every 500 rows
            if len(batch) >= batch_size:
                try:
                    cursor.executemany(sql, batch)
                    conn.commit()
                    inserted += len(batch)
                    print(f"   ✔ Imported {inserted} rows...")
                    batch = []
                except Error as e:
                    print(f"   ❌ Batch error: {e}")
                    failed += len(batch)
                    batch = []

    # Insert remaining rows
    if batch:
        try:
            cursor.executemany(sql, batch)
            conn.commit()
            inserted += len(batch)
        except Error as e:
            print(f"   ❌ Final batch error: {e}")
            failed += len(batch)

    cursor.close()
    return inserted, failed


def seed_patients(conn) -> int:
    """
    Extract unique patient IDs from availability table
    and create patient profiles with synthetic data.
    """
    cursor = conn.cursor()
    print("\n👤 Seeding patient profiles...")

    # Get all unique patient IDs from the availability table
    cursor.execute("""
        SELECT DISTINCT patient_to_attend
        FROM doctor_availability
        WHERE patient_to_attend IS NOT NULL
        ORDER BY patient_to_attend
    """)
    patient_ids = [row[0] for row in cursor.fetchall()]
    print(f"   Found {len(patient_ids)} unique patients in the schedule")

    insurances = [
        "BlueCross BlueShield",
        "Aetna",
        "Cigna",
        "Delta Dental",
        "MetLife",
        "Guardian",
        "United Healthcare",
        "Humana",
        "No Insurance (Self-Pay)",
    ]

    sql = """
        INSERT IGNORE INTO patients
            (patient_id, first_name, last_name, email, phone, date_of_birth, insurance)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
    """

    batch = []
    for pid in patient_ids:
        first, last = generate_patient_name(pid)
        dob = generate_dob(pid)
        email = generate_email(first, last, pid)
        phone = f"+1-555-{pid % 900 + 100:03d}-{pid % 9000 + 1000:04d}"
        insurance = insurances[pid % len(insurances)]
        batch.append((pid, first, last, email, phone, dob.isoformat(), insurance))

    cursor.executemany(sql, batch)
    conn.commit()
    inserted = cursor.rowcount
    cursor.close()
    print(f"   ✔ Created {len(batch)} patient records")
    return len(batch)


def seed_appointments_from_availability(conn) -> int:
    """
    For every booked slot in the availability table,
    create a corresponding appointment record.
    """
    cursor = conn.cursor()
    print("\n📅 Creating appointment records from booked slots...")

    cursor.execute("""
        SELECT da.date_slot, da.doctor_name, da.specialization, da.patient_to_attend
        FROM doctor_availability da
        WHERE da.is_available = FALSE
          AND da.patient_to_attend IS NOT NULL
        ORDER BY da.date_slot
    """)
    booked_slots = cursor.fetchall()

    reasons = [
        "Routine cleaning",
        "Filling",
        "Crown fitting",
        "Root canal",
        "Teeth whitening",
        "Braces consultation",
        "Emergency pain",
        "Extraction",
        "Implant checkup",
        "X-ray review",
        "New patient exam",
        "Follow-up visit",
    ]

    sql = """
        INSERT IGNORE INTO appointments
            (patient_id, doctor_name, specialization, appointment_dt, status, reason, confirmation_code)
        VALUES (%s, %s, %s, %s, 'scheduled', %s, %s)
    """

    batch = []
    for dt, doc, spec, pid in booked_slots:
        reason = reasons[pid % len(reasons)]
        # Generate unique confirmation code
        code = f"D{pid:07d}-{str(dt.strftime('%m%d%H%M'))}"
        batch.append((pid, doc, spec, dt, reason, code))

    if batch:
        cursor.executemany(sql, batch)
        conn.commit()

    count = len(batch)
    cursor.close()
    print(f"   ✔ Created {count} appointment records")
    return count


def verify_import(conn):
    """Run verification queries and print a summary table."""
    cursor = conn.cursor()
    print("\n" + "=" * 55)
    print("  IMPORT VERIFICATION SUMMARY")
    print("=" * 55)

    queries = {
        "Total availability rows": "SELECT COUNT(*) FROM doctor_availability",
        "Available slots": "SELECT COUNT(*) FROM doctor_availability WHERE is_available = TRUE",
        "Booked slots": "SELECT COUNT(*) FROM doctor_availability WHERE is_available = FALSE",
        "Total patients": "SELECT COUNT(*) FROM patients",
        "Total appointments": "SELECT COUNT(*) FROM appointments",
        "Total doctors": "SELECT COUNT(*) FROM doctors",
    }

    for label, q in queries.items():
        cursor.execute(q)
        print(f"  {label:<28} {cursor.fetchone()[0]:>8,}")

    print("\n  Slots by specialization:")
    cursor.execute("""
        SELECT specialization, 
               COUNT(*) AS total, 
               SUM(is_available) AS available
        FROM doctor_availability
        GROUP BY specialization
        ORDER BY specialization
    """)
    for row in cursor.fetchall():
        print(f"    {row[0]:<22} {row[1]:>5} total | {row[2]:>5} available")

    print("=" * 55)
    cursor.close()


# ── Entry point ────────────────────────────────────────────
if __name__ == "__main__":
    print("=" * 55)
    print("  DentAI Pro — CSV to MySQL Importer")
    print("=" * 55)

    conn = get_connection()

    # Import the CSV
    ins, fail = import_availability(conn, CSV_PATH)
    print(f"\n✅ Availability import: {ins:,} rows inserted, {fail} failed")

    # Seed patient profiles
    seed_patients(conn)

    # Create appointment records
    seed_appointments_from_availability(conn)

    # Verify everything
    verify_import(conn)

    conn.close()
   
