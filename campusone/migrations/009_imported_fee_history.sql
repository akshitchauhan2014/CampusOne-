CREATE TABLE IF NOT EXISTS student_fee_import_summaries (
    student_id INTEGER NOT NULL REFERENCES students(id) ON DELETE CASCADE,
    term TEXT NOT NULL,
    total_fees_paise INTEGER CHECK (total_fees_paise IS NULL OR total_fees_paise >= 0),
    submitted_fees_paise INTEGER CHECK (submitted_fees_paise IS NULL OR submitted_fees_paise >= 0),
    pending_fees_paise INTEGER CHECK (pending_fees_paise IS NULL OR pending_fees_paise >= 0),
    source_name TEXT NOT NULL,
    imported_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    PRIMARY KEY (student_id, term)
);

INSERT INTO student_fee_import_summaries(
    student_id,term,total_fees_paise,submitted_fees_paise,pending_fees_paise,source_name,imported_at
)
SELECT s.id,
       CASE WHEN s.semester IS NULL THEN 'Unassigned' ELSE 'Semester ' || s.semester END,
       x.total_fees_paise,x.submitted_fees_paise,x.pending_fees_paise,x.source_name,x.imported_at
FROM student_import_summaries x
JOIN students s ON s.id=x.student_id
WHERE x.total_fees_paise IS NOT NULL
   OR x.submitted_fees_paise IS NOT NULL
   OR x.pending_fees_paise IS NOT NULL;

CREATE INDEX idx_student_fee_import_summaries_term
    ON student_fee_import_summaries(term,student_id);
