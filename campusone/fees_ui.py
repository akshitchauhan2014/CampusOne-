import sqlite3
from datetime import date

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPdfWriter
from PySide6.QtWidgets import (
    QComboBox, QDialog, QFileDialog, QGridLayout, QHBoxLayout, QLabel, QMessageBox,
    QPushButton, QScrollArea, QVBoxLayout, QWidget,
)

from .academics import AcademicValidationError
from .fees import FeeRepository, PAYMENT_MODES
from .ui_common import FormDialog, card, fill_table, heading, make_table, muted, query, toolbar_row


class FeesPage(QWidget):
    def __init__(self, db, parent=None):
        super().__init__(parent)
        self.db = db
        self.fees = FeeRepository(db)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QScrollArea.NoFrame)
        self.content = QWidget()
        layout = QVBoxLayout(self.content)
        layout.setContentsMargins(2, 4, 12, 14)
        layout.setSpacing(18)
        layout.addWidget(heading("CampusOne – Fee Management", 17))

        dashboard, dashboard_inner = card("Fee Collection Dashboard")
        metrics = QGridLayout()
        metrics.setHorizontalSpacing(12)
        metrics.setVerticalSpacing(12)
        self.metric_labels = {}
        for index, (key, title, color) in enumerate((
            ("total_due", "Total Fee", "#2563EB"),
            ("total_submitted", "Total Fee Submitted", "#059669"),
            ("total_pending", "Total Pending Fee", "#DC2626"),
            ("collection_percentage", "Fee Collection", "#7C3AED"),
        )):
            metric, metric_layout = card()
            metric.setStyleSheet(
                f"QFrame#pageCard{{background:#FFFFFF;border:1px solid #DCE4EF;"
                f"border-top:3px solid {color};border-radius:11px;}}"
            )
            metric_layout.addWidget(heading(title, 9))
            value = QLabel("0.0%" if key == "collection_percentage" else "₹0.00")
            value.setStyleSheet(f"font-size:19pt;font-weight:750;color:{color};")
            metric_layout.addWidget(value)
            self.metric_labels[key] = value
            metrics.addWidget(metric, 0, index)
        dashboard_inner.addLayout(metrics)

        charts = QHBoxLayout()
        charts.setSpacing(14)
        self.collection_chart_layout = self._chart_card(charts, "Submitted vs Pending", 1)
        self.term_chart_layout = self._chart_card(charts, "Collection by Semester / Term", 2)
        dashboard_inner.addLayout(charts)
        layout.addWidget(dashboard)

        structure, structure_inner = card("Fee Structures")
        set_fee = QPushButton("Set Fee Structure")
        set_fee.setProperty("primary", True)
        set_fee.clicked.connect(self.set_fee)
        structure_inner.addLayout(toolbar_row([set_fee, None]))
        self.structure_table = make_table(["ID", "Course", "Term / Semester", "Fee Head", "Amount"])
        self.structure_table.setMinimumHeight(230)
        self.structure_table.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        for column, width in enumerate((70, 210, 160, 170, 130)):
            self.structure_table.setColumnWidth(column, width)
        structure_inner.addWidget(self.structure_table)
        layout.addWidget(structure)

        payments, payments_inner = card("Recorded Payments")
        record = QPushButton("Record Payment")
        record.setProperty("primary", True)
        record.clicked.connect(self.add_payment)
        self.export_receipt = QPushButton("Export Selected Receipt")
        self.export_receipt.setEnabled(False)
        self.export_receipt.clicked.connect(self.export_selected_receipt)
        payments_inner.addLayout(toolbar_row([record, self.export_receipt, None]))
        self.payment_table = make_table([
            "ID", "Enrollment ID", "Student Name", "Term / Semester", "Payment Date",
            "Payment Mode", "Receipt Number", "Amount",
        ])
        self.payment_table.setMinimumHeight(280)
        self.payment_table.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        for column, width in enumerate((65, 135, 190, 145, 130, 130, 190, 125)):
            self.payment_table.setColumnWidth(column, width)
        self.payment_table.itemSelectionChanged.connect(
            lambda: self.export_receipt.setEnabled(self.payment_table.currentRow() >= 0)
        )
        payments_inner.addWidget(self.payment_table)
        layout.addWidget(payments)

        balances, balances_inner = card("Student Balances")
        self.term = QComboBox()
        self.term.setMinimumWidth(200)
        refresh = QPushButton("Refresh Balances")
        refresh.clicked.connect(self.refresh)
        self.term.currentIndexChanged.connect(self.refresh_balances)
        balances_inner.addLayout(toolbar_row([muted("Semester / term"), self.term, refresh, None]))
        self.balance_table = make_table([
            "Enrollment ID", "Student Name", "Course", "Semester / Term",
            "Total Fee Due", "Total Fee Submitted", "Pending Fee", "Outstanding Balance",
            "Fee Submission %", "Payment Status", "Source",
        ])
        self.balance_table.setMinimumHeight(380)
        self.balance_table.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        for column, width in enumerate((135, 185, 155, 140, 130, 150, 130, 155, 130, 245, 220)):
            self.balance_table.setColumnWidth(column, width)
        balances_inner.addWidget(self.balance_table)
        layout.addWidget(balances)
        layout.addStretch(1)

        self.scroll.setWidget(self.content)
        outer.addWidget(self.scroll)
        self.refresh()

    @staticmethod
    def _chart_card(row, title, stretch):
        frame, inner = card(title)
        row.addWidget(frame, stretch)
        return inner

    def refresh(self):
        current_term = self.term.currentData()
        self.term.blockSignals(True)
        self.term.clear()
        self.term.addItem("All semesters / terms", None)
        for term in self.fees.known_terms():
            self.term.addItem(term, term)
        index = self.term.findData(current_term)
        self.term.setCurrentIndex(index if index >= 0 else 0)
        self.term.blockSignals(False)

        fill_table(
            self.structure_table, self.fees.list_structures(),
            ["id", "course", "term", "fee_head", "amount"], "No fee structures yet.",
        )
        fill_table(
            self.payment_table, self.fees.list_payments(),
            ["id", "enrollment_no", "full_name", "term", "payment_date",
             "payment_mode", "receipt_number", "amount"],
            "No payments recorded yet.",
        )
        self.update_dashboard()
        self.refresh_balances()

    def update_dashboard(self):
        summary = self.fees.fee_dashboard_summary()
        for key in ("total_due", "total_submitted", "total_pending"):
            self.metric_labels[key].setText(f"₹{summary[key]:,.2f}")
        self.metric_labels["collection_percentage"].setText(
            f"{summary['collection_percentage']:.1f}%"
        )
        self._render_charts(summary)

    def _render_charts(self, summary):
        from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
        from matplotlib.figure import Figure

        for chart_layout in (self.collection_chart_layout, self.term_chart_layout):
            while chart_layout.count():
                item = chart_layout.takeAt(0)
                widget = item.widget()
                if widget is not None:
                    widget.deleteLater()

        figure = Figure(figsize=(4.0, 2.5), dpi=90)
        axis = figure.add_subplot(111)
        submitted, pending = summary["total_submitted"], summary["total_pending"]
        if submitted + pending:
            axis.pie(
                [submitted, pending], labels=["Submitted", "Pending"],
                colors=["#10B981", "#EF6B68"], startangle=90, autopct="%1.0f%%",
                wedgeprops={"width": 0.42, "edgecolor": "white"},
            )
            axis.set_aspect("equal")
        else:
            axis.text(
                .5, .5, "No fee collection data", ha="center", va="center",
                color="#64748B", transform=axis.transAxes,
            )
            axis.set_axis_off()
        figure.tight_layout()
        canvas = FigureCanvasQTAgg(figure)
        canvas.setMinimumHeight(230)
        self.collection_chart_layout.addWidget(canvas)

        figure = Figure(figsize=(6.5, 2.5), dpi=90)
        axis = figure.add_subplot(111)
        terms = summary["by_term"]
        if terms:
            positions = list(range(len(terms)))
            width = .36
            axis.bar(
                [position - width / 2 for position in positions],
                [row["submitted"] for row in terms], width,
                label="Submitted", color="#10B981",
            )
            axis.bar(
                [position + width / 2 for position in positions],
                [row["pending"] for row in terms], width,
                label="Pending", color="#EF6B68",
            )
            axis.set_xticks(positions, [row["term"] for row in terms], rotation=20, ha="right")
            axis.legend(frameon=False, ncol=2, loc="upper right")
            axis.grid(axis="y", color="#E5EAF1", linewidth=.8)
            axis.set_axisbelow(True)
            axis.spines[["top", "right", "left"]].set_visible(False)
            axis.tick_params(axis="y", length=0, labelsize=8)
            axis.tick_params(axis="x", length=0, labelsize=8)
        else:
            axis.text(
                .5, .5, "No semester or term data", ha="center", va="center",
                color="#64748B", transform=axis.transAxes,
            )
            axis.set_axis_off()
        figure.tight_layout()
        canvas = FigureCanvasQTAgg(figure)
        canvas.setMinimumHeight(230)
        self.term_chart_layout.addWidget(canvas)

    def refresh_balances(self):
        term = self.term.currentData()
        rows = self.fees.student_balances(term)
        display = []
        for row in rows:
            percentage = row["submission_percentage"]
            display.append({
                "enrollment_no": row["enrollment_no"],
                "full_name": row["full_name"],
                "course_name": row["course_name"] or "—",
                "term": row["term"],
                "due": f"₹{row['fee_due']:,.2f}",
                "paid": f"₹{row['paid']:,.2f}",
                "pending": f"₹{row['pending']:,.2f}",
                "balance": f"₹{row['balance']:,.2f}",
                "percentage": "—" if percentage is None else f"{percentage:.1f}%",
                "payment_status": row["payment_status"],
                "source": row["source"],
                "pending_percentage": row["pending_percentage"],
            })
        fill_table(
            self.balance_table, display,
            ["enrollment_no", "full_name", "course_name", "term", "due", "paid",
             "pending", "balance", "percentage", "payment_status", "source"],
            "No student fee records are available for this filter.",
        )
        for row_index, row in enumerate(display):
            pending_percentage = row["pending_percentage"]
            if pending_percentage is None:
                continue
            if pending_percentage > 40:
                background = QColor("#FEE2E2")
            elif pending_percentage > 0:
                background = QColor("#FEF3C7")
            else:
                background = QColor("#DCFCE7")
            for column in range(self.balance_table.columnCount()):
                item = self.balance_table.item(row_index, column)
                if item is not None:
                    item.setBackground(background)

    def set_fee(self):
        courses = query(self.db, "SELECT id,name FROM courses ORDER BY name")
        if not courses:
            QMessageBox.information(self, "Missing setup", "Create a course first.")
            return
        form = FormDialog("Fee structure", [
            {"label": "Course", "key": "course_name", "kind": "combo",
             "options": [(course["name"], course["name"]) for course in courses]},
            {"label": "Term / semester", "key": "term", "value": self.term.currentData() or "Semester 1"},
            {"label": "Fee head", "key": "fee_head", "value": "Tuition"},
            {"label": "Amount", "key": "amount", "placeholder": "Amount in rupees"},
        ], self, "Save structure")
        if form.exec() != QDialog.Accepted:
            return
        data = form.values()
        try:
            self.fees.set_fee_structure(
                data["course_name"], data["term"], data["amount"], data["fee_head"],
            )
        except (AcademicValidationError, sqlite3.Error) as error:
            QMessageBox.warning(self, "Fee structure not saved", str(error))
            return
        self.refresh()

    def add_payment(self, student_id=None):
        students = query(
            self.db, "SELECT id,enrollment_no,full_name FROM students ORDER BY full_name",
        )
        if not students:
            QMessageBox.information(self, "No students", "Create student records first.")
            return
        selected_student = next(
            (student for student in students if student["id"] == student_id), None
        )
        if student_id is not None and selected_student is None:
            QMessageBox.warning(self, "Student not found", "That student record no longer exists.")
            return
        default_term = self.term.currentData() or "Semester 1"
        if selected_student:
            student_row = query(
                self.db, "SELECT semester FROM students WHERE id=?", (student_id,),
            )
            if student_row and student_row[0]["semester"] is not None:
                default_term = f"Semester {student_row[0]['semester']}"
        if student_id is not None and self.fees.student_fees_fully_paid(student_id, default_term):
            QMessageBox.information(
                self, "Fees already paid",
                f"This student's fees for {default_term} are fully paid.",
            )
            return
        form = FormDialog("Record payment", [
            {"label": "Student", "key": "student_id", "kind": "combo",
             "options": [
                 (f"{student['enrollment_no']} — {student['full_name']}", student["id"])
                 for student in students
             ], "value": student_id},
            {"label": "Amount", "key": "amount"},
            {"label": "Payment mode", "key": "mode", "kind": "combo",
             "options": sorted(PAYMENT_MODES)},
            {"label": "Fee term", "key": "term",
             "value": default_term},
            {"label": "Payment date", "key": "day", "value": date.today().isoformat()},
        ], self, "Record payment")
        if student_id is not None:
            form.widgets["student_id"].setEnabled(False)
        if form.exec() != QDialog.Accepted:
            return
        data = form.values()
        try:
            result = self.fees.record_payment(
                data["student_id"], data["amount"], data["day"], data["mode"], data["term"],
            )
        except (AcademicValidationError, sqlite3.Error) as error:
            QMessageBox.warning(self, "Payment not saved", str(error))
            return
        QMessageBox.information(
            self, "Receipt recorded",
            f"Receipt: {result['receipt_number']}\nAmount: {result['amount']:.2f}",
        )
        self.refresh()

    def show_student_fees(self, student_id):
        rows = self.fees.student_balances(student_id=student_id)
        if not rows:
            QMessageBox.information(
                self, "No fee records",
                "No fee structure, payment, or imported fee summary is available for this student.",
            )
            return
        student_rows = query(
            self.db, "SELECT semester FROM students WHERE id=?", (student_id,),
        )
        payment_term = (
            f"Semester {student_rows[0]['semester']}"
            if student_rows and student_rows[0]["semester"] is not None else None
        )
        StudentFeeDialog(
            rows, lambda: self.add_payment(student_id), self, payment_term,
        ).exec()
        self.refresh()

    def generate_student_receipt(self, student_id):
        payments = self.fees.list_payments(student_id=student_id)
        if not payments:
            QMessageBox.information(
                self, "No recorded payments",
                "This student does not have a recorded payment to generate a receipt for.",
            )
            return
        dialog = StudentReceiptDialog(payments, self)
        if dialog.exec() != QDialog.Accepted:
            return
        payment = dialog.selected_payment()
        if payment is None:
            QMessageBox.information(self, "Select a payment", "Select a payment first.")
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Generate payment receipt",
            f"{payment['receipt_number']}.pdf", "PDF files (*.pdf)",
        )
        if not path:
            return
        if not path.casefold().endswith(".pdf"):
            path += ".pdf"
        values = [
            payment["id"], payment["enrollment_no"], payment["full_name"],
            payment["term"], payment["payment_date"], payment["payment_mode"],
            payment["receipt_number"], f"{payment['amount']:.2f}",
        ]
        try:
            StudentFeeDialog._write_receipt_pdf(path, values)
        except OSError as error:
            QMessageBox.warning(self, "Receipt generation failed", str(error))
            return
        QMessageBox.information(self, "Receipt generated", f"Receipt saved to:\n{path}")

    def export_selected_receipt(self):
        row = self.payment_table.currentRow()
        if row < 0:
            QMessageBox.information(self, "Select a payment", "Select a recorded payment first.")
            return
        values = [
            self.payment_table.item(row, column).text()
            if self.payment_table.item(row, column) else ""
            for column in range(self.payment_table.columnCount())
        ]
        path, _ = QFileDialog.getSaveFileName(
            self, "Save payment receipt", f"{values[6]}.pdf", "PDF files (*.pdf)",
        )
        if not path:
            return
        if not path.casefold().endswith(".pdf"):
            path += ".pdf"
        try:
            StudentFeeDialog._write_receipt_pdf(path, values)
        except OSError as error:
            QMessageBox.warning(self, "Receipt export failed", str(error))
            return
        QMessageBox.information(self, "Receipt exported", f"Receipt saved to:\n{path}")


