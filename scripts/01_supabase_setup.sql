-- =============================================================
--  DentAI Pro — Supabase / PostgreSQL Schema
--  Run in: Supabase → SQL Editor → New Query → Run All
--
--  Differences from MySQL version:
--    AUTO_INCREMENT  → SERIAL / GENERATED ALWAYS AS IDENTITY
--    DATETIME        → TIMESTAMP WITH TIME ZONE
--    INSERT IGNORE   → ON CONFLICT DO NOTHING
--    DATE(col)       → col::date
--    TIME(col)       → col::time
--    DAYNAME(col)    → TO_CHAR(col, 'Day')
--    DATE_ADD()      → col + INTERVAL '30 days'
-- =============================================================

-- ── 1. Doctors ──────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS doctors (
    id              SERIAL PRIMARY KEY,
    doctor_name     VARCHAR(100) NOT NULL,
    specialization  VARCHAR(100) NOT NULL,
    email           VARCHAR(150) UNIQUE,
    phone           VARCHAR(25),
    years_exp       INT          DEFAULT 0,
    bio             TEXT,
    active          BOOLEAN      DEFAULT TRUE,
    created_at      TIMESTAMPTZ  DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_doctors_spec ON doctors (specialization);
CREATE INDEX IF NOT EXISTS idx_doctors_name ON doctors (doctor_name);

-- ── 2. Patients ─────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS patients (
    patient_id   INT          PRIMARY KEY,   -- matches CSV patient_to_attend
    first_name   VARCHAR(100),
    last_name    VARCHAR(100),
    email        VARCHAR(150) UNIQUE,
    phone        VARCHAR(25),
    date_of_birth DATE,
    insurance    VARCHAR(100),
    created_at   TIMESTAMPTZ  DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_patients_email ON patients (email);
CREATE INDEX IF NOT EXISTS idx_patients_phone ON patients (phone);

-- ── 3. Doctor Availability (imported from CSV) ──────────────
CREATE TABLE IF NOT EXISTS doctor_availability (
    slot_id            SERIAL       PRIMARY KEY,
    date_slot          TIMESTAMPTZ  NOT NULL,
    specialization     VARCHAR(100) NOT NULL,
    doctor_name        VARCHAR(100) NOT NULL,
    is_available       BOOLEAN      NOT NULL DEFAULT TRUE,
    patient_to_attend  INT          REFERENCES patients (patient_id) ON DELETE SET NULL,
    slot_duration_min  INT          DEFAULT 30,
    last_updated       TIMESTAMPTZ  DEFAULT NOW(),

    UNIQUE (doctor_name, date_slot)
);

CREATE INDEX IF NOT EXISTS idx_avail_date     ON doctor_availability (date_slot);
CREATE INDEX IF NOT EXISTS idx_avail_doctor   ON doctor_availability (doctor_name);
CREATE INDEX IF NOT EXISTS idx_avail_spec     ON doctor_availability (specialization);
CREATE INDEX IF NOT EXISTS idx_avail_avail    ON doctor_availability (is_available);
CREATE INDEX IF NOT EXISTS idx_avail_patient  ON doctor_availability (patient_to_attend);

-- ── 4. Appointments ─────────────────────────────────────────
CREATE TABLE IF NOT EXISTS appointments (
    id                  SERIAL PRIMARY KEY,
    patient_id          INT          NOT NULL REFERENCES patients (patient_id),
    doctor_name         VARCHAR(100) NOT NULL,
    specialization      VARCHAR(100) NOT NULL,
    appointment_dt      TIMESTAMPTZ  NOT NULL,
    duration_min        INT          DEFAULT 30,
    status              VARCHAR(20)  NOT NULL DEFAULT 'scheduled'
                            CHECK (status IN ('scheduled','completed','cancelled','rescheduled','no_show')),
    reason              VARCHAR(255),
    notes               TEXT,
    confirmation_code   VARCHAR(25)  UNIQUE,
    booked_at           TIMESTAMPTZ  DEFAULT NOW(),
    cancelled_at        TIMESTAMPTZ,
    cancellation_reason VARCHAR(255)
);

CREATE INDEX IF NOT EXISTS idx_appt_patient  ON appointments (patient_id);
CREATE INDEX IF NOT EXISTS idx_appt_doctor   ON appointments (doctor_name);
CREATE INDEX IF NOT EXISTS idx_appt_dt       ON appointments (appointment_dt);
CREATE INDEX IF NOT EXISTS idx_appt_status   ON appointments (status);
CREATE INDEX IF NOT EXISTS idx_appt_code     ON appointments (confirmation_code);

-- ── 5. Conversation Sessions ────────────────────────────────
CREATE TABLE IF NOT EXISTS conversation_sessions (
    session_id      VARCHAR(64)  PRIMARY KEY,
    patient_id      INT,
    channel         VARCHAR(20)  DEFAULT 'web',   -- web | whatsapp | telegram
    started_at      TIMESTAMPTZ  DEFAULT NOW(),
    ended_at        TIMESTAMPTZ,
    intent          VARCHAR(100),
    outcome         VARCHAR(100),
    turns           INT          DEFAULT 0,
    appointment_id  INT          REFERENCES appointments (id) ON DELETE SET NULL
);

-- ── 6. Seed Doctors ─────────────────────────────────────────
INSERT INTO doctors (doctor_name, specialization, email, years_exp, bio) VALUES
  ('john doe',        'general_dentist',   'john.doe@dentaipro.com',        12, 'Expert in preventive care and routine dental procedures.'),
  ('emily johnson',   'general_dentist',   'emily.johnson@dentaipro.com',    8, 'Specialises in family dentistry and patient education.'),
  ('jane smith',      'cosmetic_dentist',  'jane.smith@dentaipro.com',      10, 'Award-winning cosmetic dentist with expertise in smile design.'),
  ('lisa brown',      'cosmetic_dentist',  'lisa.brown@dentaipro.com',       6, 'Specialises in teeth whitening, veneers and cosmetic bonding.'),
  ('kevin anderson',  'orthodontist',      'k.anderson@dentaipro.com',      15, 'Certified orthodontist specialising in Invisalign and braces.'),
  ('sarah wilson',    'pediatric_dentist', 's.wilson@dentaipro.com',         9, 'Child-friendly dentist creating positive dental experiences for kids.'),
  ('michael green',   'prosthodontist',    'm.green@dentaipro.com',         14, 'Expert in dental implants, crowns, bridges and dentures.'),
  ('robert martinez', 'oral_surgeon',      'r.martinez@dentaipro.com',      11, 'Oral surgeon specialising in wisdom teeth, implants and jaw surgery.'),
  ('daniel miller',   'emergency_dentist', 'd.miller@dentaipro.com',         7, 'Emergency care specialist available for urgent dental needs.'),
  ('susan davis',     'emergency_dentist', 's.davis@dentaipro.com',          5, 'Emergency dentist providing rapid pain relief and urgent treatment.')
ON CONFLICT (email) DO NOTHING;

-- ── 7. Useful Views ─────────────────────────────────────────

-- Available future slots (what the booking agent queries most)
CREATE OR REPLACE VIEW v_available_slots AS
SELECT
    slot_id,
    date_slot,
    doctor_name,
    specialization,
    slot_duration_min,
    date_slot::date                      AS slot_date,
    date_slot::time                      AS slot_time,
    TRIM(TO_CHAR(date_slot, 'Day'))      AS day_of_week
FROM doctor_availability
WHERE is_available = TRUE
  AND date_slot >= NOW()
ORDER BY date_slot;

-- Doctor schedule with patient names
CREATE OR REPLACE VIEW v_doctor_schedule AS
SELECT
    da.date_slot,
    da.doctor_name,
    da.specialization,
    da.patient_to_attend            AS patient_id,
    p.first_name,
    p.last_name,
    da.is_available
FROM doctor_availability da
LEFT JOIN patients p ON da.patient_to_attend = p.patient_id
ORDER BY da.doctor_name, da.date_slot;

-- Patient appointment history
CREATE OR REPLACE VIEW v_patient_appointments AS
SELECT
    a.id                                        AS appointment_id,
    a.patient_id,
    CONCAT(p.first_name, ' ', p.last_name)      AS patient_name,
    a.doctor_name,
    a.specialization,
    a.appointment_dt,
    a.status,
    a.reason,
    a.confirmation_code
FROM appointments a
LEFT JOIN patients p ON a.patient_id = p.patient_id
ORDER BY a.appointment_dt DESC;

-- Channel analytics
CREATE OR REPLACE VIEW v_channel_stats AS
SELECT
    channel,
    COUNT(*)                                       AS total_sessions,
    COUNT(appointment_id)                          AS bookings_made,
    ROUND(COUNT(appointment_id)::numeric / NULLIF(COUNT(*), 0) * 100, 1) AS conversion_pct
FROM conversation_sessions
GROUP BY channel;

SELECT 'Supabase schema ready. Now run: python scripts/02_csv_to_supabase.py' AS status;
