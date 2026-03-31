"""
============================================================

  Built on LangGraph + MySQL | 7 Specialised Tools
    python scripts/03_dental_agent.py

    # Streamlit web app:
    streamlit run scripts/03_dental_agent.py

  TOOLS INCLUDED:
    1. get_availability          - Doctor schedule by date/specialization
    2. get_patient_appointments  - Patient's full appointment history
    3. check_slot_available      - Single slot availability check
    4. list_doctors_by_spec      - Doctors by specialization with profiles
    5. booking_agent             - Full appointment booking with confirmation
    6. cancellation_agent        - Cancel appointment with reason
    7. rescheduling_agent        - Reschedule to new slot

  ARCHITECTURE:
    User → Triage Node → Specialised Node → Tools → MySQL
============================================================
"""

# ── Standard Library ──────────────────────────────────────
import os
import sys
import json
import random
import string
import logging
from datetime import datetime, timedelta
from typing import TypedDict, Annotated, Optional, Literal
import operator
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

# ── LangGraph / LangChain ─────────────────────────────────
from langgraph.graph import StateGraph, END
from langgraph.prebuilt import ToolNode
from langchain_openai import ChatOpenAI
from langchain_core.messages import BaseMessage, HumanMessage, AIMessage, SystemMessage
from langchain_core.tools import tool

# ── Local ─────────────────────────────────────────────────
from scripts.db_connection import DBManager

# ── Logging ───────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("dentai_agent.log", encoding="utf-8"),
    ],
)
log = logging.getLogger("dentai.agent")

# ──────────────────────────────────────────────────────────
# CONSTANTS — Domain Knowledge
# ──────────────────────────────────────────────────────────

SPECIALIZATIONS = {
    "general_dentist": "Routine care, cleanings, fillings, X-rays, preventive dentistry",
    "cosmetic_dentist": "Teeth whitening, veneers, bonding, smile makeovers",
    "orthodontist": "Braces, Invisalign, retainers, teeth alignment",
    "pediatric_dentist": "Children's dentistry (0–18 years), sealants, fluoride",
    "prosthodontist": "Implants, crowns, bridges, dentures, complex restorations",
    "oral_surgeon": "Wisdom teeth, extractions, jaw surgery, implant placement",
    "emergency_dentist": "Severe toothache, broken tooth, knocked-out tooth, abscess",
}

EMERGENCY_KEYWORDS = [
    "severe pain",
    "unbearable",
    "can't sleep",
    "abscess",
    "swollen",
    "swelling",
    "knocked out",
    "bleeding",
    "fever",
    "can't eat",
    "excruciating",
    "throbbing",
    "cracked",
    "broken tooth",
    "emergency",
    "urgent",
    "extreme pain",
]

PROCEDURE_TO_SPEC = {
    "cleaning": "general_dentist",
    "filling": "general_dentist",
    "checkup": "general_dentist",
    "whitening": "cosmetic_dentist",
    "veneers": "cosmetic_dentist",
    "braces": "orthodontist",
    "invisalign": "orthodontist",
    "retainer": "orthodontist",
    "child": "pediatric_dentist",
    "kids": "pediatric_dentist",
    "implant": "prosthodontist",
    "crown": "prosthodontist",
    "bridge": "prosthodontist",
    "denture": "prosthodontist",
    "wisdom": "oral_surgeon",
    "extraction": "oral_surgeon",
    "root canal": "general_dentist",
    "emergency": "emergency_dentist",
}


# ──────────────────────────────────────────────────────────
# TOOL 1: get_availability
# ──────────────────────────────────────────────────────────
@tool
def get_availability(
    target_date: str,
    specialization: Optional[str] = None,
    doctor_name: Optional[str] = None,
) -> str:
    """
    Get available appointment slots for a given date.
    Can filter by specialization and/or specific doctor name.

    Args:
        target_date: Date to check in YYYY-MM-DD format (e.g., '2026-07-08')
        specialization: Optional dental specialization filter.
                        Options: general_dentist, cosmetic_dentist, orthodontist,
                        pediatric_dentist, prosthodontist, oral_surgeon, emergency_dentist
        doctor_name: Optional specific doctor name filter (e.g., 'john doe')

    Returns:
        JSON string with available slots grouped by doctor
    """
    try:
        with DBManager() as db:
            sql = """
                SELECT 
                    doctor_name,
                    specialization,
                    TIME(date_slot) AS time_slot,
                    DATE(date_slot) AS slot_date,
                    DAYNAME(date_slot) AS day_name
                FROM doctor_availability
                WHERE DATE(date_slot) = %s
                  AND is_available = TRUE
            """
            params = [target_date]

            if specialization:
                sql += " AND specialization = %s"
                params.append(specialization.lower())

            if doctor_name:
                sql += " AND doctor_name LIKE %s"
                params.append(f"%{doctor_name.lower()}%")

            sql += " ORDER BY doctor_name, date_slot"

            rows = db.query(sql, tuple(params))

            if not rows:
                return json.dumps(
                    {
                        "status": "no_slots",
                        "date": target_date,
                        "message": (
                            f"No available slots found for {target_date}"
                            + (f" with {specialization}" if specialization else "")
                            + ". Try a different date or specialization."
                        ),
                    }
                )

            # Group by doctor
            by_doctor: dict = {}
            for r in rows:
                doc = r["doctor_name"]
                if doc not in by_doctor:
                    by_doctor[doc] = {
                        "doctor": doc,
                        "specialization": r["specialization"],
                        "date": str(r["slot_date"]),
                        "day": r["day_name"],
                        "available_times": [],
                    }
                by_doctor[doc]["available_times"].append(str(r["time_slot"])[:5])

            return json.dumps(
                {
                    "status": "ok",
                    "date": target_date,
                    "total_slots": len(rows),
                    "doctors": list(by_doctor.values()),
                },
                default=str,
            )

    except Exception as e:
        log.error(f"get_availability error: {e}")
        return json.dumps({"status": "error", "message": str(e)})


