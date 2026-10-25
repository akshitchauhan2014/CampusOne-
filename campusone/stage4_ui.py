from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QDialog, QFormLayout, QFrame, QHBoxLayout, QLabel, QLineEdit, QMainWindow,
    QMessageBox, QPushButton, QStackedWidget, QVBoxLayout, QWidget, QInputDialog
)
from PySide6.QtGui import QPixmap
from .config import APP_ICON_PATH
from .login_ui import LoginCard, LoginIllustration
from .portal import StudentPortalService, DEFAULT_CODE_TTL_DAYS
from .ui_common import card, fill_table, heading, make_table, muted, query, selected_id, toolbar_row


def _label(text, size=None, color=None, bold=False):
    widget = QLabel(text)
    styles = []
    if size: styles.append(f"font-size:{size}pt")
    if color: styles.append(f"color:{color}")
    if bold: styles.append("font-weight:700")
    if styles: widget.setStyleSheet(";".join(styles))
    widget.setWordWrap(True)
    return widget


class RegistrationDialog(QDialog):
    def __init__(self, service, parent=None):
        super().__init__(parent)
        self.service = service
        self.verified = None
        self.enrollment = None
        self.code = None
        self.setWindowTitle("Student portal registration")
        self.setMinimumWidth(460)
        box = QVBoxLayout(self)
        box.setContentsMargins(28, 26, 28, 26)
        box.setSpacing(12)
        box.addWidget(_label("CAMPUSONE", 10, "#2563EB", True))
        box.addWidget(_label("Create your student account", 20, "#132746", True))
        box.addWidget(_label("Use your official enrollment number and the single-use code issued by the college.", 10, "#64748B"))
        form = QFormLayout()
        self.enrollment_edit = QLineEdit()
        self.code_edit = QLineEdit()
        form.addRow("Enrollment number", self.enrollment_edit)
        form.addRow("Registration code", self.code_edit)
        box.addLayout(form)
        verify = QPushButton("Verify details")
        verify.setProperty("primary", True)
        verify.clicked.connect(self.verify)
        box.addWidget(verify)
        self.confirm_box = QWidget()
        inner = QVBoxLayout(self.confirm_box)
        inner.setContentsMargins(0, 8, 0, 0)
        self.official = _label("", 10, "#132746")
        inner.addWidget(self.official)
        pwform = QFormLayout()
        self.password = QLineEdit(); self.password.setEchoMode(QLineEdit.Password)
        self.confirm = QLineEdit(); self.confirm.setEchoMode(QLineEdit.Password)
        pwform.addRow("Create password", self.password)
        pwform.addRow("Confirm password", self.confirm)
        inner.addLayout(pwform)
        finish = QPushButton("Create student account")
        finish.setProperty("primary", True)
        finish.clicked.connect(self.finish)
        inner.addWidget(finish)
        self.confirm_box.setVisible(False)
        box.addWidget(self.confirm_box)
        self.status = _label("", 10, "#BE123C")
        box.addWidget(self.status)

    def verify(self):
        ok, msg, details = self.service.verify_registration(self.enrollment_edit.text(), self.code_edit.text())
        if not ok:
            self.status.setText(msg); return
        self.verified = details
        self.enrollment = self.enrollment_edit.text().strip()
        self.code = self.code_edit.text().strip()
        self.official.setText(f"Confirm your official details:\nName: {details['full_name']}\nCourse: {details.get('course_name') or 'Not assigned'}")
        self.confirm_box.setVisible(True)
        self.status.setText("Details verified. Create your password to finish registration.")
        self.status.setStyleSheet("color:#15803D;")

    def finish(self):
        if not self.verified:
            return
        try:
            self.service.complete_registration(self.verified["student_id"], self.enrollment, self.code,
                                               self.password.text(), self.confirm.text())
        except Exception as e:
            self.status.setStyleSheet("color:#BE123C;")
            self.status.setText(str(e)); return
        QMessageBox.information(self, "Registration complete",
                                "Your student account has been created. Sign in with your enrollment number and password.")
        self.accept()


