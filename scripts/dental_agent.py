from __future__ import annotations
"""
pyt — DentAI Pro Core Agent (Production)
=============================================================
LangGraph multi-agent with 7 Supabase tools.
LLM  : Groq llama-3.3-70b-versatile (free, ~300 tok/s)
DB   : Supabase / PostgreSQL

PUBLIC API:
    from scripts.dental_agent import run_agent, SPECIALIZATIONS
    result = run_agent("Book a cleaning", session_id="abc", channel="web")
    reply  = result["reply"]

PRODUCTION CHANGES vs demo:
  - Centralised logging via scripts.logging_config (token-safe)
  - Groq tool_use_failed 400 → retry with exponential backoff (up to 3×)
  - conversation_sessions INSERT fixed: no RETURNING id (table has no id col)
  - run_agent returns patient_id so Streamlit can cache it across turns
  - get_graph() is lazily initialised once per process (thread-safe singleton)
"""

import json
import logging
import operator
import os
import random
import string
import sys
import time
from datetime import datetime
from pathlib import Path
from threading import Lock
from typing import Annotated, Optional, TypedDict

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
load_dotenv()

# ── Logging — must be first ─────────────────────────────────
from scripts.logging_config import configure_logging
configure_logging()
log = logging.getLogger("dentai.agent")

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langchain_core.tools import tool
from langchain_groq import ChatGroq
from langgraph.graph import END, StateGraph
from langgraph.prebuilt import ToolNode

from scripts.db_connection import DBManager

# ──────────────────────────────────────────────────────────
# CONSTANTS
# ──────────────────────────────────────────────────────────

SPECIALIZATIONS: dict[str, str] = {
    "general_dentist":   "Routine care, cleanings, fillings, X-rays, preventive dentistry",
    "cosmetic_dentist":  "Teeth whitening, veneers, bonding, smile makeovers",
    "orthodontist":      "Braces, Invisalign, retainers, teeth alignment",
    "pediatric_dentist": "Children's dentistry (0–18 years), sealants, fluoride",
    "prosthodontist":    "Implants, crowns, bridges, dentures, complex restorations",
    "oral_surgeon":      "Wisdom teeth, extractions, jaw surgery, implant placement",
    "emergency_dentist": "Severe toothache, broken tooth, knocked-out tooth, abscess",
}

EMERGENCY_KEYWORDS: tuple[str, ...] = (
    "difficulty breathing", "difficulty swallowing", "spreading swelling",
    "severe pain", "extreme pain", "excruciating", "knocked out",
    "broken tooth", "can't sleep", "can't eat", "abscess",
    "swelling", "swollen", "bleeding", "throbbing",
    "unbearable", "emergency", "urgent", "fever",
)

PROCEDURE_TO_SPEC: dict[str, str] = {
    "root canal":  "general_dentist",
    "cleaning":    "general_dentist",
    "filling":     "general_dentist",
    "checkup":     "general_dentist",
    "whitening":   "cosmetic_dentist",
    "veneers":     "cosmetic_dentist",
    "invisalign":  "orthodontist",
    "retainer":    "orthodontist",
    "braces":      "orthodontist",
    "implant":     "prosthodontist",
    "denture":     "prosthodontist",
    "bridge":      "prosthodontist",
    "crown":       "prosthodontist",
    "wisdom":      "oral_surgeon",
    "extraction":  "oral_surgeon",
    "child":       "pediatric_dentist",
    "kids":        "pediatric_dentist",
    "emergency":   "emergency_dentist",
}

SPEC_ALIASES: dict[str, str] = {
    "general":      "general_dentist",
    "cosmetic":     "cosmetic_dentist",
    "ortho":        "orthodontist",
    "pediatric":    "pediatric_dentist",
    "kids":         "pediatric_dentist",
    "oral surgery": "oral_surgeon",
    "prostho":      "prosthodontist",
    "emergency":    "emergency_dentist",
}

_SPECS_JSON = json.dumps(SPECIALIZATIONS, indent=2)

# ──────────────────────────────────────────────────────────
# LLM — with Groq tool_use_failed retry
# ──────────────────────────────────────────────────────────

def get_llm(temperature: float = 0.3) -> ChatGroq:
    key = os.environ.get("GROQ_API_KEY")
    if not key:
        raise EnvironmentError(
            "GROQ_API_KEY not set in .env. Get free key: https://console.groq.com"
        )
    return ChatGroq(
        model="llama-3.3-70b-versatile",
        temperature=temperature,
        api_key=key,
        max_retries=2,        # Groq SDK-level retry for 429s
    )