# ──────────────────────────────────────────────────────────
# TOOL 2: get_patient_appointments
# ──────────────────────────────────────────────────────────
@tool
def get_patient_appointments(patient_id: int) -> str:
    """
    Retrieve a patient's full appointment history — past and upcoming.
    Shows status (scheduled, completed, cancelled, rescheduled).

    Args:
        patient_id: The patient's numeric ID (e.g., 1000048)

    Returns:
        JSON with patient profile and appointment list
    """
    try:
        with DBManager() as db:
            patient = db.query_one(
                "SELECT * FROM patients WHERE patient_id = %s", (patient_id,)
            )

            appts = db.query(
                """
                SELECT 
                    a.appointment_id,
                    a.appointment_dt,
                    a.doctor_name,
                    a.specialization,
                    a.status,
                    a.reason,
                    a.confirmation_code
                FROM appointments a
                WHERE a.patient_id = %s
                ORDER BY a.appointment_dt DESC
                LIMIT 20
            """,
                (patient_id,),
            )

            if not patient and not appts:
                return json.dumps(
                    {
                        "status": "not_found",
                        "message": f"No records found for patient ID {patient_id}.",
                    }
                )

            return json.dumps(
                {
                    "status": "ok",
                    "patient": {
                        "id": patient_id,
                        "name": f"{patient.get('first_name', 'Unknown')} {patient.get('last_name', '')}".strip()
                        if patient
                        else "Unknown",
                        "insurance": patient.get("insurance", "Unknown")
                        if patient
                        else "Unknown",
                    },
                    "appointments": [
                        {
                            "id": a["appointment_id"],
                            "date": str(a["appointment_dt"])[:16],
                            "doctor": a["doctor_name"],
                            "specialty": a["specialization"],
                            "status": a["status"],
                            "reason": a["reason"],
                            "confirmation": a["confirmation_code"],
                        }
                        for a in appts
                    ],
                    "total_appointments": len(appts),
                },
                default=str,
            )

    except Exception as e:
        log.error(f"get_patient_appointments error: {e}")
        return json.dumps({"status": "error", "message": str(e)})


# ──────────────────────────────────────────────────────────
# TOOL 3: check_slot_available
# ──────────────────────────────────────────────────────────
@tool
def check_slot_available(doctor_name: str, date_slot: str) -> str:
    """
    Check whether a specific doctor is available at a specific date and time.
    Use this BEFORE booking to confirm the exact slot is free.

    Args:
        doctor_name: Doctor's full name (e.g., 'john doe')
        date_slot: Exact datetime string (e.g., '2026-07-08 09:00:00')

    Returns:
        JSON with availability status and next available slots if taken
    """
    try:
        with DBManager() as db:
            row = db.query_one(
                """
                SELECT is_available, patient_to_attend, specialization
                FROM doctor_availability
                WHERE doctor_name = %s AND date_slot = %s
            """,
                (doctor_name.lower(), date_slot),
            )

            if not row:
                return json.dumps(
                    {
                        "status": "slot_not_found",
                        "message": f"No schedule entry found for Dr. {doctor_name} at {date_slot}. "
                        "This date may not be a working day.",
                    }
                )

            if row["is_available"]:
                return json.dumps(
                    {
                        "status": "available",
                        "doctor": doctor_name,
                        "datetime": date_slot,
                        "specialization": row["specialization"],
                        "message": f"✅ Slot is available! Dr. {doctor_name} is free at {date_slot}.",
                    }
                )
            else:
                # Find next 3 available slots for this doctor
                next_slots = db.query(
                    """
                    SELECT TIME(date_slot) AS t, DATE(date_slot) AS d
                    FROM doctor_availability
                    WHERE doctor_name = %s
                      AND is_available = TRUE
                      AND date_slot > %s
                    ORDER BY date_slot
                    LIMIT 3
                """,
                    (doctor_name.lower(), date_slot),
                )

                return json.dumps(
                    {
                        "status": "taken",
                        "doctor": doctor_name,
                        "datetime": date_slot,
                        "message": f"❌ That slot is already booked.",
                        "next_available": [
                            f"{str(s['d'])} at {str(s['t'])[:5]}" for s in next_slots
                        ],
                    },
                    default=str,
                )

    except Exception as e:
        log.error(f"check_slot_available error: {e}")
        return json.dumps({"status": "error", "message": str(e)})