class StudentLoginDialog(QDialog):
    def __init__(self, service, parent=None):
        super().__init__(parent)
        self.service = service
        self.session = None
        self.setWindowTitle("CampusOne · Student sign in")
        self.setMinimumSize(860, 540)
        self.resize(1024, 576)
        self.setStyleSheet("QDialog { background:#EAF2F9; }")
        scene = QHBoxLayout(self)
        scene.setContentsMargins(64, 48, 64, 48)
        scene.setSpacing(44)

        self.card = LoginCard()
        self.card.setFixedWidth(480)
        box = QVBoxLayout(self.card)
        box.setContentsMargins(44, 30, 44, 28)
        box.setSpacing(9)

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
        brand.addWidget(_label("CAMPUSONE", 24, "#FFFFFF", True), 0, Qt.AlignVCenter)
        brand.addStretch()
        box.addLayout(brand)

        heading = _label("Student sign in", 22, "#132746", True)
        heading.setAlignment(Qt.AlignCenter)
        box.addStretch(1)
        box.addWidget(heading)
        description = _label("Sign in with your enrollment number and password.", 10, "#64748B")
        description.setAlignment(Qt.AlignCenter)
        box.addWidget(description)

        form = QFormLayout()
        form.setSpacing(6)
        form.setLabelAlignment(Qt.AlignLeft)
        form.setFormAlignment(Qt.AlignTop)
        self.enrollment = QLineEdit(); self.enrollment.setPlaceholderText("Enrollment number")
        self.password = QLineEdit(); self.password.setPlaceholderText("Password"); self.password.setEchoMode(QLineEdit.Password)
        self.password.returnPressed.connect(self.signin)
        input_style = (
            "QLineEdit { background:#FFFFFF; color:#27364B; border:1px solid #D8E1ED; "
            "border-radius:5px; padding:7px 10px; min-height:20px; }"
            "QLineEdit:focus { border:1px solid #5578B2; }"
        )
        for field in (self.enrollment, self.password):
            field.setStyleSheet(input_style)
        form.addRow(_label("Enrollment number", 9, "#132746"), self.enrollment)
        form.addRow(_label("Password", 9, "#132746"), self.password)
        box.addLayout(form)

        self.status = _label("", 9, "#FFF0F0", True)
        box.addWidget(self.status)
        login = QPushButton("Sign in")
        login.setMinimumHeight(39)
        login.setStyleSheet(
            "QPushButton { background:#20296F; color:#000000; border:1px solid #20296F; "
            "border-radius:6px; padding:8px 14px; font-weight:700; }"
            "QPushButton:hover { background:#293781; }"
        )
        login.clicked.connect(self.signin)
        box.addWidget(login)
        divider = _label("OR", 9, "#536984")
        divider.setAlignment(Qt.AlignCenter)
        box.addWidget(divider)
        admin_login = QPushButton("LOGIN AS ADMIN")
        admin_login.setMinimumHeight(36)
        admin_login.setStyleSheet(
            "QPushButton { background:transparent; color:#000000; border:1px solid #526A87; "
            "border-radius:6px; padding:7px 12px; font-weight:700; }"
            "QPushButton:hover { background:rgba(255,255,255,90); }"
        )
        admin_login.clicked.connect(self.reject)
        box.addWidget(admin_login)
        bar = QHBoxLayout()
        register = QPushButton("Register with a college code")
        register.clicked.connect(self.register)
        reset = QPushButton("Reset password")
        reset.clicked.connect(self.reset)
        bar.addWidget(register); bar.addWidget(reset)
        box.addLayout(bar)
        box.addStretch(1)
        scene.addStretch(1)
        scene.addWidget(self.card)
        scene.addStretch(1)
        self.illustration = LoginIllustration(self)
        self.illustration.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.illustration.lower()
        self.illustration.hide()
        self.enrollment.setFocus()

    def resizeEvent(self, event):
        super().resizeEvent(event)
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

    def signin(self):
        ok, msg, session = self.service.student_login(self.enrollment.text(), self.password.text())
        if not ok:
            self.status.setText(msg); return
        self.session = session
        self.accept()

    def register(self):
        RegistrationDialog(self.service, self).exec()

    def reset(self):
        PasswordResetDialog(self.service, self).exec()


class PasswordResetDialog(QDialog):
    def __init__(self, service, parent=None):
        super().__init__(parent)
        self.service = service
        self.setWindowTitle("Student password reset")
        self.setMinimumWidth(460)
        box = QVBoxLayout(self)
        box.setContentsMargins(26, 24, 26, 24)
        box.addWidget(_label("Reset your password", 18, "#132746", True))
        box.addWidget(_label("Use the single-use reset code provided by an administrator after in-person identity verification.", 10, "#64748B"))
        form = QFormLayout()
        self.enrollment = QLineEdit()
        self.code = QLineEdit()
        self.password = QLineEdit(); self.password.setEchoMode(QLineEdit.Password)
        self.confirm = QLineEdit(); self.confirm.setEchoMode(QLineEdit.Password)
        form.addRow("Enrollment number", self.enrollment)
        form.addRow("Reset code", self.code)
        form.addRow("New password", self.password)
        form.addRow("Confirm password", self.confirm)
        box.addLayout(form)
        b = QPushButton("Reset password")
        b.setProperty("primary", True)
        b.clicked.connect(self.reset)
        box.addWidget(b)
        self.status = _label("", 10, "#BE123C")
        box.addWidget(self.status)

    def reset(self):
        try:
            self.service.reset_password(self.enrollment.text(), self.code.text(), self.password.text(), self.confirm.text())
        except Exception as e:
            self.status.setText(str(e)); return
        QMessageBox.information(self, "Password reset", "Password updated. You can now sign in.")
        self.accept()


