import json
from pathlib import Path
import pandas as pd
from PySide6.QtWidgets import (
    QComboBox, QDialog, QFileDialog, QHBoxLayout, QInputDialog, QLabel,
    QMessageBox, QPushButton, QScrollArea, QTabWidget, QVBoxLayout, QWidget
)
from .academics import AcademicRepository
from .analytics import (
    AnalyticsRepository, APPROVED_FEATURES, band_for_score, ACADEMIC_THRESHOLD,
    performance_risk,
)
from .logging_setup import get_logger
from .early_warning import load_training_file, train_and_evaluate, predict_frame
from .ui_common import card, fill_table, heading, make_table, muted, toolbar_row


def add_chart(layout, kind, title, labels, values):
    if not labels or not values:
        layout.addWidget(muted(f"{title}: no validated data available yet."))
        return
    try:
        from matplotlib.figure import Figure
        from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
        fig = Figure(figsize=(6, 2.6), dpi=90)
        ax = fig.add_subplot(111)
        if kind == "bar":
            ax.bar(labels, values, color="#3B82F6")
            ax.set_ylabel("Percent")
            ax.tick_params(axis="x", rotation=30)
        elif kind == "line":
            ax.plot(labels, values, marker="o", color="#2563EB")
            ax.set_ylabel("Percent")
            ax.tick_params(axis="x", rotation=25)
        elif kind == "pie":
            ax.pie(values, labels=labels, colors=["#36B878", "#ED6B59"], autopct="%1.1f%%")
        ax.set_title(title)
        fig.tight_layout()
        layout.addWidget(FigureCanvasQTAgg(fig))
    except Exception:
        layout.addWidget(muted(f"{title}: chart renderer unavailable; tabular data is shown below."))


def _clear(layout):
    while layout.count():
        item = layout.takeAt(0)
        widget = item.widget()
        if widget:
            widget.deleteLater()