# ──────────────────────────────────────────────────────────
# TOOL 4: list_doctors_by_specialization
# ──────────────────────────────────────────────────────────
@tool
def list_doctors_by_specialization(specialization: str) -> str:
    """
    List all doctors for a given dental specialization, with their profiles
    and how many slots are available in the next 30 days.

    Args:
        specialization: Dental specialty. Options:
            general_dentist, cosmetic_dentist, orthodontist,
            pediatric_dentist, prosthodontist, oral_surgeon, emergency_dentist

    Returns:
        JSON with doctor profiles and availability counts
    """
    try:
        spec_clean = specialization.lower().strip().replace(" ", "_")

        # Handle common aliases
        aliases = {
            "general": "general_dentist",
            "cosmetic": "cosmetic_dentist",
            "ortho": "orthodontist",
            "pediatric": "pediatric_dentist",
            "kids": "pediatric_dentist",
            "oral surgery": "oral_surgeon",
            "prostho": "prosthodontist",
            "emergency": "emergency_dentist",
        }
        spec_clean = aliases.get(spec_clean, spec_clean)

        with DBManager() as db:
            # Get doctors with availability count
            doctors = db.query(
                """
                SELECT 
                    d.doctor_name,
                    d.specialization,
                    d.years_exp,
                    d.bio,
                    COUNT(CASE WHEN da.is_available = TRUE AND da.date_slot >= NOW() THEN 1 END) AS open_slots
                FROM doctors d
                LEFT JOIN doctor_availability da 
                    ON d.doctor_name = da.doctor_name
                   AND da.date_slot <= DATE_ADD(NOW(), INTERVAL 30 DAY)
                WHERE d.specialization = %s AND d.active = TRUE
                GROUP BY d.doctor_name, d.specialization, d.years_exp, d.bio
                ORDER BY open_slots DESC
            """,
                (spec_clean,),
            )

            if not doctors:
                # Try partial match
                doctors = db.query(
                    """
                    SELECT doctor_name, specialization, years_exp, bio
                    FROM doctors
                    WHERE specialization LIKE %s AND active = TRUE
                """,
                    (f"%{spec_clean}%",),
                )

            if not doctors:
                all_specs = db.query(
                    "SELECT DISTINCT specialization FROM doctors ORDER BY specialization"
                )
                return json.dumps(
                    {
                        "status": "not_found",
                        "message": f"No doctors found for '{specialization}'.",
                        "available_specializations": [
                            r["specialization"] for r in all_specs
                        ],
                        "what_each_treats": SPECIALIZATIONS,
                    }
                )

            desc = SPECIALIZATIONS.get(spec_clean, "Dental specialist")

            return json.dumps(
                {
                    "status": "ok",
                    "specialization": spec_clean,
                    "description": desc,
                    "doctors": [
                        {
                            "name": d["doctor_name"].title(),
                            "specialization": d["specialization"],
                            "experience": f"{d['years_exp']} years",
                            "bio": d["bio"],
                            "open_slots_30d": d.get("open_slots", 0),
                        }
                        for d in doctors
                    ],
                }
            )

    except Exception as e:
        log.error(f"list_doctors_by_specialization error: {e}")
        return json.dumps({"status": "error", "message": str(e)})


# ──────────────────────────────────────────────────────────
# TOOL 5: booking_agent
# ──────────────────────────────────────────────────────────
@tool
def booking_agent(
    patient_id: int,
    doctor_name: str,
    date_slot: str,
    reason: str,
    patient_email: Optional[str] = None,
) -> str:
    """
    Book a dental appointment for a patient.
    Verifies slot availability, creates appointment record,
    marks the slot as booked in the schedule, and returns
    a confirmation code.

    Args:
        patient_id: Patient's numeric ID (e.g., 1000048)
        doctor_name: Doctor's full name (e.g., 'john doe')
        date_slot: Appointment datetime (e.g., '2026-07-08 09:00:00')
        reason: Reason for visit (e.g., 'Teeth cleaning', 'Root canal')
        patient_email: Optional email for confirmation (update patient record)

    Returns:
        JSON with booking confirmation or failure reason
    """
    try:
        with DBManager() as db:
            # Step 1: Verify slot is available (with optimistic check)
            slot = db.query_one(
                """
                SELECT slot_id, is_available, specialization, slot_duration_min
                FROM doctor_availability
                WHERE doctor_name = %s AND date_slot = %s
            """,
                (doctor_name.lower(), date_slot),
            )

            if not slot:
                return json.dumps(
                    {
                        "status": "error",
                        "message": f"No slot found for Dr. {doctor_name} at {date_slot}. "
                        "Use get_availability to find valid slots.",
                    }
                )

            if not slot["is_available"]:
                return json.dumps(
                    {
                        "status": "slot_taken",
                        "message": f"This slot is already booked. Use get_availability to find an open slot.",
                    }
                )

            # Step 2: Check patient exists; create record if not
            patient = db.query_one(
                "SELECT patient_id, first_name FROM patients WHERE patient_id = %s",
                (patient_id,),
            )
            if not patient:
                first, last = "New", "Patient"
                db.execute(
                    "INSERT IGNORE INTO patients (patient_id, first_name, last_name, email) VALUES (%s, %s, %s, %s)",
                    (
                        patient_id,
                        first,
                        last,
                        patient_email or f"patient{patient_id}@clinic.com",
                    ),
                )

            # Step 3: Generate unique confirmation code
            conf_code = (
                "DENT-"
                + str(patient_id)[-4:]
                + "-"
                + "".join(random.choices(string.ascii_uppercase + string.digits, k=6))
            )

            # Step 4: Mark slot as booked
            db.execute(
                """
                UPDATE doctor_availability
                SET is_available = FALSE, patient_to_attend = %s
                WHERE doctor_name = %s AND date_slot = %s
            """,
                (patient_id, doctor_name.lower(), date_slot),
            )

            # Step 5: Create appointment record
            appt_id = db.execute(
                """
                INSERT INTO appointments
                    (patient_id, doctor_name, specialization, appointment_dt,
                     reason, confirmation_code, status)
                VALUES (%s, %s, %s, %s, %s, %s, 'scheduled')
            """,
                (
                    patient_id,
                    doctor_name.lower(),
                    slot["specialization"],
                    date_slot,
                    reason,
                    conf_code,
                ),
            )

            # Step 6: Update email if provided
            if patient_email:
                db.execute(
                    "UPDATE patients SET email = %s WHERE patient_id = %s",
                    (patient_email, patient_id),
                )

            # Step 7: Log session event
            db.execute(
                """
                INSERT INTO conversation_sessions (session_id, patient_id, intent, outcome, appointment_id)
                VALUES (%s, %s, 'booking', 'success', %s)
            """,
                (f"auto-{appt_id}", patient_id, appt_id),
            )

            dt = datetime.strptime(date_slot, "%Y-%m-%d %H:%M:%S")
            return json.dumps(
                {
                    "status": "booked",
                    "confirmation_code": conf_code,
                    "appointment_id": appt_id,
                    "patient_id": patient_id,
                    "doctor": doctor_name.title(),
                    "specialization": slot["specialization"],
                    "date": dt.strftime("%A, %B %d, %Y"),
                    "time": dt.strftime("%I:%M %p"),
                    "duration": f"{slot['slot_duration_min']} minutes",
                    "reason": reason,
                    "message": (
                        f"✅ Appointment confirmed! Save your code: {conf_code}. "
                        f"Please arrive 10 minutes early. "
                        f"Call us at (555) DENTIST to cancel or reschedule."
                    ),
                }
            )

    except Exception as e:
        log.error(f"booking_agent error: {e}")
        return json.dumps({"status": "error", "message": str(e)})


