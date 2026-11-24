"""Appearance and user preferences for the desktop frontend."""

import sys
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QUrl
from PySide6.QtGui import QColor, QDesktopServices, QPalette
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
)

LANGUAGES = {"en": "English", "ru": "Русский", "es": "Español"}
NAME_MODES = ("filename", "relative", "absolute")


@dataclass
class Preferences:
    language: str = "en"
    report_language: str = ""
    theme: str = "dark"
    name_mode: str = "filename"
    workers: int = 4
    history: bool = False

    @property
    def preferred_report_language(self):
        return self.report_language or self.language

    @classmethod
    def read(cls, settings):
        language = settings.value("language", "en", type=str)
        report_language = settings.value("report_language", "", type=str)
        theme = settings.value("theme", "dark", type=str)
        name_mode = settings.value("name_mode", "filename", type=str)
        return cls(
            language=language if language in LANGUAGES else "en",
            report_language=report_language if report_language in LANGUAGES else "",
            theme=theme if theme in ("dark", "light") else "dark",
            name_mode=name_mode if name_mode in NAME_MODES else "filename",
            workers=min(64, max(1, settings.value("workers", 4, type=int))),
            history=settings.value("history", False, type=bool),
        )

    def write(self, settings):
        for key, value in vars(self).items():
            settings.setValue(key, value)


def choose_preferences(parent, tr, current):
    dialog = QDialog(parent)
    dialog.setWindowTitle(tr("settings"))
    dialog.setMinimumWidth(445)
    body = QVBoxLayout(dialog)
    form = QFormLayout()
    body.addLayout(form)

    options = {
        "language": LANGUAGES.items(),
        "report_language": (("", tr("follow_interface")), *LANGUAGES.items()),
        "theme": ((option, tr(option)) for option in ("dark", "light")),
        "name_mode": ((option, tr(f"name_{option}")) for option in NAME_MODES),
    }
    combos = {}
    for key, choices in options.items():
        combo = QComboBox()
        for value, text in choices:
            combo.addItem(text, value)
        combo.setCurrentIndex(combo.findData(getattr(current, key)))
        form.addRow(tr(key), combo)
        combos[key] = combo

    workers = QSpinBox()
    workers.setRange(1, 64)
    workers.setValue(current.workers)
    form.addRow(tr("workers"), workers)
    history = QCheckBox(tr("history"))
    history.setChecked(current.history)
    body.addWidget(history)
    hint = QLabel(tr("history_help"))
    hint.setWordWrap(True)
    hint.setProperty("muted", True)
    body.addWidget(hint)

    buttons = QHBoxLayout()
    bundle = getattr(sys, "_MEIPASS", None)
    if bundle:
        notices = Path(bundle) / "notices"
        if (notices / "THIRD_PARTY_NOTICES.md").is_file():
            licenses = QPushButton(tr("licenses"))
            licenses.clicked.connect(
                lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(notices)))
            )
            buttons.addWidget(licenses)
    buttons.addStretch()
    cancel = QPushButton(tr("cancel"))
    cancel.clicked.connect(dialog.reject)
    save = QPushButton(tr("save"))
    save.setProperty("primary", True)
    save.clicked.connect(dialog.accept)
    save.setDefault(True)
    buttons.addWidget(cancel)
    buttons.addWidget(save)
    body.addLayout(buttons)
    if not dialog.exec():
        return None
    return Preferences(
        **{key: combo.currentData() for key, combo in combos.items()},
        workers=workers.value(),
        history=history.isChecked(),
    )


