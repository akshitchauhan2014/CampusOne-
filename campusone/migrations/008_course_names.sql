CREATE TABLE students_v8 (
    id INTEGER PRIMARY KEY,
    enrollment_no TEXT NOT NULL UNIQUE COLLATE NOCASE,
    full_name TEXT NOT NULL,
    email TEXT,
    phone TEXT,
    course_name TEXT COLLATE NOCASE REFERENCES courses(name) ON UPDATE CASCADE ON DELETE SET NULL,
    semester INTEGER CHECK(semester IS NULL OR semester>=1),
    batch TEXT,
    status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active','inactive','graduated','withdrawn'))
);
INSERT INTO students_v8(id,enrollment_no,full_name,email,phone,course_name,semester,batch,status)
SELECT s.id,s.enrollment_no,s.full_name,s.email,s.phone,c.name,s.semester,s.batch,s.status
FROM students s LEFT JOIN courses c ON c.id=s.course_id;
DROP TABLE students;
ALTER TABLE students_v8 RENAME TO students;

CREATE TABLE subjects_v8 (
    id INTEGER PRIMARY KEY,
    course_name TEXT NOT NULL COLLATE NOCASE REFERENCES courses(name) ON UPDATE CASCADE ON DELETE RESTRICT,
    semester INTEGER NOT NULL CHECK(semester>=1),
    subject_code TEXT NOT NULL COLLATE NOCASE,
    name TEXT NOT NULL,
    UNIQUE(course_name,semester,subject_code)
);
INSERT INTO subjects_v8(id,course_name,semester,subject_code,name)
SELECT sub.id,c.name,sub.semester,sub.subject_code,sub.name
FROM subjects sub JOIN courses c ON c.id=sub.course_id;
DROP TABLE subjects;
ALTER TABLE subjects_v8 RENAME TO subjects;

CREATE TABLE fee_structures_v8 (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    course_name TEXT NOT NULL COLLATE NOCASE REFERENCES courses(name) ON UPDATE CASCADE ON DELETE RESTRICT,
    term TEXT NOT NULL,
    fee_head TEXT NOT NULL DEFAULT 'Tuition',
    amount_paise INTEGER NOT NULL CHECK (amount_paise >= 0),
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    UNIQUE(course_name,term,fee_head)
);
INSERT INTO fee_structures_v8(id,course_name,term,fee_head,amount_paise,created_at)
SELECT fs.id,c.name,fs.term,fs.fee_head,fs.amount_paise,fs.created_at
FROM fee_structures fs JOIN courses c ON c.id=fs.course_id;
DROP TABLE fee_structures;
ALTER TABLE fee_structures_v8 RENAME TO fee_structures;

CREATE INDEX idx_students_course_name ON students(course_name);
CREATE INDEX idx_fee_structures_course_name_term ON fee_structures(course_name,term);
DROP INDEX IF EXISTS idx_students_course;
DROP INDEX IF EXISTS idx_fee_structures_course_term;