# ──────────────────────────────────────────────────────────
# TOOL 6: cancellation_agent
# ──────────────────────────────────────────────────────────
@tool
def cancellation_agent(
    confirmation_code: str, patient_id: int, cancellation_reason: Optional[str] = None
) -> str:
    """
    Cancel an existing appointment using the confirmation code.
    Frees the slot back for other patients to book.
    Requires both confirmation code AND patient ID for security.

    Args:
        confirmation_code: The confirmation code from booking (e.g., 'DENT-0048-AB3X7K')
        patient_id: Patient's ID for identity verification
        cancellation_reason: Why they are cancelling (optional but helpful)

    Returns:
        JSON with cancellation confirmation or error
    """
    try:
        with DBManager() as db:
            # Find the appointment (verify ownership)
            appt = db.query_one(
                """
                SELECT appointment_id, patient_id, doctor_name, appointment_dt,
                       specialization, status
                FROM appointments
                WHERE confirmation_code = %s AND patient_id = %s
            """,
                (confirmation_code, patient_id),
            )

            if not appt:
                return json.dumps(
                    {
                        "status": "not_found",
                        "message": "No appointment found with that confirmation code and patient ID. "
                        "Please check both and try again.",
                    }
                )

            if appt["status"] in ("cancelled", "completed"):
                return json.dumps(
                    {
                        "status": "already_done",
                        "message": f"This appointment is already {appt['status']}. Nothing to cancel.",
                    }
                )

            now = datetime.now()
            appt_dt = appt["appointment_dt"]

            # Business rule: can't cancel within 2 hours of appointment
            if isinstance(appt_dt, datetime):
                hours_until = (appt_dt - now).total_seconds() / 3600
                if 0 < hours_until < 2:
                    return json.dumps(
                        {
                            "status": "too_late",
                            "message": (
                                f"Appointments can only be cancelled at least 2 hours in advance. "
                                f"Your appointment is in {hours_until:.1f} hours. "
                                f"Please call us directly at (555) DENTIST."
                            ),
                        }
                    )

            # Cancel the appointment
            db.execute(
                """
                UPDATE appointments
                SET status = 'cancelled',
                    cancelled_at = NOW(),
                    cancellation_reason = %s
                WHERE appointment_id = %s
            """,
                (cancellation_reason or "Patient request", appt["appointment_id"]),
            )

            # Free the slot in availability table
            db.execute(
                """
                UPDATE doctor_availability
                SET is_available = TRUE, patient_to_attend = NULL
                WHERE doctor_name = %s AND date_slot = %s
            """,
                (appt["doctor_name"], appt["appointment_dt"]),
            )

            dt_str = str(appt["appointment_dt"])[:16]
            return json.dumps(
                {
                    "status": "cancelled",
                    "appointment_id": appt["appointment_id"],
                    "confirmation_code": confirmation_code,
                    "doctor": appt["doctor_name"].title(),
                    "was_scheduled_for": dt_str,
                    "message": (
                        f"✅ Appointment on {dt_str} with Dr. {appt['doctor_name'].title()} "
                        f"has been cancelled. The slot is now open for other patients. "
                        f"We hope to see you soon — use booking to reschedule anytime."
                    ),
                },
                default=str,
            )

    except Exception as e:
        log.error(f"cancellation_agent error: {e}")
        return json.dumps({"status": "error", "message": str(e)})