def _llm_invoke_with_retry(llm, messages: list, max_attempts: int = 3) -> AIMessage:
    """
    Invoke the LLM with retry on Groq tool_use_failed (400) errors.

    Groq's llama-3.3-70b-versatile occasionally emits malformed tool-call
    JSON, which comes back as a 400 BadRequestError with code='tool_use_failed'.
    A second attempt with the same prompt almost always succeeds.

    Args:
        llm:          Bound ChatGroq instance.
        messages:     Full message list to send.
        max_attempts: Maximum invocation attempts before raising.

    Returns:
        AIMessage from the model.

    Raises:
        Exception: Re-raises the last error if all attempts fail.
    """
    last_exc: Exception | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            return llm.invoke(messages)
        except Exception as exc:
            last_exc = exc
            err_str = str(exc).lower()
            if "tool_use_failed" in err_str or "failed to call a function" in err_str:
                wait = 2 ** (attempt - 1)   # 1s, 2s, 4s
                log.warning(
                    "Groq tool_use_failed (attempt %d/%d) — retrying in %ds",
                    attempt, max_attempts, wait,
                )
                time.sleep(wait)
                continue
            raise   # non-retryable error
    raise last_exc  # type: ignore[misc]


# ──────────────────────────────────────────────────────────
# STATE
# ──────────────────────────────────────────────────────────

class DentalState(TypedDict):
    messages:                Annotated[list[BaseMessage], operator.add]
    session_id:              str
    channel:                 str
    is_emergency:            bool
    detected_intent:         str
    detected_specialization: Optional[str]
    patient_id:              Optional[int]
    current_node:            str
    analytics:               list[str]


def make_state(
    message: str,
    session_id: str,
    channel: str = "web",
    patient_id: Optional[int] = None,
) -> DentalState:
    return DentalState(
        messages=[HumanMessage(content=message)],
        session_id=session_id,
        channel=channel,
        is_emergency=False,
        detected_intent="general",
        detected_specialization=None,
        patient_id=patient_id,
        current_node="start",
        analytics=[],
    )


# ──────────────────────────────────────────────────────────
# HELPERS
# ──────────────────────────────────────────────────────────

def _conf_code(patient_id: int, prefix: str = "DENT") -> str:
    suffix = "".join(random.choices(string.ascii_uppercase + string.digits, k=6))
    return f"{prefix}-{str(patient_id)[-4:]}-{suffix}"


# ──────────────────────────────────────────────────────────
# TOOLS
# ──────────────────────────────────────────────────────────

@tool
def get_availability(
    target_date: str,
    specialization: Optional[str] = None,
    doctor_name: Optional[str] = None,
) -> str:
    """
    Get available appointment slots for a given date.

    Args:
        target_date:    Date in YYYY-MM-DD format (e.g., '2026-07-08')
        specialization: Optional — general_dentist | cosmetic_dentist | orthodontist |
                        pediatric_dentist | prosthodontist | oral_surgeon | emergency_dentist
        doctor_name:    Optional specific doctor name (e.g., 'john doe')

    Returns:
        JSON with available slots grouped by doctor.
    """
    try:
        sql = """
            SELECT doctor_name, specialization,
                   date_slot::time                  AS time_slot,
                   date_slot::date                  AS slot_date,
                   TRIM(TO_CHAR(date_slot,'Day'))   AS day_name
            FROM doctor_availability
            WHERE date_slot::date = %s AND is_available = TRUE
        """
        params: list = [target_date]
        if specialization:
            sql += " AND specialization = %s"
            params.append(specialization.lower())
        if doctor_name:
            sql += " AND doctor_name ILIKE %s"
            params.append(f"%{doctor_name.lower()}%")
        sql += " ORDER BY doctor_name, date_slot"

        with DBManager() as db:
            rows = db.query(sql, tuple(params))

        if not rows:
            suffix = f" with {specialization}" if specialization else ""
            return json.dumps({
                "status": "no_slots", "date": target_date,
                "message": f"No slots on {target_date}{suffix}. Try a different date.",
            })

        by_doc: dict[str, dict] = {}
        for r in rows:
            doc = r["doctor_name"]
            if doc not in by_doc:
                by_doc[doc] = {
                    "doctor": doc, "specialization": r["specialization"],
                    "date": str(r["slot_date"]), "day": r["day_name"],
                    "available_times": [],
                }
            by_doc[doc]["available_times"].append(str(r["time_slot"])[:5])

        return json.dumps({
            "status": "ok", "date": target_date,
            "total_slots": len(rows), "doctors": list(by_doc.values()),
        }, default=str)

    except Exception as exc:
        log.error("get_availability: %s", exc)
        return json.dumps({"status": "error", "message": str(exc)})


