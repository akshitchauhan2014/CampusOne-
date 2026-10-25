import json, re, sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
import pandas as pd
from .students import STUDENT_FIELDS, validate_student, StudentValidationError
from .fees import to_paise
from .security import safe_export_value

MAX_WORKBOOK_BYTES = 25 * 1024 * 1024   # Provisional limit; review against real-world performance.
MAX_IMPORT_ROWS = 50_000
KEEP_PRE_IMPORT_BACKUPS = 20

ALIASES = {
 "enrollment_no": ["enrollment_no", "enrollment number", "enrollment", "enrollment no", "roll no", "roll number", "student id", "registration number"],
 "full_name": ["full_name", "full name", "student name", "name"],
 "father_name": ["father_name", "father name", "father's name", "father", "parent name"],
 "email": ["email", "email address", "e-mail"], "phone": ["phone", "phone no", "mobile", "mobile number", "contact"],
 "course_name": ["course", "course name", "course_name", "programme", "program", "branch", "department"],
 "semester": ["semester", "sem"],
 "batch": ["batch", "academic batch", "year"], "status": ["status", "student status"],
 "attendance_percentage": ["attendance", "attendance percentage", "attendance in %"],
 "result_percentage": ["result", "result percentage", "result in %"],
 "total_fees": ["total fees", "total fee"],
 "submitted_fees": ["submitted fees", "submitted fee", "paid fees"],
 "pending_fees": ["pending fees", "pending fee", "outstanding fees"]
}
REQUIRED = ("enrollment_no", "full_name")
SUMMARY_INPUT_FIELDS = ("attendance_percentage", "result_percentage", "total_fees", "submitted_fees", "pending_fees")
SUMMARY_FIELDS = ("attendance_percentage", "result_percentage", "total_fees_paise", "submitted_fees_paise", "pending_fees_paise")
IMPORT_TARGETS = STUDENT_FIELDS + SUMMARY_INPUT_FIELDS

def norm(value): return re.sub(r"[^a-z0-9]+", " ", str(value or "").strip().lower()).strip()

def suggest_mapping(columns):
    normalized = {norm(c): str(c) for c in columns}
    result = {}
    for target, aliases in ALIASES.items():
        for alias in aliases:
            if norm(alias) in normalized:
                result[target] = normalized[norm(alias)]; break
    return result

def _check_size(path):
    try: size = Path(path).stat().st_size
    except OSError: raise ValueError("The workbook could not be read.")
    if size > MAX_WORKBOOK_BYTES:
        raise ValueError(f"Workbook is larger than the {MAX_WORKBOOK_BYTES // (1024*1024)} MB limit. Split it into smaller files.")

def preview_workbook(path):
    path = Path(path)
    if path.suffix.lower() not in {".xlsx", ".xlsm", ".xltx", ".xltm"}:
        raise ValueError("Use an Excel workbook in .xlsx or .xlsm format.")
    _check_size(path)
    book = pd.ExcelFile(path, engine="openpyxl")
    sheets = []
    for sheet in book.sheet_names:
        frame = pd.read_excel(path, sheet_name=sheet, nrows=5, dtype=object, engine="openpyxl")
        sheets.append({"name": sheet, "columns": [str(c) for c in frame.columns],
                       "sample_rows": frame.fillna("").astype(str).to_dict("records")})
    return {"path": str(path.resolve()), "sheets": sheets}

def _value(v):
    if pd.isna(v): return None
    if isinstance(v, float) and v.is_integer(): return int(v)
    if hasattr(v, "isoformat"): return v.isoformat()
    return v

@dataclass
class ImportRow:
    source_row: int
    action: str
    enrollment_no: str | None
    data: dict
    errors: dict
    # Only the fields the workbook actually supplied (non-blank). Updates apply these and nothing else,
    # so blank cells or an unmapped Status column can never wipe or reset existing data.
    provided: dict = field(default_factory=dict)
    warnings: dict = field(default_factory=dict)

@dataclass
class ImportPreview:
    source_path: str
    sheet_name: str
    mapping: dict
    rows: list[ImportRow]
    @property
    def counts(self):
        return {k: sum(r.action == k for r in self.rows) for k in ("add", "update", "skip", "reject")}

