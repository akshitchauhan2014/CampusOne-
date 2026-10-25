from __future__ import annotations
import math, sqlite3, json
from datetime import date
from .students import StudentValidationError

class AcademicValidationError(ValueError):
    def __init__(self, errors):
        self.errors = errors
        super().__init__("; ".join(f"{k}: {v}" for k, v in errors.items()))

def _whole(value):
    """Accept 12, "12", 12.0 - reject 12.5, True, NaN, text. (int() alone silently truncated 12.9.)"""
    if isinstance(value, bool): raise ValueError
    if isinstance(value, float):
        if not value.is_integer(): raise ValueError
        return int(value)
    return int(str(value).strip())

def _iso_date(value, field):
    try: return date.fromisoformat(str(value or "").strip()).isoformat()
    except ValueError: raise AcademicValidationError({field: "Date must use YYYY-MM-DD."})

def _text(value, field, limit=80):
    value = _required(value, field)
    if len(value) > limit: raise AcademicValidationError({field: f"Maximum length is {limit} characters."})
    return value

def attendance_percentage(classes_held: int, classes_attended: int) -> float | None:
    try:
        held, attended = _whole(classes_held), _whole(classes_attended)
    except (TypeError, ValueError):
        raise AcademicValidationError({"attendance": "Class counts must be whole numbers."})
    errors = {}
    if held < 0: errors["classes_held"] = "Classes held cannot be negative."
    if attended < 0: errors["classes_attended"] = "Classes attended cannot be negative."
    if attended > held: errors["classes_attended"] = "Attended classes cannot exceed classes held."
    if errors: raise AcademicValidationError(errors)
    return round(attended / held * 100, 2) if held > 0 else None

def _required(value, field):
    value = str(value or "").strip()
    if not value: raise AcademicValidationError({field: f"{field.replace('_',' ').title()} is required."})
    return value

