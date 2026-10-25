from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QFileDialog, QHBoxLayout, QLabel,
    QLineEdit, QMessageBox, QPushButton, QTableWidget, QTableWidgetItem,
    QVBoxLayout, QWidget
)
from PySide6.QtCore import QItemSelectionModel
from .excel_import import ExcelImportService, preview_workbook, suggest_mapping, export_rejected
from .students import StudentRepository
from .ui_common import FormDialog, card, fill_table, heading, make_table, muted, query, selected_id, toolbar_row


class StudentRecordsPage(QWidget):
    PAGE_SIZE = 500   # large rosters (10,000+) are paged; rendering every row freezes the window

    def __init__(self, db, parent=None):
        super().__init__(parent)
        self.db = db
        self.repo = StudentRepository(db)
        self.importer = ExcelImportService(db)
        self.last_preview = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        layout.addWidget(heading("Student records"))
        layout.addWidget(muted("Create and maintain official student records. Excel import previews changes before anything is saved."))
        frame, inner = card()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search enrollment, student/father name, phone, or email")
        self.search.textChanged.connect(self.refresh)
        self.status = QComboBox()
        self.status.addItems(["All statuses", "active", "inactive", "graduated", "withdrawn"])
        self.status.currentTextChanged.connect(self.refresh)
        self.select_all = QCheckBox("Select all shown")
        self.select_all.setToolTip(f"Select every student currently shown (up to {self.PAGE_SIZE:,} rows).")
        self.select_all.toggled.connect(self.select_visible_rows)
        add = QPushButton("Add student")
        add.setProperty("primary", True)
        add.clicked.connect(self.add_student)
        edit = QPushButton("Edit selected")
        edit.clicked.connect(self.edit_student)
        delete = QPushButton("Delete selected")
        delete.setProperty("danger", True)
        delete.clicked.connect(self.delete_selected)
        toggle = QPushButton("Activate / deactivate")
        toggle.clicked.connect(self.toggle_status)
        inner.addLayout(toolbar_row([self.search, self.status, self.select_all, None, add, edit, delete, toggle]))
        self.table = make_table([
            "ID", "Enrollment", "Full name", "Father's name", "Phone", "Email", "Course", "Semester", "Status",
            "Attendance %", "Result %", "Total fees", "Submitted fees", "Pending fees"
        ])
        self.table.setSelectionMode(QTableWidget.ExtendedSelection)
        self.table.itemSelectionChanged.connect(self.update_select_all)
        inner.addWidget(self.table)
        self.list_note = muted("")
        inner.addWidget(self.list_note)
        layout.addWidget(frame, 1)
        import_frame, import_inner = card("Import Student Data")
        import_inner.addWidget(muted("Map workbook columns, preview add/update/skip/reject counts, then commit. Imported attendance and result percentages are kept as summaries; pending fees are calculated as total fees minus submitted fees. No partial changes are kept if import fails."))
        choose = QPushButton("Import student data")
        choose.setProperty("primary", True)
        choose.clicked.connect(self.import_excel)
        export = QPushButton("Export rejected rows")
        export.clicked.connect(self.export_rejected_rows)
        import_inner.addLayout(toolbar_row([choose, export, None]))
        self.import_status = muted("No preview yet.")
        import_inner.addWidget(self.import_status)
        layout.addWidget(import_frame)
        self.refresh()

    def select_visible_rows(self, checked):
        selection = self.table.selectionModel()
        if selection is None:
            return
        if checked:
            self.table.blockSignals(True)
            try:
                for row in range(self.table.rowCount()):
                    item = self.table.item(row, 0)
                    if item is not None and item.text().strip().isdigit():
                        selection.select(
                            self.table.model().index(row, 0),
                            QItemSelectionModel.Select | QItemSelectionModel.Rows,
                        )
            finally:
                self.table.blockSignals(False)
            self.update_select_all()
        else:
            self.table.clearSelection()

    def update_select_all(self):
        rows = [
            row for row in range(self.table.rowCount())
            if self.table.item(row, 0) is not None
            and self.table.item(row, 0).text().strip().isdigit()
        ]
        selected = self.table.selectionModel().selectedRows()
        checked = bool(rows) and len(selected) == len(rows)
        self.select_all.blockSignals(True)
        self.select_all.setChecked(checked)
        self.select_all.blockSignals(False)

    def delete_selected(self):
        student_ids = sorted({
            int(self.table.item(index.row(), 0).text())
            for index in self.table.selectionModel().selectedRows()
            if self.table.item(index.row(), 0) is not None
            and self.table.item(index.row(), 0).text().strip().isdigit()
        })
        if not student_ids:
            QMessageBox.information(self, "Select students", "Select one or more student rows to delete.")
            return
        confirmation = (
            f"Delete {len(student_ids):,} selected student record(s) and all associated data?\n\n"
            "This permanently removes their academic results, attendance, fee payments and imported "
            "summaries, portal accounts, registration/reset codes, and risk predictions. "
            "Audit history is retained. This action cannot be undone."
        )
        if QMessageBox.question(
            self, "Confirm student data deletion", confirmation,
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        ) != QMessageBox.Yes:
            return
        try:
            deleted = self.repo.delete_many(student_ids)
        except Exception as exc:
            QMessageBox.warning(self, "Deletion failed", str(exc))
            return
        self.select_all.setChecked(False)
        self.refresh()
        QMessageBox.information(self, "Student data deleted", f"Deleted {deleted:,} student record(s) and associated data.")

    def courses(self):
        rows = query(self.db, "SELECT id,name FROM courses ORDER BY name")
        return [(r["name"], r["name"]) for r in rows]

    def refresh(self, *_):
        self.table.clearSelection()
        self.select_all.blockSignals(True)
        self.select_all.setChecked(False)
        self.select_all.blockSignals(False)
        status = self.status.currentText()
        status = None if status == "All statuses" else status
        total = self.repo.count(self.search.text(), status)
        rows = self.repo.search(self.search.text(), status, limit=self.PAGE_SIZE)
        self.list_note.setText(f"Showing the first {len(rows):,} of {total:,} matching students. Refine the search to narrow the list."
                               if total > len(rows) else f"{total:,} matching student(s).")
        summaries = {}
        if rows:
            marks = ", ".join("?" for _ in rows)
            summaries = {
                r["student_id"]: r for r in query(
                    self.db,
                    f"SELECT * FROM student_import_summaries WHERE student_id IN ({marks})",
                    [item["id"] for item in rows]
                )
            }
        display = []
        for item in rows:
            summary = summaries.get(item["id"], {})
            money = lambda key: "" if summary.get(key) is None else f"{summary[key] / 100:,.2f}"
            display.append({
                "id": item["id"], "enrollment_no": item["enrollment_no"], "full_name": item["full_name"],
                "father_name": item.get("father_name") or "", "phone": item.get("phone") or "",
                "email": item.get("email") or "", "course": item.get("course_name") or "—",
                "semester": item.get("semester") or "", "status": item["status"],
                "attendance_percentage": "" if summary.get("attendance_percentage") is None else f"{summary['attendance_percentage']:.2f}",
                "result_percentage": "" if summary.get("result_percentage") is None else f"{summary['result_percentage']:.2f}",
                "total_fees": money("total_fees_paise"),
                "submitted_fees": money("submitted_fees_paise"),
                "pending_fees": money("pending_fees_paise")
            })
        fill_table(self.table, display, [
            "id", "enrollment_no", "full_name", "father_name", "phone", "email", "course", "semester", "status",
            "attendance_percentage", "result_percentage", "total_fees", "submitted_fees", "pending_fees"
        ],
                   "No student records match this search.")

    def student_form(self, existing=None):
        courses = [("Not assigned", None), *self.courses()]
        current_course = (existing or {}).get("course_name")
        form = FormDialog("Student record", [
            {"label": "Enrollment number", "key": "enrollment_no", "value": (existing or {}).get("enrollment_no"), "placeholder": "Official enrollment number"},
            {"label": "Full name", "key": "full_name", "value": (existing or {}).get("full_name")},
            {"label": "Father's name", "key": "father_name", "value": (existing or {}).get("father_name")},
            {"label": "Email", "key": "email", "value": (existing or {}).get("email")},
            {"label": "Phone", "key": "phone", "value": (existing or {}).get("phone")},
            {"label": "Course", "key": "course_name", "kind": "combo", "options": courses, "value": current_course},
            {"label": "Semester", "key": "semester", "value": (existing or {}).get("semester"), "placeholder": "Optional"},
            {"label": "Batch", "key": "batch", "value": (existing or {}).get("batch")},
            {"label": "Status", "key": "status", "kind": "combo",
             "options": ["active", "inactive", "graduated", "withdrawn"],
             "value": (existing or {}).get("status", "active")},
        ], self, "Save student")
        if form.exec() != QDialog.Accepted:
            return None
        return form.values()

    def add_student(self):
        data = self.student_form()
        if data is None:
            return
        try:
            self.repo.create(data)
        except Exception as e:
            QMessageBox.warning(self, "Could not save student", str(e)); return
        self.refresh()

    def edit_student(self):
        sid = selected_id(self.table)
        if sid is None:
            QMessageBox.information(self, "Select a student", "Select a row first."); return
        current = self.repo.get(sid)
        if current is None:
            QMessageBox.warning(self, "Not found", "That student record no longer exists."); self.refresh(); return
        data = self.student_form(current)
        if data is None:
            return
        try:
            self.repo.update(sid, data)
        except Exception as e:
            QMessageBox.warning(self, "Could not update student", str(e)); return
        self.refresh()

    def toggle_status(self):
        sid = selected_id(self.table)
        if sid is None:
            return
        current = self.repo.get(sid)
        if current is None:
            QMessageBox.warning(self, "Not found", "That student record no longer exists."); self.refresh(); return
        target = "inactive" if current["status"] == "active" else "active"
        try:
            self.repo.set_status(sid, target)
        except Exception as e:
            QMessageBox.warning(self, "Status update failed", str(e)); return
        self.refresh()

    def import_excel(self):
        path, _ = QFileDialog.getOpenFileName(self, "Choose Excel workbook", "", "Excel workbooks (*.xlsx *.xlsm)")
        if not path:
            return
        try:
            info = preview_workbook(path)
        except Exception as e:
            QMessageBox.warning(self, "Workbook error", str(e)); return
        dlg = ImportMappingDialog(info, self)
        if dlg.exec() != QDialog.Accepted:
            return
        sheet, mapping, update = dlg.result
        try:
            preview = self.importer.preview(path, sheet, mapping, update_existing=update)
        except Exception as e:
            QMessageBox.warning(self, "Preview failed", str(e)); return
        self.last_preview = preview
        counts = preview.counts
        sample_errors = "\n".join(f"Row {r.source_row}: {r.errors}" for r in preview.rows if r.action == "reject")[:1800]
        sample_warnings = "\n".join(f"Row {r.source_row}: {r.warnings}" for r in preview.rows if r.warnings)[:1200]
        self.import_status.setText(
            f"Last preview — add {counts['add']}, update {counts['update']}, skip {counts['skip']}, reject {counts['reject']}."
        )
        message = (f"Preview only — no records have changed.\n\nAdd: {counts['add']}\nUpdate: {counts['update']}\n"
                   f"Skip: {counts['skip']}\nReject: {counts['reject']}\n\n{sample_errors}"
                   f"\n\nWarnings:\n{sample_warnings}\n\nCommit valid add/update rows?")
        if QMessageBox.question(self, "Confirm import", message, QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
            return
        try:
            result = self.importer.commit(preview, True)
        except Exception as e:
            QMessageBox.critical(self, "Import rolled back", f"No partial changes were committed.\n{e}"); return
        QMessageBox.information(self, "Import complete", str(result))
        self.refresh()

    def export_rejected_rows(self):
        if self.last_preview is None:
            QMessageBox.information(self, "No preview", "Run an Excel import preview first."); return
        path, _ = QFileDialog.getSaveFileName(self, "Export rejected rows", "rejected_rows.xlsx", "Excel workbook (*.xlsx)")
        if not path:
            return
        try:
            export_rejected(self.last_preview, path)
        except Exception as e:
            QMessageBox.warning(self, "Export failed", str(e)); return
        QMessageBox.information(self, "Export complete", f"Rejected rows saved to {path}")


class ImportMappingDialog(QDialog):
    def __init__(self, info, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Map Excel columns")
        self.setMinimumWidth(520)
        self.result = None
        self.info = info
        box = QVBoxLayout(self)
        box.setContentsMargins(20, 18, 20, 18)
        box.addWidget(heading("Workbook mapping", 14))
        box.addWidget(muted("Choose the worksheet and source columns. Imported course names are added to the course catalogue automatically. Attendance and result percentages are imported as summaries. Pending fees are calculated from total and submitted fees."))
        self.sheet = QComboBox()
        self.sheet.addItems([x["name"] for x in info["sheets"]])
        self.sheet.currentTextChanged.connect(self.rebuild)
        box.addWidget(QLabel("Worksheet"))
        box.addWidget(self.sheet)
        self.table = QTableWidget(0, 2)
        self.table.setHorizontalHeaderLabels(["CampusOne field", "Workbook column"])
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.verticalHeader().setVisible(False)
        box.addWidget(self.table)
        self.update = QCheckBox("Update existing students when imported fields differ (recommended)")
        self.update.setChecked(True)
        box.addWidget(self.update)
        save = QPushButton("Preview import")
        save.setProperty("primary", True)
        save.clicked.connect(self.accept_mapping)
        box.addWidget(save)
        self.rebuild()

    def columns(self):
        name = self.sheet.currentText()
        return next(x["columns"] for x in self.info["sheets"] if x["name"] == name)

    def rebuild(self, *_):
        columns = self.columns()
        mapping = suggest_mapping(columns)
        targets = [
            "enrollment_no", "full_name", "father_name", "email", "phone", "course_name", "semester", "batch", "status",
            "attendance_percentage", "result_percentage", "total_fees", "submitted_fees", "pending_fees"
        ]
        self.table.setRowCount(len(targets))
        self.combos = {}
        for i, target in enumerate(targets):
            self.table.setItem(i, 0, QTableWidgetItem(target))
            combo = QComboBox()
            combo.addItem("(not mapped)", None)
            for col in columns:
                combo.addItem(col, col)
            suggested = mapping.get(target)
            if suggested:
                combo.setCurrentText(suggested)
            self.table.setCellWidget(i, 1, combo)
            self.combos[target] = combo

    def accept_mapping(self):
        mapping = {key: combo.currentData() for key, combo in self.combos.items() if combo.currentData()}
        self.result = (self.sheet.currentText(), mapping, self.update.isChecked())
        self.accept()


class StudentManagerDialog(QDialog):
    def __init__(self, db, parent=None):
        super().__init__(parent)
        self.setWindowTitle("CampusOne — Student Records")
        self.resize(1100, 680)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.addWidget(StudentRecordsPage(db))