class StudentReceiptDialog(QDialog):
    def __init__(self, payments, parent=None):
        super().__init__(parent)
        self.payments = payments
        self.setWindowTitle("Generate student receipt")
        self.resize(850, 420)
        layout = QVBoxLayout(self)
        layout.addWidget(heading("Select a recorded payment", 14))
        self.table = make_table([
            "Payment ID", "Term", "Date", "Mode", "Receipt number", "Amount",
        ])
        display = [{
            "id": payment["id"],
            "term": payment["term"],
            "date": payment["payment_date"],
            "mode": payment["payment_mode"],
            "receipt": payment["receipt_number"],
            "amount": f"₹{payment['amount']:,.2f}",
        } for payment in payments]
        fill_table(self.table, display, ["id", "term", "date", "mode", "receipt", "amount"])
        layout.addWidget(self.table, 1)
        buttons = QHBoxLayout()
        buttons.addStretch()
        generate = QPushButton("Generate PDF receipt")
        generate.setProperty("primary", True)
        generate.clicked.connect(self.accept)
        generate.setEnabled(bool(payments))
        self.table.itemDoubleClicked.connect(lambda *_: self.accept())
        close = QPushButton("Cancel")
        close.clicked.connect(self.reject)
        buttons.addWidget(generate)
        buttons.addWidget(close)
        layout.addLayout(buttons)

    def selected_payment(self):
        row = self.table.currentRow()
        if row < 0:
            return None
        payment_id = int(self.table.item(row, 0).text())
        return next((payment for payment in self.payments if payment["id"] == payment_id), None)