class AnalyticsPage(QWidget):
    def __init__(self, db, admin_session, parent=None):
        super().__init__(parent)
        self.db = db
        self.admin = admin_session
        self.repo = AnalyticsRepository(db)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        layout.addWidget(heading("Dashboards, reports and early warning"))
        layout.addWidget(muted("Figures come from the live database. Risk scores are administrator-only advisory estimates and must not trigger automatic penalties."))
        self.course = QComboBox(); self.course.addItem("All courses", None)
        for c in self.repo.filters():
            self.course.addItem(c["name"], c["name"])
        self.semester = QComboBox(); self.semester.addItem("All semesters", None)
        with self.db.connect() as connection:
            semesters = connection.execute(
                "SELECT DISTINCT semester FROM students WHERE semester IS NOT NULL ORDER BY semester"
            ).fetchall()
        for row in semesters:
            self.semester.addItem(str(row["semester"]), row["semester"])
        refresh = QPushButton("Refresh")
        refresh.clicked.connect(self.refresh)
        export = QPushButton("Export report")
        export.clicked.connect(self.export)
        train = QPushButton("Train / evaluate model")
        train.clicked.connect(self.train)
        predict = QPushButton("Generate risk predictions")
        predict.setProperty("primary", True)
        predict.clicked.connect(self.predict)
        layout.addLayout(toolbar_row([
            muted("Course"), self.course, muted("Semester"), self.semester, None, refresh, export, train, predict
        ]))
        self.tabs = QTabWidget()
        self.summary = QWidget(); self.summary_layout = QVBoxLayout(self.summary)
        self.academic = QWidget(); self.academic_layout = QVBoxLayout(self.academic)
        self.fees = QWidget(); self.fees_layout = QVBoxLayout(self.fees)
        self.risk = QWidget(); self.risk_layout = QVBoxLayout(self.risk)
        self.models = QWidget(); self.models_layout = QVBoxLayout(self.models)
        for widget, title in [
            (self.summary, "Overview"), (self.academic, "Academic"), (self.fees, "Fees"),
            (self.risk, "Early Warning"), (self.models, "Model Evaluation")
        ]:
            scroll = QScrollArea(); scroll.setWidgetResizable(True); scroll.setWidget(widget)
            self.tabs.addTab(scroll, title)
        layout.addWidget(self.tabs, 1)
        self.course.currentIndexChanged.connect(self.refresh)
        self.semester.currentIndexChanged.connect(self.refresh)
        self.refresh()

    def selected_filters(self):
        return self.course.currentData(), self.semester.currentData()

    def refresh(self):
        course, semester = self.selected_filters()
        summary = self.repo.admin_summary(course, semester)
        self._fill_overview(summary)
        academic = self.repo.academic_summary(course, semester)
        self._fill_academic(academic, summary["imported_summary"])
        self._fill_fees(summary)
        self._fill_risk(course, semester)
        self._fill_models()

    def _fill_overview(self, summary):
        _clear(self.summary_layout)
        frame, inner = card("Current snapshot")
        for key, label in [("students", "Students"), ("registered_accounts", "Registered student accounts"),
                           ("fee_collected", "Fees collected"), ("outstanding_fees", "Outstanding fees")]:
            inner.addWidget(QLabel(f"{label}: {summary[key]}"))
        inner.addWidget(muted("Outstanding fees are an estimate from fee structures and recorded payments."))
        self.summary_layout.addWidget(frame)
        imported = summary["imported_summary"]
        imported_frame, imported_layout = card("Imported data snapshot")
        average_attendance = imported["average_attendance"]
        average_result = imported["average_result"]
        imported_layout.addWidget(muted(
            f"Attendance: {imported['attendance_students']:,} students, average "
            f"{'—' if average_attendance is None else f'{average_attendance:.2f}%'}"
        ))
        imported_layout.addWidget(muted(
            f"Results: {imported['result_students']:,} students, average "
            f"{'—' if average_result is None else f'{average_result:.2f}%'}"
        ))
        imported_layout.addWidget(muted(
            f"Imported fees — total: {imported['total_fees']:,.2f}; "
            f"submitted: {imported['submitted_fees']:,.2f}; "
            f"pending: {imported['pending_fees']:,.2f}"
        ))
        imported_layout.addWidget(muted(
            "Imported percentages and fee amounts are summary snapshots; they are separate from subject-level records and dated fee transactions."
        ))
        self.summary_layout.addWidget(imported_frame)
        imports, import_layout = card("Recent imports")
        table = make_table(["Source", "Started", "Status", "Seen", "Imported", "Rejected"])
        fill_table(table, summary["recent_imports"],
                   ["source_name", "started_at", "status", "rows_seen", "rows_imported", "rows_rejected"],
                   "No import history yet.")
        import_layout.addWidget(table)
        self.summary_layout.addWidget(imports)
        self.summary_layout.addStretch()

    def _fill_academic(self, academic, imported):
        _clear(self.academic_layout)
        imported_frame, imported_layout = card("Imported academic summaries")
        average_attendance = imported["average_attendance"]
        average_result = imported["average_result"]
        imported_layout.addWidget(muted(
            f"Attendance: {imported['attendance_students']:,} student summaries; average "
            f"{'—' if average_attendance is None else f'{average_attendance:.2f}%'}"
        ))
        imported_layout.addWidget(muted(
            f"Results: {imported['result_students']:,} student summaries; average "
            f"{'—' if average_result is None else f'{average_result:.2f}%'}"
        ))
        imported_layout.addWidget(muted(
            "Imported percentages have no subject or exam breakdown. Detailed operational records are shown separately below."
        ))
        self.academic_layout.addWidget(imported_frame)
        self.academic_layout.addWidget(QLabel("Average marks by subject"))
        add_chart(self.academic_layout, "bar", "Average marks by subject",
                  [str(r["subject"]) for r in academic["marks_by_subject"]],
                  [float(r["average_pct"] or 0) for r in academic["marks_by_subject"]])
        table = make_table(["Subject", "Semester", "Average %", "Assessments"])
        fill_table(table, academic["marks_by_subject"], ["subject", "semester", "average_pct", "assessments"],
                   "No marks recorded yet.")
        self.academic_layout.addWidget(table)
        self.academic_layout.addWidget(QLabel("Attendance distribution by subject"))
        att = make_table(["Subject", "Semester", "Attendance %", "Students"])
        fill_table(att, academic["attendance_by_subject"], ["subject", "semester", "attendance_pct", "students"],
                   "No attendance recorded yet.")
        self.academic_layout.addWidget(att)
        self.academic_layout.addWidget(QLabel("Average result percentage by term"))
        add_chart(self.academic_layout, "line", "Average results by term",
                  [str(r["term"]) for r in academic["term_trend"]],
                  [float(r["average_pct"] or 0) for r in academic["term_trend"]])
        trend = make_table(["Term", "Average %", "Assessments"])
        fill_table(trend, academic["term_trend"], ["term", "average_pct", "assessments"], "No term trend yet.")
        self.academic_layout.addWidget(trend)
        self.academic_layout.addWidget(muted("Pass rates shown only for exam types labelled final/end-semester; 40% is a provisional calculation rule."))
        passes = make_table(["Term", "Pass %", "Assessments"])
        fill_table(passes, academic["pass_rates"], ["term", "pass_pct", "assessments"], "No pass-rate data yet.")
        self.academic_layout.addWidget(passes)

    def _fill_fees(self, summary):
        _clear(self.fees_layout)
        self.fees_layout.addWidget(QLabel(f"Collected fees (filtered): {summary['fee_collected']:.2f}"))
        self.fees_layout.addWidget(muted(
            f"Outstanding estimate: {summary['outstanding_fees']:.2f}. Verify fee structure and payment periods before using for reconciliation."
        ))
        if summary["fee_collected"] or summary["outstanding_fees"]:
            add_chart(self.fees_layout, "pie", "Collected vs outstanding fees",
                      ["Collected", "Outstanding"],
                      [max(0, summary["fee_collected"]), max(0, summary["outstanding_fees"])])
        else:
            self.fees_layout.addWidget(muted("Fee chart: no validated fee data available yet."))
        imported = summary["imported_summary"]
        self.fees_layout.addWidget(QLabel(
                f"Imported fee summary (filtered) — total: {imported['total_fees']:,.2f}; "
                f"submitted: {imported['submitted_fees']:,.2f}; "
                f"pending: {imported['pending_fees']:,.2f}"
            ))
        self.fees_layout.addWidget(muted(
            "Imported fee snapshots are shown separately; they are not dated payment transactions or fee-structure balances."
        ))
        if imported["submitted_fees"] or imported["pending_fees"]:
            add_chart(self.fees_layout, "pie", "Imported submitted vs pending fees",
                      ["Submitted", "Pending"],
                      [max(0, imported["submitted_fees"]), max(0, imported["pending_fees"])])

    def _fill_risk(self, course, semester):
        _clear(self.risk_layout)
        try:
            risks = self.repo.student_performance_rows(course, semester)
        except Exception:
            get_logger().exception("Student risk rows could not be loaded")
            self.risk_layout.addWidget(muted("Student risk rows could not be loaded. See the application log."))
            return
        threshold = AcademicRepository(self.db).get_low_attendance_threshold()
        self.risk_layout.addWidget(muted(
            f"Administrator-only advisory list. High risk means exam result below {ACADEMIC_THRESHOLD:g}% "
            f"and attendance below {threshold:g}%; Medium means either is below threshold. "
            "Students missing either measure are marked Insufficient data. Flags must not trigger automatic penalties."
        ))
        self.risk_band = QComboBox()
        self.risk_band.addItems(["All statuses", "High", "Medium", "Low", "Insufficient data"])
        self.risk_band.currentIndexChanged.connect(self.refresh_risk)
        self.risk_layout.addWidget(self.risk_band)
        self.risk_table = make_table([
            "Enrollment", "Student", "Course", "Semester", "Exam Result %",
            "Attendance %", "Risk Status", "Alert / Data Status"
        ])
        fill_table(self.risk_table, risks,
                   ["enrollment_no", "full_name", "course", "semester", "result_pct",
                    "attendance_pct", "risk_status", "risk_reason"],
                   "No students match the selected course and semester filters.")
        self.risk_layout.addWidget(self.risk_table)

    def _fill_models(self):
        _clear(self.models_layout)
        self.models_layout.addWidget(muted("Model evaluation history. Synthetic demonstration metrics are not real-world accuracy."))
        runs = self.repo.model_runs()
        data = []
        for r in runs:
            metrics = json.loads(r["metrics_json"])
            data.append({"version": r["version"], "algorithm": r["algorithm"], "training_data_size": r["training_data_size"],
                         "dataset_kind": r["dataset_kind"], "active": r["active"], "metrics": json.dumps(metrics)[:700]})
        table = make_table(["Version", "Algorithm", "Training size", "Dataset", "Active", "Metrics"])
        fill_table(table, data, ["version", "algorithm", "training_data_size", "dataset_kind", "active", "metrics"],
                   "No model evaluation runs yet.")
        self.models_layout.addWidget(table)

    def refresh_risk(self):
        course, semester = self.selected_filters()
        band = self.risk_band.currentText()
        rows = self.repo.student_performance_rows(course, semester)
        if band != "All statuses":
            rows = [row for row in rows if row["risk_status"] == band]
        fill_table(self.risk_table, rows,
                   ["enrollment_no", "full_name", "course", "semester", "result_pct",
                    "attendance_pct", "risk_status", "risk_reason"],
                   "No students match the selected filters.")

    def export(self):
        course, semester = self.selected_filters()
        choices = ["Student summary", "Academic by subject", "Attendance by subject", "Risk predictions", "Fee collection summary"]
        choice, ok = QInputDialog.getItem(self, "Export report", "Report", choices, 0, False)
        if not ok:
            return
        if choice == "Student summary":
            with self.db.connect() as c:
                rows = [dict(r) for r in c.execute("SELECT enrollment_no,full_name,course_name,semester,status FROM students ORDER BY full_name")]
        elif choice == "Academic by subject":
            rows = self.repo.academic_summary(course, semester)["marks_by_subject"]
        elif choice == "Attendance by subject":
            rows = self.repo.academic_summary(course, semester)["attendance_by_subject"]
        elif choice == "Risk predictions":
            rows = self.repo.risk_rows(course, semester)
        else:
            with self.db.connect() as c:
                rows = [dict(r) for r in c.execute(
                    "SELECT payment_date,term,payment_mode,COUNT(*) AS payments,SUM(amount_paise)/100.0 AS collected FROM fee_payments GROUP BY payment_date,term,payment_mode ORDER BY payment_date")]
        path, _ = QFileDialog.getSaveFileName(self, "Save report", "CampusOne_Report.xlsx", "Excel (*.xlsx);;CSV (*.csv)")
        if not path:
            return
        if not Path(path).suffix:
            path += ".xlsx"
        try:
            self.repo.export_report(rows, path, choice)
        except Exception as e:
            QMessageBox.warning(self, "Export failed", str(e)); return
        QMessageBox.information(self, "Report exported", f"Report saved to:\n{path}")

    def train(self):
        path, _ = QFileDialog.getOpenFileName(self, "Select anonymized historical training data", "", "CSV (*.csv);;Excel (*.xlsx)")
        if not path:
            return
        kind, ok = QInputDialog.getItem(self, "Dataset type", "Dataset source", ["historical", "synthetic_demo"], 0, False)
        if not ok:
            return
        comparison, ok = QInputDialog.getItem(self, "Comparison model", "Choose comparison algorithm", ["random_forest", "gradient_boosting"], 0, False)
        if not ok:
            return
        try:
            frame = load_training_file(path)
            output = self.db.path.parent / "models"      # follows CAMPUSONE_DB_PATH instead of the source tree
            result = train_and_evaluate(frame, output, dataset_kind=kind, comparison=comparison,
                                        attendance_threshold=AcademicRepository(self.db).get_low_attendance_threshold())
            report = result["report"]; active = result["active_source"]
            metrics = report["models"].get("logistic_regression", {})
            version = report["version"]
            self.repo.save_model_run(version, active, len(frame), report, "historical" if kind == "historical" else "synthetic_demo",
                                     result["active_model_path"], activate=True)
            QMessageBox.information(self, "Evaluation complete",
                f"Primary logistic regression recall: {metrics.get('recall')}\n"
                f"Baseline recall: {report['models'].get('attendance_baseline', {}).get('recall', 'N/A')}\n"
                f"Production rule: {active}\nModel beats baseline: {report['model_outperforms_baseline']}\n\n"
                f"Detailed report: {output / (version + '_evaluation.json')}\n{report['note']}")
            self.refresh()
        except Exception as e:
            get_logger().exception("Model training failed")
            QMessageBox.warning(self, "Training failed", str(e))

    def predict(self):
        path, _ = QFileDialog.getOpenFileName(self, "Select mid-semester prediction data", "", "CSV (*.csv);;Excel (*.xlsx)")
        if not path:
            return
        try:
            frame = load_training_file(path)
            if "student_id" not in frame.columns:
                raise ValueError("Prediction data must include student_id to map rows to official student records.")
            ids = frame["student_id"].astype(int).tolist()
            features = frame.drop(columns=["student_id"])
            extra = set(features.columns) - set(APPROVED_FEATURES)
            if extra:
                raise ValueError("Prediction file contains unapproved columns: " + ", ".join(sorted(extra)))
            if not set(features.columns):
                raise ValueError("Prediction file contains no approved feature columns.")
            version = "attendance-baseline"; source = "attendance_baseline"
            threshold = AcademicRepository(self.db).get_low_attendance_threshold()
            active = self.repo.active_model()
            if active and active["algorithm"] == "logistic_regression":
                # An active trained model must be usable. Falling back silently to the baseline would
                # mislabel the run, so a missing or tampered file is reported instead.
                if not active["model_path"] or not Path(active["model_path"]).exists():
                    raise ValueError("The active model file is missing. Retrain the model before generating predictions.")
                preds, version = predict_frame(active["model_path"], features, active["model_sha256"]); source = "model"
            else:
                academic_features = [
                    name for name in ("internal_marks_pct", "previous_result_pct")
                    if name in features.columns
                ]
                if "attendance_pct" not in features.columns or not academic_features:
                    raise ValueError(
                        "No active eligible model. Baseline prediction requires attendance_pct "
                        "and at least one of internal_marks_pct or previous_result_pct."
                    )
                att = pd.to_numeric(features["attendance_pct"], errors="coerce")
                academic = features[academic_features].apply(pd.to_numeric, errors="coerce").mean(axis=1)
                if att.isna().any() or academic.isna().any():
                    raise ValueError(
                        "Attendance and academic result percentages must be numeric for every student."
                    )
                preds = []
                for attendance_pct, result_pct in zip(att, academic):
                    status, reason = performance_risk(attendance_pct, result_pct, threshold)
                    score = {"Low": 0.0, "Medium": 0.5, "High": 1.0, "Insufficient data": 1.0}[status]
                    preds.append({
                        "score": score,
                        "band": band_for_score(score),
                        "factors": [{"factor": reason, "direction": "increases estimated risk"}
                                    if status in {"High", "Medium"} else
                                    {"factor": reason, "direction": "does not increase estimated risk"}],
                    })
            with self.db.connect() as c:
                valid = {r[0] for r in c.execute("SELECT id FROM students")}
            if any(i not in valid for i in ids):
                raise ValueError("Prediction file contains an unknown student_id.")
            records = [{"student_id": sid, "score": pred["score"], "band": pred["band"], "factors": pred["factors"]}
                       for sid, pred in zip(ids, preds)]
            self.repo.save_predictions(records, version, source)
            QMessageBox.information(self, "Predictions saved",
                                    f"Generated {len(records)} advisory predictions using {source}. Only administrators can view risk details.")
            self.refresh()
        except Exception as e:
            get_logger().exception("Prediction failed")
            QMessageBox.warning(self, "Prediction failed", str(e))


class AnalyticsDialog(QDialog):
    def __init__(self, db, admin_session, parent=None):
        super().__init__(parent)
        self.setWindowTitle("CampusOne — Dashboards, Reports & Early Warning")
        self.resize(1100, 720)
        layout = QVBoxLayout(self)
        layout.addWidget(AnalyticsPage(db, admin_session))
