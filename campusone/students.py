import re, sqlite3
from typing import Any

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_PHONE_RE = re.compile(r"^[0-9+()\-.\s]{5,20}$")

ALLOWED_STATUSES = {"active", "inactive", "graduated", "withdrawn"}
STUDENT_FIELDS = ("enrollment_no", "full_name", "father_name", "email", "phone", "course_name", "semester", "batch", "status")

class StudentValidationError(ValueError):
    def __init__(self, errors):
        self.errors = errors
        super().__init__("; ".join(f"{k}: {v}" for k, v in errors.items()))

def validate_student(data: dict[str, Any]) -> dict[str, Any]:
    clean = dict(data)
    errors = {}
    enrollment = str(clean.get("enrollment_no") or "").strip()
    name = str(clean.get("full_name") or "").strip()
    father_name = str(clean.get("father_name") or "").strip() or None
    if not enrollment: errors["enrollment_no"] = "Enrollment number is required."
    elif len(enrollment) > 80: errors["enrollment_no"] = "Maximum length is 80 characters."
    if not name: errors["full_name"] = "Full name is required."
    elif len(name) > 200: errors["full_name"] = "Maximum length is 200 characters."
    if father_name and len(father_name) > 200:
        errors["father_name"] = "Maximum length is 200 characters."
    email = str(clean.get("email") or "").strip() or None
    if email and (len(email) > 254 or not _EMAIL_RE.match(email)):
        errors["email"] = "Enter a valid email address."
    phone = str(clean.get("phone") or "").strip() or None
    if phone and not _PHONE_RE.match(phone):
        errors["phone"] = "Phone may contain only digits, spaces and + - ( ) . (5-20 characters)."
    batch = str(clean.get("batch") or "").strip() or None
    if batch and len(batch) > 40: errors["batch"] = "Maximum length is 40 characters."
    status = str(clean.get("status") or "active").strip().lower()
    if status not in ALLOWED_STATUSES:
        errors["status"] = "Use active, inactive, graduated, or withdrawn."
    semester = clean.get("semester")
    if semester in ("", None): semester = None
    else:
        try:
            semester = int(semester)
            if semester < 1 or semester > 20: raise ValueError
        except (TypeError, ValueError): errors["semester"] = "Semester must be a whole number from 1 to 20."
    course_name = str(clean.get("course_name") or "").strip() or None
    if course_name and len(course_name) > 120:
        errors["course_name"] = "Maximum length is 120 characters."
    clean.update(enrollment_no=enrollment, full_name=name, father_name=father_name, email=email,
                 phone=phone, semester=semester, course_name=course_name, batch=batch, status=status)
    if errors: raise StudentValidationError(errors)
    return clean

