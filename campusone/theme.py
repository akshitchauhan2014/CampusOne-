from PySide6.QtWidgets import QApplication

APP_STYLE = r"""
* { font-family: "Segoe UI", "Inter", sans-serif; font-size: 10pt; }
QWidget { color: #172B4D; }
QMainWindow, QDialog { background: #F4F7FB; }
QLabel { background: transparent; }
QLineEdit, QComboBox, QDateEdit, QSpinBox, QDoubleSpinBox, QTextEdit, QPlainTextEdit {
    background: #FFFFFF; border: 1px solid #D8E1ED; border-radius: 8px;
    padding: 8px 10px; min-height: 18px; selection-background-color: #2563EB;
}
QLineEdit:focus, QComboBox:focus, QTextEdit:focus, QPlainTextEdit:focus {
    border: 1px solid #3977E8;
}
QComboBox::drop-down { border: 0; width: 26px; }
QPushButton {
    background: #E8EEF7; color: #000000; border: 1px solid #DCE5F0;
    border-radius: 9px; padding: 9px 14px; font-weight: 600; min-height: 18px;
}
QPushButton:hover { background: #DCE8FA; border-color: #B8CEF4; }
QPushButton:pressed { background: #CADAF4; }
QPushButton:disabled { background: #EEF1F5; color: #000000; }
QPushButton[primary="true"] { background: #2563EB; color: #000000; border: 1px solid #2563EB; }
QPushButton[primary="true"]:hover { background: #1D4ED8; }
QPushButton[danger="true"] { background: #FFF1F2; color: #000000; border-color: #FECDD3; }
QTableWidget, QTableView {
    background: white; alternate-background-color: #F8FAFD; gridline-color: #E8EDF4;
    border: 1px solid #E1E8F0; border-radius: 8px; selection-background-color: #DCEAFE;
    selection-color: #172B4D;
}
QHeaderView::section {
    background: #EDF2F8; color: #465A78; padding: 9px 8px; border: none;
    border-bottom: 1px solid #DDE5EF; font-weight: 700;
}
QTabWidget::pane { background: white; border: 1px solid #E1E8F0; border-radius: 10px; top: -1px; }
QTabBar::tab { background: #EAF0F7; color: #52627A; padding: 10px 16px; margin-right: 4px; border-top-left-radius: 8px; border-top-right-radius: 8px; }
QTabBar::tab:selected { background: #FFFFFF; color: #1D4ED8; font-weight: 700; }
QGroupBox { border: 1px solid #E1E8F0; border-radius: 10px; margin-top: 14px; padding: 14px; font-weight: 700; }
QGroupBox::title { subcontrol-origin: margin; left: 12px; padding: 0 5px; }
QScrollArea { border: none; background: transparent; }
QToolTip { background: #172B4D; color: white; border: 0; padding: 5px; }
QMessageBox { background: #F4F7FB; }
QMenu { background: #FFFFFF; border: 1px solid #D8E1ED; padding: 6px; }
QMenu::item { padding: 8px 18px; border-radius: 6px; }
QMenu::item:selected { background: #E8F0FE; color: #1D4ED8; }
QCheckBox { spacing: 8px; }
"""

def apply_theme(app: QApplication):
    app.setStyle("Fusion")
    app.setStyleSheet(APP_STYLE)
