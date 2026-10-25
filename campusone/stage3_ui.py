from datetime import date
from PySide6.QtWidgets import (
    QComboBox, QDialog, QHBoxLayout, QLineEdit, QMessageBox, QPushButton, QScrollArea,
    QTabWidget, QVBoxLayout, QWidget
)
from .academics import AcademicRepository
from .fees import FeeRepository, PAYMENT_MODES
from .fees_ui import FeesPage as FeeManagementPage
from .ui_common import FormDialog, card, fill_table, heading, make_table, muted, query, selected_id, toolbar_row


class CoursesPage(QWidget):
    def __init__(self, db, parent=None):
        super().__init__(parent)
        self.db = db
        self.academic = AcademicRepository(db)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        layout.addWidget(heading("Courses and subjects"))
        layout.addWidget(muted("Create courses first, then add subjects. Attendance, results and fees all depend on this catalogue."))
        row = QHBoxLayout()
        course_card, course_inner = card("Courses")
        add_course = QPushButton("Add course")
        add_course.setProperty("primary", True)
        add_course.clicked.connect(self.add_course)
        course_inner.addLayout(toolbar_row([add_course, None]))
        self.course_table = make_table(["Course", "Description"])
        self.course_table.itemSelectionChanged.connect(self.refresh_subjects)
        course_inner.addWidget(self.course_table)
        subject_card, subject_inner = card("Subjects")
        add_subject = QPushButton("Add subject")
        add_subject.setProperty("primary", True)
        add_subject.clicked.connect(self.add_subject)
        subject_inner.addLayout(toolbar_row([add_subject, None]))
        self.subject_table = make_table(["ID", "Course", "Semester", "Code", "Name"])
        subject_inner.addWidget(self.subject_table)
        row.addWidget(course_card, 1)
        row.addWidget(subject_card, 2)
        layout.addLayout(row, 1)
        self.refresh()

    def refresh(self):
        fill_table(self.course_table, self.academic.list_courses(), ["name", "description"],
                   "No courses yet. Add a course to start setup.")
        self.refresh_subjects()

    def refresh_subjects(self):
        row = self.course_table.currentRow()
        course_name = self.course_table.item(row, 0).text() if row >= 0 else None
        rows = self.academic.list_subjects(course_name)
        fill_table(self.subject_table, rows, ["id", "course", "semester", "subject_code", "name"],
                   "No subjects for this course yet.")

    def add_course(self):
        form = FormDialog("New course", [
            {"label": "Course name", "key": "name", "placeholder": "B.Sc. Computer Science"},
            {"label": "Description", "key": "description", "placeholder": "Optional"},
        ], self, "Create course")
        if form.exec() != QDialog.Accepted:
            return
        try:
            self.academic.create_course(form.values()["name"], form.values()["description"])
        except Exception as e:
            QMessageBox.warning(self, "Could not save course", str(e)); return
        self.refresh()

    def add_subject(self):
        courses = [(c["name"], c["name"]) for c in self.academic.list_courses()]
        if not courses:
            QMessageBox.information(self, "Missing setup", "Create a course first."); return
        row = self.course_table.currentRow()
        selected = self.course_table.item(row, 0).text() if row >= 0 else None
        form = FormDialog("New subject", [
            {"label": "Course", "key": "course_name", "kind": "combo", "options": courses, "value": selected},
            {"label": "Semester", "key": "semester", "value": "1"},
            {"label": "Subject code", "key": "subject_code", "placeholder": "PHY101"},
            {"label": "Subject name", "key": "name", "placeholder": "Physics"},
        ], self, "Create subject")
        if form.exec() != QDialog.Accepted:
            return
        data = form.values()
        try:
            self.academic.create_subject(data["course_name"], data["semester"], data["subject_code"], data["name"])
        except Exception as e:
            QMessageBox.warning(self, "Could not save subject", str(e)); return
        self.refresh()