# ──────────────────────────────────────────────────────────
# TOOL 7: rescheduling_agent
# ──────────────────────────────────────────────────────────
@tool
def rescheduling_agent(
    confirmation_code: str,
    patient_id: int,
    new_date_slot: str,
    reason: Optional[str] = None,
) -> str:
    """
    Reschedule an existing appointment to a new date and time.
    Frees the old slot and books the new one in a single atomic operation.
    Generates a new confirmation code for the rescheduled appointment.

    Args:
        confirmation_code: Original booking confirmation code
        patient_id: Patient's ID for verification
        new_date_slot: New appointment datetime (e.g., '2026-08-15 10:00:00')
        reason: Reason for rescheduling (optional)

    Returns:
        JSON with new confirmation or error
    """
    try:
        with DBManager() as db:
            # Find original appointment
            appt = db.query_one(
                """
                SELECT appointment_id, patient_id, doctor_name, appointment_dt,
                       specialization, status, reason
                FROM appointments
                WHERE confirmation_code = %s AND patient_id = %s
            """,
                (confirmation_code, patient_id),
            )

            if not appt:
                return json.dumps(
                    {
                        "status": "not_found",
                        "message": "Original appointment not found. Check confirmation code and patient ID.",
                    }
                )

            if appt["status"] in ("cancelled", "completed"):
                return json.dumps(
                    {
                        "status": "invalid_status",
                        "message": f"Cannot reschedule — appointment is already {appt['status']}.",
                    }
                )

            # Check new slot is available
            new_slot = db.query_one(
                """
                SELECT is_available, specialization
                FROM doctor_availability
                WHERE doctor_name = %s AND date_slot = %s
            """,
                (appt["doctor_name"], new_date_slot),
            )

            if not new_slot:
                return json.dumps(
                    {
                        "status": "slot_not_found",
                        "message": f"No schedule entry for {new_date_slot}. Use get_availability to find valid slots.",
                    }
                )

            if not new_slot["is_available"]:
                return json.dumps(
                    {
                        "status": "slot_taken",
                        "message": f"New slot {new_date_slot} is already booked. Try a different time.",
                    }
                )

            # Generate new confirmation code
            new_conf = (
                "DRSCH-"
                + str(patient_id)[-4:]
                + "-"
                + "".join(random.choices(string.ascii_uppercase + string.digits, k=5))
            )

            # Free old slot
            db.execute(
                """
                UPDATE doctor_availability
                SET is_available = TRUE, patient_to_attend = NULL
                WHERE doctor_name = %s AND date_slot = %s
            """,
                (appt["doctor_name"], appt["appointment_dt"]),
            )

            # Book new slot
            db.execute(
                """
                UPDATE doctor_availability
                SET is_available = FALSE, patient_to_attend = %s
                WHERE doctor_name = %s AND date_slot = %s
            """,
                (patient_id, appt["doctor_name"], new_date_slot),
            )

            # Update appointment record
            db.execute(
                """
                UPDATE appointments
                SET appointment_dt = %s,
                    status = 'rescheduled',
                    confirmation_code = %s,
                    notes = %s
                WHERE appointment_id = %s
            """,
                (
                    new_date_slot,
                    new_conf,
                    reason or "Patient requested reschedule",
                    appt["appointment_id"],
                ),
            )

            old_dt = str(appt["appointment_dt"])[:16]
            new_dt_obj = datetime.strptime(new_date_slot, "%Y-%m-%d %H:%M:%S")

            return json.dumps(
                {
                    "status": "rescheduled",
                    "old_confirmation": confirmation_code,
                    "new_confirmation": new_conf,
                    "doctor": appt["doctor_name"].title(),
                    "old_datetime": old_dt,
                    "new_date": new_dt_obj.strftime("%A, %B %d, %Y"),
                    "new_time": new_dt_obj.strftime("%I:%M %p"),
                    "message": (
                        f"✅ Your appointment has been rescheduled! "
                        f"Old slot ({old_dt}) is now free. "
                        f"New slot: {new_dt_obj.strftime('%A, %B %d at %I:%M %p')} "
                        f"with Dr. {appt['doctor_name'].title()}. "
                        f"New confirmation code: {new_conf}"
                    ),
                },
                default=str,
            )

    except Exception as e:
        log.error(f"rescheduling_agent error: {e}")
        return json.dumps({"status": "error", "message": str(e)})


# ──────────────────────────────────────────────────────────
# ALL TOOLS LIST — Used by LangGraph ToolNode
# ──────────────────────────────────────────────────────────
ALL_TOOLS = [
    get_availability,
    get_patient_appointments,
    check_slot_available,
    list_doctors_by_specialization,
    booking_agent,
    cancellation_agent,
    rescheduling_agent,
]

tool_node = ToolNode(ALL_TOOLS)


# ──────────────────────────────────────────────────────────
# STATE DEFINITION
# ──────────────────────────────────────────────────────────
class DentalState(TypedDict):
    messages: Annotated[list[BaseMessage], operator.add]
    session_id: str
    is_emergency: bool
    detected_intent: str  # booking/cancel/reschedule/info/emergency/general
    detected_specialization: Optional[str]
    patient_id: Optional[int]
    current_node: str
    analytics: list[str]


# ──────────────────────────────────────────────────────────
# LLM
# ──────────────────────────────────────────────────────────
# ── LLM SETUP ─────────────────────────────────────────────
# ── LLM SETUP ─────────────────────────────────────────────
def get_llm(temperature: float = 0.3) -> ChatOpenAI:
    """Fixed: explicitly force API key as string (fixes the sync/async error)"""
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise ValueError(
            "❌ OPENAI_API_KEY not found!\n"
            "   Please add it to your .env file: OPENAI_API_KEY=sk-..."
        )
    return ChatOpenAI(
        model="gpt-4o",
        temperature=temperature,
        api_key=api_key,
    )


# ──────────────────────────────────────────────────────────
# NODE FUNCTIONS
# ──────────────────────────────────────────────────────────


def triage_node(state: DentalState) -> DentalState:
    """
    Entry node — classifies intent and detects emergencies.
    No LLM call — pure Python for speed and reliability.
    """
    msg = state["messages"][-1].content.lower()

    # Emergency detection (always check first — patient safety)
    is_emergency = any(kw in msg for kw in EMERGENCY_KEYWORDS)

    # Intent classification
    intent = "general"
    if is_emergency:
        intent = "emergency"
    elif any(
        w in msg
        for w in [
            "book",
            "schedule",
            "appointment",
            "see the doctor",
            "come in",
            "visit",
        ]
    ):
        intent = "booking"
    elif any(
        w in msg for w in ["cancel", "cancellation", "won't make it", "can't come"]
    ):
        intent = "cancel"
    elif any(
        w in msg
        for w in ["reschedule", "move", "change my appointment", "different time"]
    ):
        intent = "reschedule"
    elif any(
        w in msg
        for w in ["my appointments", "history", "past visits", "upcoming", "when is my"]
    ):
        intent = "patient_history"
    elif any(
        w in msg for w in ["who", "doctors", "specialist", "available", "which doctor"]
    ):
        intent = "doctor_info"

    # Detect specialization from context
    detected_spec = None
    for keyword, spec in PROCEDURE_TO_SPEC.items():
        if keyword in msg:
            detected_spec = spec
            break

    analytics = state.get("analytics", [])
    analytics.append(
        f"triage|intent={intent}|emergency={is_emergency}|spec={detected_spec}"
    )

    return {
        **state,
        "is_emergency": is_emergency,
        "detected_intent": intent,
        "detected_specialization": detected_spec,
        "current_node": "triage",
        "analytics": analytics,
    }


