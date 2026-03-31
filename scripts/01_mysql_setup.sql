-- ============================================================
--  DentAI Pro — MySQL Database Setup
-- ============================================================

-- Step 1: Create the database
CREATE DATABASE IF NOT EXISTS dentai_pro
    CHARACTER SET utf8mb4
    COLLATE utf8mb4_unicode_ci;

USE dentai_pro;

-- ============================================================
-- TABLE 1: doctors
-- Stores all doctor profiles and their specializations
-- ============================================================
CREATE TABLE IF NOT EXISTS doctors (
    doctor_id       INT AUTO_INCREMENT PRIMARY KEY,
    doctor_name     VARCHAR(100) NOT NULL,
    specialization  VARCHAR(100) NOT NULL,
    email           VARCHAR(150) UNIQUE,
    phone           VARCHAR(20),
    years_exp       INT DEFAULT 0,
    bio             TEXT,
    active          BOOLEAN DEFAULT TRUE,
    created_at      DATETIME DEFAULT CURRENT_TIMESTAMP,
    
    INDEX idx_specialization (specialization),
    INDEX idx_name (doctor_name)
) ENGINE=InnoDB;

-- ============================================================
-- TABLE 2: patients
-- Stores all patient profiles
-- ============================================================
CREATE TABLE IF NOT EXISTS patients (
    patient_id      INT PRIMARY KEY,          -- Matches patient_to_attend in CSV
    first_name      VARCHAR(100),
    last_name       VARCHAR(100),
    email           VARCHAR(150) UNIQUE,
    phone           VARCHAR(20),
    date_of_birth   DATE,
    insurance       VARCHAR(100),
    medical_notes   TEXT,
    created_at      DATETIME DEFAULT CURRENT_TIMESTAMP,
    
    INDEX idx_email (email),
    INDEX idx_phone (phone)
) ENGINE=InnoDB;

-- ============================================================
-- TABLE 3: doctor_availability
-- The core scheduling table — seeded from your CSV file
-- ============================================================
CREATE TABLE IF NOT EXISTS doctor_availability (
    slot_id             INT AUTO_INCREMENT PRIMARY KEY,
    date_slot           DATETIME NOT NULL,
    specialization      VARCHAR(100) NOT NULL,
    doctor_name         VARCHAR(100) NOT NULL,
    is_available        BOOLEAN NOT NULL DEFAULT TRUE,
    patient_to_attend   INT NULL,                          -- FK to patients
    slot_duration_min   INT DEFAULT 30,                    -- Duration in minutes
    last_updated        DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    
    INDEX idx_date_slot (date_slot),
    INDEX idx_doctor (doctor_name),
    INDEX idx_specialization (specialization),
    INDEX idx_available (is_available),
    INDEX idx_patient (patient_to_attend),
    
    -- Prevent double-booking: one doctor can't have two bookings at same time
    UNIQUE KEY uq_doctor_slot (doctor_name, date_slot)
) ENGINE=InnoDB;

-- ============================================================
-- TABLE 4: appointments
-- Booking records — created when a patient books a slot
-- ============================================================
CREATE TABLE IF NOT EXISTS appointments (
    appointment_id      INT AUTO_INCREMENT PRIMARY KEY,
    patient_id          INT NOT NULL,
    doctor_name         VARCHAR(100) NOT NULL,
    specialization      VARCHAR(100) NOT NULL,
    appointment_dt      DATETIME NOT NULL,
    duration_min        INT DEFAULT 30,
    status              ENUM('scheduled','completed','cancelled','rescheduled','no_show')
                        DEFAULT 'scheduled',
    reason              VARCHAR(255),                      -- Reason for visit
    notes               TEXT,                              -- Dentist notes
    confirmation_code   VARCHAR(20) UNIQUE,
    booked_at           DATETIME DEFAULT CURRENT_TIMESTAMP,
    cancelled_at        DATETIME NULL,
    cancellation_reason VARCHAR(255) NULL,
    
    INDEX idx_patient (patient_id),
    INDEX idx_doctor (doctor_name),
    INDEX idx_datetime (appointment_dt),
    INDEX idx_status (status),
    INDEX idx_confirmation (confirmation_code)
) ENGINE=InnoDB;

-- ============================================================
-- TABLE 5: conversation_sessions
-- Stores AI chat sessions for analytics and audit
-- ============================================================
CREATE TABLE IF NOT EXISTS conversation_sessions (
    session_id      VARCHAR(64) PRIMARY KEY,
    patient_id      INT NULL,
    started_at      DATETIME DEFAULT CURRENT_TIMESTAMP,
    ended_at        DATETIME NULL,
    intent          VARCHAR(100),                          -- booking/cancel/info/emergency
    outcome         VARCHAR(100),                          -- success/failed/abandoned
    turns           INT DEFAULT 0,
    appointment_id  INT NULL
) ENGINE=InnoDB;

