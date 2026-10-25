from __future__ import annotations
import sqlite3
import uuid
from datetime import date
from decimal import Decimal, InvalidOperation
import re
from .academics import AcademicValidationError

PAYMENT_MODES = {"cash","upi","card","bank_transfer","cheque","other"}

MAX_AMOUNT_PAISE = 100_000_000_00  # 10 crore rupees per entry: catches typos, far above any real fee

def to_paise(amount):
    """Exact decimal conversion (no binary-float drift); rejects NaN/inf, >2 decimals, negatives."""
    try:
        d = Decimal(str(amount).strip().replace(",", ""))
    except (InvalidOperation, ValueError):
        raise AcademicValidationError({"amount":"Amount must be a valid number."})
    if not d.is_finite(): raise AcademicValidationError({"amount":"Amount must be a valid number."})
    if d < 0: raise AcademicValidationError({"amount":"Amount cannot be negative."})
    paise = d * 100
    if paise != paise.to_integral_value():
        raise AcademicValidationError({"amount":"Amount can have at most two decimal places."})
    if paise > MAX_AMOUNT_PAISE: raise AcademicValidationError({"amount":"Amount is unrealistically large; check for a typing error."})
    return int(paise)

class FeeRepository:
    def __init__(self, db): self.db=db

    def set_fee_structure(self, course_name, term, amount, fee_head="Tuition"):
        term, fee_head = str(term or "").strip()[:80], str(fee_head or "").strip()[:80]
        if not term: raise AcademicValidationError({"term":"Term is required."})
        if not fee_head: raise AcademicValidationError({"fee_head":"Fee head is required."})
        course_name = str(course_name or "").strip()
        if not course_name: raise AcademicValidationError({"course_name":"Course name is required."})
        paise=to_paise(amount)
        try:
            with self.db.transaction() as c:
                old=c.execute("SELECT amount_paise FROM fee_structures WHERE course_name=? AND term=? AND fee_head=?",
                              (course_name,term,fee_head)).fetchone()
                c.execute("""INSERT INTO fee_structures(course_name,term,fee_head,amount_paise) VALUES(?,?,?,?)
                    ON CONFLICT(course_name,term,fee_head) DO UPDATE SET amount_paise=excluded.amount_paise""",
                    (course_name,term,fee_head,paise))
                row=c.execute("SELECT id FROM fee_structures WHERE course_name=? AND term=? AND fee_head=?",
                              (course_name,term,fee_head)).fetchone()
                self.db.write_audit(c,"fee_structure_saved","fee_structure",row["id"],
                                    old={"amount_paise":old[0]} if old else None,new={"amount_paise":paise,"term":term,"fee_head":fee_head,"course_name":course_name})
                return int(row["id"])
        except sqlite3.IntegrityError as e:
            raise AcademicValidationError({"course_name":"Selected course does not exist."}) from e

    def record_payment(self, student_id, amount, payment_date, payment_mode, term, notes=None):
        paise=to_paise(amount)
        if paise <= 0: raise AcademicValidationError({"amount":"Payment amount must be greater than zero."})
        try: payment_date = date.fromisoformat(str(payment_date).strip()).isoformat()  # normalised so range filters sort correctly
        except ValueError: raise AcademicValidationError({"payment_date":"Date must use YYYY-MM-DD."})
        term = str(term or "").strip()[:80]
        if not term: raise AcademicValidationError({"term":"Term is required for fee allocation."})
        mode=str(payment_mode or "").strip().lower()
        if mode not in PAYMENT_MODES: raise AcademicValidationError({"payment_mode":"Unsupported payment mode."})
        notes=(str(notes).strip()[:500] or None) if notes else None
        try:
            with self.db.transaction() as c:
                cur=c.execute("""INSERT INTO fee_payments(
                    student_id,amount_paise,payment_date,term,payment_mode,receipt_number,notes,created_at
                ) VALUES(?,?,?,?,?,?,?,strftime('%Y-%m-%dT%H:%M:%fZ','now'))""",
                    (int(student_id),paise,str(payment_date),term,mode,
                     f"pending-{uuid.uuid4().hex}",notes))
                pid=int(cur.lastrowid)
                receipt=f"CO-{pid:012d}"
                if c.execute("SELECT 1 FROM fee_payments WHERE receipt_number=? COLLATE NOCASE", (receipt,)).fetchone():
                    receipt=f"{receipt}-{uuid.uuid4().hex[:12].upper()}"
                c.execute("UPDATE fee_payments SET receipt_number=? WHERE id=?", (receipt,pid))
                new = {"student_id":int(student_id),"amount_paise":paise,"payment_date":str(payment_date),
                       "term":term,"payment_mode":mode,"receipt_number":receipt}
                self.db.write_audit(c,"fee_payment_recorded","fee_payment",pid,new=new,details=f"Receipt {receipt} recorded")
                return {"payment_id":pid,"receipt_number":receipt,"amount":paise/100}
        except sqlite3.IntegrityError as e:
            raise AcademicValidationError({"student_id":"Student does not exist or payment violates a constraint."}) from e

    def student_balance(self, student_id, term):
        with self.db.connect() as c:
            student=c.execute("SELECT course_name FROM students WHERE id=?",(student_id,)).fetchone()
            if not student: raise LookupError("Student not found.")
            course_name=student["course_name"]
            due=c.execute("SELECT COALESCE(SUM(amount_paise),0) FROM fee_structures WHERE course_name=? AND term=?",
                          (course_name,term)).fetchone()[0]
            paid=c.execute("SELECT COALESCE(SUM(amount_paise),0) FROM fee_payments WHERE student_id=? AND term=?",
                           (student_id, str(term))).fetchone()[0]
        return {"student_id":student_id,"term":term,"fee_due":due/100,"paid":paid/100,"balance":(due-paid)/100}

    def collection_summary(self, start_date=None, end_date=None, payment_mode=None):
        sql="""SELECT payment_mode,COUNT(*) AS payment_count,SUM(amount_paise) AS total_paise
               FROM fee_payments WHERE 1=1"""
        args=[]
        if start_date: sql+=" AND payment_date>=?"; args.append(start_date)
        if end_date: sql+=" AND payment_date<=?"; args.append(end_date)
        if payment_mode: sql+=" AND payment_mode=?"; args.append(payment_mode)
        sql+=" GROUP BY payment_mode ORDER BY payment_mode"
        with self.db.connect() as c: rows=[dict(r) for r in c.execute(sql,args).fetchall()]
        for r in rows: r["total"]=r.pop("total_paise")/100
        return rows

    def fee_totals(self, course_name=None, semester=None):
        """Single source of truth for dashboard and analytics fee figures.
        billed      = for every ACTIVE student, the fee structure of their course (per term)
        outstanding = sum of each student-term's positive balance (overpayments do not offset other students)
        collected   = every recorded payment by students matching the filters."""
        flt, args = "", []
        if course_name is not None: flt += " AND s.course_name=?"; args.append(course_name)
        if semester is not None: flt += " AND s.semester=?"; args.append(int(semester))
        with self.db.connect() as c:
            collected = c.execute(f"""SELECT COALESCE(SUM(p.amount_paise),0) FROM fee_payments p
                JOIN students s ON s.id=p.student_id WHERE 1=1 {flt}""", args).fetchone()[0]
            rows = [dict(r) for r in c.execute(f"""
                WITH due AS (SELECT s.id student_id, s.course_name, fs.term, SUM(fs.amount_paise) due
                             FROM students s JOIN fee_structures fs ON fs.course_name=s.course_name
                             WHERE s.status='active' {flt} GROUP BY s.id, fs.term),
                     paid AS (SELECT student_id, term, SUM(amount_paise) paid FROM fee_payments GROUP BY student_id, term)
                SELECT d.course_name course, SUM(d.due) billed,
                       SUM(CASE WHEN d.due-COALESCE(p.paid,0)>0 THEN d.due-COALESCE(p.paid,0) ELSE 0 END) outstanding
                FROM due d
                LEFT JOIN paid p ON p.student_id=d.student_id AND p.term=d.term
                GROUP BY d.course_name ORDER BY d.course_name""", args).fetchall()]
            by_course_paid = {r[0]: r[1] for r in c.execute(f"""SELECT s.course_name, COALESCE(SUM(p.amount_paise),0)
                FROM fee_payments p JOIN students s ON s.id=p.student_id WHERE 1=1 {flt} GROUP BY s.course_name""", args)}
        seen = {r["course"] for r in rows}
        for name, paid in by_course_paid.items():
            if name is not None and name not in seen: rows.append({"course":name,"billed":0,"outstanding":0})
        by_course = [{"course":r["course"],"billed":(r["billed"] or 0)/100,
                      "outstanding":(r["outstanding"] or 0)/100,"collected":by_course_paid.get(r["course"],0)/100} for r in rows]
        return {"collected":collected/100,"billed":sum(r["billed"] for r in by_course),
                "outstanding":sum(r["outstanding"] for r in by_course),"by_course":by_course}

    def _balances(self, term):
        """One set-based query for every active student (the old per-student loop issued ~3 queries each)."""
        term=str(term)
        with self.db.connect() as c:
            return [dict(r) for r in c.execute("""
                SELECT s.id, s.enrollment_no, s.full_name, s.course_name,
                       COALESCE(d.due,0) AS due, COALESCE(p.paid,0) AS paid
                FROM students s
                LEFT JOIN (SELECT course_name, SUM(amount_paise) due FROM fee_structures WHERE term=? GROUP BY course_name) d ON d.course_name=s.course_name
                LEFT JOIN (SELECT student_id, SUM(amount_paise) paid FROM fee_payments WHERE term=? GROUP BY student_id) p ON p.student_id=s.id
                WHERE s.status='active' ORDER BY s.full_name""", (term, term)).fetchall()]

    def _balance_rows(self, term):
        return [{"id":r["id"],"enrollment_no":r["enrollment_no"],"full_name":r["full_name"],"course_name":r["course_name"],
                 "student_id":r["id"],"term":term,"fee_due":r["due"]/100,"paid":r["paid"]/100,"balance":(r["due"]-r["paid"])/100}
                for r in self._balances(term)]

    def outstanding_summary(self, term):
        return [r for r in self._balance_rows(term) if r["balance"] > 0]

    def imported_fee_summaries(self, limit=501):
        with self.db.connect() as c:
            return [dict(r) for r in c.execute("""SELECT s.enrollment_no,s.full_name,s.course_name,
                    x.term,s.semester,x.total_fees_paise,x.submitted_fees_paise,
                    x.pending_fees_paise,x.source_name,x.imported_at
                FROM student_fee_import_summaries x JOIN students s ON s.id=x.student_id
                WHERE x.total_fees_paise IS NOT NULL OR x.submitted_fees_paise IS NOT NULL
                    OR x.pending_fees_paise IS NOT NULL
                ORDER BY s.full_name,s.enrollment_no,x.term LIMIT ?""", (int(limit),)).fetchall()]

    @staticmethod
    def _term_key(term):
        value = str(term or "").strip().casefold()
        match = re.fullmatch(r"(?:semester|term)?\s*(\d+)", value)
        return f"semester:{int(match.group(1))}" if match else f"name:{value}"

    def student_balances(self, term=None, student_id=None, course_name=None, semester=None):
        """Combine fee structures and payments, using imported snapshots only as historical fallbacks.

        When a structure exists, it and its recorded payments are authoritative. Otherwise the
        imported snapshot is the opening balance, and only payments recorded after that snapshot
        are added so older ledger entries are not counted twice.
        """
        with self.db.connect() as c:
            students = [dict(r) for r in c.execute("""
                SELECT s.id,s.enrollment_no,s.full_name,s.course_name,s.semester
                FROM students s
                WHERE (s.status='active' OR s.id=?) AND (? IS NULL OR s.id=?)
                    AND (? IS NULL OR s.course_name=?) AND (? IS NULL OR s.semester=?)
                ORDER BY s.full_name,s.enrollment_no""",
                (student_id, student_id, student_id, course_name, course_name,
                 semester, semester)).fetchall()]
            imports = [dict(r) for r in c.execute("""
                SELECT student_id,term,total_fees_paise,submitted_fees_paise,
                       pending_fees_paise,source_name,imported_at
                FROM student_fee_import_summaries
                WHERE total_fees_paise IS NOT NULL OR submitted_fees_paise IS NOT NULL
                    OR pending_fees_paise IS NOT NULL""").fetchall()]
            structures = [dict(r) for r in c.execute("""
                SELECT course_name,term,SUM(amount_paise) AS due_paise
                FROM fee_structures GROUP BY course_name,term""").fetchall()]
            payments = [dict(r) for r in c.execute("""
                SELECT student_id,term,amount_paise,created_at
                FROM fee_payments""").fetchall()]

        imports_by_student = {}
        for row in imports:
            imports_by_student.setdefault(row["student_id"], []).append(row)
        structures_by_course = {}
        for row in structures:
            structures_by_course.setdefault(row["course_name"], {})[row["term"]] = row["due_paise"]
        payments_by_student = {}
        for row in payments:
            payments_by_student.setdefault(row["student_id"], {}).setdefault(row["term"], []).append(row)

        result = []
        for student in students:
            structure_terms = structures_by_course.get(student["course_name"], {})
            student_payments = payments_by_student.get(student["id"], {})
            term_names = list(structure_terms)
            for payment_term in student_payments:
                if payment_term not in term_names:
                    term_names.append(payment_term)
            import_targets = {}
            for snapshot in imports_by_student.get(student["id"], []):
                snapshot_term = snapshot["term"]
                matching_terms = [
                    name for name in term_names
                    if self._term_key(name) == self._term_key(snapshot_term)
                ]
                target = next(
                    (name for name in matching_terms if name.casefold() == snapshot_term.casefold()),
                    next((name for name in matching_terms if name.casefold().startswith("semester ")),
                         matching_terms[0] if matching_terms else snapshot_term)
                )
                if target not in term_names:
                    term_names.append(target)
                import_targets[target] = snapshot

            for term_name in term_names:
                term_payments = student_payments.get(term_name, [])
                actual_paid = sum(payment["amount_paise"] for payment in term_payments)
                has_structure = term_name in structure_terms
                snapshot = import_targets.get(term_name)
                if has_structure:
                    due_paise = structure_terms[term_name]
                    paid_paise = actual_paid
                    source = "Fee structure and recorded payments"
                    if snapshot:
                        source += f" (import retained: {snapshot['source_name']})"
                elif snapshot:
                    due_paise = snapshot["total_fees_paise"]
                    if due_paise is None:
                        due_paise = (
                            (snapshot["submitted_fees_paise"] or 0)
                            + (snapshot["pending_fees_paise"] or 0)
                        )
                    base_paid = snapshot["submitted_fees_paise"]
                    if base_paid is None:
                        base_paid = max(0, due_paise - (snapshot["pending_fees_paise"] or 0))
                    new_paid = sum(
                        payment["amount_paise"]
                        for payment_term, term_payments in student_payments.items()
                        if self._term_key(payment_term) == self._term_key(term_name)
                        for payment in term_payments
                        if not snapshot["imported_at"] or payment["created_at"] > snapshot["imported_at"]
                    )
                    paid_paise = base_paid + new_paid
                    source = f"Imported snapshot: {snapshot['source_name'] or 'source unavailable'}"
                else:
                    due_paise = 0
                    paid_paise = actual_paid
                    source = "Recorded payments"
                due_paise = due_paise or 0
                pending_paise = max(0, due_paise - paid_paise)
                if term is not None and self._term_key(term_name) != self._term_key(term):
                    continue
                due = due_paise / 100
                paid = paid_paise / 100
                pending = pending_paise / 100
                percentage = paid * 100 / due if due > 0 else None
                result.append({
                    "id": student["id"], "student_id": student["id"],
                    "enrollment_no": student["enrollment_no"], "full_name": student["full_name"],
                    "course_name": student["course_name"], "term": term_name,
                    "fee_due": due, "paid": paid, "pending": pending,
                    "balance": pending, "submission_percentage": percentage,
                    "pending_percentage": pending * 100 / due if due > 0 else None,
                    "payment_status": (
                        "Fee Pending – Attention Required" if due > 0 and pending * 100 / due > 40
                        else "Partially paid" if pending > 0
                        else "Paid" if due > 0
                        else "No fee due"
                    ),
                    "source": source,
                })
        return result

    def student_fees_fully_paid(self, student_id, term=None):
        if term is None:
            with self.db.connect() as c:
                student = c.execute(
                    "SELECT semester FROM students WHERE id=?", (student_id,),
                ).fetchone()
            if student is None or student["semester"] is None:
                return False
            term = f"Semester {student['semester']}"
        balances = self.student_balances(student_id=student_id)
        term_balance = next(
            (row for row in balances if self._term_key(row["term"]) == self._term_key(term)),
            None,
        )
        return bool(
            term_balance
            and term_balance["fee_due"] > 0
            and term_balance["pending"] <= 0
        )

    def fee_dashboard_summary(self, course_name=None, semester=None):
        rows = self.student_balances(course_name=course_name, semester=semester)
        by_term = {}
        for row in rows:
            totals = by_term.setdefault(row["term"], {"term": row["term"], "due": 0.0, "submitted": 0.0, "pending": 0.0})
            totals["due"] += row["fee_due"]
            totals["submitted"] += row["paid"]
            totals["pending"] += row["pending"]
        due = sum(row["fee_due"] for row in rows)
        submitted = sum(row["paid"] for row in rows)
        pending = sum(row["pending"] for row in rows)
        return {
            "rows": rows, "total_due": due, "total_submitted": submitted,
            "total_pending": pending,
            "collection_percentage": submitted * 100 / due if due > 0 else 0.0,
            "by_term": sorted(by_term.values(), key=lambda row: row["term"].casefold()),
        }

    def list_structures(self):
        with self.db.connect() as c:
            return [dict(r) for r in c.execute("""SELECT fs.id,fs.course_name AS course,fs.term,fs.fee_head,
                fs.amount_paise/100.0 AS amount FROM fee_structures fs
                ORDER BY fs.course_name,fs.term,fs.fee_head""").fetchall()]

    def list_payments(self, limit=400, student_id=None):
        where = ""
        args = []
        if student_id is not None:
            where = " WHERE p.student_id=?"
            args.append(int(student_id))
        args.append(int(limit))
        with self.db.connect() as c:
            return [dict(r) for r in c.execute("""SELECT p.id,s.enrollment_no,s.full_name,p.term,p.payment_date,
                p.payment_mode,p.receipt_number,p.amount_paise/100.0 AS amount
                FROM fee_payments p JOIN students s ON s.id=p.student_id
                """ + where + " ORDER BY p.payment_date DESC,p.id DESC LIMIT ?", args).fetchall()]

    def known_terms(self):
        with self.db.connect() as c:
            rows=c.execute("""SELECT term FROM fee_structures UNION SELECT term FROM fee_payments
                              ORDER BY term""").fetchall()
            imported = c.execute("""SELECT DISTINCT term FROM student_fee_import_summaries
                WHERE total_fees_paise IS NOT NULL OR submitted_fees_paise IS NOT NULL
                    OR pending_fees_paise IS NOT NULL""").fetchall()
        terms = [r[0] for r in rows]
        terms.extend(r[0] for r in imported)
        return sorted(set(terms), key=str.casefold)