@tool
def get_patient_appointments(patient_id: int) -> str:
    """
    Retrieve a patient's full appointment history — past and upcoming.

    Args:
        patient_id: Patient's numeric ID (e.g., 1000048)

    Returns:
        JSON with patient profile and up to 20 most-recent appointments.
    """
    try:
        with DBManager() as db:
            patient = db.query_one("SELECT * FROM patients WHERE patient_id = %s", (patient_id,))
            appts = db.query(
                "SELECT id, appointment_dt, doctor_name, specialization, "
                "       status, reason, confirmation_code "
                "FROM appointments WHERE patient_id = %s "
                "ORDER BY appointment_dt DESC LIMIT 20",
                (patient_id,),
            )

        if not patient and not appts:
            return json.dumps({"status": "not_found",
                               "message": f"No records for patient {patient_id}."})

        name = insurance = "Unknown"
        if patient:
            name = (
                f"{patient.get('first_name') or ''} {patient.get('last_name') or ''}".strip()
                or "Unknown"
            )
            insurance = patient.get("insurance") or "Unknown"

        return json.dumps({
            "status": "ok",
            "patient": {"id": patient_id, "name": name, "insurance": insurance},
            "appointments": [
                {
                    "id": a["id"], "date": str(a["appointment_dt"])[:16],
                    "doctor": a["doctor_name"], "specialty": a["specialization"],
                    "status": a["status"], "reason": a["reason"],
                    "confirmation": a["confirmation_code"],
                }
                for a in appts
            ],
            "total": len(appts),
        }, default=str)

    except Exception as exc:
        log.error("get_patient_appointments: %s", exc)
        return json.dumps({"status": "error", "message": str(exc)})


@tool
def check_slot_available(doctor_name: str, date_slot: str) -> str:
    """
    Check if a specific doctor-slot is free. Always call BEFORE booking_agent.

    Args:
        doctor_name: Doctor's full name (e.g., 'john doe')
        date_slot:   Exact datetime string (e.g., '2026-07-08 09:00:00')

    Returns:
        JSON with status 'available' or 'taken' (includes next 3 free slots if taken).
    """
    try:
        with DBManager() as db:
            row = db.query_one(
                "SELECT is_available, specialization FROM doctor_availability "
                "WHERE doctor_name = %s AND date_slot = %s",
                (doctor_name.lower(), date_slot),
            )

        if not row:
            return json.dumps({
                "status": "slot_not_found",
                "message": f"No schedule entry for Dr. {doctor_name} at {date_slot}.",
            })

        if row["is_available"]:
            return json.dumps({
                "status": "available", "doctor": doctor_name,
                "datetime": date_slot, "specialization": row["specialization"],
                "message": f"Slot is free — Dr. {doctor_name} at {date_slot}.",
            })

        with DBManager() as db:
            nxt = db.query(
                "SELECT date_slot::time AS t, date_slot::date AS d "
                "FROM doctor_availability "
                "WHERE doctor_name = %s AND is_available = TRUE AND date_slot > %s "
                "ORDER BY date_slot LIMIT 3",
                (doctor_name.lower(), date_slot),
            )

        return json.dumps({
            "status": "taken", "doctor": doctor_name, "datetime": date_slot,
            "message": "That slot is already booked.",
            "next_available": [f"{s['d']} at {str(s['t'])[:5]}" for s in nxt],
        }, default=str)

    except Exception as exc:
        log.error("check_slot_available: %s", exc)
        return json.dumps({"status": "error", "message": str(exc)})


@tool
def list_doctors_by_specialization(specialization: str) -> str:
    """
    List active doctors for a dental specialization with 30-day open-slot counts.

    Args:
        specialization: One of: general_dentist, cosmetic_dentist, orthodontist,
            pediatric_dentist, prosthodontist, oral_surgeon, emergency_dentist.
            Short aliases also accepted: ortho, cosmetic, emergency, general.

    Returns:
        JSON with doctor profiles and availability counts.
    """
    try:
        spec = specialization.lower().strip().replace(" ", "_")
        spec = SPEC_ALIASES.get(spec, spec)

        with DBManager() as db:
            doctors = db.query(
                """
                SELECT d.doctor_name, d.specialization, d.years_exp, d.bio,
                    COUNT(da.slot_id) FILTER (
                        WHERE da.is_available = TRUE
                          AND da.date_slot BETWEEN NOW() AND NOW() + INTERVAL '30 days'
                    ) AS open_slots
                FROM doctors d
                LEFT JOIN doctor_availability da ON d.doctor_name = da.doctor_name
                WHERE d.specialization = %s AND d.active = TRUE
                GROUP BY d.doctor_name, d.specialization, d.years_exp, d.bio
                ORDER BY open_slots DESC
                """,
                (spec,),
            )

            if not doctors:
                doctors = db.query(
                    "SELECT doctor_name, specialization, years_exp, bio "
                    "FROM doctors WHERE specialization ILIKE %s AND active = TRUE",
                    (f"%{spec}%",),
                )

            if not doctors:
                all_specs = db.query(
                    "SELECT DISTINCT specialization FROM doctors ORDER BY specialization"
                )
                return json.dumps({
                    "status": "not_found",
                    "message": f"No doctors found for '{specialization}'.",
                    "available": [r["specialization"] for r in all_specs],
                    "what_each_treats": SPECIALIZATIONS,
                })

        return json.dumps({
            "status": "ok", "specialization": spec,
            "description": SPECIALIZATIONS.get(spec, "Dental specialist"),
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
        })

    except Exception as exc:
        log.error("list_doctors_by_specialization: %s", exc)
        return json.dumps({"status": "error", "message": str(exc)})


