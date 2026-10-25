import json, sys
from PySide6.QtCore import Qt, QSize, Signal
from PySide6.QtWidgets import (
    QApplication, QComboBox, QDialog, QFormLayout, QLabel, QLineEdit, QPushButton,
    QVBoxLayout, QMainWindow, QMessageBox, QWidget, QHBoxLayout, QGridLayout,
    QFrame, QStackedWidget, QTableWidget, QTableWidgetItem, QHeaderView, QMenu,
    QSizePolicy
)
from PySide6.QtGui import QColor, QAction, QPainter, QPainterPath, QPen, QIcon, QPixmap
from .config import APP_ICON_PATH, DB_PATH
from .db import Database, DatabaseError
from .auth import AuthService
from .theme import apply_theme
from .fees import FeeRepository
from .logging_setup import setup_logging, install_excepthook, get_logger
from .stage2_ui import StudentRecordsPage
from .stage3_ui import CoursesPage, AttendancePage, ResultsPage
from .fees_ui import FeesPage
from .stage4_ui import RegistrationCodesPage, StudentLoginDialog, StudentPortalWindow
from .stage5_ui import AnalyticsPage
from .stage6_ui import OperationsPage
from .login_ui import LoginCard, LoginIllustration
from .students import StudentRepository
from .analytics import AnalyticsRepository, ACADEMIC_THRESHOLD


def label(text, size=None, color=None, bold=False):
    w = QLabel(text)
    if size or color or bold:
        styles = []
        if size: styles.append(f"font-size:{size}pt")
        if color: styles.append(f"color:{color}")
        if bold: styles.append("font-weight:700")
        w.setStyleSheet(";".join(styles))
    w.setWordWrap(True)
    return w


class Setup(QDialog):
    def __init__(self, auth):
        super().__init__(); self.auth = auth
        self.setWindowTitle("CampusOne · Administrator setup"); self.setMinimumWidth(480)
        self.setModal(True)
        box = QVBoxLayout(self); box.setContentsMargins(30, 28, 30, 28); box.setSpacing(14)
        box.addWidget(label("CAMPUSONE", 10, "#2563EB", True))
        box.addWidget(label("Set up your workspace", 23, "#132746", True))
        box.addWidget(label("Create the first administrator account to secure your college data.", 10, "#64748B"))
        form = QFormLayout(); form.setSpacing(12)
        self.name = QLineEdit(); self.name.setPlaceholderText("Choose an administrator username")
        self.pw = QLineEdit(); self.pw.setPlaceholderText("At least 12 characters"); self.pw.setEchoMode(QLineEdit.Password)
        self.confirm = QLineEdit(); self.confirm.setPlaceholderText("Enter password again"); self.confirm.setEchoMode(QLineEdit.Password)
        form.addRow("Username", self.name); form.addRow("Password", self.pw); form.addRow("Confirm password", self.confirm)
        box.addLayout(form)
        self.status = label("", 10, "#BE123C"); box.addWidget(self.status)
        b = QPushButton("Create administrator account"); b.setProperty("primary", True); b.clicked.connect(self.create); box.addWidget(b)
        box.addWidget(label("Your recovery key will be shown once. Store it somewhere safe.", 9, "#64748B"))

    def create(self):
        if self.pw.text() != self.confirm.text():
            self.status.setText("Passwords do not match."); return
        try:
            key = self.auth.setup(self.name.text(), self.pw.text())
        except ValueError as e:
            self.status.setText(str(e)); return
        except Exception:
            self.status.setText("Setup could not be completed. Check the database and try again."); return
        QMessageBox.information(self, "Save recovery key",
                                "This key is shown once. Save it securely; it cannot be retrieved later.\n\n" + key)
        self.accept()


class Recovery(QDialog):
    def __init__(self, auth, parent=None):
        super().__init__(parent); self.auth = auth
        self.setWindowTitle("CampusOne · Recover administrator access")
        self.setMinimumWidth(460)
        box = QVBoxLayout(self); box.setContentsMargins(28, 26, 28, 26); box.setSpacing(12)
        box.addWidget(label("Recover administrator access", 18, "#132746", True))
        box.addWidget(label("Use your one-time recovery key. A new password is stored and a replacement recovery key is issued (shown once).", 10, "#64748B"))
        form = QFormLayout()
        self.name = QLineEdit(); self.key = QLineEdit(); self.pw = QLineEdit(); self.pw.setEchoMode(QLineEdit.Password)
        self.confirm = QLineEdit(); self.confirm.setEchoMode(QLineEdit.Password)
        form.addRow("Username", self.name)
        form.addRow("Recovery key", self.key)
        form.addRow("New password", self.pw)
        form.addRow("Confirm password", self.confirm)
        box.addLayout(form)
        self.status = label("", 10, "#BE123C"); box.addWidget(self.status)
        b = QPushButton("Reset password"); b.setProperty("primary", True); b.clicked.connect(self.reset); box.addWidget(b)

    def reset(self):
        if self.pw.text() != self.confirm.text():
            self.status.setText("Passwords do not match."); return
        try:
            new_key = self.auth.recover(self.name.text(), self.key.text(), self.pw.text())
        except ValueError as e:
            self.status.setText(str(e)); return
        except Exception:
            get_logger().exception("Administrator recovery failed")
            self.status.setText("Recovery could not be completed. See the application log."); return
        QMessageBox.information(self, "Password updated",
                                "Your password was changed. The recovery key you just used is now void.\n\n"
                                "Your NEW recovery key is shown once; store it somewhere safe:\n\n" + new_key)
        self.accept()


