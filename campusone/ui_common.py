from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QComboBox, QDialog, QFormLayout, QFrame, QHBoxLayout, QHeaderView,
    QLabel, QLineEdit, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout
)


def query(db, sql, args=()):
    with db.connect() as c:
        return [dict(r) for r in c.execute(sql, args).fetchall()]


def heading(text, size=13):
    label = QLabel(text)
    label.setStyleSheet(f"font-size:{size}pt;font-weight:750;color:#172B4D;")
    return label


def muted(text):
    label = QLabel(text)
    label.setWordWrap(True)
    label.setStyleSheet("color:#64748B;font-size:9pt;")
    return label


def make_table(headers):
    table = QTableWidget(0, len(headers))
    table.setHorizontalHeaderLabels(headers)
    table.setSelectionBehavior(QTableWidget.SelectRows)
    table.setSelectionMode(QTableWidget.SingleSelection)
    table.setEditTriggers(QTableWidget.NoEditTriggers)
    table.setAlternatingRowColors(True)
    table.verticalHeader().setVisible(False)
    table.horizontalHeader().setStretchLastSection(True)
    table.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
    table.setShowGrid(False)
    return table


def fill_table(table, rows, keys, empty_message="No records yet."):
    table.clearSpans()
    table.setRowCount(0)
    if not rows:
        table.insertRow(0)
        table.setSpan(0, 0, 1, table.columnCount())
        item = QTableWidgetItem(empty_message)
        item.setForeground(QColor("#64748B"))
        table.setItem(0, 0, item)
        return
    for i, row in enumerate(rows):
        table.insertRow(i)
        for j, key in enumerate(keys):
            value = row.get(key, "") if isinstance(row, dict) else row[j]
            item = QTableWidgetItem("" if value is None else str(value))
            item.setToolTip(item.text())
            table.setItem(i, j, item)


def selected_id(table, column=0):
    row = table.currentRow()
    if row < 0 or table.item(row, column) is None:
        return None
    try:
        return int(table.item(row, column).text())
    except (TypeError, ValueError):
        return None


def card(title=None):
    frame = QFrame()
    frame.setObjectName("pageCard")
    frame.setStyleSheet("QFrame#pageCard{background:#FFFFFF;border:1px solid #DCE4EF;border-radius:13px;}")
    layout = QVBoxLayout(frame)
    layout.setContentsMargins(16, 14, 16, 14)
    layout.setSpacing(10)
    if title:
        layout.addWidget(heading(title, 12))
    return frame, layout


class FormDialog(QDialog):
    def __init__(self, title, spec, parent=None, save_label="Save"):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(460)
        self.widgets = {}
        box = QVBoxLayout(self)
        box.setContentsMargins(22, 20, 22, 20)
        box.setSpacing(12)
        box.addWidget(heading(title, 14))
        form = QFormLayout()
        form.setSpacing(10)
        for item in spec:
            key = item["key"]
            kind = item.get("kind", "line")
            if kind == "combo":
                widget = QComboBox()
                for option in item.get("options", []):
                    if isinstance(option, (tuple, list)):
                        widget.addItem(str(option[0]), option[1])
                    else:
                        widget.addItem(str(option), option)
                value = item.get("value")
                if value is not None:
                    index = widget.findData(value)
                    if index < 0:
                        index = widget.findText(str(value))
                    if index >= 0:
                        widget.setCurrentIndex(index)
            else:
                widget = QLineEdit(str(item.get("value") or ""))
                if item.get("placeholder"):
                    widget.setPlaceholderText(item["placeholder"])
                if item.get("password"):
                    widget.setEchoMode(QLineEdit.Password)
            self.widgets[key] = widget
            form.addRow(item["label"], widget)
        box.addLayout(form)
        self.status = QLabel()
        self.status.setWordWrap(True)
        self.status.setStyleSheet("color:#BE123C;")
        box.addWidget(self.status)
        save = QPushButton(save_label)
        save.setProperty("primary", True)
        save.clicked.connect(self.accept)
        box.addWidget(save)

    def values(self):
        data = {}
        for key, widget in self.widgets.items():
            if isinstance(widget, QComboBox):
                # Return the item's data even when it is None ("Not assigned"); falling back to the
                # display text sent the string "Not assigned" as a course id and always failed validation.
                data[key] = widget.currentData()
            else:
                data[key] = widget.text()
        return data


def toolbar_row(widgets):
    bar = QHBoxLayout()
    bar.setSpacing(8)
    for widget in widgets:
        if widget is None:
            bar.addStretch()
        else:
            bar.addWidget(widget)
    return bar