@tool
def booking_agent(
    patient_id: int,
    doctor_name: str,
    date_slot: str,
    reason: str,
    patient_email: Optional[str] = None,
) -> str:
    """
    Book a dental appointment atomically. Verifies availability, creates the
    appointment record, marks the slot as booked, returns a confirmation code.

    Args:
        patient_id:    Patient's numeric ID (e.g., 1000048)
        doctor_name:   Doctor's full name (e.g., 'john doe')
        date_slot:     Appointment datetime ('2026-07-08 09:00:00')
        reason:        Reason for visit (e.g., 'Teeth cleaning')
        patient_email: Optional email to store on the patient record

    Returns:
        JSON with confirmation code and appointment details, or failure reason.
    """
    try:
        with DBManager() as db:
            # 1: Verify slot is free
            slot = db.query_one(
                "SELECT slot_id, is_available, specialization, slot_duration_min "
                "FROM doctor_availability WHERE doctor_name = %s AND date_slot = %s",
                (doctor_name.lower(), date_slot),
            )
            if not slot:
                return json.dumps({
                    "status": "error",
                    "message": f"No slot for Dr. {doctor_name} at {date_slot}. "
                               "Use get_availability to find valid slots.",
                })
            if not slot["is_available"]:
                return json.dumps({
                    "status": "slot_taken",
                    "message": "Slot already booked. Use get_availability to find a free one.",
                })

            # 2: Upsert patient
            patient = db.query_one(
                "SELECT patient_id FROM patients WHERE patient_id = %s", (patient_id,)
            )
            if not patient:
                db.execute(
                    "INSERT INTO patients (patient_id, first_name, last_name, email) "
                    "VALUES (%s, %s, %s, %s) ON CONFLICT (patient_id) DO NOTHING",
                    (patient_id, "New", "Patient",
                     patient_email or f"patient{patient_id}@clinic.com"),
                )

            code = _conf_code(patient_id)

            # 3: Mark slot booked
            db.execute(
                "UPDATE doctor_availability "
                "SET is_available = FALSE, patient_to_attend = %s "
                "WHERE doctor_name = %s AND date_slot = %s",
                (patient_id, doctor_name.lower(), date_slot),
            )

            # 4: Create appointment  — RETURNING id to get generated PK
            appt_id = db.execute(
                "INSERT INTO appointments "
                "(patient_id, doctor_name, specialization, appointment_dt, "
                " reason, confirmation_code, status) "
                "VALUES (%s, %s, %s, %s, %s, %s, 'scheduled') RETURNING id",
                (patient_id, doctor_name.lower(), slot["specialization"],
                 date_slot, reason, code),
            )

            # 5: Store email if provided
            if patient_email:
                db.execute(
                    "UPDATE patients SET email = %s WHERE patient_id = %s",
                    (patient_email, patient_id),
                )

            # 6: Log session
            # FIX: conversation_sessions has NO id column — do NOT add RETURNING id.
            # Use session_id (VARCHAR PK) + ON CONFLICT to avoid duplicates.
            db.execute(
                "INSERT INTO conversation_sessions "
                "(session_id, patient_id, intent, outcome, appointment_id) "
                "VALUES (%s, %s, 'booking', 'success', %s) "
                "ON CONFLICT (session_id) DO UPDATE SET "
                "outcome = EXCLUDED.outcome, appointment_id = EXCLUDED.appointment_id",
                (f"booking-{appt_id}", patient_id, appt_id),
            )

        dt = datetime.strptime(date_slot, "%Y-%m-%d %H:%M:%S")
        return json.dumps({
            "status": "booked",
            "confirmation_code": code,
            "appointment_id": appt_id,
            "patient_id": patient_id,
            "doctor": doctor_name.title(),
            "specialization": slot["specialization"],
            "date": dt.strftime("%A, %B %d, %Y"),
            "time": dt.strftime("%I:%M %p"),
            "duration": f"{slot['slot_duration_min']} minutes",
            "reason": reason,
            "message": (
                f"Appointment confirmed! Code: {code}. "
                "Please arrive 10 minutes early. "
                "Call (555) DENTIST to cancel or reschedule."
            ),
        })

    except Exception as exc:
        log.error("booking_agent: %s", exc)
        return json.dumps({"status": "error", "message": str(exc)})