class Login(QDialog):
    def __init__(self, auth):
        super().__init__(); self.auth = auth; self.user = None
        self.setWindowTitle("CampusOne · Sign in")
        self.setMinimumSize(860, 540)
        self.resize(1024, 576)
        self.setStyleSheet("QDialog { background: #EAF2F9; }")
        scene = QHBoxLayout(self)
        scene.setContentsMargins(64, 48, 64, 48)
        scene.setSpacing(44)

        self.card = LoginCard()
        self.card.setFixedWidth(480)
        card_layout = QVBoxLayout(self.card)
        card_layout.setContentsMargins(44, 30, 44, 28)
        card_layout.setSpacing(9)

        brand = QHBoxLayout()
        brand.setSpacing(9)
        brand.addStretch()
        mark = QFrame()
        mark.setStyleSheet("background:white;border-radius:5px;")
        mark.setFixedSize(34, 34)
        mark_layout = QVBoxLayout(mark)
        mark_layout.setContentsMargins(3, 3, 3, 3)
        icon = QLabel()
        icon.setAlignment(Qt.AlignCenter)
        icon.setPixmap(QPixmap(str(APP_ICON_PATH)).scaled(
            28, 28, Qt.KeepAspectRatio, Qt.SmoothTransformation
        ))
        mark_layout.addWidget(icon)
        brand.addWidget(mark, 0, Qt.AlignVCenter)
        brand.addWidget(label("CAMPUSONE", 24, "#FFFFFF", True), 0, Qt.AlignVCenter)
        brand.addStretch()
        card_layout.addLayout(brand)

        heading = label("Welcome back!", 22, "#132746", True)
        heading.setAlignment(Qt.AlignCenter)
        card_layout.addStretch(1)
        card_layout.addWidget(heading)

        form = QFormLayout()
        form.setSpacing(6)
        form.setLabelAlignment(Qt.AlignLeft)
        form.setFormAlignment(Qt.AlignTop)
        self.name = QLineEdit()
        self.name.setPlaceholderText("Administrator username")
        self.pw = QLineEdit()
        self.pw.setPlaceholderText("Enter your password")
        self.pw.setEchoMode(QLineEdit.Password)
        self.pw.returnPressed.connect(self.signin)
        input_style = (
            "QLineEdit { background:#FFFFFF; color:#27364B; border:1px solid #D8E1ED; "
            "border-radius:5px; padding:7px 10px; min-height:20px; }"
            "QLineEdit:focus { border:1px solid #5578B2; }"
        )
        for field in (self.name, self.pw):
            field.setStyleSheet(input_style)
        username_label = label("Administrator username", 9, "#132746")
        password_label = label("Password", 9, "#132746")
        form.addRow(username_label, self.name)
        form.addRow(password_label, self.pw)
        card_layout.addLayout(form)

        self.status = label("", 9, "#FFF0F0", True)
        card_layout.addWidget(self.status)
        b = QPushButton("Login now")
        b.setMinimumHeight(39)
        b.setStyleSheet(
            "QPushButton, QPushButton:hover, QPushButton:pressed, QPushButton:checked, "
            "QPushButton:disabled { color:#FFFFFF; }"
            "QPushButton { background:#20296F; border:1px solid #20296F; "
            "border-radius:6px; padding:8px 14px; font-weight:700; }"
            "QPushButton:hover { background:#293781; }"
        )
        b.clicked.connect(self.signin)
        card_layout.addWidget(b)

        divider = label("OR", 9, "#536984")
        divider.setAlignment(Qt.AlignCenter)
        card_layout.addWidget(divider)
        recover = QPushButton("Forgot password? Use recovery key")
        recover.setMinimumHeight(36)
        recover.setStyleSheet(
            "QPushButton { background:transparent; color:#000000; border:1px solid #526A87; "
            "border-radius:6px; padding:7px 12px; font-weight:600; }"
            "QPushButton:hover { background:rgba(255,255,255,90); }"
        )
        recover.clicked.connect(self.recover)
        card_layout.addWidget(recover)
        student_login = QPushButton("LOGIN AS STUDENT")
        student_login.setMinimumHeight(36)
        student_login.setStyleSheet(
            "QPushButton { background:transparent; color:#000000; border:1px solid #526A87; "
            "border-radius:6px; padding:7px 12px; font-weight:700; }"
            "QPushButton:hover { background:rgba(255,255,255,90); }"
        )
        self.student_login = student_login
        card_layout.addWidget(student_login)
        card_layout.addStretch(1)
        scene.addStretch(1)
        scene.addWidget(self.card)
        scene.addStretch(1)
        self.illustration = LoginIllustration(self)
        self.illustration.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.illustration.lower()
        self.illustration.hide()
        self.name.setFocus()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if not hasattr(self, "illustration"):
            return
        available_width = self.width() - self.card.geometry().right() - 40
        size = min(300, available_width, self.height() - 80)
        if size < 130:
            self.illustration.hide()
            return
        x = self.card.geometry().right() + 16
        y = (self.height() - size) // 2
        self.illustration.setGeometry(x, y, size, size)
        self.illustration.show()
        self.illustration.lower()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.fillRect(self.rect(), QColor("#EAF2F9"))
        bounds = self.rect()
        wave = QPainterPath()
        wave.moveTo(0, bounds.height() * .61)
        wave.cubicTo(bounds.width() * .28, bounds.height() * .56,
                     bounds.width() * .34, bounds.height() * .15,
                     bounds.width(), bounds.height() * .24)
        wave.lineTo(bounds.width(), bounds.height() * .46)
        wave.cubicTo(bounds.width() * .67, bounds.height() * .42,
                     bounds.width() * .31, bounds.height() * .84,
                     0, bounds.height() * .86)
        wave.closeSubpath()
        painter.fillPath(wave, QColor(211, 226, 240, 150))

        wave = QPainterPath()
        wave.moveTo(0, bounds.height() * .82)
        wave.cubicTo(bounds.width() * .28, bounds.height() * .72,
                     bounds.width() * .61, bounds.height() * .48,
                     bounds.width(), bounds.height() * .42)
        wave.lineTo(bounds.width(), bounds.height())
        wave.lineTo(0, bounds.height())
        wave.closeSubpath()
        painter.fillPath(wave, QColor(255, 255, 255, 95))

    def signin(self):
        ok, msg, user = self.auth.login(self.name.text(), self.pw.text())
        if not ok:
            self.status.setText(msg); return
        self.user = user; self.accept()

    def recover(self):
        Recovery(self.auth, self).exec()