class StudentPortalWindow(QMainWindow):
    signed_out = Signal()

    def __init__(self, db, session, parent=None):
        super().__init__(parent)
        self.db = db
        self.session = session
        self.service = StudentPortalService(db)
        profile = self.service.own_profile(session)
        self.setWindowTitle("CampusOne — Student Portal")
        self.resize(1100, 720)
        outer = QWidget()
        root = QHBoxLayout(outer)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        sidebar = QFrame()
        sidebar.setFixedWidth(240)
        sidebar.setStyleSheet("QFrame{background:#1D2B40;}")
        side = QVBoxLayout(sidebar)
        side.setContentsMargins(16, 20, 16, 16)
        side.addWidget(_label("CampusOne", 16, "white", True))
        side.addWidget(_label("Student portal", 9, "#94A3B8"))
        side.addSpacing(16)
        self.pages = QStackedWidget()
        nav = [
            ("My profile", self._profile_page(profile)),
            ("Attendance", self._table_page("Attendance",
                self.service.own_attendance(session),
                ["subject_code", "subject_name", "classes_held", "classes_attended", "percentage"],
                ["Code", "Subject", "Held", "Attended", "Percent"],
                "Your attendance will appear after the college records it.")),
            ("Results", self._table_page("Results",
                self.service.own_results(session),
                ["subject_code", "subject_name", "exam_type", "term", "marks", "percentage"],
                ["Code", "Subject", "Exam", "Term", "Marks", "Percent"],
                "Your results will appear after they are recorded.")),
            ("Semester trends", self._trend_page()),
        ]
        self.nav_buttons = []
        for i, (title, page) in enumerate(nav):
            self.pages.addWidget(page)
            button = QPushButton(title)
            button.setCursor(Qt.PointingHandCursor)
            button.setMinimumHeight(42)
            button.setStyleSheet("QPushButton{background:transparent;color:#000000;text-align:left;border:0;border-radius:8px;padding:8px 10px;} QPushButton:hover{background:#2A3A50;color:#000000;}")
            button.clicked.connect(lambda _, index=i, name=title: self.show_page(index, name))
            side.addWidget(button)
            self.nav_buttons.append(button)
        side.addStretch()
        signout = QPushButton("Sign out")
        signout.clicked.connect(lambda: self.signed_out.emit())
        side.addWidget(signout)
        root.addWidget(sidebar)
        main = QWidget()
        main.setStyleSheet("background:#F3F5F9;")
        ml = QVBoxLayout(main)
        ml.setContentsMargins(24, 20, 24, 20)
        self.page_title = _label("My profile", 20, "#0F172A", True)
        ml.addWidget(self.page_title)
        ml.addWidget(_label(f"{profile['full_name']}  ·  {profile['enrollment_no']}", 10, "#64748B"))
        ml.addWidget(self.pages, 1)
        root.addWidget(main, 1)
        self.setCentralWidget(outer)
        self.show_page(0, "My profile")

    def show_page(self, index, title):
        self.pages.setCurrentIndex(index)
        self.page_title.setText(title)
        for i, button in enumerate(self.nav_buttons):
            button.setStyleSheet(
                "QPushButton{background:%s;color:#000000;text-align:left;border:0;border-radius:8px;padding:8px 10px;font-weight:%s;}"
                % ("#34465F" if i == index else "transparent", "700" if i == index else "600")
            )

    def _profile_page(self, profile):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 12, 0, 0)
        frame, inner = card("Official record")
        for label, key in [("Full name", "full_name"), ("Enrollment", "enrollment_no"), ("Course", "course_name"),
                           ("Semester", "semester"), ("Batch", "batch"), ("Email", "email"), ("Status", "status")]:
            inner.addWidget(_label(f"{label}: {profile.get(key) or 'Not assigned'}", 11, "#172B4D"))
        inner.addWidget(muted("You can view only your own college records. Contact an administrator for corrections."))
        layout.addWidget(frame)
        layout.addStretch()
        return page

    def _table_page(self, title, rows, keys, headers, empty):
        formatted = []
        for row in rows:
            item = dict(row)
            if title == "Attendance":
                item["percentage"] = "Not yet available" if row["percentage"] is None else f"{row['percentage']:.2f}%"
            elif title == "Results":
                item["marks"] = f"{row['marks_obtained']:g}/{row['maximum_marks']:g}"
                item["percentage"] = f"{row['percentage']:.2f}%"
            formatted.append(item)
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 12, 0, 0)
        frame, inner = card(title)
        table = make_table(headers)
        fill_table(table, formatted, keys, empty)
        inner.addWidget(table)
        if title == "Attendance":
            threshold = 75.0
            try:
                from .academics import AcademicRepository
                threshold = AcademicRepository(self.db).get_low_attendance_threshold()
            except Exception:
                pass
            for row in rows:
                if row["percentage"] is not None and row["percentage"] < threshold:
                    inner.addWidget(muted(
                        f"Attendance notice: your attendance in {row['subject_name']} is below the college threshold ({threshold:g}%)."
                    ))
        layout.addWidget(frame, 1)
        return page

    def _trend_page(self):
        trend_values = {}
        for item in self.service.own_results(self.session):
            trend_values.setdefault(item["term"], []).append(item["percentage"])
        rows = [{"term": term, "average_pct": round(sum(vals) / len(vals), 2)} for term, vals in trend_values.items()]
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 12, 0, 0)
        frame, inner = card("Your average marks by term")
        table = make_table(["Term", "Average %"])
        fill_table(table, rows, ["term", "average_pct"], "Results will appear here after they are recorded.")
        inner.addWidget(table)
        layout.addWidget(frame, 1)
        return page


