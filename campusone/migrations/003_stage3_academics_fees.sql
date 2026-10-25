CREATE TABLE IF NOT EXISTS attendance (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id INTEGER NOT NULL REFERENCES students(id) ON DELETE RESTRICT,
    subject_id INTEGER NOT NULL REFERENCES subjects(id) ON DELETE RESTRICT,
    period_date TEXT NOT NULL,
    period_label TEXT NOT NULL DEFAULT 'regular',
    classes_held INTEGER NOT NULL CHECK (classes_held >= 0),
    classes_attended INTEGER NOT NULL CHECK (classes_attended >= 0 AND classes_attended <= classes_held),
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    UNIQUE(student_id, subject_id, period_date, period_label)
);

CREATE TABLE IF NOT EXISTS results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id INTEGER NOT NULL REFERENCES students(id) ON DELETE RESTRICT,
    subject_id INTEGER NOT NULL REFERENCES subjects(id) ON DELETE RESTRICT,
    exam_type TEXT NOT NULL,
    term TEXT NOT NULL,
    marks_obtained REAL NOT NULL CHECK (marks_obtained >= 0),
    maximum_marks REAL NOT NULL CHECK (maximum_marks > 0 AND marks_obtained <= maximum_marks),
    recorded_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    UNIQUE(student_id, subject_id, exam_type, term)
);

CREATE TABLE IF NOT EXISTS fee_structures (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    course_id INTEGER NOT NULL REFERENCES courses(id) ON DELETE RESTRICT,
    term TEXT NOT NULL,
    fee_head TEXT NOT NULL DEFAULT 'Tuition',
    amount_paise INTEGER NOT NULL CHECK (amount_paise >= 0),
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    UNIQUE(course_id, term, fee_head)
);

CREATE TABLE IF NOT EXISTS fee_payments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id INTEGER NOT NULL REFERENCES students(id) ON DELETE RESTRICT,
    amount_paise INTEGER NOT NULL CHECK (amount_paise > 0),
    payment_date TEXT NOT NULL,
    term TEXT NOT NULL,
    payment_mode TEXT NOT NULL CHECK (payment_mode IN ('cash','upi','card','bank_transfer','cheque','other')),
    receipt_number TEXT NOT NULL UNIQUE COLLATE NOCASE,
    notes TEXT,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);

CREATE TABLE IF NOT EXISTS app_settings (
    setting_key TEXT PRIMARY KEY,
    setting_value TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);

CREATE INDEX IF NOT EXISTS idx_attendance_student_subject ON attendance(student_id, subject_id);
CREATE INDEX IF NOT EXISTS idx_attendance_date ON attendance(period_date);
CREATE INDEX IF NOT EXISTS idx_results_student_subject ON results(student_id, subject_id);
CREATE INDEX IF NOT EXISTS idx_fee_payments_student_date ON fee_payments(student_id, payment_date);
CREATE INDEX IF NOT EXISTS idx_fee_structures_course_term ON fee_structures(course_id, term);