class StartupWindow(QMainWindow):
    def __init__(self, auth, db, setup_required):
        super().__init__()
        self.auth = auth
        self.db = db
        self.setWindowTitle("CampusOne")
        self.setMinimumSize(860, 600)
        self.setStyleSheet("QMainWindow { background:#EAF2F9; }")

        self.pages = QStackedWidget()
        self.setCentralWidget(self.pages)
        self.active_window = None
        self.admin_login = Login(auth)
        from .portal import StudentPortalService
        self.student_login = StudentLoginDialog(StudentPortalService(db))

        self.admin_login.accepted.connect(self.open_admin_dashboard)
        self.admin_login.rejected.connect(self.close)
        self.admin_login.student_login.clicked.connect(self.show_student_login)
        self.student_login.accepted.connect(self.open_student_portal)
        self.student_login.rejected.connect(self.show_admin_login)

        if setup_required:
            self.setup = Setup(auth)
            self.setup.accepted.connect(self.show_admin_login)
            self.setup.rejected.connect(self.close)
            setup_page = self.centered_page(self.setup, 640)
            self.pages.addWidget(setup_page)
            self.pages.setCurrentWidget(setup_page)
        else:
            self.setup = None
            self.show_admin_login()

    def centered_page(self, content, max_width):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(32, 24, 32, 24)
        layout.addStretch()
        content.setWindowFlags(Qt.Widget)
        content.setModal(False)
        content.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        content.setMaximumWidth(max_width)
        layout.addWidget(content, 0, Qt.AlignHCenter)
        layout.addStretch()
        return page

    def show_admin_login(self):
        self.show_page(self.admin_login)

    def show_student_login(self):
        self.show_page(self.student_login)

    def show_page(self, page):
        page.setWindowFlags(Qt.Widget)
        page.setModal(False)
        page.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        if self.pages.indexOf(page) == -1:
            self.pages.addWidget(page)
        self.pages.setCurrentWidget(page)
        page.show()

    def open_admin_dashboard(self):
        if self.admin_login.user.get("role") != "admin":
            QMessageBox.warning(self, "Access denied", "Use the Student Portal option for student accounts.")
            self.show_admin_login()
            return
        window = AdminWindow(self.admin_login.user, self.db)
        self.open_main_window(window, self.admin_login)

    def open_student_portal(self):
        window = StudentPortalWindow(self.db, self.student_login.session)
        self.open_main_window(window, self.student_login)

    def open_main_window(self, window, login_page):
        window.signed_out.connect(lambda: self.return_to_login(login_page))
        self.active_window = window
        window.showMaximized()
        self.hide()

    def return_to_login(self, login_page):
        if login_page is self.admin_login:
            self.admin_login.user = None
            self.admin_login.name.clear()
            self.admin_login.pw.clear()
            self.admin_login.status.clear()
        else:
            self.student_login.session = None
            self.student_login.enrollment.clear()
            self.student_login.password.clear()
            self.student_login.status.clear()

        self.show_page(login_page)
        self.showMaximized()
        if self.active_window is not None:
            self.active_window.close()
            self.active_window.deleteLater()
            self.active_window = None


class DashboardData:
    """Read-only dashboard queries; all displayed figures come from the active database."""
    def __init__(self, db):
        self.db = db

    def load(self, course_name=None, semester=None):
        filters = ["s.status='active'"]
        args = []
        if course_name is not None:
            filters.append("s.course_name=?")
            args.append(course_name)
        if semester is not None:
            filters.append("s.semester=?")
            args.append(semester)
        where = " AND ".join(filters)
        rows = AnalyticsRepository(self.db).student_performance_rows(course_name, semester)
        with self.db.connect() as c:
            total = c.execute(
                f"SELECT COUNT(*) FROM students s WHERE {where}", args
            ).fetchone()[0]
            registered = c.execute(
                f"""SELECT COUNT(*) FROM users u JOIN students s ON s.id=u.student_id
                    WHERE u.role='student' AND u.is_active=1 AND {where}""",
                args,
            ).fetchone()[0]
            imported_summary = dict(c.execute(
                f"""SELECT COUNT(x.attendance_percentage) attendance_students,
                           AVG(x.attendance_percentage) average_attendance,
                           COUNT(x.result_percentage) result_students,
                           AVG(x.result_percentage) average_result
                    FROM student_import_summaries x JOIN students s ON s.id=x.student_id
                    WHERE {where}""",
                args,
            ).fetchone())
            imported_fees = [dict(row) for row in c.execute(f"""SELECT s.course_name course,
                    SUM(x.total_fees_paise) total_paise,SUM(x.submitted_fees_paise) submitted_paise,
                    SUM(x.pending_fees_paise) pending_paise
                FROM student_import_summaries x JOIN students s ON s.id=x.student_id
                WHERE {where} AND (x.total_fees_paise IS NOT NULL
                    OR x.submitted_fees_paise IS NOT NULL OR x.pending_fees_paise IS NOT NULL)
                GROUP BY s.course_name ORDER BY s.course_name""", args).fetchall()]
            model = c.execute(
                "SELECT version,algorithm,dataset_kind,created_at FROM model_runs WHERE active=1 LIMIT 1"
            ).fetchone()
            threshold_row = c.execute(
                "SELECT setting_value FROM app_settings WHERE setting_key='low_attendance_threshold'"
            ).fetchone()
        threshold = float(threshold_row[0]) if threshold_row else 75.0
        for row in imported_fees:
            row["course"] = row["course"] or "Unassigned"
            for key in ("total", "submitted", "pending"):
                row[key] = (row.pop(key + "_paise") or 0) / 100
        imported_fee_totals = {
            key: sum(row[key] for row in imported_fees)
            for key in ("total", "submitted", "pending")
        }
        fee_repository = FeeRepository(self.db)
        recorded_fees = fee_repository.fee_totals(course_name, semester)
        fee_summary = fee_repository.fee_dashboard_summary(course_name, semester)
        fee_chart_by_course = {}
        for row in fee_summary["rows"]:
            course = row["course_name"] or "Unassigned"
            totals = fee_chart_by_course.setdefault(
                course, {"course": course, "collected": 0, "outstanding": 0}
            )
            totals["collected"] += row["paid"]
            totals["outstanding"] += row["pending"]
        fee_chart = sorted(fee_chart_by_course.values(), key=lambda row: row["course"])
        watch = [{
            "enrollment_no": row["enrollment_no"], "full_name": row["full_name"],
            "course": row["course"] or "—", "semester": row["semester"],
            "result_pct": row["result_pct"], "attendance_pct": row["attendance_pct"],
            "band": row["risk_status"], "factors": row["risk_reason"],
        } for row in rows]
        bands = {
            band: sum(row["risk_status"] == band for row in rows)
            for band in ("Low", "Medium", "High", "Insufficient data")
        }
        attendance_rows = [row for row in rows if row["attendance_pct"] is not None]
        low_label, mid_label, high_label = f"<{threshold:g}%", f"{threshold:g}–85%", ">85%"
        attendance_buckets = {low_label: 0, mid_label: 0, high_label: 0}
        for row in attendance_rows:
            pct = float(row["attendance_pct"])
            attendance_buckets[low_label if pct < threshold else mid_label if pct <= 85 else high_label] += 1
        risk_mode = (
            f"Combined indicators · High when exam result <{ACADEMIC_THRESHOLD:g}% "
            f"and attendance <{threshold:g}%; Medium when either is below threshold"
        )
        performance = {
            "average_result": [row["result_pct"] for row in rows if row["result_pct"] is not None],
            "average_attendance": [row["attendance_pct"] for row in rows if row["attendance_pct"] is not None],
            "average_overall": [row["overall_pct"] for row in rows if row["overall_pct"] is not None],
        }
        performance = {
            key: round(sum(values) / len(values), 2) if values else None
            for key, values in performance.items()
        }
        return {
            "students": int(total), "registered": int(registered),
            "collected": fee_summary["total_submitted"], "outstanding": fee_summary["total_pending"],
            "has_recorded_fees": bool(recorded_fees["collected"] or recorded_fees["outstanding"]),
            "imported_fees": imported_fee_totals, "imported_fee_chart": imported_fees,
            "high_risk": bands["High"], "bands": bands,
            "attendance_buckets": attendance_buckets, "fee_chart": fee_chart,
            "watchlist": watch, "student_rows": rows, "risk_mode": risk_mode,
            "model": dict(model) if model else None, "threshold": threshold,
            "attendance_count": len(attendance_rows), "performance": performance,
            "imported_summary": imported_summary,
        }