@tool
def cancellation_agent(
    confirmation_code: str,
    patient_id: int,
    cancellation_reason: Optional[str] = None,
) -> str:
    """
    Cancel an existing appointment and free the slot.
    Requires BOTH confirmation code AND patient ID for identity verification.

    Args:
        confirmation_code:   Booking code (e.g., 'DENT-0048-AB3X7K')
        patient_id:          Patient ID for verification
        cancellation_reason: Optional reason

    Returns:
        JSON confirmation or error reason.
    """
    try:
        with DBManager() as db:
            appt = db.query_one(
                "SELECT id, doctor_name, appointment_dt, specialization, status "
                "FROM appointments WHERE confirmation_code = %s AND patient_id = %s",
                (confirmation_code, patient_id),
            )
            if not appt:
                return json.dumps({
                    "status": "not_found",
                    "message": "No appointment found with that code and patient ID.",
                })
            if appt["status"] in ("cancelled", "completed"):
                return json.dumps({
                    "status": "already_done",
                    "message": f"Appointment already {appt['status']}.",
                })

            appt_dt = appt["appointment_dt"]
            if isinstance(appt_dt, datetime):
                hours = (appt_dt - datetime.now()).total_seconds() / 3600
                if 0 < hours < 2:
                    return json.dumps({
                        "status": "too_late",
                        "message": (
                            f"Less than 2h away ({hours:.1f}h). "
                            "Please call (555) DENTIST directly."
                        ),
                    })

            db.execute(
                "UPDATE appointments SET status='cancelled', cancelled_at=NOW(), "
                "cancellation_reason=%s WHERE id = %s",
                (cancellation_reason or "Patient request", appt["id"]),
            )
            db.execute(
                "UPDATE doctor_availability SET is_available=TRUE, patient_to_attend=NULL "
                "WHERE doctor_name=%s AND date_slot=%s",
                (appt["doctor_name"], appt_dt),
            )

        return json.dumps({
            "status": "cancelled",
            "appointment_id": appt["id"],
            "confirmation_code": confirmation_code,
            "doctor": appt["doctor_name"].title(),
            "was_scheduled_for": str(appt_dt)[:16],
            "message": (
                f"Appointment on {str(appt_dt)[:16]} with Dr. "
                f"{appt['doctor_name'].title()} has been cancelled. "
                "Slot is now open for others."
            ),
        }, default=str)

    except Exception as exc:
        log.error("cancellation_agent: %s", exc)
        return json.dumps({"status": "error", "message": str(exc)})


