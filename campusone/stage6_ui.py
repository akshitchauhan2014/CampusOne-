from pathlib import Path
from PySide6.QtWidgets import (
    QComboBox, QDialog, QFileDialog, QLineEdit, QMessageBox, QPushButton,
    QTabWidget, QVBoxLayout, QWidget
)
from .operations import BackupManager, AuditRepository
from .ui_common import card, fill_table, heading, make_table, muted, toolbar_row


class OperationsPage(QWidget):
    def __init__(self, db, admin, parent=None):
        super().__init__(parent)
        self.db = db
        self.admin = admin
        self.backups = BackupManager(db)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        layout.addWidget(heading("Backup, restore and audit history"))
        layout.addWidget(muted("Create a verified SQLite backup before restore. Restore replaces the live database after a safety copy is saved."))
        create = QPushButton("Create backup")
        create.setProperty("primary", True)
        create.clicked.connect(self.create_backup)
        restore = QPushButton("Restore backup")
        restore.setProperty("danger", True)
        restore.clicked.connect(self.restore_backup)
        refresh = QPushButton("Refresh history")
        refresh.clicked.connect(self.refresh)
        layout.addLayout(toolbar_row([create, restore, refresh, None]))
        self.tabs = QTabWidget()
        audit_tab = QWidget()
        audit_layout = QVBoxLayout(audit_tab)
        self.action_filter = QLineEdit()
        self.action_filter.setPlaceholderText("Filter by action text (optional)")
        self.entity_filter = QComboBox()
        self.entity_filter.addItems(["All entities", "student", "attendance", "result", "fee_payment", "user", "model_run", "database", "course", "subject"])
        apply_filters = QPushButton("Apply filters")
        apply_filters.clicked.connect(self.refresh_audit)
        audit_layout.addLayout(toolbar_row([self.action_filter, self.entity_filter, apply_filters, None]))
        self.audit_table = make_table(["ID", "When", "Actor", "Action", "Entity", "Entity ID", "Before", "After", "Details"])
        audit_layout.addWidget(self.audit_table)
        self.tabs.addTab(audit_tab, "Audit history")
        backup_tab = QWidget()
        backup_layout = QVBoxLayout(backup_tab)
        self.backup_table = make_table(["ID", "Operation", "Source", "Destination", "Outcome", "Details", "When"])
        backup_layout.addWidget(self.backup_table)
        self.tabs.addTab(backup_tab, "Backup / restore history")
        layout.addWidget(self.tabs, 1)
        self.refresh()

    def create_backup(self):
        path, _ = QFileDialog.getSaveFileName(self, "Create database backup", "CampusOne_Backup.sqlite", "SQLite database (*.sqlite *.db)")
        if not path:
            return
        if not Path(path).suffix:
            path += ".sqlite"
        try:
            result = self.backups.create_backup(path)
        except Exception as e:
            QMessageBox.critical(self, "Backup failed", str(e)); self.refresh(); return
        QMessageBox.information(self, "Backup verified", f"Backup created and verified:\n{result['path']}")
        self.refresh()

    def restore_backup(self):
        path, _ = QFileDialog.getOpenFileName(self, "Select valid CampusOne backup", "", "SQLite database (*.sqlite *.db);;All files (*)")
        if not path:
            return
        try:
            info = self.backups.validate_backup(path)
        except Exception as e:
            QMessageBox.warning(self, "Invalid backup", str(e)); return
        answer = QMessageBox.warning(self, "Confirm database restore",
            f"Restore schema version {info['schema_version']} from:\n{path}\n\nCurrent data will be replaced. Create a separate backup of the current database first. Continue?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if answer != QMessageBox.Yes:
            return
        safe_path, _ = QFileDialog.getSaveFileName(self, "Save safety backup of current database", "CampusOne_PreRestore.sqlite", "SQLite database (*.sqlite *.db)")
        if not safe_path:
            return
        try:
            self.backups.create_backup(safe_path)
            result = self.backups.restore_backup(path)
        except Exception as e:
            QMessageBox.critical(self, "Restore failed", str(e)); self.refresh(); return
        QMessageBox.information(self, "Restore verified",
                                f"Restored and verified database.\nSchema: {result['schema_version']}\nStudents available: {result['students']}")
        self.refresh()

    def refresh_audit(self):
        rows = AuditRepository(self.db).search(
            500, self.action_filter.text().strip() or None,
            None if self.entity_filter.currentText() == "All entities" else self.entity_filter.currentText())
        fill_table(self.audit_table, rows,
                   ["id", "occurred_at", "actor", "action", "entity_type", "entity_id", "old_values", "new_values", "details"],
                   "No audit events match these filters.")

    def refresh(self):
        self.refresh_audit()
        fill_table(self.backup_table, self.backups.backup_history(),
                   ["id", "operation", "source_path", "destination_path", "outcome", "details", "occurred_at"],
                   "No backup or restore history yet.")


class OperationsDialog(QDialog):
    def __init__(self, db, admin, parent=None):
        super().__init__(parent)
        self.setWindowTitle("CampusOne — Backup, Restore & Audit History")
        self.resize(1050, 650)
        layout = QVBoxLayout(self)
        layout.addWidget(OperationsPage(db, admin))