class AttendancePage(QWidget):
    def __init__(self, db, parent=None):
        super().__init__(parent)
        self.db = db
        self.academic = AcademicRepository(db)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        layout.addWidget(heading("Attendance"))
        layout.addWidget(muted("Record classes held and attended by student and subject. The low-attendance threshold is configurable and requires college approval."))
        frame, inner = card()
        self.threshold = QLineEdit(str(self.academic.get_low_attendance_threshold()))
        self.threshold.setMaximumWidth(90)
        save = QPushButton("Save threshold")
        save.clicked.connect(self.save_threshold)
        add = QPushButton("Record attendance")
        add.setProperty("primary", True)
        add.clicked.connect(self.add_attendance)
        refresh = QPushButton("Refresh")
        refresh.clicked.connect(self.refresh)
        inner.addLayout(toolbar_row([muted("Low attendance threshold %"), self.threshold, save, None, add, refresh]))
        self.table = make_table(["Enrollment", "Student", "Subject", "Held", "Attended", "Percent", "Warning"])
        inner.addWidget(self.table)
        layout.addWidget(frame, 1)
        imported, imported_inner = card("Imported attendance summaries")
        imported_inner.addWidget(muted("Imported percentages do not include subject-level class counts. Record-level attendance remains separate."))
        self.imported_note = muted("")
        imported_inner.addWidget(self.imported_note)
        self.imported_table = make_table(["Enrollment", "Student", "Course", "Semester", "Attendance %", "Source"])
        imported_inner.addWidget(self.imported_table)
        layout.addWidget(imported, 1)
        self.refresh()

    def save_threshold(self):
        try:
            self.academic.set_low_attendance_threshold(self.threshold.text())
        except Exception as e:
            QMessageBox.warning(self, "Invalid threshold", str(e)); return
        self.refresh()

    def add_attendance(self):
        students = query(self.db, "SELECT id,enrollment_no,full_name FROM students WHERE status='active' ORDER BY full_name")
        subjects = self.academic.list_subjects()
        if not students or not subjects:
            QMessageBox.information(self, "Missing setup", "Add students and subjects first."); return
        form = FormDialog("Record attendance", [
            {"label": "Student", "key": "student_id", "kind": "combo",
             "options": [(f"{s['enrollment_no']} — {s['full_name']}", s["id"]) for s in students]},
            {"label": "Subject", "key": "subject_id", "kind": "combo",
             "options": [(f"{s['subject_code']} — {s['name']}", s["id"]) for s in subjects]},
            {"label": "Period date", "key": "period_date", "value": date.today().isoformat(), "placeholder": "YYYY-MM-DD"},
            {"label": "Period label", "key": "period_label", "value": "regular"},
            {"label": "Classes held", "key": "classes_held", "value": "1"},
            {"label": "Classes attended", "key": "classes_attended", "value": "1"},
        ], self, "Save attendance")
        if form.exec() != QDialog.Accepted:
            return
        data = form.values()
        try:
            self.academic.record_attendance(data["student_id"], data["subject_id"], data["period_date"],
                                            data["classes_held"], data["classes_attended"], data["period_label"])
        except Exception as e:
            QMessageBox.warning(self, "Attendance not saved", str(e)); return
        self.refresh()

    def refresh(self):
        rows = self.academic.attendance_summary()
        display = [{
            "enrollment_no": r["enrollment_no"], "full_name": r["full_name"],
            "subject": f"{r['subject_code']} — {r['subject_name']}",
            "held": r["classes_held"], "attended": r["classes_attended"],
            "percent": "N/A" if r["percentage"] is None else f"{r['percentage']:.2f}%",
            "warning": "LOW" if r["below_threshold"] else ""
        } for r in rows]
        fill_table(self.table, display,
                   ["enrollment_no", "full_name", "subject", "held", "attended", "percent", "warning"],
                   "No attendance has been recorded yet.")
        imported = self.academic.imported_attendance_summaries()
        truncated = len(imported) > 500
        imported = imported[:500]
        self.imported_note.setText(
            "Showing the first 500 imported summaries; all imported percentages are also available in Student records."
            if truncated else f"{len(imported):,} imported attendance summary(s)."
        )
        imported_display = [{
            "enrollment_no": r["enrollment_no"], "full_name": r["full_name"],
            "course_name": r["course_name"] or "—", "semester": r["semester"] or "—",
            "attendance_percentage": f"{r['attendance_percentage']:.2f}%",
            "source_name": r["source_name"]
        } for r in imported]
        fill_table(self.imported_table, imported_display,
                   ["enrollment_no", "full_name", "course_name", "semester", "attendance_percentage", "source_name"],
                   "No attendance summaries were imported.")