def apply_appearance(window, theme):
    dark = theme == "dark"
    colors = {
        "background": "#141921" if dark else "#edf1f6",
        "panel": "#1b222e" if dark else "#ffffff",
        "surface": "#161c26" if dark else "#f9fbff",
        "button": "#263244" if dark else "#eef3fa",
        "button_hover": "#31435b" if dark else "#e1ebf7",
        "border": "#344154" if dark else "#ccd6e3",
        "text": "#edf2fb" if dark else "#172536",
        "muted": "#a9b7cc" if dark else "#506276",
        "blue": "#397fde" if dark else "#226ac7",
        "blue_hover": "#4b8ce4" if dark else "#1c5bb0",
        "disabled": "#828e9f" if dark else "#687989",
    }
    palette = QPalette()
    for role, color in (
        ("Window", "background"),
        ("WindowText", "text"),
        ("Base", "surface"),
        ("AlternateBase", "panel"),
        ("Text", "text"),
        ("Button", "button"),
        ("ButtonText", "text"),
        ("Highlight", "blue"),
        ("HighlightedText", "text"),
        ("ToolTipBase", "panel"),
        ("ToolTipText", "text"),
    ):
        palette.setColor(getattr(QPalette.ColorRole, role), QColor(colors[color]))
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor("#ffffff"))
    for role in ("WindowText", "Text", "ButtonText"):
        palette.setColor(
            QPalette.ColorGroup.Disabled,
            getattr(QPalette.ColorRole, role),
            QColor(colors["disabled"]),
        )
    window.setPalette(palette)
    window.setStyleSheet(f"""
        QWidget {{ color: {colors["text"]}; }}
        QMainWindow, QDialog {{ background: {colors["background"]}; }}
        QFrame#toolbar, QFrame#panel, QFrame#status, QFrame#results {{
            background: {colors["panel"]}; border: 1px solid {colors["border"]}; border-radius: 9px;
        }}
        QLabel#wordmark {{ font-size: 22px; font-weight: 700; }}
        QLabel[heading="true"] {{ font-size: 16px; font-weight: 650; }}
        QLabel[muted="true"] {{ color: {colors["muted"]}; }}
        QLineEdit, QPlainTextEdit, QListView, QTableView, QSpinBox, QComboBox {{
            background: {colors["surface"]}; border: 1px solid {colors["border"]}; border-radius: 5px;
        }}
        QLineEdit, QSpinBox, QComboBox {{ padding: 5px 8px; }}
        QPlainTextEdit {{ padding: 10px; }}
        QListView::item {{ padding: 4px 6px; }}
        QTableView {{ alternate-background-color: {colors["panel"]}; }}
        QScrollBar {{ background: {colors["surface"]}; border: none; }}
        QScrollBar:vertical {{ width: 10px; }}
        QScrollBar:horizontal {{ height: 10px; }}
        QScrollBar::handle {{ background: {colors["border"]}; border-radius: 4px; }}
        QScrollBar::handle:hover {{ background: {colors["muted"]}; }}
        QScrollBar::handle:vertical {{ min-height: 25px; margin: 1px; }}
        QScrollBar::handle:horizontal {{ min-width: 25px; margin: 1px; }}
        QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
        QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
        QAbstractScrollArea::corner {{ background: {colors["surface"]}; }}
        QPushButton {{
            padding: 7px 11px; border: 1px solid {colors["border"]}; border-radius: 5px;
            background: {colors["button"]}; font-weight: 550;
        }}
        QPushButton:hover {{ background: {colors["button_hover"]}; }}
        QPushButton[primary="true"] {{
            background: {colors["blue"]}; border-color: {colors["blue"]}; color: white; font-weight: 650;
        }}
        QPushButton[primary="true"]:hover {{ background: {colors["blue_hover"]}; }}
        QPushButton:disabled {{
            background: {colors["panel"]}; border-color: {colors["border"]}; color: {colors["disabled"]};
        }}
        QToolButton {{ color: {colors["text"]}; padding: 4px; border: none; font-weight: 600; }}
        QToolButton:hover {{ background: {colors["button_hover"]}; }}
        QHeaderView {{ background: {colors["button"]}; }}
        QHeaderView::section {{
            background: {colors["button"]}; color: {colors["text"]};
            padding: 6px; border: none; border-right: 1px solid {colors["border"]};
        }}
        QTabWidget::pane {{ border: none; }}
        QTabBar::tab {{ padding: 8px 12px; color: {colors["muted"]}; border-bottom: 2px solid transparent; }}
        QTabBar::tab:selected {{ color: {colors["text"]}; border-bottom-color: {colors["blue"]}; }}
        QProgressBar {{ background: {colors["surface"]}; border: 0; border-radius: 3px; }}
        QProgressBar::chunk {{ background: {colors["blue"]}; border-radius: 3px; }}
        QScrollArea {{ border: 0; background: transparent; }}
        QSplitter::handle {{ background: transparent; }}
        QToolTip {{ border: 1px solid {colors["border"]}; color: {colors["text"]}; }}
    """)