@tool
def rescheduling_agent(
    confirmation_code: str,
    patient_id: int,
    new_date_slot: str,
    reason: Optional[str] = None,
) -> str:
    """
    Reschedule an existing appointment. Old slot freed, new slot booked atomically.

    Args:
        confirmation_code: Original confirmation code
        patient_id:        Patient ID for verification
        new_date_slot:     New datetime (e.g., '2026-08-15 10:00:00')
        reason:            Reason for rescheduling (optional)

    Returns:
        JSON with new confirmation code, or error reason.
    """
    try:
        with DBManager() as db:
            appt = db.query_one(
                "SELECT id, doctor_name, appointment_dt, specialization, status "
                "FROM appointments WHERE confirmation_code = %s AND patient_id = %s",
                (confirmation_code, patient_id),
            )
            if not appt:
                return json.dumps({"status": "not_found",
                                   "message": "Appointment not found. Check code and patient ID."})
            if appt["status"] in ("cancelled", "completed"):
                return json.dumps({
                    "status": "invalid_status",
                    "message": f"Cannot reschedule — appointment is already {appt['status']}.",
                })

            new_slot = db.query_one(
                "SELECT is_available FROM doctor_availability "
                "WHERE doctor_name=%s AND date_slot=%s",
                (appt["doctor_name"], new_date_slot),
            )
            if not new_slot:
                return json.dumps({
                    "status": "slot_not_found",
                    "message": f"No entry for {new_date_slot}. Use get_availability first.",
                })
            if not new_slot["is_available"]:
                return json.dumps({"status": "slot_taken",
                                   "message": f"{new_date_slot} is already booked."})

            new_code = _conf_code(patient_id, prefix="DRSCH")

            db.execute(
                "UPDATE doctor_availability SET is_available=TRUE, patient_to_attend=NULL "
                "WHERE doctor_name=%s AND date_slot=%s",
                (appt["doctor_name"], appt["appointment_dt"]),
            )
            db.execute(
                "UPDATE doctor_availability SET is_available=FALSE, patient_to_attend=%s "
                "WHERE doctor_name=%s AND date_slot=%s",
                (patient_id, appt["doctor_name"], new_date_slot),
            )
            db.execute(
                "UPDATE appointments SET appointment_dt=%s, status='rescheduled', "
                "confirmation_code=%s, notes=%s WHERE id=%s",
                (new_date_slot, new_code, reason or "Patient rescheduled", appt["id"]),
            )

        old_dt = str(appt["appointment_dt"])[:16]
        new_dt = datetime.strptime(new_date_slot, "%Y-%m-%d %H:%M:%S")
        return json.dumps({
            "status": "rescheduled",
            "old_confirmation": confirmation_code,
            "new_confirmation": new_code,
            "doctor": appt["doctor_name"].title(),
            "old_datetime": old_dt,
            "new_date": new_dt.strftime("%A, %B %d, %Y"),
            "new_time": new_dt.strftime("%I:%M %p"),
            "message": (
                f"Rescheduled! Old slot ({old_dt}) freed. "
                f"New: {new_dt.strftime('%A %B %d at %I:%M %p')} — Code: {new_code}"
            ),
        }, default=str)

    except Exception as exc:
        log.error("rescheduling_agent: %s", exc)
        return json.dumps({"status": "error", "message": str(exc)})


# ──────────────────────────────────────────────────────────
# TOOL REGISTRY
# ──────────────────────────────────────────────────────────

ALL_TOOLS = [
    get_availability, get_patient_appointments, check_slot_available,
    list_doctors_by_specialization, booking_agent, cancellation_agent, rescheduling_agent,
]
_tool_node = ToolNode(ALL_TOOLS)

# ──────────────────────────────────────────────────────────
# SYSTEM PROMPTS
# ──────────────────────────────────────────────────────────

_SYS_EMERGENCY = SystemMessage(content="""
You are a compassionate dental emergency triage assistant at DentAI Pro.
PROTOCOL:
1. Acknowledge pain FIRST — empathy before anything else
2. Classify: CRITICAL (ER now: spreading swelling, can't breathe) vs URGENT (same-day)
3. Knocked-out tooth: hold by crown, keep in milk, get here within 30 min
4. Abscess: cold pack only — NOT heat
5. Use get_availability to check emergency slots; offer to book NOW
6. OTC pain relief: ibuprofen (if not contraindicated) + cold pack
Emergency dentists: Daniel Miller, Susan Davis | (555) DENTIST
""".strip())

_SYS_CANCEL = SystemMessage(content="""
You handle appointment cancellations at DentAI Pro.
WORKFLOW:
1. Express understanding — no judgment
2. Ask for: confirmation code AND patient ID (both required)
3. No code? Use get_patient_appointments to find it
4. Use cancellation_agent
5. Confirm cancellation; offer to rebook
6. Within 2 h: direct to (555) DENTIST
""".strip())

_SYS_RESCHEDULE = SystemMessage(content="""
You handle appointment rescheduling at DentAI Pro.
WORKFLOW:
1. Collect confirmation code and patient ID
2. get_patient_appointments to show current bookings
3. Ask for preferred new date
4. get_availability to show open slots with the same doctor
5. Confirm new slot with patient
6. rescheduling_agent — atomic swap, zero double-booking risk
7. Deliver NEW confirmation code clearly
""".strip())

_SYS_HISTORY = SystemMessage(content="""
You help patients view their appointment history at DentAI Pro.
WORKFLOW:
1. Ask for patient ID
2. get_patient_appointments
3. Upcoming appointments first; past as summary
4. Format: Date | Doctor | Reason | Status | Code
5. Offer to book, reschedule, or cancel
""".strip())

_SYS_GENERAL = SystemMessage(content="""
You are the friendly AI assistant for DentAI Pro Dental Clinic.
Hours: Mon–Fri 8am–4:30pm | Sat 9am–2pm | Emergency: (555) DENTIST

You can help with: booking, cancellations, rescheduling, procedure questions,
specialist routing, emergencies, insurance, and dental health tips.

FAQ: Cleaning 30–60 min every 6 months | Filling 30–45 min | Root canal 60–90 min.
Always end with a clear next-step offer.
""".strip())