class RegistrationCodesPage(QWidget):
    def __init__(self, db, admin_session, parent=None):
        super().__init__(parent)
        self.db = db
        self.admin = admin_session
        self.service = StudentPortalService(db)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        layout.addWidget(heading("Registration codes"))
        layout.addWidget(muted("Issue a single-use registration or password-reset code after verifying the student. Codes are shown once and should be distributed securely."))
        frame, inner = card()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search by student name or enrollment number")
        self.search.textChanged.connect(self.refresh)
        inner.addWidget(self.search)
        issue = QPushButton("Activate code")
        issue.setProperty("primary", True)
        issue.clicked.connect(self.issue)
        revoke = QPushButton("Revoke code")
        revoke.clicked.connect(self.revoke)
        reset = QPushButton("Issue password reset code")
        reset.clicked.connect(self.issue_reset)
        refresh = QPushButton("Refresh")
        refresh.clicked.connect(self.refresh)
        inner.addLayout(toolbar_row([issue, revoke, reset, refresh, None]))
        self.table = make_table(["ID", "Enrollment", "Student", "Portal account"])
        inner.addWidget(self.table)
        layout.addWidget(frame, 1)
        self.refresh()

    def refresh(self):
        search = self.search.text().strip()
        data = query(self.db, """SELECT s.id,s.enrollment_no,s.full_name,
            CASE WHEN u.id IS NULL THEN 'No' ELSE 'Yes' END AS has_account
            FROM students s LEFT JOIN users u ON u.student_id=s.id
            WHERE instr(lower(s.full_name), lower(?)) > 0
                OR instr(lower(s.enrollment_no), lower(?)) > 0
            ORDER BY s.full_name""", (search, search))
        fill_table(self.table, data, ["id", "enrollment_no", "full_name", "has_account"],
                   "Create student records before issuing portal codes.")

    def issue(self):
        sid = selected_id(self.table)
        if sid is None:
            QMessageBox.information(self, "Select student", "Select a student first."); return
        ttl, ok = QInputDialog.getInt(self, "Code expiry", "Expiry in days", DEFAULT_CODE_TTL_DAYS, 1, 365)
        if not ok:
            return
        try:
            code = self.service.issue_registration_code(sid, self.admin["id"], ttl)
        except Exception as e:
            QMessageBox.warning(self, "Could not issue code", str(e)); return
        QMessageBox.information(self, "One-time registration code",
                                "A new active registration code was created. Distribute it securely; it will not be shown again:\n\n" + code)
        self.refresh()

    def revoke(self):
        sid = selected_id(self.table)
        if sid is None:
            return
        try:
            n = self.service.revoke_registration_code(sid, self.admin["id"])
        except Exception as e:
            QMessageBox.warning(self, "Could not revoke", str(e)); return
        QMessageBox.information(self, "Registration code", f"Revoked {n} active code(s).")

    def issue_reset(self):
        sid = selected_id(self.table)
        if sid is None:
            QMessageBox.information(self, "Select student", "Select a student first."); return
        if QMessageBox.question(self, "Identity verification",
                                "Confirm that this student's identity has been verified in person by an authorized administrator?",
                                QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
            return
        try:
            code = self.service.issue_reset_code(sid, self.admin["id"])
        except Exception as e:
            QMessageBox.warning(self, "Could not issue reset code", str(e)); return
        QMessageBox.information(self, "One-time reset code",
                                "Give this code to the verified student securely. It expires in 30 minutes and will not be shown again:\n\n" + code)


class RegistrationCodeDialog(QDialog):
    def __init__(self, db, admin_session, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Registration codes")
        self.resize(900, 560)
        layout = QVBoxLayout(self)
        layout.addWidget(RegistrationCodesPage(db, admin_session))