def emergency_node(state: DentalState) -> DentalState:
    """Handles dental emergencies with urgency and empathy."""
    llm = get_llm(temperature=0.15)
    llm_tools = llm.bind_tools([get_availability, booking_agent])

    system = SystemMessage(
        content="""
You are a compassionate dental emergency triage assistant at DentAI Pro.

The patient has reported a potential dental emergency.

YOUR RESPONSE PROTOCOL:
1. Acknowledge their pain FIRST with genuine empathy (they are scared and hurting)
2. Assess danger level:
   - CRITICAL (go to ER NOW): Spreading facial swelling, difficulty breathing/swallowing, high fever
   - URGENT (same-day visit): Severe toothache, abscess, knocked-out tooth, broken tooth with pain
3. For a knocked-out tooth: tell them to hold by crown, rinse gently, keep in milk or saliva,
   and get to a dentist within 30 minutes — time is critical
4. For abscess: do NOT ignore, do NOT apply heat, use cold pack for comfort
5. Use get_availability to check today's emergency slots
6. Offer to book an emergency appointment RIGHT NOW
7. Give immediate pain management: OTC ibuprofen (if not contraindicated), cold pack (NOT heat)

Emergency line: (555) DENTIST | Emergency dentists: daniel miller, susan davis

Be warm but direct. Do not minimize pain. Every dental emergency is real.
"""
    )
    response = llm_tools.invoke([system] + state["messages"])
    return {**state, "messages": [response], "current_node": "emergency"}


def booking_node(state: DentalState) -> DentalState:
    """Handles appointment booking with smart procedure-to-specialist routing."""
    llm = get_llm(temperature=0.3)
    llm_tools = llm.bind_tools(ALL_TOOLS)

    spec_hint = ""
    if state.get("detected_specialization"):
        spec = state["detected_specialization"]
        spec_hint = (
            f"\nFrom the conversation, the patient likely needs: {spec}. "
            f"Suggest doctors in that specialization first. "
            f"Use list_doctors_by_specialization('{spec}') to show options."
        )

    system = SystemMessage(
        content=f"""
You are a professional dental appointment scheduler at DentAI Pro Clinic.
Personality: warm, efficient, helpful — the best receptionist they've ever spoken to.

BOOKING WORKFLOW (follow this order):
1. If patient mentions a procedure → use list_doctors_by_specialization to find the right specialist
2. Ask the patient for their preferred date
3. Use get_availability to show available slots for that date
4. If they have a preference → use check_slot_available to confirm it's free
5. Collect: patient ID (if returning), doctor name, date/time, reason for visit
6. Use booking_agent to complete the booking
7. Provide confirmation code and care instructions

SPECIALIST ROUTING GUIDE:
- Cleaning / filling / routine → general_dentist
- Whitening / veneers → cosmetic_dentist  
- Braces / Invisalign → orthodontist
- Children (under 18) → pediatric_dentist
- Crowns / implants / dentures → prosthodontist
- Wisdom teeth / extraction → oral_surgeon
- Severe pain / emergency → emergency_dentist

RULES:
- Never book without confirming slot availability first
- For new patients: inform them the first visit is a full exam (allow 60 minutes)
- Always say: "Please arrive 10 minutes early to complete paperwork"
- Give confirmation code clearly: "Your confirmation code is: [CODE]"
{spec_hint}
"""
    )
    response = llm_tools.invoke([system] + state["messages"])
    return {**state, "messages": [response], "current_node": "booking"}


def cancel_node(state: DentalState) -> DentalState:
    """Handles appointment cancellation with grace."""
    llm = get_llm(temperature=0.3)
    llm_tools = llm.bind_tools([get_patient_appointments, cancellation_agent])

    system = SystemMessage(
        content="""
You are handling appointment cancellations at DentAI Pro Clinic.

CANCELLATION WORKFLOW:
1. Express understanding — cancellations happen, no judgment
2. Ask for: confirmation code AND patient ID (both required for security)
3. Use get_patient_appointments to show their upcoming appointments if they don't have the code
4. Use cancellation_agent to cancel the appointment
5. Confirm cancellation and gently offer to rebook for another time
6. Remind them: cancellations within 2 hours of appointment require a phone call

Business rule: 24-hour notice is preferred but not mandatory.
Note: if slot was cancelled successfully, it becomes available for other patients.
"""
    )
    response = llm_tools.invoke([system] + state["messages"])
    return {**state, "messages": [response], "current_node": "cancel"}


def reschedule_node(state: DentalState) -> DentalState:
    """Handles rescheduling with old→new slot atomic swap."""
    llm = get_llm(temperature=0.3)
    llm_tools = llm.bind_tools(
        [get_patient_appointments, get_availability, rescheduling_agent]
    )

    system = SystemMessage(
        content="""
You are handling appointment rescheduling at DentAI Pro Clinic.

RESCHEDULING WORKFLOW:
1. Collect the patient's confirmation code and patient ID
2. Use get_patient_appointments to show their current upcoming appointments
3. Ask for their preferred new date
4. Use get_availability to show open slots for that date with the same doctor
5. Confirm the new slot with the patient
6. Use rescheduling_agent to atomically swap old slot → new slot
7. Provide the NEW confirmation code clearly

Key point: the old slot is freed and the new slot is booked in a single operation —
there is no risk of double-booking or losing either slot.

If the patient wants a different doctor when rescheduling, use list_doctors_by_specialization
to help them find a suitable alternative.
"""
    )
    response = llm_tools.invoke([system] + state["messages"])
    return {**state, "messages": [response], "current_node": "reschedule"}


