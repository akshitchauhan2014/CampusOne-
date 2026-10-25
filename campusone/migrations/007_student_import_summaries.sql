CREATE TABLE IF NOT EXISTS student_import_summaries (
    student_id INTEGER PRIMARY KEY REFERENCES students(id) ON DELETE CASCADE,
    attendance_percentage REAL CHECK (attendance_percentage IS NULL OR attendance_percentage BETWEEN 0 AND 100),
    result_percentage REAL CHECK (result_percentage IS NULL OR result_percentage BETWEEN 0 AND 100),
    total_fees_paise INTEGER CHECK (total_fees_paise IS NULL OR total_fees_paise >= 0),
    submitted_fees_paise INTEGER CHECK (submitted_fees_paise IS NULL OR submitted_fees_paise >= 0),
    pending_fees_paise INTEGER CHECK (pending_fees_paise IS NULL OR pending_fees_paise >= 0),
    source_name TEXT NOT NULL,
    imported_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