class StudentRepository:
    def __init__(self, db): self.db = db

    def create(self, data):
        d = validate_student(data)
        cols = ", ".join(STUDENT_FIELDS)
        marks = ", ".join("?" for _ in STUDENT_FIELDS)
        try:
            with self.db.transaction() as conn:
                cur = conn.execute(f"INSERT INTO students ({cols}) VALUES ({marks})",
                                   tuple(d.get(k) for k in STUDENT_FIELDS))
                sid = int(cur.lastrowid)
                self.db.write_audit(conn, "student_created", "student", sid,
                                    new={k: d.get(k) for k in STUDENT_FIELDS}, details="Student record created")
                return sid
        except sqlite3.IntegrityError as exc:
            if "unique" in str(exc).lower():
                raise StudentValidationError({"enrollment_no": "Enrollment number already exists."}) from exc
            raise

    def update(self, student_id, data):
        d = validate_student(data)
        assignments = ", ".join(f"{k} = ?" for k in STUDENT_FIELDS)
        try:
            with self.db.transaction() as conn:
                old = conn.execute("SELECT * FROM students WHERE id = ?", (student_id,)).fetchone()
                if old is None: raise LookupError("Student record was not found.")
                conn.execute(f"UPDATE students SET {assignments} WHERE id = ?",
                             tuple(d.get(k) for k in STUDENT_FIELDS) + (student_id,))
                before = {k: old[k] for k in STUDENT_FIELDS if old[k] != d.get(k)}
                self.db.write_audit(conn, "student_updated", "student", student_id, old=before,
                                    new={k: d.get(k) for k in before}, details="Student record updated")
        except sqlite3.IntegrityError as exc:
            if "unique" in str(exc).lower():
                raise StudentValidationError({"enrollment_no": "Enrollment number already exists."}) from exc
            raise StudentValidationError({"course_name": "Selected course does not exist."}) from exc

    def set_status(self, student_id, status):
        if status not in ALLOWED_STATUSES:
            raise StudentValidationError({"status": "Unsupported student status."})
        with self.db.transaction() as conn:
            old = conn.execute("SELECT status FROM students WHERE id = ?", (student_id,)).fetchone()
            if old is None: raise LookupError("Student record was not found.")
            conn.execute("UPDATE students SET status = ? WHERE id = ?", (status, student_id))
            self.db.write_audit(conn, "student_status_changed", "student", student_id,
                                old={"status": old["status"]}, new={"status": status}, details=f"Status set to {status}")

    def delete_many(self, student_ids):
        ids = list(dict.fromkeys(int(student_id) for student_id in student_ids))
        if not ids:
            raise ValueError("Select at least one student to delete.")
        with self.db.transaction() as conn:
            marks = ",".join("?" for _ in ids)
            rows = [dict(row) for row in conn.execute(
                f"SELECT id,enrollment_no,full_name,course_name,semester FROM students WHERE id IN ({marks})",
                ids,
            ).fetchall()]
            if len(rows) != len(ids):
                raise LookupError("One or more selected student records no longer exist. Refresh and try again.")

            for row in rows:
                self.db.write_audit(
                    conn, "student_deleted", "student", row["id"],
                    old={key: row[key] for key in ("enrollment_no", "full_name", "course_name", "semester")},
                    details="Student record and all associated student data deleted",
                )

            conn.execute(f"DELETE FROM attendance WHERE student_id IN ({marks})", ids)
            conn.execute(f"DELETE FROM results WHERE student_id IN ({marks})", ids)
            conn.execute(f"DELETE FROM fee_payments WHERE student_id IN ({marks})", ids)
            conn.execute(f"DELETE FROM student_fee_import_summaries WHERE student_id IN ({marks})", ids)
            conn.execute(f"DELETE FROM student_import_summaries WHERE student_id IN ({marks})", ids)
            conn.execute(f"DELETE FROM risk_predictions WHERE student_id IN ({marks})", ids)
            conn.execute(f"DELETE FROM registration_codes WHERE student_id IN ({marks})", ids)
            conn.execute(
                f"""DELETE FROM password_reset_codes
                    WHERE user_id IN (SELECT id FROM users WHERE student_id IN ({marks}))""",
                ids,
            )
            conn.execute(
                f"""DELETE FROM registration_attempts
                    WHERE enrollment_no IN ({",".join("?" for _ in rows)})""",
                [row["enrollment_no"] for row in rows],
            )
            conn.execute(f"DELETE FROM users WHERE student_id IN ({marks})", ids)
            conn.execute(f"DELETE FROM students WHERE id IN ({marks})", ids)
        return len(rows)

    def get(self, student_id):
        with self.db.connect() as conn:
            row = conn.execute("SELECT * FROM students WHERE id = ?", (student_id,)).fetchone()
        return dict(row) if row else None

    def _filters(self, query, status, course_name, semester):
        sql, args = "", []
        if query.strip():
            for term in query.split():
                esc = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
                pat = f"%{esc}%"
                conditions = ["""s.enrollment_no LIKE ? ESCAPE '\\' COLLATE NOCASE
                    OR s.full_name LIKE ? ESCAPE '\\' COLLATE NOCASE
                    OR COALESCE(s.father_name, '') LIKE ? ESCAPE '\\' COLLATE NOCASE
                    OR COALESCE(s.phone, '') LIKE ? ESCAPE '\\' COLLATE NOCASE
                    OR s.email LIKE ? ESCAPE '\\' COLLATE NOCASE
                    OR (s.full_name || ' ' || COALESCE(s.father_name, '')) LIKE ? ESCAPE '\\' COLLATE NOCASE"""]
                args.extend([pat] * 6)
                phone_digits = re.sub(r"[\s+().-]", "", term)
                if phone_digits.isdigit() and phone_digits:
                    conditions.append("""REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(
                        COALESCE(s.phone, ''), '+', ''), ' ', ''), '-', ''), '(', ''), ')', ''), '.', '')
                        LIKE ? ESCAPE '\\' COLLATE NOCASE""")
                    args.append(f"%{phone_digits}%")
                sql += " AND (" + " OR ".join(conditions) + ")"
        if status: sql += " AND s.status = ?"; args.append(status)
        if course_name is not None: sql += " AND s.course_name = ?"; args.append(course_name)
        if semester is not None: sql += " AND s.semester = ?"; args.append(semester)
        return sql, args

    def count(self, query="", status=None, course_name=None, semester=None):
        sql, args = self._filters(query, status, course_name, semester)
        with self.db.connect() as conn:
            return conn.execute("SELECT COUNT(*) FROM students s WHERE 1=1" + sql, args).fetchone()[0]

    def search(self, query="", status=None, course_name=None, semester=None, limit=None, offset=0):
        """limit/offset let the UI page through large rosters instead of loading every row."""
        filt, args = self._filters(query, status, course_name, semester)
        sql = "SELECT s.* FROM students s WHERE 1=1" + filt
        sql += " ORDER BY s.full_name COLLATE NOCASE, s.enrollment_no"
        if limit is not None:
            sql += " LIMIT ? OFFSET ?"; args = args + [int(limit), int(offset)]
        with self.db.connect() as conn:
            return [dict(r) for r in conn.execute(sql, args).fetchall()]