class StudentFeeDialog(QDialog):
    def __init__(self, balances, record_payment, parent=None, payment_term=None):
        super().__init__(parent)
        student = balances[0]
        self.setWindowTitle(f"Fee details · {student['enrollment_no']}")
        self.resize(820, 430)
        layout = QVBoxLayout(self)
        layout.addWidget(heading(
            f"{student['full_name']} · {student['enrollment_no']}", 14,
        ))
        self.table = make_table([
            "Term / semester", "Total fee", "Submitted", "Pending", "Payment status", "Source",
        ])
        display = [{
            "term": row["term"],
            "due": f"₹{row['fee_due']:,.2f}",
            "paid": f"₹{row['paid']:,.2f}",
            "pending": f"₹{row['pending']:,.2f}",
            "status": row["payment_status"],
            "source": row["source"],
        } for row in balances]
        fill_table(
            self.table, display, ["term", "due", "paid", "pending", "status", "source"],
            "No fee records are available.",
        )
        layout.addWidget(self.table, 1)
        buttons = QHBoxLayout()
        buttons.addStretch()
        payment = QPushButton("Submit new fee")
        payment.setProperty("primary", True)
        due_balances = [
            row for row in balances
            if payment_term is not None
            and FeeRepository._term_key(row["term"]) == FeeRepository._term_key(payment_term)
            and row["fee_due"] > 0
        ]
        fully_paid = bool(due_balances) and all(row["pending"] <= 0 for row in due_balances)
        payment.setEnabled(not fully_paid)
        if fully_paid:
            payment.setToolTip("All fees with a recorded amount due have been paid.")
        payment.clicked.connect(self.accept)
        close = QPushButton("Close")
        close.clicked.connect(self.reject)
        buttons.addWidget(payment)
        buttons.addWidget(close)
        layout.addLayout(buttons)
        self._record_payment = record_payment
        self.accepted.connect(self._record_payment)

    @staticmethod
    def _write_receipt_pdf(path, values):
        writer = QPdfWriter(path)
        writer.setResolution(96)
        painter = QPainter(writer)
        if not painter.isActive():
            raise OSError("The PDF receipt could not be created.")
        painter.setFont(QFont("Arial", 18, QFont.Bold))
        painter.drawText(100, 150, "CampusOne Payment Receipt")
        painter.setFont(QFont("Arial", 11))
        fields = (
            ("Receipt Number", values[6]),
            ("Payment Record", values[0]),
            ("Enrollment ID", values[1]),
            ("Student Name", values[2]),
            ("Term / Semester", values[3]),
            ("Payment Date", values[4]),
            ("Payment Mode", values[5]),
            ("Amount", f"₹{values[7]}"),
        )
        for index, (label_text, value) in enumerate(fields, start=1):
            painter.drawText(100, 220 + index * 70, f"{label_text}: {value}")
        painter.end()