class ResultsPage(QWidget):
    def __init__(self, db, parent=None):
        super().__init__(parent)
        self.db = db
        self.academic = AcademicRepository(db)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        layout.addWidget(heading("Results"))
        layout.addWidget(muted("Save subject-wise marks. The same student, subject, exam type and term combination updates the existing result."))
        frame, inner = card()
        add = QPushButton("Record / update result")
        add.setProperty("primary", True)
        add.clicked.connect(self.add_result)
        refresh = QPushButton("Refresh")
        refresh.clicked.connect(self.refresh)
        inner.addLayout(toolbar_row([add, refresh, None]))
        self.table = make_table(["Enrollment", "Student", "Subject", "Exam", "Term", "Marks", "Percent"])
        inner.addWidget(self.table)
        layout.addWidget(frame, 1)
        imported, imported_inner = card("Imported result summaries")
        imported_inner.addWidget(muted("Imported percentages do not include subject, exam, term, or raw marks. Record-level results remain separate."))
        self.imported_note = muted("")
        imported_inner.addWidget(self.imported_note)
        self.imported_table = make_table(["Enrollment", "Student", "Course", "Semester", "Result %", "Source"])
        imported_inner.addWidget(self.imported_table)
        layout.addWidget(imported, 1)
        self.refresh()

    def add_result(self):
        students = query(self.db, "SELECT id,enrollment_no,full_name FROM students WHERE status='active' ORDER BY full_name")
        subjects = self.academic.list_subjects()
        if not students or not subjects:
            QMessageBox.information(self, "Missing setup", "Add students and subjects first."); return
        form = FormDialog("Record result", [
            {"label": "Student", "key": "student_id", "kind": "combo",
             "options": [(f"{s['enrollment_no']} — {s['full_name']}", s["id"]) for s in students]},
            {"label": "Subject", "key": "subject_id", "kind": "combo",
             "options": [(f"{s['subject_code']} — {s['name']}", s["id"]) for s in subjects]},
            {"label": "Exam type", "key": "exam_type", "value": "Internal"},
            {"label": "Term / semester", "key": "term", "value": "Semester 1"},
            {"label": "Marks obtained", "key": "marks_obtained", "value": "0"},
            {"label": "Maximum marks", "key": "maximum_marks", "value": "100"},
        ], self, "Save result")
        if form.exec() != QDialog.Accepted:
            return
        data = form.values()
        try:
            self.academic.save_result(data["student_id"], data["subject_id"], data["exam_type"],
                                      data["term"], data["marks_obtained"], data["maximum_marks"])
        except Exception as e:
            QMessageBox.warning(self, "Result not saved", str(e)); return
        self.refresh()

    def refresh(self):
        rows = self.academic.results()
        display = [{
            "enrollment_no": r["enrollment_no"], "full_name": r["full_name"],
            "subject": f"{r['subject_code']} — {r['subject_name']}",
            "exam_type": r["exam_type"], "term": r["term"],
            "marks": f"{r['marks_obtained']:g}/{r['maximum_marks']:g}",
            "percent": f"{r['marks_obtained']/r['maximum_marks']*100:.2f}%"
        } for r in rows]
        fill_table(self.table, display,
                   ["enrollment_no", "full_name", "subject", "exam_type", "term", "marks", "percent"],
                   "No results have been recorded yet.")
        imported = self.academic.imported_result_summaries()
        truncated = len(imported) > 500
        imported = imported[:500]
        self.imported_note.setText(
            "Showing the first 500 imported summaries; all imported percentages are also available in Student records."
            if truncated else f"{len(imported):,} imported result summary(s)."
        )
        imported_display = [{
            "enrollment_no": r["enrollment_no"], "full_name": r["full_name"],
            "course_name": r["course_name"] or "—", "semester": r["semester"] or "—",
            "result_percentage": f"{r['result_percentage']:.2f}%",
            "source_name": r["source_name"]
        } for r in imported]
        fill_table(self.imported_table, imported_display,
                   ["enrollment_no", "full_name", "course_name", "semester", "result_percentage", "source_name"],
                   "No result summaries were imported.")