-- ============================================================
-- SEED DATA: Insert the 10 doctors from the CSV
-- ============================================================
INSERT IGNORE INTO doctors (doctor_name, specialization, email, years_exp, bio) VALUES
('john doe',        'general_dentist',   'john.doe@dentaipro.com',       12, 'Expert in preventive care and routine dental procedures.'),
('emily johnson',   'general_dentist',   'emily.j@dentaipro.com',         8, 'Specialises in family dentistry and patient education.'),
('jane smith',      'cosmetic_dentist',  'jane.smith@dentaipro.com',     10, 'Award-winning cosmetic dentist with expertise in smile design.'),
('lisa brown',      'cosmetic_dentist',  'lisa.brown@dentaipro.com',      6, 'Specialises in teeth whitening, veneers and cosmetic bonding.'),
('kevin anderson',  'orthodontist',      'k.anderson@dentaipro.com',     15, 'Certified orthodontist specialising in Invisalign and braces.'),
('sarah wilson',    'pediatric_dentist', 's.wilson@dentaipro.com',        9, 'Child-friendly dentist creating positive dental experiences for kids.'),
('michael green',   'prosthodontist',    'm.green@dentaipro.com',         14, 'Expert in dental implants, crowns, bridges and dentures.'),
('robert martinez', 'oral_surgeon',      'r.martinez@dentaipro.com',      11, 'Oral surgeon specialising in wisdom teeth, implants and jaw surgery.'),
('daniel miller',   'emergency_dentist', 'd.miller@dentaipro.com',         7, 'Emergency care specialist available for urgent dental needs.'),
('susan davis',     'emergency_dentist', 's.davis@dentaipro.com',          5, 'Emergency dentist providing rapid pain relief and urgent treatment.');

-- ============================================================
-- USEFUL VIEWS
-- ============================================================

-- View: Available slots (easy query for the agent)
CREATE OR REPLACE VIEW v_available_slots AS
SELECT 
    da.slot_id,
    da.date_slot,
    da.doctor_name,
    da.specialization,
    da.slot_duration_min,
    DATE(da.date_slot) AS slot_date,
    TIME(da.date_slot) AS slot_time,
    DAYNAME(da.date_slot) AS day_of_week
FROM doctor_availability da
WHERE da.is_available = TRUE
  AND da.date_slot >= NOW()
ORDER BY da.date_slot;

-- View: Doctor schedule (booked slots)
CREATE OR REPLACE VIEW v_doctor_schedule AS
SELECT 
    da.date_slot,
    da.doctor_name,
    da.specialization,
    da.patient_to_attend AS patient_id,
    p.first_name,
    p.last_name,
    da.is_available
FROM doctor_availability da
LEFT JOIN patients p ON da.patient_to_attend = p.patient_id
ORDER BY da.doctor_name, da.date_slot;

-- View: Patient appointment history
CREATE OR REPLACE VIEW v_patient_appointments AS
SELECT 
    a.appointment_id,
    a.patient_id,
    CONCAT(p.first_name, ' ', p.last_name) AS patient_name,
    a.doctor_name,
    a.specialization,
    a.appointment_dt,
    a.status,
    a.reason,
    a.confirmation_code
FROM appointments a
LEFT JOIN patients p ON a.patient_id = p.patient_id
ORDER BY a.appointment_dt DESC;

-- ============================================================
-- STORED PROCEDURE: Book an appointment atomically
-- Prevents race conditions in concurrent booking
-- ============================================================
DELIMITER $$

CREATE PROCEDURE IF NOT EXISTS sp_book_appointment(
    IN  p_patient_id     INT,
    IN  p_doctor_name    VARCHAR(100),
    IN  p_date_slot      DATETIME,
    IN  p_reason         VARCHAR(255),
    IN  p_conf_code      VARCHAR(20),
    OUT p_result         VARCHAR(100)
)
BEGIN
    DECLARE slot_available BOOLEAN;
    DECLARE EXIT HANDLER FOR SQLEXCEPTION
    BEGIN
        ROLLBACK;
        SET p_result = 'ERROR: Database transaction failed';
    END;
    
    START TRANSACTION;
    
    -- Check slot is still available (with row lock)
    SELECT is_available INTO slot_available
    FROM doctor_availability
    WHERE doctor_name = p_doctor_name AND date_slot = p_date_slot
    FOR UPDATE;
    
    IF slot_available = TRUE THEN
        -- Mark slot as booked
        UPDATE doctor_availability
        SET is_available = FALSE, patient_to_attend = p_patient_id
        WHERE doctor_name = p_doctor_name AND date_slot = p_date_slot;
        
        -- Create appointment record
        INSERT INTO appointments
            (patient_id, doctor_name, specialization, appointment_dt, reason, confirmation_code)
        SELECT p_patient_id, p_doctor_name, specialization, p_date_slot, p_reason, p_conf_code
        FROM doctor_availability
        WHERE doctor_name = p_doctor_name AND date_slot = p_date_slot;
        
        COMMIT;
        SET p_result = 'SUCCESS';
    ELSE
        ROLLBACK;
        SET p_result = 'SLOT_TAKEN';
    END IF;
END$$

DELIMITER ;

SELECT 'Database setup complete! Run 02_csv_to_mysql.py next.' AS status;