# ──────────────────────────────────────────────────────────
# NODES (all use _llm_invoke_with_retry)
# ──────────────────────────────────────────────────────────

def triage_node(state: DentalState) -> DentalState:
    msg = state["messages"][-1].content.lower()
    is_emergency = any(kw in msg for kw in EMERGENCY_KEYWORDS)

    if is_emergency:
        intent = "emergency"
    elif any(w in msg for w in ("book", "schedule", "appointment", "see the doctor", "come in")):
        intent = "booking"
    elif any(w in msg for w in ("cancel", "cancellation", "won't make it", "can't come")):
        intent = "cancel"
    elif any(w in msg for w in ("reschedule", "move", "change my appointment", "different time")):
        intent = "reschedule"
    elif any(w in msg for w in ("my appointments", "history", "past visits", "upcoming", "when is my")):
        intent = "patient_history"
    elif any(w in msg for w in ("who", "doctors", "specialist", "which doctor")):
        intent = "doctor_info"
    else:
        intent = "general"

    detected_spec: Optional[str] = next(
        (spec for kw, spec in PROCEDURE_TO_SPEC.items() if kw in msg), None
    )
    analytics = list(state.get("analytics", []))
    analytics.append(f"triage|intent={intent}|emergency={is_emergency}|spec={detected_spec}")

    return {
        **state,
        "is_emergency": is_emergency,
        "detected_intent": intent,
        "detected_specialization": detected_spec,
        "current_node": "triage",
        "analytics": analytics,
    }


def emergency_node(state: DentalState) -> DentalState:
    llm = get_llm(0.15).bind_tools([get_availability, booking_agent])
    return {**state,
            "messages": [_llm_invoke_with_retry(llm, [_SYS_EMERGENCY] + state["messages"])],
            "current_node": "emergency"}


def booking_node(state: DentalState) -> DentalState:
    spec = state.get("detected_specialization")
    hint = (
        f"\nDetected procedure need: {spec}. "
        f"Use list_doctors_by_specialization('{spec}') first."
    ) if spec else ""
    system = SystemMessage(content=f"""
You are a professional dental scheduler at DentAI Pro — warm, efficient, expert.
BOOKING WORKFLOW:
1. Procedure? → list_doctors_by_specialization
2. Preferred date? → get_availability
3. Specific slot? → check_slot_available
4. Collect: patient ID, doctor, date/time, reason
5. booking_agent → give code clearly
6. "Please arrive 10 minutes early for paperwork"
ROUTING: cleaning/filling → general_dentist | whitening/veneers → cosmetic_dentist |
braces/Invisalign → orthodontist | children → pediatric_dentist |
crowns/implants → prosthodontist | wisdom/extractions → oral_surgeon |
severe pain → emergency_dentist{hint}
""".strip())
    llm = get_llm(0.3).bind_tools(ALL_TOOLS)
    return {**state,
            "messages": [_llm_invoke_with_retry(llm, [system] + state["messages"])],
            "current_node": "booking"}


def cancel_node(state: DentalState) -> DentalState:
    llm = get_llm(0.3).bind_tools([get_patient_appointments, cancellation_agent])
    return {**state,
            "messages": [_llm_invoke_with_retry(llm, [_SYS_CANCEL] + state["messages"])],
            "current_node": "cancel"}


def reschedule_node(state: DentalState) -> DentalState:
    llm = get_llm(0.3).bind_tools([get_patient_appointments, get_availability, rescheduling_agent])
    return {**state,
            "messages": [_llm_invoke_with_retry(llm, [_SYS_RESCHEDULE] + state["messages"])],
            "current_node": "reschedule"}


def patient_history_node(state: DentalState) -> DentalState:
    llm = get_llm(0.3).bind_tools([get_patient_appointments])
    return {**state,
            "messages": [_llm_invoke_with_retry(llm, [_SYS_HISTORY] + state["messages"])],
            "current_node": "patient_history"}


def doctor_info_node(state: DentalState) -> DentalState:
    system = SystemMessage(content=f"""
You help patients find the right dental specialist at DentAI Pro.
SPECIALIZATIONS:\n{_SPECS_JSON}
WORKFLOW: listen → recommend specialization → list_doctors_by_specialization → offer to book
""".strip())
    llm = get_llm(0.4).bind_tools([list_doctors_by_specialization, get_availability])
    return {**state,
            "messages": [_llm_invoke_with_retry(llm, [system] + state["messages"])],
            "current_node": "doctor_info"}