class MetricCard(QFrame):
    def __init__(self, title, value, accent="#2563EB", glyph="•", subtitle=None, alert=False, parent=None):
        super().__init__(parent)
        self.setObjectName("metricCard")
        self.setStyleSheet(f"""
            QFrame#metricCard {{ background:{'#FFF1F2' if alert else '#FFFFFF'}; border:1px solid {'#FDA4AF' if alert else '#DCE4EF'}; border-radius:13px; }}
        """)
        row = QHBoxLayout(self); row.setContentsMargins(14, 14, 14, 14); row.setSpacing(11)
        icon_label = QLabel(glyph)
        icon_label.setFixedSize(40, 40)
        icon_label.setAlignment(Qt.AlignCenter)
        icon_label.setStyleSheet(f"background:{accent}22;color:{accent};border-radius:9px;font-size:13pt;font-weight:700;")
        row.addWidget(icon_label, 0, Qt.AlignTop)
        col = QVBoxLayout(); col.setSpacing(5)
        title_label = QLabel(title); title_label.setStyleSheet("color:#5B6B82;font-size:9pt;font-weight:600;")
        self.value_label = QLabel(value); self.value_label.setStyleSheet(f"color:{'#B91C1C' if alert else '#12213B'};font-size:20pt;font-weight:750;")
        col.addWidget(title_label); col.addWidget(self.value_label)
        if subtitle:
            sub = QLabel(subtitle); sub.setStyleSheet("color:#7B8798;font-size:8pt;"); sub.setWordWrap(True); col.addWidget(sub)
        row.addLayout(col, 1)


class ChartCard(QFrame):
    def __init__(self, title, parent=None):
        super().__init__(parent); self.setObjectName("chartCard")
        self.setStyleSheet("QFrame#chartCard{background:#FFFFFF;border:1px solid #DCE4EF;border-radius:13px;}")
        self.layout_box = QVBoxLayout(self); self.layout_box.setContentsMargins(16, 14, 16, 12); self.layout_box.setSpacing(8)
        heading = QLabel(title); heading.setStyleSheet("font-size:11pt;font-weight:700;color:#172B4D;")
        self.layout_box.addWidget(heading)


class StudentSearchDialog(QDialog):
    def __init__(self, query, rows, total, parent=None, fee_repository=None):
        super().__init__(parent)
        self.setWindowTitle("Search students")
        self.resize(900, 520)
        self.action = None
        self.fee_repository = fee_repository
        layout = QVBoxLayout(self)
        layout.addWidget(label(f"Results for “{query}”", 14, "#132746", True))
        note = label(
            f"Showing {len(rows):,} of {total:,} matching student(s)."
            if total > len(rows) else f"{total:,} matching student(s).",
            9, "#64748B",
        )
        layout.addWidget(note)
        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(
            ["ID", "Enrollment", "Student name", "Father's name", "Phone", "Status"]
        )
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setSelectionMode(QTableWidget.SingleSelection)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setStretchLastSection(True)
        for row in rows:
            index = self.table.rowCount()
            self.table.insertRow(index)
            values = (
                row["id"], row["enrollment_no"], row["full_name"],
                row.get("father_name") or "", row.get("phone") or "", row["status"],
            )
            for column, value in enumerate(values):
                self.table.setItem(index, column, QTableWidgetItem(str(value)))
        layout.addWidget(self.table, 1)
        buttons = QHBoxLayout()
        buttons.addStretch()
        self.action_buttons = {}
        for action, title in (
            ("fees", "Check fee balance"),
            ("payment", "Submit new fee"),
            ("receipt", "Generate receipt"),
            ("student", "Open Student Record"),
        ):
            button = QPushButton(title)
            button.setProperty("primary", action == "payment")
            button.setEnabled(False)
            button.clicked.connect(lambda checked=False, selected_action=action: self.choose_action(selected_action))
            self.action_buttons[action] = button
            buttons.addWidget(button)
        self.table.itemSelectionChanged.connect(
            self.update_action_buttons
        )
        close_button = QPushButton("Close")
        close_button.clicked.connect(self.reject)
        buttons.addWidget(close_button)
        layout.addLayout(buttons)
        self.table.itemDoubleClicked.connect(lambda *_: self.choose_action("student"))

    def update_action_buttons(self):
        student = self.selected_student()
        enabled = student is not None
        for button in self.action_buttons.values():
            button.setEnabled(enabled)
        payment = self.action_buttons["payment"]
        payment.setToolTip("")
        if student is not None and self.fee_repository is not None:
            try:
                fully_paid = self.fee_repository.student_fees_fully_paid(student["id"])
            except Exception as exc:
                get_logger().exception("Could not determine student fee payment status")
                payment.setEnabled(False)
                payment.setToolTip("Fee status could not be checked. See the application log.")
                QMessageBox.warning(
                    self, "Could not check fee status",
                    f"Payment availability could not be checked ({type(exc).__name__}).",
                )
                return
            payment.setEnabled(not fully_paid)
            if fully_paid:
                payment.setToolTip("All fees with a recorded amount due have been paid.")

    def choose_action(self, action):
        if self.selected_student() is None:
            return
        self.action = action
        self.accept()

    def selected_student(self):
        row = self.table.currentRow()
        if row < 0 or self.table.item(row, 0) is None:
            return None
        return {
            "id": int(self.table.item(row, 0).text()),
            "enrollment_no": self.table.item(row, 1).text(),
        }