class ExcelImportService:
    def __init__(self, db): self.db = db

    def preview(self, path, sheet_name, mapping, update_existing=False):
        _check_size(path)
        frame = pd.read_excel(path, sheet_name=sheet_name, dtype=object, engine="openpyxl")
        if len(frame) > MAX_IMPORT_ROWS:
            raise ValueError(f"Worksheet has {len(frame):,} rows; the limit is {MAX_IMPORT_ROWS:,}. Split the file.")
        missing = [f for f in REQUIRED if f not in mapping or mapping[f] not in frame.columns]
        if missing: raise ValueError("Map required fields before preview: " + ", ".join(missing))
        rows, seen = [], set()
        with self.db.connect() as conn:
            existing = {r["enrollment_no"].casefold(): dict(r) for r in conn.execute("SELECT * FROM students")}
            course_by_name = {r["name"].casefold(): r["name"] for r in conn.execute("SELECT name FROM courses")}
            summaries = {r["student_id"]: dict(r) for r in conn.execute("SELECT * FROM student_import_summaries")}
            fee_summaries = {
                (r["student_id"], r["term"]): dict(r)
                for r in conn.execute("SELECT * FROM student_fee_import_summaries")
            }
        for idx, source in frame.iterrows():
            raw = {target: _value(source.get(col)) for target, col in mapping.items() if target in IMPORT_TARGETS}
            if not any(v not in (None, "") for v in raw.values()): continue
            errors = {}
            warnings = {}
            summary_raw = {key: raw.pop(key, None) for key in SUMMARY_INPUT_FIELDS}
            course_name = raw.get("course_name")
            if course_name not in (None, ""):
                course_name = str(course_name).strip()
                raw["course_name"] = course_by_name.get(course_name.casefold(), course_name)
            try: data = validate_student(raw)
            except StudentValidationError as exc:
                data = raw; errors.update(exc.errors)
            enrollment = str(data.get("enrollment_no") or "").strip()
            key = enrollment.casefold()
            if not errors and key in seen: errors["enrollment_no"] = "Duplicate enrollment number within this workbook."
            if key: seen.add(key)
            provided = {k: data[k] for k in STUDENT_FIELDS
                        if k != "enrollment_no" and k in raw and raw[k] not in (None, "") and data.get(k) is not None}
            summary_provided = {}
            current_student = existing.get(key, {})
            prior_summary = summaries.get(current_student.get("id"), {})
            import_semester = data.get("semester", current_student.get("semester"))
            import_term = f"Semester {import_semester}" if import_semester is not None else "Unassigned"
            prior_fee_summary = fee_summaries.get((current_student.get("id"), import_term))
            if prior_fee_summary is None and current_student.get("semester") == import_semester:
                prior_fee_summary = prior_summary
            prior_summary = dict(prior_summary)
            if prior_fee_summary:
                for field_name in ("total_fees_paise", "submitted_fees_paise", "pending_fees_paise"):
                    prior_summary[field_name] = prior_fee_summary.get(field_name)
            for field_name in ("attendance_percentage", "result_percentage"):
                value = summary_raw[field_name]
                if value in (None, ""):
                    continue
                try:
                    number = float(str(value).strip().removesuffix("%"))
                    if not 0 <= number <= 100:
                        raise ValueError
                    summary_provided[field_name] = number
                except (TypeError, ValueError):
                    errors[field_name] = "Enter a percentage from 0 to 100."
            for input_field, stored_field in (("total_fees", "total_fees_paise"),
                                               ("submitted_fees", "submitted_fees_paise")):
                value = summary_raw[input_field]
                if value in (None, ""):
                    continue
                try:
                    summary_provided[stored_field] = to_paise(value)
                except ValueError as exc:
                    errors[input_field] = str(exc)
            total_fees = summary_provided.get("total_fees_paise", prior_summary.get("total_fees_paise"))
            submitted_fees = summary_provided.get("submitted_fees_paise", prior_summary.get("submitted_fees_paise"))
            if total_fees is not None and submitted_fees is not None:
                if submitted_fees > total_fees:
                    errors["submitted_fees"] = "Submitted fees cannot exceed total fees."
                else:
                    summary_provided["pending_fees_paise"] = total_fees - submitted_fees
                    source_pending = summary_raw["pending_fees"]
                    if source_pending not in (None, ""):
                        try:
                            if to_paise(source_pending) != summary_provided["pending_fees_paise"]:
                                warnings["pending_fees"] = "Source pending fees differed; imported pending fees were calculated as total minus submitted."
                        except ValueError as exc:
                            errors["pending_fees"] = str(exc)
            elif summary_raw["pending_fees"] not in (None, ""):
                errors["pending_fees"] = "Pending fees are calculated from total fees minus submitted fees; map both source columns."
            provided.update(summary_provided)
            if errors: action = "reject"
            elif key in existing:
                changed = any(v != existing[key].get(k) for k, v in provided.items() if k in STUDENT_FIELDS)
                changed = changed or any(v != prior_summary.get(k) for k, v in provided.items() if k in SUMMARY_FIELDS)
                action = "update" if update_existing and changed else "skip"
            else: action = "add"
            data.update(summary_raw)
            data.update(summary_provided)
            rows.append(ImportRow(int(idx)+2, action, enrollment or None, data, errors, provided, warnings))
        return ImportPreview(str(Path(path).resolve()), sheet_name, mapping, rows)

    def backup(self):
        path = self.db.path
        if not path.exists(): raise FileNotFoundError("Database does not exist; cannot back up.")
        folder = path.parent / "backups"; folder.mkdir(parents=True, exist_ok=True)
        dest = folder / f"campusone_pre_import_{datetime.now():%Y%m%d_%H%M%S_%f}.db"
        src = sqlite3.connect(str(path)); out = sqlite3.connect(str(dest))
        try:
            src.backup(out)
            if out.execute("PRAGMA quick_check").fetchone()[0] != "ok": raise RuntimeError("Pre-import backup failed its integrity check.")
        except Exception:
            out.close(); src.close()
            try: dest.unlink()
            except OSError: pass
            raise
        out.close(); src.close()
        # Retention: keep the newest N automatic pre-import copies so the disk does not fill silently.
        old = sorted(folder.glob("campusone_pre_import_*.db"))[:-KEEP_PRE_IMPORT_BACKUPS]
        for f in old:
            try: f.unlink()
            except OSError: pass
        return dest

    def _log_failure(self, preview, backup, message):
        try:
            with self.db.transaction() as conn:
                conn.execute("""INSERT INTO import_logs(source_name,status,rows_seen,rows_rejected,completed_at,message,worksheet_name,backup_path,source_path)
                    VALUES (?,'failed',?,?,strftime('%Y-%m-%dT%H:%M:%fZ','now'),?,?,?,?)""",
                    (Path(preview.source_path).name, len(preview.rows), preview.counts["reject"], message[:500],
                     preview.sheet_name, str(backup), preview.source_path))
                self.db.write_audit(conn, "excel_import_failed", "import_log", None, details=message[:500])
        except Exception: pass   # logging must never mask the original error

    def commit(self, preview, confirmed):
        if not confirmed:
            return {"committed": False, "message": "Cancelled; no changes made.", **preview.counts}
        backup = self.backup()
        added = updated = skipped = 0
        try:
            with self.db.transaction() as conn:
                cur = conn.execute("""INSERT INTO import_logs(source_name,status,rows_seen,worksheet_name,mapping_json,backup_path,source_path)
                    VALUES (?,'started',?,?,?,?,?)""",
                    (Path(preview.source_path).name, len(preview.rows), preview.sheet_name,
                     json.dumps(preview.mapping, sort_keys=True), str(backup), preview.source_path))
                log_id = cur.lastrowid
                # Re-check against the database as it is NOW, not as it was when the preview was drawn.
                existing = {r[0].casefold() for r in conn.execute("SELECT enrollment_no FROM students")}
                for row in preview.rows:
                    if row.action not in ("add", "update"): continue
                    d = validate_student(row.data)
                    key = d["enrollment_no"].casefold()
                    if d.get("course_name"):
                        conn.execute("INSERT OR IGNORE INTO courses(name) VALUES(?)", (d["course_name"],))
                        canonical = conn.execute("SELECT name FROM courses WHERE name=? COLLATE NOCASE",
                                                 (d["course_name"],)).fetchone()
                        d["course_name"] = canonical["name"]
                    if row.action == "add":
                        if key in existing: skipped += 1; continue
                        cols = ", ".join(STUDENT_FIELDS); marks = ", ".join("?" for _ in STUDENT_FIELDS)
                        cur = conn.execute(f"INSERT INTO students ({cols}) VALUES ({marks})", tuple(d.get(k) for k in STUDENT_FIELDS))
                        student_id = int(cur.lastrowid)
                        existing.add(key); added += 1
                    else:
                        if key not in existing or not row.provided: skipped += 1; continue
                        student_id = conn.execute("SELECT id FROM students WHERE enrollment_no = ?", (d["enrollment_no"],)).fetchone()[0]
                        fields = [k for k in STUDENT_FIELDS if k in row.provided]
                        if fields:
                            assignments = ", ".join(f"{k} = ?" for k in fields)
                            conn.execute(f"UPDATE students SET {assignments} WHERE enrollment_no = ?",
                                         tuple(row.provided[k] for k in fields) + (d["enrollment_no"],))
                        updated += 1
                    summary_fields = [k for k in SUMMARY_FIELDS if k in row.provided]
                    if summary_fields:
                        cols = ["student_id", *summary_fields, "source_name"]
                        values = [student_id, *(row.provided[k] for k in summary_fields), Path(preview.source_path).name]
                        assignments = ", ".join(
                            [*(f"{k}=excluded.{k}" for k in summary_fields),
                             "source_name=excluded.source_name",
                             "imported_at=strftime('%Y-%m-%dT%H:%M:%fZ','now')"])
                        conn.execute(f"INSERT INTO student_import_summaries ({', '.join(cols)}) "
                                     f"VALUES ({', '.join('?' for _ in cols)}) "
                                     f"ON CONFLICT(student_id) DO UPDATE SET {assignments}", values)
                    fee_inputs = ("total_fees", "submitted_fees", "pending_fees")
                    if any(row.data.get(field_name) not in (None, "") for field_name in fee_inputs):
                        student = conn.execute(
                            "SELECT semester FROM students WHERE id=?", (student_id,)
                        ).fetchone()
                        term = f"Semester {student['semester']}" if student["semester"] is not None else "Unassigned"
                        previous = conn.execute("""SELECT total_fees_paise,submitted_fees_paise,
                            pending_fees_paise FROM student_fee_import_summaries
                            WHERE student_id=? AND term=?""", (student_id, term)).fetchone()
                        total = row.provided.get(
                            "total_fees_paise", previous["total_fees_paise"] if previous else None,
                        )
                        submitted = row.provided.get(
                            "submitted_fees_paise",
                            previous["submitted_fees_paise"] if previous else None,
                        )
                        pending = row.provided.get(
                            "pending_fees_paise",
                            previous["pending_fees_paise"] if previous else None,
                        )
                        if total is not None and submitted is not None:
                            pending = total - submitted
                        conn.execute("""INSERT INTO student_fee_import_summaries(
                            student_id,term,total_fees_paise,submitted_fees_paise,
                            pending_fees_paise,source_name
                        ) VALUES(?,?,?,?,?,?)
                        ON CONFLICT(student_id,term) DO UPDATE SET
                            total_fees_paise=excluded.total_fees_paise,
                            submitted_fees_paise=excluded.submitted_fees_paise,
                            pending_fees_paise=excluded.pending_fees_paise,
                            source_name=excluded.source_name,
                            imported_at=strftime('%Y-%m-%dT%H:%M:%fZ','now')""",
                            (student_id, term, total, submitted, pending, Path(preview.source_path).name))
                conn.execute("""UPDATE import_logs SET status='succeeded',completed_at=strftime('%Y-%m-%dT%H:%M:%fZ','now'),
                    rows_imported=?,rows_rejected=?,message=? WHERE id=?""",
                    (added+updated, preview.counts["reject"], "Import committed", log_id))
                self.db.write_audit(conn, "excel_import", "import_log", log_id,
                    details=f"Added {added}; updated {updated}; skipped {preview.counts['skip']+skipped}; rejected {preview.counts['reject']}")
        except Exception as exc:
            # The transaction above rolled back: nothing partial is committed. Record the failure separately.
            self._log_failure(preview, backup, f"{type(exc).__name__}: {exc}")
            raise
        return {"committed": True, "backup_path": str(backup), "added": added, "updated": updated,
                "skipped": preview.counts["skip"] + skipped, "rejected": preview.counts["reject"]}

def export_rejected(preview, destination):
    rows = []
    for row in preview.rows:
        if row.action != "reject": continue
        item = {"source_row": row.source_row, **row.data,
                "validation_errors": "; ".join(f"{k}: {v}" for k,v in row.errors.items())}
        rows.append({k: safe_export_value(v) for k,v in item.items()})
    dest = Path(destination); dest.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_excel(dest, index=False, engine="openpyxl")
    return dest