def general_node(state: DentalState) -> DentalState:
    llm = get_llm(0.5)
    return {**state,
            "messages": [_llm_invoke_with_retry(llm, [_SYS_GENERAL] + state["messages"])],
            "current_node": "general"}


# ──────────────────────────────────────────────────────────
# ROUTING
# ──────────────────────────────────────────────────────────

_INTENT_MAP: dict[str, str] = {
    "emergency": "emergency", "booking": "booking", "cancel": "cancel",
    "reschedule": "reschedule", "patient_history": "patient_history",
    "doctor_info": "doctor_info", "general": "general",
}
_ACTION_NODES = frozenset(
    ("emergency", "booking", "cancel", "reschedule", "patient_history", "doctor_info")
)


def _route_triage(state: DentalState) -> str:
    return "emergency" if state["is_emergency"] else \
        _INTENT_MAP.get(state.get("detected_intent", "general"), "general")


def _route_after_action(state: DentalState) -> str:
    return "tools" if getattr(state["messages"][-1], "tool_calls", None) else END


def _route_after_tools(state: DentalState) -> str:
    node = state.get("current_node", "general")
    return node if node in _ACTION_NODES else "general"


# ──────────────────────────────────────────────────────────
# GRAPH (singleton — lazy-init, thread-safe)
# ──────────────────────────────────────────────────────────

_graph = None
_graph_lock = Lock()


def build_graph():
    g = StateGraph(DentalState)
    for name, fn in {
        "triage": triage_node, "emergency": emergency_node,
        "booking": booking_node, "cancel": cancel_node,
        "reschedule": reschedule_node, "patient_history": patient_history_node,
        "doctor_info": doctor_info_node, "general": general_node,
        "tools": _tool_node,
    }.items():
        g.add_node(name, fn)

    g.set_entry_point("triage")
    g.add_conditional_edges("triage", _route_triage, _INTENT_MAP)
    for node in _ACTION_NODES:
        g.add_conditional_edges(node, _route_after_action, {"tools": "tools", END: END})
    g.add_conditional_edges(
        "tools", _route_after_tools,
        {n: n for n in _ACTION_NODES} | {"general": "general"},
    )
    g.add_edge("general", END)
    return g.compile()


def get_graph():
    global _graph
    if _graph is None:
        with _graph_lock:
            if _graph is None:
                _graph = build_graph()
    return _graph


# ──────────────────────────────────────────────────────────
# PUBLIC API
# ──────────────────────────────────────────────────────────

def run_agent(
    message: str,
    session_id: str,
    channel: str = "web",
    patient_id: Optional[int] = None,
) -> dict:
    """
    Single-turn agent call. Safe to call from any channel.

    Args:
        message:    User's raw message text.
        session_id: Unique conversation identifier.
        channel:    'web' | 'telegram' | 'whatsapp' | 'cli'
        patient_id: Previously resolved patient ID (or None).

    Returns:
        dict with keys:
            reply        (str)  — plain-text reply for any channel
            is_emergency (bool) — True if emergency keywords detected
            intent       (str)  — detected intent label
            patient_id   (int | None) — resolved patient ID if known
            analytics    (list) — internal routing trace
    """
    try:
        result = get_graph().invoke(make_state(message, session_id, channel, patient_id))
    except Exception as exc:
        log.error("run_agent graph error: %s", exc, exc_info=True)
        return {
            "reply": "I'm having a technical issue right now. Please try again in a moment.",
            "is_emergency": False,
            "intent": "error",
            "patient_id": patient_id,
            "analytics": [],
        }

    ai_msg = next(
        (m for m in reversed(result["messages"]) if isinstance(m, AIMessage)), None
    )
    return {
        "reply":        ai_msg.content if ai_msg else "Sorry, something went wrong.",
        "is_emergency": result.get("is_emergency", False),
        "intent":       result.get("detected_intent", "general"),
        "patient_id":   result.get("patient_id"),
        "analytics":    result.get("analytics", []),
    }


# ──────────────────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────────────────

def _cli() -> None:
    print("\n" + "=" * 58)
    print("  DentAI Pro | Groq + Supabase | CLI mode")
    print("  Type 'quit' to exit")
    print("=" * 58 + "\n")
    sid = datetime.now().strftime("%Y%m%d%H%M%S")
    while True:
        try:
            user_input = input("You: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nGoodbye!")
            break
        if not user_input:
            continue
        if user_input.lower() in ("quit", "exit", "q"):
            print("Stay healthy! 🦷")
            break
        out = run_agent(user_input, sid, channel="cli")
        print(f"\nDentAI: {out['reply']}\n")
        if out["is_emergency"]:
            print("  [EMERGENCY DETECTED — call (555) DENTIST]\n")


if __name__ == "__main__":
    _cli()