class AdminWindow(QMainWindow):
    signed_out = Signal()

    def __init__(self, user, db=None):
        super().__init__(); self.user = user; self.db = db; self.data = None; self.load_error = None
        if db is not None: db.actor_id = user.get("id")   # every audit row written from this window names this administrator
        self.setWindowTitle("CampusOne — Admin Dashboard"); self.resize(1580, 900); self.setMinimumSize(1100, 700)
        if db:
            try:
                self.data = DashboardData(db).load()
            except Exception as exc:
                get_logger().exception("Dashboard failed to load")
                self.data = None; self.load_error = f"Dashboard data could not be loaded ({type(exc).__name__}). Figures below are NOT current."
        outer = QWidget(); root = QHBoxLayout(outer); root.setContentsMargins(0, 0, 0, 0); root.setSpacing(0)
        self.sidebar = QFrame(); self.sidebar.setObjectName("sidebar"); self.sidebar.setFixedWidth(255)
        self.sidebar.setStyleSheet("QFrame#sidebar{background:#1D2B40;border:0;}")
        side = QVBoxLayout(self.sidebar); side.setContentsMargins(16, 20, 16, 16); side.setSpacing(8)
        brandrow = QHBoxLayout()
        brand = QLabel("CampusOne"); brand.setAlignment(Qt.AlignCenter)
        brand.setStyleSheet("color:white;font-size:19pt;font-weight:750;")
        brandrow.addStretch(); brandrow.addWidget(brand); brandrow.addStretch(); side.addLayout(brandrow); side.addSpacing(18)

        main = QWidget(); main.setStyleSheet("background:#F3F5F9;")
        ml = QVBoxLayout(main); ml.setContentsMargins(20, 0, 20, 16); ml.setSpacing(16)
        topbar = QFrame(); topbar.setFixedHeight(76); topbar.setStyleSheet("background:#FFFFFF;border-bottom:1px solid #DDE4ED;")
        tb = QHBoxLayout(topbar); tb.setContentsMargins(0, 0, 0, 0)
        self.page_title = QLabel("CampusOne — Admin Dashboard"); self.page_title.setStyleSheet("font-size:20pt;font-weight:750;color:#0F172A;")
        tb.addWidget(self.page_title); tb.addStretch()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search enrollment, name, father's name, or phone")
        self.search.setToolTip("Search students from any section. Press Enter to see matching records.")
        self.search.setFixedWidth(350)
        self.search.textChanged.connect(self.filter_watchlist); tb.addWidget(self.search)
        self.search.returnPressed.connect(self.search_students)
        refresh = QPushButton("Refresh"); refresh.setToolTip("Refresh dashboard data"); refresh.clicked.connect(self.refresh_dashboard); tb.addWidget(refresh)
        menu = QMenu(self)
        sign_out_action = QAction("Sign out", self)
        sign_out_action.triggered.connect(lambda: self.signed_out.emit())
        menu.addAction(sign_out_action)
        profile = QPushButton(user.get("username", "Admin"))
        profile.setMenu(menu)
        profile.setStyleSheet("background:#F1F5F9;border:0;border-radius:18px;padding:9px 14px;")
        tb.addWidget(profile)
        ml.addWidget(topbar)
        self.pages = QStackedWidget(); ml.addWidget(self.pages, 1)
        self.dashboard_page = QWidget(); self.build_dashboard(self.dashboard_page); self.pages.addWidget(self.dashboard_page)
        self.module_pages = {}
        if db:
            self.module_pages = {
                "Courses & Subjects": CoursesPage(db),
                "Students": StudentRecordsPage(db),
                "Attendance": AttendancePage(db),
                "Results": ResultsPage(db),
                "Fee Management": FeesPage(db),
                "Early-Warning Analytics": AnalyticsPage(db, user),
                "Registration Codes": RegistrationCodesPage(db, user),
                "Backup & Audit": OperationsPage(db, user),
            }
            for page in self.module_pages.values():
                self.pages.addWidget(page)
        nav_items = [
            ("Admin Overview", self.show_dashboard),
            ("Courses & Subjects", lambda: self.show_module("Courses & Subjects")),
            ("Students", lambda: self.show_module("Students")),
            ("Attendance", lambda: self.show_module("Attendance")),
            ("Results", lambda: self.show_module("Results")),
            ("Fee Management", lambda: self.show_module("Fee Management")),
            ("Import Student Data", lambda: self.show_module("Students", "Import Student Data")),
            ("Early-Warning Analytics", lambda: self.show_module("Early-Warning Analytics")),
            ("Registration Codes", lambda: self.show_module("Registration Codes")),
            ("Backup & Audit", lambda: self.show_module("Backup & Audit")),
        ]
        self.nav_buttons = []
        for title, fn in nav_items:
            b = QPushButton(title)
            b.setCursor(Qt.PointingHandCursor); b.setMinimumHeight(42); b.setProperty("nav", True)
            b.setStyleSheet(
                "QPushButton, QPushButton:hover, QPushButton:pressed, QPushButton:checked, "
                "QPushButton:disabled, QPushButton[active='true'] { color:#FFFFFF; }"
                "QPushButton{background:transparent;text-align:left;border:0;border-radius:8px;"
                "padding:8px 10px;font-size:10pt;}"
                "QPushButton:hover{background:#2A3A50;}"
                "QPushButton[active='true']{background:#34465F;font-weight:700;}"
            )
            b.clicked.connect(fn); side.addWidget(b); self.nav_buttons.append((b, title))
        side.addStretch()
        bottom = QFrame(); bottom.setStyleSheet("background:transparent;border-top:1px solid #3A485A;")
        bl = QHBoxLayout(bottom); bl.setContentsMargins(10, 12, 10, 0)
        bl.addWidget(QLabel("v1.0")); bl.addStretch(); bl.addWidget(QLabel("Admin"))
        for w in bottom.findChildren(QLabel):
            w.setStyleSheet("color:#94A3B8;")
        side.addWidget(bottom); root.addWidget(self.sidebar)
        root.addWidget(main, 1); self.setCentralWidget(outer)
        self.show_dashboard()

    def build_dashboard(self, page, course_name=None, semester=None):
        layout = QVBoxLayout(page); layout.setContentsMargins(0, 2, 0, 0); layout.setSpacing(15)
        if self.load_error:
            banner = QLabel(self.load_error); banner.setWordWrap(True)
            banner.setStyleSheet("background:#FEE2E2;color:#991B1B;border:1px solid #FCA5A5;border-radius:8px;padding:10px;font-weight:600;")
            layout.addWidget(banner)
        filters = QHBoxLayout()
        filters.addWidget(QLabel("Performance filters"))
        filters.addWidget(QLabel("Course"))
        self.course_filter = QComboBox()
        self.course_filter.addItem("All courses", None)
        self.semester_filter = QComboBox()
        self.semester_filter.addItem("All semesters", None)
        if self.db is not None:
            with self.db.connect() as c:
                courses = c.execute("SELECT name FROM courses ORDER BY name").fetchall()
                semesters = c.execute(
                    "SELECT DISTINCT semester FROM students WHERE semester IS NOT NULL ORDER BY semester"
                ).fetchall()
            for row in courses:
                self.course_filter.addItem(row["name"], row["name"])
            for row in semesters:
                self.semester_filter.addItem(str(row["semester"]), row["semester"])
        if course_name is not None:
            index = self.course_filter.findData(course_name)
            if index >= 0: self.course_filter.setCurrentIndex(index)
        if semester is not None:
            index = self.semester_filter.findData(semester)
            if index >= 0: self.semester_filter.setCurrentIndex(index)
        filters.addWidget(self.course_filter)
        filters.addWidget(QLabel("Semester"))
        filters.addWidget(self.semester_filter)
        filters.addStretch()
        layout.addLayout(filters)
        self.course_filter.currentIndexChanged.connect(self.refresh_dashboard)
        self.semester_filter.currentIndexChanged.connect(self.refresh_dashboard)
        cards = QGridLayout(); cards.setSpacing(13)
        d = self.data or {"students": 0, "registered": 0, "collected": 0, "outstanding": 0, "high_risk": 0,
                          "imported_fees": {"total": 0, "submitted": 0, "pending": 0},
                          "imported_fee_chart": [],
                          "bands": {"Low": 0, "Medium": 0, "High": 0},
                          "attendance_buckets": {"<75%": 0, "75–85%": 0, ">85%": 0},
                          "fee_chart": [], "watchlist": [], "risk_mode": "No dashboard data available",
                          "model": None, "attendance_count": 0}
        has_imported_fees = bool(d["imported_fees"]["submitted"] or d["imported_fees"]["pending"])
        has_recorded_fees = d.get("has_recorded_fees", bool(d["collected"] or d["outstanding"]))
        imported_fee_labels = has_imported_fees and not has_recorded_fees
        self.metric_cards = [
            MetricCard("Total Students", f"{d['students']:,}", "#2563EB", "S", "Active student records"),
            MetricCard("Registered Accounts", f"{d['registered']:,}", "#10B981", "A", "Active student portal accounts"),
            MetricCard("Fees Submitted (Imported)" if imported_fee_labels else "Fee Collection",
                       self.money(d["imported_fees"]["submitted"] if imported_fee_labels else d["collected"]),
                       "#059669", "₹", "Imported student summaries" if imported_fee_labels else "Payments recorded"),
            MetricCard("Fees Pending (Imported)" if imported_fee_labels else "Outstanding Fees",
                       self.money(d["imported_fees"]["pending"] if imported_fee_labels else d["outstanding"]),
                       "#EA580C", "₹",
                       "Imported student summaries" if imported_fee_labels else "Unpaid balances, active students"),
            MetricCard("High Risk Students", f"{d['high_risk']:,}", "#DC2626", "!", "Advisory indicator only", True),
        ]
        for i, card in enumerate(self.metric_cards):
            cards.addWidget(card, 0, i)
        layout.addLayout(cards)
        charts = QGridLayout(); charts.setSpacing(14)
        self.risk_chart = ChartCard("Students by Risk Band")
        self.att_chart = ChartCard("Attendance Distribution")
        self.performance_chart = ChartCard("Overall Student Performance")
        imported_fee_data = bool(self.data and self.data.get("imported_fee_chart"))
        recorded_fee_data = bool(self.data and self.data.get(
            "has_recorded_fees",
            any(r["collected"] or r["outstanding"] for r in self.data["fee_chart"]),
        ))
        fee_chart_title = ("Collected vs Outstanding Fees by Course" if recorded_fee_data
                           else "Imported Submitted vs Pending Fees by Course" if imported_fee_data
                           else "Collected vs Outstanding Fees by Course")
        self.fee_chart = ChartCard(fee_chart_title)
        charts.addWidget(self.performance_chart, 0, 0); charts.addWidget(self.risk_chart, 0, 1)
        charts.addWidget(self.att_chart, 0, 2); charts.addWidget(self.fee_chart, 0, 3)
        layout.addLayout(charts, 4)
        self.draw_performance_chart(d); self.draw_risk_chart(d); self.draw_attendance_chart(d); self.draw_fee_chart(d)
        watchcard = QFrame(); watchcard.setObjectName("watchCard")
        watchcard.setStyleSheet("QFrame#watchCard{background:#FFFFFF;border:1px solid #DCE4EF;border-radius:13px;}")
        wl = QVBoxLayout(watchcard); wl.setContentsMargins(0, 0, 0, 0); wl.setSpacing(0)
        heading = QHBoxLayout(); heading.setContentsMargins(16, 13, 16, 12)
        heading.addWidget(QLabel("Student Performance & Early-Warning List"))
        heading.itemAt(0).widget().setStyleSheet("font-size:12pt;font-weight:750;color:#172B4D;")
        heading.addStretch()
        self.risk_note = QLabel(d["risk_mode"]); self.risk_note.setStyleSheet("color:#64748B;font-size:8pt;"); heading.addWidget(self.risk_note)
        retrain = QPushButton("Retrain model"); retrain.clicked.connect(lambda: self.show_module("Early-Warning Analytics")); heading.addWidget(retrain)
        import_btn = QPushButton("Import Student Data"); import_btn.clicked.connect(lambda: self.show_module("Students")); heading.addWidget(import_btn)
        wl.addLayout(heading)
        self.watch_table = QTableWidget(0, 8)
        self.watch_table.setHorizontalHeaderLabels([
            "Enrollment No.", "Student Name", "Course", "Semester",
            "Exam Result %", "Attendance %", "Risk Status", "Alert / Data Status"
        ])
        self.watch_table.setAlternatingRowColors(True); self.watch_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.watch_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.watch_table.verticalHeader().setVisible(False); self.watch_table.horizontalHeader().setStretchLastSection(True)
        self.watch_table.setColumnWidth(0, 125); self.watch_table.setColumnWidth(1, 155)
        self.watch_table.setColumnWidth(2, 115); self.watch_table.setColumnWidth(3, 70)
        self.watch_table.setColumnWidth(4, 95); self.watch_table.setColumnWidth(5, 95)
        self.watch_table.setColumnWidth(6, 105)
        self.watch_table.setMinimumHeight(210); wl.addWidget(self.watch_table)
        layout.addWidget(watchcard, 5)
        self.populate_watchlist(d["watchlist"])

    @staticmethod
    def money(value):
        try:
            value = float(value)
            if value >= 10000000: return f"₹{value / 10000000:.2f} Cr"
            if value >= 100000: return f"₹{value / 100000:.2f} L"
            return "₹" + format(value, ",.0f")
        except Exception:
            return "₹0"

    def add_chart_canvas(self, card, figure):
        try:
            from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
            canvas = FigureCanvasQTAgg(figure); canvas.setMinimumHeight(190); card.layout_box.addWidget(canvas, 1)
        except Exception:
            fallback = QLabel("Chart renderer unavailable. Install the dependencies in requirements.txt.")
            fallback.setWordWrap(True); card.layout_box.addWidget(fallback)

    def draw_risk_chart(self, d):
        try:
            from matplotlib.figure import Figure
            fig = Figure(figsize=(3.6, 2.0), dpi=90); ax = fig.add_subplot(111)
            labels = ["Low", "Medium", "High", "Insufficient data"]
            values = [d["bands"].get(k, 0) for k in labels]
            if sum(values):
                ax.pie(values, labels=None, colors=["#36B878", "#F5A23B", "#ED5757", "#94A3B8"], startangle=90,
                       wedgeprops={"width": 0.42, "edgecolor": "white", "linewidth": 2})
                ax.legend(labels, loc="lower center", bbox_to_anchor=(0.5, -0.08), ncol=2, frameon=False, fontsize=8)
            else:
                ax.text(.5, .55, "No risk data yet", ha="center", va="center", fontsize=11, color="#64748B", transform=ax.transAxes)
                ax.text(.5, .40, "Record both exam results and attendance", ha="center", va="center", fontsize=7, color="#94A3B8", transform=ax.transAxes)
            ax.set_aspect("equal"); fig.tight_layout(); self.add_chart_canvas(self.risk_chart, fig)
        except Exception:
            self.risk_chart.layout_box.addWidget(QLabel("Risk chart unavailable."))

    def draw_performance_chart(self, d):
        try:
            from matplotlib.figure import Figure
            fig = Figure(figsize=(3.6, 2.0), dpi=90); ax = fig.add_subplot(111)
            values = d.get("performance", {})
            labels = ["Exam results", "Attendance", "Combined average"]
            scores = [values.get("average_result"), values.get("average_attendance"), values.get("average_overall")]
            valid = [(name, float(score)) for name, score in zip(labels, scores) if score is not None]
            if valid:
                names, numbers = zip(*valid)
                colors = ["#2563EB", "#10B981", "#7C3AED"]
                ax.bar(names, numbers, color=colors[:len(numbers)])
                ax.set_ylim(0, 100); ax.set_ylabel("Average percentage")
                ax.tick_params(axis="x", labelsize=7, rotation=15)
                ax.bar_label(ax.containers[0], fmt="%.1f%%", padding=2, fontsize=7)
                ax.grid(axis="y", color="#E5EAF1", linewidth=.8); ax.set_axisbelow(True)
            else:
                ax.text(.5, .55, "No performance records available", ha="center", va="center",
                        transform=ax.transAxes, color="#64748B")
                ax.set_xticks([]); ax.set_yticks([])
            ax.spines[["top", "right", "left"]].set_visible(False)
            fig.tight_layout(); self.add_chart_canvas(self.performance_chart, fig)
        except Exception:
            self.performance_chart.layout_box.addWidget(QLabel("Performance chart unavailable."))

    def draw_attendance_chart(self, d):
        try:
            from matplotlib.figure import Figure
            fig = Figure(figsize=(3.6, 2.0), dpi=90); ax = fig.add_subplot(111)
            labels = list(d["attendance_buckets"]); vals = [d["attendance_buckets"][k] for k in labels]
            ax.bar(labels, vals, color="#607A9B", width=.62)
            ax.set_ylim(bottom=0); ax.grid(axis="y", color="#E5EAF1", linewidth=.8); ax.set_axisbelow(True)
            ax.spines[["top", "right", "left"]].set_visible(False); ax.spines["bottom"].set_color("#D8E0EA")
            ax.tick_params(axis="y", labelsize=8, length=0); ax.tick_params(axis="x", labelsize=8, length=0, pad=7)
            ax.set_ylabel("Students", fontsize=8, color="#64748B")
            if not sum(vals):
                ax.text(.5, .65, "No attendance recorded", ha="center", transform=ax.transAxes, color="#64748B")
            fig.tight_layout(); self.add_chart_canvas(self.att_chart, fig)
        except Exception:
            self.att_chart.layout_box.addWidget(QLabel("Attendance chart unavailable."))

    def draw_fee_chart(self, d):
        try:
            from matplotlib.figure import Figure
            fig = Figure(figsize=(4.0, 2.0), dpi=90); ax = fig.add_subplot(111)
            rows = d["fee_chart"][:4]; labels = [r["course"][:12] for r in rows]
            collected = [r["collected"] for r in rows]; outstanding = [r["outstanding"] for r in rows]
            imported_mode = not d.get("has_recorded_fees", any(collected) or any(outstanding)) and bool(
                d.get("imported_fee_chart")
            )
            if imported_mode:
                rows = d["imported_fee_chart"][:4]
                labels = [r["course"][:12] for r in rows]
                collected = [r["submitted"] for r in rows]
                outstanding = [r["pending"] for r in rows]
            if rows:
                x = list(range(len(rows))); width = .34
                ax.bar([v - width / 2 for v in x], collected, width, label="Collected", color="#607A9B")
                ax.bar([v + width / 2 for v in x], outstanding, width, label="Outstanding", color="#ED6B59")
                ax.set_xticks(x, labels, fontsize=7)
                if imported_mode:
                    ax.legend(["Imported submitted", "Imported pending"], frameon=False, fontsize=7,
                              loc="upper center", ncol=2)
                else:
                    ax.legend(frameon=False, fontsize=7, loc="upper center", ncol=2)
                ax.tick_params(axis="y", labelsize=7, length=0); ax.grid(axis="y", color="#E5EAF1", linewidth=.8); ax.set_axisbelow(True)
                ax.spines[["top", "right", "left"]].set_visible(False); ax.spines["bottom"].set_color("#D8E0EA")
            else:
                ax.text(.5, .55, "No course fee data", ha="center", va="center", transform=ax.transAxes, color="#64748B")
                ax.set_xticks([]); ax.set_yticks([])
            fig.tight_layout(); self.add_chart_canvas(self.fee_chart, fig)
        except Exception:
            self.fee_chart.layout_box.addWidget(QLabel("Fee chart unavailable."))

    def populate_watchlist(self, rows):
        self.watch_table.setRowCount(0)
        if not rows:
            self.watch_table.insertRow(0)
            self.watch_table.setSpan(0, 0, 1, 8)
            empty = QTableWidgetItem("No students match the selected course and semester filters.")
            empty.setForeground(QColor("#64748B"))
            self.watch_table.setItem(0, 0, empty)
            return
        for r in rows:
            i = self.watch_table.rowCount(); self.watch_table.insertRow(i)
            vals = [
                r.get("enrollment_no", ""), r.get("full_name", ""), r.get("course", "—"),
                r.get("semester") if r.get("semester") is not None else "—",
                f'{r["result_pct"]:.1f}%' if r.get("result_pct") is not None else "—",
                f'{r["attendance_pct"]:.1f}%' if r.get("attendance_pct") is not None else "—",
                r.get("band", "—"), r.get("factors", ""),
            ]
            for j, val in enumerate(vals):
                item = QTableWidgetItem(str(val)); item.setToolTip(str(val))
                if j == 6:
                    band = str(val)
                    color = {
                        "High": ("#FEE2E2", "#B91C1C"),
                        "Medium": ("#FFEDD5", "#C2410C"),
                        "Low": ("#DCFCE7", "#15803D"),
                        "Insufficient data": ("#F1F5F9", "#475569"),
                    }.get(band, ("#F1F5F9", "#475569"))
                    item.setForeground(QColor(color[1])); item.setBackground(QColor(color[0]))
                self.watch_table.setItem(i, j, item)
        self.filter_watchlist(self.search.text() if hasattr(self, "search") else "")

    def filter_watchlist(self, text):
        text = str(text or "").casefold()
        if not hasattr(self, "watch_table"):
            return
        for i in range(self.watch_table.rowCount()):
            visible = any(text in (self.watch_table.item(i, j).text().casefold() if self.watch_table.item(i, j) else "")
                          for j in range(self.watch_table.columnCount()))
            self.watch_table.setRowHidden(i, bool(text) and not visible)

    def search_students(self):
        term = self.search.text().strip()
        if not term:
            QMessageBox.information(self, "Search students", "Enter an enrollment number, student or father's name, or phone number.")
            return
        try:
            repository = StudentRepository(self.db)
            total = repository.count(term)
            rows = repository.search(term, limit=500)
        except Exception as exc:
            get_logger().exception("Student search failed")
            QMessageBox.warning(self, "Student search failed", f"Could not search student records ({type(exc).__name__}).")
            return
        dialog = StudentSearchDialog(
            term, rows, total, self, fee_repository=FeeRepository(self.db),
        )
        if dialog.exec() != QDialog.Accepted:
            return
        selected = dialog.selected_student()
        if selected is None or dialog.action is None:
            return
        if dialog.action == "student":
            self.show_module("Students")
            page = self.module_pages.get("Students")
            if page is not None:
                page.status.setCurrentIndex(0)
                page.search.setText(selected["enrollment_no"])
                for row_index in range(page.table.rowCount()):
                    item = page.table.item(row_index, 0)
                    if item is not None and item.text() == str(selected["id"]):
                        page.table.setCurrentCell(row_index, 0)
                        break
        elif dialog.action in {"fees", "payment", "receipt"}:
            self.show_module("Fee Management")
            page = self.module_pages.get("Fee Management")
            if page is not None:
                if dialog.action == "fees":
                    page.show_student_fees(selected["id"])
                elif dialog.action == "receipt":
                    page.generate_student_receipt(selected["id"])
                else:
                    page.add_payment(selected["id"])

    def refresh_dashboard(self):
        try:
            course_name = self.course_filter.currentData() if hasattr(self, "course_filter") else None
            semester = self.semester_filter.currentData() if hasattr(self, "semester_filter") else None
            self.data = DashboardData(self.db).load(course_name, semester); self.load_error = None
        except Exception as e:
            get_logger().exception("Dashboard refresh failed")
            QMessageBox.warning(self, "Dashboard refresh failed", f"{type(e).__name__}: {e}"); return
        self.pages.removeWidget(self.dashboard_page); self.dashboard_page.deleteLater()
        self.dashboard_page = QWidget()
        self.build_dashboard(self.dashboard_page, course_name, semester)
        self.pages.insertWidget(0, self.dashboard_page)
        self.show_dashboard()
        for page in self.module_pages.values():
            if hasattr(page, "refresh"):
                try:
                    page.refresh()
                except Exception:
                    get_logger().exception("Page refresh failed")

    def set_active_nav(self, title):
        for b, name in self.nav_buttons:
            active = name == title; b.setProperty("active", active); b.style().unpolish(b); b.style().polish(b)
        self.page_title.setText("CampusOne — " + title)

    def show_dashboard(self):
        self.pages.setCurrentWidget(self.dashboard_page)
        self.set_active_nav("Admin Overview")
        self.search.setVisible(True)

    def show_module(self, title, nav_title=None):
        page = self.module_pages.get(title)
        if page is None:
            return
        if hasattr(page, "refresh"):
            try:
                page.refresh()
            except Exception:
                get_logger().exception("Page refresh failed")
                QMessageBox.warning(self, "Could not refresh", "This page could not be refreshed. Details were saved to the application log.")
        self.pages.setCurrentWidget(page)
        self.set_active_nav(nav_title or title)
        self.search.setVisible(True)


def main():
    app = QApplication(sys.argv); app.setApplicationName("CampusOne")
    app_icon = QIcon(str(APP_ICON_PATH))
    if app_icon.isNull():
        raise RuntimeError(f"Could not load application icon: {APP_ICON_PATH}")
    app.setWindowIcon(app_icon); apply_theme(app)
    setup_logging(DB_PATH.parent)
    install_excepthook(lambda msg: QMessageBox.critical(None, "Unexpected problem", msg))
    db = Database(DB_PATH)
    try:
        db.initialize()
    except DatabaseError as e:
        get_logger().error("Database initialisation failed: %s", e)
        QMessageBox.critical(None, "Database error", str(e)); return 1
    auth = AuthService(db)
    window = StartupWindow(auth, db, setup_required=not db.has_admin())
    window.showMaximized()
    return app.exec()