class _LegacyFeesPage(QWidget):
    def __init__(self, db, parent=None):
        super().__init__(parent)
        self.db = db
        self.fees = FeeRepository(db)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        layout.addWidget(heading("Fee management"))
        layout.addWidget(muted("Fee structures are set by course, term and fee head. Payments require a unique receipt number and are allocated to a term."))
        top = QHBoxLayout()
        struct_card, struct_inner = card("Fee structures")
        set_fee = QPushButton("Set fee structure")
        set_fee.setProperty("primary", True)
        set_fee.clicked.connect(self.set_fee)
        struct_inner.addLayout(toolbar_row([set_fee, None]))
        self.structure_table = make_table(["ID", "Course", "Term", "Fee head", "Amount"])
        struct_inner.addWidget(self.structure_table)
        pay_card, pay_inner = card("Recorded payments")
        add_pay = QPushButton("Record payment")
        add_pay.setProperty("primary", True)
        add_pay.clicked.connect(self.add_payment)
        pay_inner.addLayout(toolbar_row([add_pay, None]))
        self.payment_table = make_table(["ID", "Enrollment", "Student", "Term", "Date", "Mode", "Receipt", "Amount"])
        pay_inner.addWidget(self.payment_table)
        top.addWidget(struct_card, 1)
        top.addWidget(pay_card, 2)
        layout.addLayout(top, 3)
        bal_card, bal_inner = card("Student balances")
        self.term = QComboBox()
        self.term.setEditable(True)
        refresh = QPushButton("Refresh balances")
        refresh.clicked.connect(self.refresh_balances)
        bal_inner.addLayout(toolbar_row([muted("Term"), self.term, refresh, None]))
        self.balance_table = make_table(["Enrollment", "Student", "Term", "Fee due", "Paid", "Balance"])
        bal_inner.addWidget(self.balance_table)
        layout.addWidget(bal_card, 2)
        imported, imported_inner = card("Imported fee summaries")
        imported_inner.addWidget(muted("These are imported fee snapshots, not dated payments or term-based fee structures."))
        self.imported_note = muted("")
        imported_inner.addWidget(self.imported_note)
        self.imported_table = make_table(["Enrollment", "Student", "Course", "Semester", "Total", "Submitted", "Pending", "Source"])
        imported_inner.addWidget(self.imported_table)
        layout.addWidget(imported, 2)
        self.refresh()

    def refresh(self):
        terms = self.fees.known_terms() or ["Semester 1"]
        current = self.term.currentText()
        self.term.blockSignals(True)
        self.term.clear()
        self.term.addItems(terms)
        if current:
            self.term.setEditText(current)
        elif terms:
            self.term.setCurrentIndex(0)
        self.term.blockSignals(False)
        fill_table(self.structure_table, self.fees.list_structures(),
                   ["id", "course", "term", "fee_head", "amount"], "No fee structures yet.")
        fill_table(self.payment_table, self.fees.list_payments(),
                   ["id", "enrollment_no", "full_name", "term", "payment_date", "payment_mode", "receipt_number", "amount"],
                   "No payments recorded yet.")
        self.refresh_balances()
        imported = self.fees.imported_fee_summaries()
        truncated = len(imported) > 500
        imported = imported[:500]
        self.imported_note.setText(
            "Showing the first 500 imported summaries; all imported amounts are also available in Student records."
            if truncated else f"{len(imported):,} imported fee summary(s)."
        )
        imported_display = [{
            "enrollment_no": r["enrollment_no"], "full_name": r["full_name"],
            "course_name": r["course_name"] or "—", "semester": r["semester"] or "—",
            "total": "" if r["total_fees_paise"] is None else f"{r['total_fees_paise'] / 100:,.2f}",
            "submitted": "" if r["submitted_fees_paise"] is None else f"{r['submitted_fees_paise'] / 100:,.2f}",
            "pending": "" if r["pending_fees_paise"] is None else f"{r['pending_fees_paise'] / 100:,.2f}",
            "source_name": r["source_name"]
        } for r in imported]
        fill_table(self.imported_table, imported_display,
                   ["enrollment_no", "full_name", "course_name", "semester", "total", "submitted", "pending", "source_name"],
                   "No fee summaries were imported.")

    def refresh_balances(self):
        term = self.term.currentText().strip()
        if not term:
            fill_table(self.balance_table, [], [], "Enter a term to view balances.")
            return
        rows = self.fees.student_balances(term)
        display = [{
            "enrollment_no": r["enrollment_no"], "full_name": r["full_name"], "term": term,
            "due": f"{r['fee_due']:.2f}", "paid": f"{r['paid']:.2f}", "balance": f"{r['balance']:.2f}"
        } for r in rows]
        fill_table(self.balance_table, display,
                   ["enrollment_no", "full_name", "term", "due", "paid", "balance"],
                   "No active students, or no fee structure for this term.")

    def set_fee(self):
        courses = query(self.db, "SELECT id,name FROM courses ORDER BY name")
        if not courses:
            QMessageBox.information(self, "Missing setup", "Create a course first."); return
        form = FormDialog("Fee structure", [
            {"label": "Course", "key": "course_name", "kind": "combo",
             "options": [(c["name"], c["name"]) for c in courses]},
            {"label": "Term / semester", "key": "term", "value": self.term.currentText() or "Semester 1"},
            {"label": "Fee head", "key": "fee_head", "value": "Tuition"},
            {"label": "Amount", "key": "amount", "placeholder": "Amount in rupees"},
        ], self, "Save structure")
        if form.exec() != QDialog.Accepted:
            return
        data = form.values()
        try:
            self.fees.set_fee_structure(data["course_name"], data["term"], data["amount"], data["fee_head"])
        except Exception as e:
            QMessageBox.warning(self, "Fee structure not saved", str(e)); return
        self.refresh()

    def add_payment(self):
        students = query(self.db, "SELECT id,enrollment_no,full_name FROM students ORDER BY full_name")
        if not students:
            QMessageBox.information(self, "No students", "Create student records first."); return
        form = FormDialog("Record payment", [
            {"label": "Student", "key": "student_id", "kind": "combo",
             "options": [(f"{s['enrollment_no']} — {s['full_name']}", s["id"]) for s in students]},
            {"label": "Amount", "key": "amount"},
            {"label": "Payment mode", "key": "mode", "kind": "combo", "options": sorted(PAYMENT_MODES)},
            {"label": "Receipt number", "key": "receipt", "placeholder": "Must be unique"},
            {"label": "Fee term", "key": "term", "value": self.term.currentText() or "Semester 1"},
            {"label": "Payment date", "key": "day", "value": date.today().isoformat()},
        ], self, "Save receipt")
        if form.exec() != QDialog.Accepted:
            return
        data = form.values()
        try:
            result = self.fees.record_payment(data["student_id"], data["amount"], data["day"],
                                              data["mode"], data["receipt"], data["term"])
        except Exception as e:
            QMessageBox.warning(self, "Payment not saved", str(e)); return
        QMessageBox.information(self, "Receipt recorded",
                                f"Receipt: {result['receipt_number']}\nAmount: {result['amount']:.2f}")
        self.refresh()


FeesPage = FeeManagementPage


class Stage3Dialog(QDialog):
    def __init__(self, db, parent=None):
        super().__init__(parent)
        self.setWindowTitle("CampusOne — Attendance, Results & Fees")
        self.resize(1100, 680)
        layout = QVBoxLayout(self)
        tabs = QTabWidget()
        for page, title in [
            (AttendancePage(db), "Attendance"),
            (ResultsPage(db), "Results"),
            (FeeManagementPage(db), "Fees")
        ]:
            if isinstance(page, FeeManagementPage):
                tabs.addTab(page, title)
            else:
                scroll = QScrollArea()
                scroll.setWidgetResizable(True)
                scroll.setWidget(page)
                tabs.addTab(scroll, title)
        layout.addWidget(tabs)