def patient_history_node(state: DentalState) -> DentalState:
    """Retrieves and presents patient appointment history."""
    llm = get_llm(temperature=0.3)
    llm_tools = llm.bind_tools([get_patient_appointments])

    system = SystemMessage(
        content="""
You help patients view their appointment history at DentAI Pro.

WORKFLOW:
1. Ask for the patient's ID number (or offer to look up by name if they don't have it)
2. Use get_patient_appointments to fetch their history
3. Present upcoming appointments first (most important)
4. Show past appointments as a summary
5. Offer to book, reschedule, or cancel after reviewing their history

Format appointments clearly: Date | Doctor | Reason | Status | Confirmation code
"""
    )
    response = llm_tools.invoke([system] + state["messages"])
    return {**state, "messages": [response], "current_node": "patient_history"}


def doctor_info_node(state: DentalState) -> DentalState:
    """Helps patients find the right doctor/specialization."""
    llm = get_llm(temperature=0.4)
    llm_tools = llm.bind_tools([list_doctors_by_specialization, get_availability])

    system = SystemMessage(
        content=f"""
You help patients find the right dental specialist at DentAI Pro.

AVAILABLE SPECIALIZATIONS AND WHAT THEY TREAT:
{json.dumps(SPECIALIZATIONS, indent=2)}

WORKFLOW:
1. Listen to the patient's symptoms or desired treatment
2. Recommend the right specialization with a clear explanation of why
3. Use list_doctors_by_specialization to show the available doctors
4. Share doctor bios to help the patient choose
5. Offer to check availability and book an appointment

Be educational and reassuring — many patients don't know which specialist they need.
"""
    )
    response = llm_tools.invoke([system] + state["messages"])
    return {**state, "messages": [response], "current_node": "doctor_info"}


def general_node(state: DentalState) -> DentalState:
    """Friendly general conversation — clinic info, FAQ, dental education."""
    llm = get_llm(temperature=0.5)

    system = SystemMessage(
        content="""
You are the friendly AI assistant for DentAI Pro Dental Clinic.

Hours: Mon–Fri 8:00am–4:30pm | Emergency: (555) DENTIST
Location: 123 Smile Avenue, Suite 200

You can help with:
✓ Booking, cancelling, or rescheduling appointments
✓ Questions about procedures and what to expect
✓ Finding the right specialist for your needs  
✓ Dental emergency guidance
✓ Insurance and payment questions
✓ General dental health tips

DENTAL ANXIETY SUPPORT:
If the patient expresses fear or anxiety, acknowledge it warmly. Many people share this.
Mention: gentle dentists, modern anesthesia, sedation options, and that pain is not inevitable.

COMMON FAQ ANSWERS:
- Cleaning: 30-60 min, every 6 months, includes X-rays annually
- Filling: 30-45 min, local anesthesia, avoid hard foods 24h after
- Root canal: less painful than the toothache itself, 60-90 min
- Whitening: up to 8 shades brighter, avoid staining foods 48h after

Always end with a clear offer to help further.
"""
    )
    response = llm.invoke([system] + state["messages"])
    return {**state, "messages": [response], "current_node": "general"}


# ──────────────────────────────────────────────────────────
# ROUTING FUNCTIONS
# ──────────────────────────────────────────────────────────


def route_from_triage(state: DentalState) -> str:
    """Routes to the right specialised node based on triage classification."""
    if state["is_emergency"]:
        return "emergency"
    intent = state.get("detected_intent", "general")
    routing = {
        "booking": "booking",
        "cancel": "cancel",
        "reschedule": "reschedule",
        "patient_history": "patient_history",
        "doctor_info": "doctor_info",
        "emergency": "emergency",
        "general": "general",
    }
    return routing.get(intent, "general")


def route_after_action(state: DentalState) -> str:
    """After a node runs — check if tools need executing."""
    last = state["messages"][-1]
    if hasattr(last, "tool_calls") and last.tool_calls:
        return "tools"
    return END


def route_after_tools(state: DentalState) -> str:
    """After tools execute — route back to the calling node."""
    node = state.get("current_node", "general")
    return (
        node
        if node
        in (
            "booking",
            "cancel",
            "reschedule",
            "patient_history",
            "doctor_info",
            "emergency",
        )
        else "general"
    )


# ──────────────────────────────────────────────────────────
# BUILD THE GRAPH
# ──────────────────────────────────────────────────────────
def build_graph():
    """
    Compile the full LangGraph multi-agent dental system.

    Graph topology:

    [triage] → [emergency] → [tools] ⟳
             → [booking] → [tools] ⟳
             → [cancel] → [tools] ⟳
             → [reschedule] → [tools] ⟳
             → [patient_history] → [tools] ⟳
             → [doctor_info] → [tools] ⟳
             → [general] → END
    """
    g = StateGraph(DentalState)

    # Register all nodes
    g.add_node("triage", triage_node)
    g.add_node("emergency", emergency_node)
    g.add_node("booking", booking_node)
    g.add_node("cancel", cancel_node)
    g.add_node("reschedule", reschedule_node)
    g.add_node("patient_history", patient_history_node)
    g.add_node("doctor_info", doctor_info_node)
    g.add_node("general", general_node)
    g.add_node("tools", tool_node)

    # Entry → triage
    g.set_entry_point("triage")

    # Triage → specialised nodes
    g.add_conditional_edges(
        "triage",
        route_from_triage,
        {
            "emergency": "emergency",
            "booking": "booking",
            "cancel": "cancel",
            "reschedule": "reschedule",
            "patient_history": "patient_history",
            "doctor_info": "doctor_info",
            "general": "general",
        },
    )

    # Each action node → tools (if needed) → back to node
    for node in (
        "emergency",
        "booking",
        "cancel",
        "reschedule",
        "patient_history",
        "doctor_info",
    ):
        g.add_conditional_edges(node, route_after_action, {"tools": "tools", END: END})

    g.add_conditional_edges(
        "tools",
        route_after_tools,
        {
            "emergency": "emergency",
            "booking": "booking",
            "cancel": "cancel",
            "reschedule": "reschedule",
            "patient_history": "patient_history",
            "doctor_info": "doctor_info",
            "general": "general",
        },
    )

    # General always goes to END
    g.add_edge("general", END)

    return g.compile()