class AcademicRepository:
    def __init__(self, db, low_attendance_threshold=None):
        self.db = db
        if low_attendance_threshold is not None:
            self.set_low_attendance_threshold(low_attendance_threshold)

    def set_low_attendance_threshold(self, threshold):
        try: threshold = float(threshold)
        except (TypeError, ValueError): raise AcademicValidationError({"threshold":"Threshold must be a number."})
        if not math.isfinite(threshold) or not 0 <= threshold <= 100: raise AcademicValidationError({"threshold":"Threshold must be between 0 and 100."})
        with self.db.transaction() as c:
            old = c.execute("SELECT setting_value FROM app_settings WHERE setting_key='low_attendance_threshold'").fetchone()
            c.execute("""INSERT INTO app_settings(setting_key,setting_value) VALUES('low_attendance_threshold',?)
                ON CONFLICT(setting_key) DO UPDATE SET setting_value=excluded.setting_value,
                updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now')""", (str(threshold),))
            self.db.write_audit(c, "attendance_threshold_changed", "setting", "low_attendance_threshold",
                                old={"value": old[0] if old else None}, new={"value": threshold})

    def get_low_attendance_threshold(self):
        with self.db.connect() as c:
            row = c.execute("SELECT setting_value FROM app_settings WHERE setting_key='low_attendance_threshold'").fetchone()
        return float(row[0]) if row else 75.0

    def record_attendance(self, student_id, subject_id, period_date, classes_held, classes_attended, period_label="regular"):
        period_date = _iso_date(period_date, "period_date")
        period_label = _text(period_label, "period_label")
        attendance_percentage(classes_held,classes_attended)
        classes_held, classes_attended = _whole(classes_held), _whole(classes_attended)
        try:
            with self.db.transaction() as c:
                old = c.execute("SELECT classes_held,classes_attended FROM attendance WHERE student_id=? AND subject_id=? AND period_date=? AND period_label=?",
                                (int(student_id),int(subject_id),str(period_date),str(period_label))).fetchone()
                cur = c.execute("""INSERT INTO attendance(student_id,subject_id,period_date,period_label,classes_held,classes_attended)
                    VALUES(?,?,?,?,?,?) ON CONFLICT(student_id,subject_id,period_date,period_label)
                    DO UPDATE SET classes_held=excluded.classes_held,classes_attended=excluded.classes_attended""",
                    (int(student_id),int(subject_id),str(period_date),str(period_label),int(classes_held),int(classes_attended)))
                row = c.execute("SELECT id FROM attendance WHERE student_id=? AND subject_id=? AND period_date=? AND period_label=?",
                                (int(student_id),int(subject_id),str(period_date),str(period_label))).fetchone()
                new = {"classes_held":int(classes_held),"classes_attended":int(classes_attended)}
                self.db.write_audit(c, "attendance_saved", "attendance", row["id"], old=dict(old) if old else None,
                                    new=new, details=f"Attendance period {period_date} saved for student {int(student_id)}, subject {int(subject_id)}")
                return int(row["id"])
        except sqlite3.IntegrityError as e:
            raise AcademicValidationError({"reference":"Student or subject does not exist, or attendance violates a constraint."}) from e

    def attendance_summary(self, student_id=None, subject_id=None):
        sql = """SELECT a.student_id,s.enrollment_no,s.full_name,a.subject_id,sub.subject_code,sub.name AS subject_name,
                 SUM(a.classes_held) AS classes_held,SUM(a.classes_attended) AS classes_attended
                 FROM attendance a JOIN students s ON s.id=a.student_id
                 JOIN subjects sub ON sub.id=a.subject_id WHERE 1=1"""
        args=[]
        if student_id is not None: sql += " AND a.student_id=?"; args.append(student_id)
        if subject_id is not None: sql += " AND a.subject_id=?"; args.append(subject_id)
        sql += " GROUP BY a.student_id,a.subject_id ORDER BY s.full_name,sub.subject_code"
        threshold = self.get_low_attendance_threshold()
        with self.db.connect() as c:
            rows = [dict(r) for r in c.execute(sql,args).fetchall()]
        for r in rows:
            r["percentage"] = attendance_percentage(r["classes_held"],r["classes_attended"])
            r["below_threshold"] = r["percentage"] is not None and r["percentage"] < threshold
            r["threshold"] = threshold
        return rows

    def imported_attendance_summaries(self, limit=501):
        with self.db.connect() as c:
            return [dict(r) for r in c.execute("""SELECT s.enrollment_no,s.full_name,s.course_name,s.semester,
                    x.attendance_percentage,x.source_name,x.imported_at
                FROM student_import_summaries x JOIN students s ON s.id=x.student_id
                WHERE x.attendance_percentage IS NOT NULL
                ORDER BY s.full_name,s.enrollment_no LIMIT ?""", (int(limit),)).fetchall()]

    def imported_result_summaries(self, limit=501):
        with self.db.connect() as c:
            return [dict(r) for r in c.execute("""SELECT s.enrollment_no,s.full_name,s.course_name,s.semester,
                    x.result_percentage,x.source_name,x.imported_at
                FROM student_import_summaries x JOIN students s ON s.id=x.student_id
                WHERE x.result_percentage IS NOT NULL
                ORDER BY s.full_name,s.enrollment_no LIMIT ?""", (int(limit),)).fetchall()]

    def save_result(self, student_id, subject_id, exam_type, term, marks_obtained, maximum_marks):
        exam_type, term = _text(exam_type,"exam_type"), _text(term,"term")
        try: marks, maximum = float(marks_obtained), float(maximum_marks)
        except (TypeError,ValueError): raise AcademicValidationError({"marks":"Marks and maximum marks must be numeric."})
        if not (math.isfinite(marks) and math.isfinite(maximum)):
            raise AcademicValidationError({"marks":"Marks and maximum marks must be finite numbers."})
        errors={}
        if maximum <= 0: errors["maximum_marks"]="Maximum marks must be greater than zero."
        if marks < 0: errors["marks_obtained"]="Marks cannot be negative."
        if marks > maximum: errors["marks_obtained"]="Marks cannot exceed maximum marks."
        if errors: raise AcademicValidationError(errors)
        try:
            with self.db.transaction() as c:
                old = c.execute("SELECT marks_obtained,maximum_marks FROM results WHERE student_id=? AND subject_id=? AND exam_type=? AND term=?",
                                (int(student_id),int(subject_id),exam_type,term)).fetchone()
                c.execute("""INSERT INTO results(student_id,subject_id,exam_type,term,marks_obtained,maximum_marks)
                    VALUES(?,?,?,?,?,?) ON CONFLICT(student_id,subject_id,exam_type,term)
                    DO UPDATE SET marks_obtained=excluded.marks_obtained,maximum_marks=excluded.maximum_marks,
                    recorded_at=strftime('%Y-%m-%dT%H:%M:%fZ','now')""",
                    (int(student_id),int(subject_id),exam_type,term,marks,maximum))
                row=c.execute("SELECT id FROM results WHERE student_id=? AND subject_id=? AND exam_type=? AND term=?",
                              (int(student_id),int(subject_id),exam_type,term)).fetchone()
                new = {"marks_obtained":marks,"maximum_marks":maximum,"exam_type":exam_type,"term":term}
                self.db.write_audit(c, "result_saved", "result", row["id"], old=dict(old) if old else None,
                                    new=new, details=f"{exam_type} result saved for {term}")
                return int(row["id"])
        except sqlite3.IntegrityError as e:
            raise AcademicValidationError({"reference":"Student or subject does not exist, or result violates a constraint."}) from e

    def results(self, student_id=None, term=None):
        sql="""SELECT r.*,s.enrollment_no,s.full_name,sub.subject_code,sub.name AS subject_name
               FROM results r JOIN students s ON s.id=r.student_id JOIN subjects sub ON sub.id=r.subject_id WHERE 1=1"""
        args=[]
        if student_id is not None: sql+=" AND r.student_id=?"; args.append(student_id)
        if term: sql+=" AND r.term=?"; args.append(term)
        sql+=" ORDER BY s.full_name,r.term,sub.subject_code,r.exam_type"
        with self.db.connect() as c: return [dict(r) for r in c.execute(sql,args).fetchall()]

    def performance_trend(self, student_id):
        sql="""SELECT term,AVG(marks_obtained/maximum_marks*100) AS average_percentage,COUNT(*) AS assessments
               FROM results WHERE student_id=? GROUP BY term ORDER BY MIN(recorded_at)"""
        with self.db.connect() as c: return [dict(r) for r in c.execute(sql,(student_id,)).fetchall()]

    def list_courses(self):
        with self.db.connect() as c:
            return [dict(r) for r in c.execute(
                "SELECT id,name,description,created_at FROM courses ORDER BY name").fetchall()]

    def create_course(self, name, description=""):
        name = _text(name, "name", 120)
        description = str(description or "").strip() or None
        try:
            with self.db.transaction() as c:
                cur = c.execute("INSERT INTO courses(name,description) VALUES(?,?)", (name, description))
                cid = int(cur.lastrowid)
                self.db.write_audit(c, "course_created", "course", cid, new={"name": name}, details=f"Course {name} created")
                return cid
        except sqlite3.IntegrityError as e:
            raise AcademicValidationError({"name":"A course with this name already exists."}) from e

    def list_subjects(self, course_name=None):
        sql = """SELECT sub.id,sub.course_name AS course,sub.semester,sub.subject_code,sub.name
                 FROM subjects sub WHERE 1=1"""
        args = []
        if course_name is not None:
            sql += " AND sub.course_name=?"; args.append(str(course_name))
        sql += " ORDER BY sub.course_name,sub.semester,sub.subject_code"
        with self.db.connect() as c:
            return [dict(r) for r in c.execute(sql, args).fetchall()]

    def create_subject(self, course_name, semester, subject_code, name):
        code = _text(subject_code, "subject_code", 40)
        name = _text(name, "name", 120)
        course_name = _text(course_name, "course_name", 120)
        try:
            semester = int(semester)
            if semester < 1: raise ValueError
        except (TypeError, ValueError):
            raise AcademicValidationError({"semester":"Semester must be a positive whole number."})
        try:
            with self.db.transaction() as c:
                cur = c.execute(
                    "INSERT INTO subjects(course_name,semester,subject_code,name) VALUES(?,?,?,?)",
                    (course_name, semester, code, name))
                sid = int(cur.lastrowid)
                self.db.write_audit(c, "subject_created", "subject", sid,
                                    new={"course_name": course_name, "semester": semester, "subject_code": code, "name": name})
                return sid
        except sqlite3.IntegrityError as e:
            raise AcademicValidationError({
                "subject":"Course is missing, or this subject code already exists for the course and semester."
            }) from e