# ──────────────────────────────────────────────────────────
# STREAMLIT UI
# ──────────────────────────────────────────────────────────
def run_streamlit():
    import streamlit as st

    st.set_page_config(page_title="DentAI Pro", page_icon="🦷", layout="centered")

    st.markdown(
        """
    <style>
    .header { font-size: 2.2rem; font-weight: 700; color: #1a3a5c; }
    .sub    { font-size: 1rem; color: #555; margin-bottom: 1rem; }
    .emerg  { background: #fdecea; padding: .6rem 1rem; border-left: 4px solid #e74c3c;
              border-radius: 4px; font-size: 0.9rem; }
    .stat   { font-size: 0.85rem; color: #777; margin-top: .25rem; }
    </style>
    """,
        unsafe_allow_html=True,
    )

    st.markdown('<div class="header">🦷 DentAI Pro</div>', unsafe_allow_html=True)
    st.markdown(
        '<div class="sub">Intelligent Dental Assistant — Book, Reschedule, Cancel 24/7</div>',
        unsafe_allow_html=True,
    )
    st.markdown(
        '<div class="emerg">🚨 <strong>Dental Emergency?</strong> Call (555) DENTIST. Same-day slots available.</div>',
        unsafe_allow_html=True,
    )
    st.divider()

    if "graph" not in st.session_state:
        st.session_state.graph = build_graph()
        st.session_state.history = []
        st.session_state.sid = datetime.now().strftime("%Y%m%d%H%M%S")

    with st.sidebar:
        st.markdown("### 🏥 Clinic Info")
        st.info("Mon–Fri: 8am–4:30pm\nSaturday: 9am–2pm\nEmergency: (555) DENTIST")
        st.markdown("### 👨‍⚕️ Our Specialists")
        for spec, desc in SPECIALIZATIONS.items():
            st.markdown(f"**{spec.replace('_', ' ').title()}**\n{desc}\n")
        st.markdown("### 💬 Quick Start")
        for q in [
            "Book a cleaning",
            "I have severe tooth pain",
            "Cancel my appointment",
            "List orthodontists",
        ]:
            if st.button(q, use_container_width=True, key=q):
                st.session_state.quick = q

    for msg in st.session_state.history:
        with st.chat_message(msg["role"]):
            st.write(msg["content"])

    prompt = st.session_state.pop("quick", None) or st.chat_input(
        "How can we help you today?"
    )
    if prompt:
        st.session_state.history.append({"role": "user", "content": prompt})
        with st.chat_message("user"):
            st.write(prompt)

        with st.chat_message("assistant"):
            with st.spinner("Connecting to our scheduling system..."):
                init: DentalState = {
                    "messages": [HumanMessage(content=prompt)],
                    "session_id": st.session_state.sid,
                    "is_emergency": False,
                    "detected_intent": "general",
                    "detected_specialization": None,
                    "patient_id": None,
                    "current_node": "start",
                    "analytics": [],
                }
                result = st.session_state.graph.invoke(init)
                ai_msg = next(
                    (
                        m
                        for m in reversed(result["messages"])
                        if isinstance(m, AIMessage)
                    ),
                    None,
                )
                text = (
                    ai_msg.content
                    if ai_msg
                    else "Something went wrong. Please try again."
                )
                st.write(text)

                if result.get("is_emergency"):
                    st.error(
                        "⚠️ EMERGENCY DETECTED — Call (555) DENTIST immediately if symptoms are severe!"
                    )

        st.session_state.history.append({"role": "assistant", "content": text})


# ──────────────────────────────────────────────────────────
# CLI DEMO
# ──────────────────────────────────────────────────────────
def run_cli():
    print("\n" + "=" * 60)
    print("  DentAI Pro — Dental Appointment System")
    print("  Commands: 'quit' to exit | 'help' for info")
    print("=" * 60 + "\n")

    graph = build_graph()
    sid = datetime.now().strftime("%Y%m%d%H%M%S")

    while True:
        try:
            user_input = input("You: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nGoodbye! 🦷")
            break

        if user_input.lower() in ("quit", "exit", "q"):
            print("Stay healthy! 🦷")
            break

        if not user_input:
            continue

        state: DentalState = {
            "messages": [HumanMessage(content=user_input)],
            "session_id": sid,
            "is_emergency": False,
            "detected_intent": "general",
            "detected_specialization": None,
            "patient_id": None,
            "current_node": "start",
            "analytics": [],
        }

        result = graph.invoke(state)
        ai = next(
            (m for m in reversed(result["messages"]) if isinstance(m, AIMessage)), None
        )
        print(f"\nDentAI: {ai.content if ai else 'Error'}\n")


if __name__ == "__main__":
    if "streamlit" in sys.argv[0] or "--streamlit" in sys.argv:
        run_streamlit()
    else:
        run_cli()
