"""Main desktop window and its scan, report, and export actions."""

import os
from collections import Counter
from html import escape
from importlib.resources import files
from itertools import chain
from pathlib import Path
from time import monotonic

from PySide6.QtCore import (
    QLibraryInfo,
    QSettings,
    QSortFilterProxyModel,
    QStandardPaths,
    Qt,
    QTimer,
    QTranslator,
)
from PySide6.QtGui import QFontDatabase, QIcon, QKeySequence, QPixmap, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QSplitter,
    QStyle,
    QTableView,
    QTabWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from malhash.core import ALGORITHMS, DEFAULT_ALGORITHMS, METADATA_FIELDS, ScanOptions
from malhash.exporters import (
    FORMATS,
    ExportConflict,
    display_text,
    format_card,
    report_file,
    write_report,
)
from malhash.gui.dialogs import PathDialog, show_message
from malhash.gui.history import load_history, save_history
from malhash.gui.model import ResultsModel
from malhash.gui.preferences import Preferences, apply_appearance, choose_preferences
from malhash.gui.worker import ScanThread
from malhash.l10n import error_text, translate

_METRIC = {"bytes": 1, "KiB": 1024, "MiB": 1024**2, "GiB": 1024**3}
_SUFFIXES = {"markdown": "md", "hash-only": "txt"}


class MainWindow(QMainWindow):
    """Present the shared offline pipeline through a Qt interface."""

    def __init__(self, settings=None, history_path=None):
        super().__init__()
        self.settings = QSettings() if settings is None else settings
        self.preferences = Preferences.read(self.settings)
        self._qt_translator = QTranslator(self)
        self.history_path = history_path or (
            Path(
                QStandardPaths.writableLocation(
                    QStandardPaths.StandardLocation.AppLocalDataLocation
                )
            )
            / "history.json"
        )
        self.setWindowTitle("MalHash")
        self.setAcceptDrops(True)
        self.resize(1190, 775)
        self.setMinimumSize(710, 490)
        self._labels = []
        self._picker_path = None
        self._thread = None
        self._close_after_scan = False
        self._started_at = None
        self._elapsed = 0.0
        self._status = "ready"
        self._fatal = None
        self._current_file = ""
        self.records = []
        self.counts = Counter()

        self.timer = QTimer(self)
        self.timer.setInterval(200)
        self.timer.timeout.connect(self._render_status)
        self._build()
        self._load_options()
        self._apply_preferences()
        if self.preferences.history:
            self._restore_history()
        geometry = self.settings.value("geometry")
        if geometry is not None:
            self.restoreGeometry(geometry)

    def tr_gui(self, key, **kwargs):
        return translate(self.preferences.language, f"gui.{key}", **kwargs)

    def _text(self, widget, key, setter="setText"):
        self._labels.append((getattr(widget, setter), key))
        return widget

    def _label(self, key, **properties):
        label = self._text(QLabel(), key)
        for name, value in properties.items():
            label.setProperty(name, value)
        return label

    def _button(self, key, action, *, pixmap=None, primary=False):
        button = self._text(QPushButton(), key)
        button.clicked.connect(action)
        if primary:
            button.setProperty("primary", True)
        if pixmap is not None:
            button.setIcon(self.style().standardIcon(pixmap))
        return button

    def _frame(self, name):
        frame = QFrame()
        frame.setObjectName(name)
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(14, 13, 14, 13)
        layout.setSpacing(9)
        return frame, layout

    def _build(self):
        root = QWidget()
        outer = QVBoxLayout(root)
        outer.setContentsMargins(15, 15, 15, 15)
        outer.setSpacing(12)
        self.setCentralWidget(root)
        outer.addWidget(self._build_toolbar())

        split = QSplitter(Qt.Orientation.Horizontal)
        split.setChildrenCollapsible(False)
        split.setHandleWidth(12)
        split.addWidget(self._build_sidebar())
        split.addWidget(self._build_workspace())
        split.setStretchFactor(0, 0)
        split.setStretchFactor(1, 1)
        split.setSizes([330, 820])
        outer.addWidget(split, 1)
        outer.addWidget(self._build_status())

        for key, action in (
            ("Ctrl+O", lambda: self._pick_input("files")),
            ("Ctrl+Shift+O", lambda: self._pick_input("folders")),
            ("Ctrl+Return", self.start_scan),
        ):
            QShortcut(QKeySequence(key), self).activated.connect(action)

    def _build_toolbar(self):
        bar = QFrame()
        bar.setObjectName("toolbar")
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(14, 10, 14, 10)
        layout.setSpacing(11)
        logo = QPixmap()
        resource = files("malhash").joinpath("resources", "malhash.png")
        if resource.is_file():
            logo.loadFromData(resource.read_bytes())
        if not logo.isNull():
            mark = QLabel()
            ratio = self.devicePixelRatioF()
            scaled = logo.scaled(
                round(36 * ratio),
                round(36 * ratio),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            scaled.setDevicePixelRatio(ratio)
            mark.setPixmap(scaled)
            self.setWindowIcon(QIcon(logo))
            layout.addWidget(mark)
        brand = QVBoxLayout()
        brand.setSpacing(0)
        title = QLabel("MalHash")
        title.setObjectName("wordmark")
        brand.addWidget(title)
        tagline = self._label("tagline", muted=True)
        tagline.setWordWrap(True)
        brand.addWidget(tagline)
        layout.addLayout(brand, 1)
        settings_button = self._button("settings", self._settings)
        layout.addWidget(settings_button)
        return bar

    def _section(self, layout, key, content):
        toggle = self._text(QToolButton(), key)
        toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        toggle.setArrowType(Qt.ArrowType.RightArrow)
        toggle.setCheckable(True)
        content.hide()
        toggle.toggled.connect(content.setVisible)
        toggle.toggled.connect(
            lambda enabled: toggle.setArrowType(
                Qt.ArrowType.DownArrow if enabled else Qt.ArrowType.RightArrow
            )
        )
        layout.addWidget(toggle, alignment=Qt.AlignmentFlag.AlignLeft)
        layout.addWidget(content)

    def _build_sidebar(self):
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setMinimumWidth(275)
        frame, panel = self._frame("panel")
        scroll.setWidget(frame)
        panel.addWidget(self._label("sources", heading=True))
        actions = QHBoxLayout()
        self.add_files = self._button(
            "add_files",
            lambda: self._pick_input("files"),
            pixmap=QStyle.StandardPixmap.SP_FileIcon,
        )
        self.add_folders = self._button(
            "add_folders",
            lambda: self._pick_input("folders"),
            pixmap=QStyle.StandardPixmap.SP_DirIcon,
        )
        actions.addWidget(self.add_files)
        actions.addWidget(self.add_folders)
        panel.addLayout(actions)
        self.drop_hint = self._label("drop_sources", muted=True)
        self.drop_hint.setWordWrap(True)
        panel.addWidget(self.drop_hint)
        self.sources = QListWidget()
        self._text(self.sources, "sources", "setAccessibleName")
        self.sources.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
        self.sources.setFixedHeight(95)
        self.sources.itemSelectionChanged.connect(self._update_actions)
        deletion = QShortcut(QKeySequence.StandardKey.Delete, self.sources)
        deletion.setContext(Qt.ShortcutContext.WidgetShortcut)
        deletion.activated.connect(self._remove_sources)
        panel.addWidget(self.sources)
        source_actions = QHBoxLayout()
        self.remove_sources = self._button("remove", self._remove_sources)
        self.clear_sources = self._button("clear", self._clear_sources)
        source_actions.addWidget(self.remove_sources)
        source_actions.addWidget(self.clear_sources)
        source_actions.addStretch()
        panel.addLayout(source_actions)
        self.recursive = self._text(QCheckBox(), "recursive")
        panel.addWidget(self.recursive)
        self._section(panel, "filters", self._build_filters())

        panel.addSpacing(5)
        panel.addWidget(self._label("algorithms", heading=True))
        self.algorithm_boxes = {}
        panel.addWidget(self._check_grid(DEFAULT_ALGORITHMS, self.algorithm_boxes))
        optional = tuple(
            algorithm for algorithm in ALGORITHMS if algorithm not in DEFAULT_ALGORITHMS
        )
        self._section(panel, "advanced", self._check_grid(optional, self.algorithm_boxes))
        self.metadata_boxes = {}
        self._section(
            panel, "metadata", self._check_grid(METADATA_FIELDS, self.metadata_boxes, metadata=True)
        )
        panel.addStretch(1)

        actions = QHBoxLayout()
        self.calculate = self._button("calculate", self.start_scan, primary=True)
        self.cancel = self._button("cancel", self.cancel_scan)
        actions.addWidget(self.calculate, 1)
        actions.addWidget(self.cancel)
        panel.addLayout(actions)
        return scroll

    def _build_filters(self):
        widget = QWidget()
        form = QFormLayout(widget)
        form.setContentsMargins(4, 0, 0, 0)
        self.filters = {}
        for key in ("extensions", "include", "exclude", "exclude_dirs"):
            field = self._text(QLineEdit(), f"{key}_hint", "setPlaceholderText")
            form.addRow(self._label(key), field)
            self.filters[key] = field
        self.max_size = QSpinBox()
        self.max_size.setRange(0, 2_147_483_647)
        self._text(self.max_size, "unlimited", "setSpecialValueText")
        self.size_unit = QComboBox()
        for key in _METRIC:
            self.size_unit.addItem(key, key)
        self.size_unit.setCurrentIndex(self.size_unit.findData("MiB"))
        row = QHBoxLayout()
        row.addWidget(self.max_size, 1)
        row.addWidget(self.size_unit)
        form.addRow(self._label("max_size"), row)
        return widget

    def _check_grid(self, names, target, *, metadata=False):
        widget = QWidget()
        grid = QGridLayout(widget)
        grid.setContentsMargins(3, 2, 3, 2)
        grid.setHorizontalSpacing(9)
        grid.setVerticalSpacing(7)
        for index, name in enumerate(names):
            box = QCheckBox(name)
            target[name] = box
            if metadata:
                box.setText(translate(self.preferences.language, f"metadata.{name}"))
            grid.addWidget(box, index // 2, index % 2)
        return widget

    def _build_workspace(self):
        pane, layout = self._frame("results")
        pane.setMinimumWidth(290)
        header = QHBoxLayout()
        header.addWidget(self._label("results", heading=True), 1)
        self.export_format = QComboBox()
        self._text(self.export_format, "export_format", "setAccessibleName")
        for name in FORMATS:
            self.export_format.addItem(name.upper() if name != "markdown" else "Markdown", name)
        header.addWidget(self.export_format)
        self.export_all = self._button("export", self._export_all)
        header.addWidget(self.export_all)

        self.tabs = QTabWidget()
        report_tab = QWidget()
        report_layout = QVBoxLayout(report_tab)
        report_layout.setContentsMargins(0, 8, 0, 0)
        report_layout.addWidget(self._build_report(), 1)
        buttons = QHBoxLayout()
        buttons.addStretch()
        self.copy_report = self._button("copy_report", self._copy_report)
        buttons.addWidget(self.copy_report)
        report_layout.addLayout(buttons)
        self.tabs.addTab(report_tab, "")
        self.tabs.addTab(self._build_table(), "")

        errors_tab = QWidget()
        errors_layout = QVBoxLayout(errors_tab)
        errors_layout.setContentsMargins(0, 8, 0, 0)
        self.errors = self._new_text_area("empty_errors")
        errors_layout.addWidget(self.errors)
        self.tabs.addTab(errors_tab, "")
        layout.addLayout(header)
        layout.addWidget(self.tabs, 1)
        return pane

    def _new_text_area(self, placeholder):
        area = self._text(QPlainTextEdit(), placeholder, "setPlaceholderText")
        area.setReadOnly(True)
        area.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        area.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
        return area

    def _build_report(self):
        self.report = self._new_text_area("empty_report")
        return self.report

    def _build_table(self):
        tab = QWidget()
        box = QVBoxLayout(tab)
        box.setContentsMargins(0, 8, 0, 0)
        controls = QHBoxLayout()
        self.search = self._text(QLineEdit(), "search", "setPlaceholderText")
        self._text(self.search, "search", "setAccessibleName")
        self.search.setClearButtonEnabled(True)
        controls.addWidget(self.search, 1)
        self.copy_selected = self._button("copy_selected", self._copy_selected)
        self.export_selected = self._button("export_selected", self._export_selected)
        controls.addWidget(self.copy_selected)
        controls.addWidget(self.export_selected)
        box.addLayout(controls)
        self.results = ResultsModel(self)
        self.proxy = QSortFilterProxyModel(self)
        self.proxy.setSourceModel(self.results)
        self.proxy.setFilterKeyColumn(-1)
        self.proxy.setFilterCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self.proxy.setSortCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self.proxy.setSortRole(Qt.ItemDataRole.UserRole)
        self.search.textChanged.connect(self.proxy.setFilterFixedString)

        self.table = QTableView()
        self.table.setModel(self.proxy)
        self.table.setSelectionBehavior(QTableView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableView.SelectionMode.ExtendedSelection)
        self.table.setAlternatingRowColors(True)
        self.table.setShowGrid(False)
        self.table.setSortingEnabled(True)
        self.table.sortByColumn(-1, Qt.SortOrder.AscendingOrder)
        self.table.verticalHeader().hide()
        self.table.verticalHeader().setDefaultSectionSize(29)
        self.table.horizontalHeader().setDefaultSectionSize(157)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._row_menu)
        header = self.table.horizontalHeader()
        header.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        header.customContextMenuRequested.connect(self._column_menu)
        self.table.selectionModel().selectionChanged.connect(self._update_actions)
        copy = QShortcut(QKeySequence.StandardKey.Copy, self.table)
        copy.setContext(Qt.ShortcutContext.WidgetShortcut)
        copy.activated.connect(self._copy_selected)
        box.addWidget(self.table, 1)
        return tab

    def _build_status(self):
        frame, box = self._frame("status")
        row = QHBoxLayout()
        self.status = QLabel()
        self.status.setProperty("muted", True)
        self.status.setWordWrap(True)
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        row.addWidget(self.status, 1)
        self.stats = QLabel()
        self.stats.setTextFormat(Qt.TextFormat.PlainText)
        row.addWidget(self.stats)
        box.addLayout(row)
        self.progress = QProgressBar()
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(5)
        self._text(self.progress, "progress", "setAccessibleName")
        box.addWidget(self.progress)
        return frame

    def _apply_preferences(self):
        application = QApplication.instance()
        application.removeTranslator(self._qt_translator)
        if self.preferences.language != "en" and self._qt_translator.load(
            f"qtbase_{self.preferences.language}",
            QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath),
        ):
            application.installTranslator(self._qt_translator)
        apply_appearance(self, self.preferences.theme)
        for setter, key in self._labels:
            setter(self.tr_gui(key))
        for key, box in self.metadata_boxes.items():
            box.setText(translate(self.preferences.language, f"metadata.{key}"))
        for index, key in enumerate(("report_view", "table_view", "errors")):
            self.tabs.setTabText(index, self.tr_gui(key))
        self.export_format.setItemText(FORMATS.index("hash-only"), self.tr_gui("hash_only"))
        self.size_unit.setItemText(self.size_unit.findData("bytes"), self.tr_gui("bytes"))
        self.results.set_display(
            self.preferences.language, self.preferences.name_mode, self.preferences.theme
        )
        self._render_status()
        self._render_report()
        self._render_errors()
        self._update_actions()

    def _load_options(self):
        self.recursive.setChecked(self.settings.value("recursive", False, type=bool))
        for name, box in self.algorithm_boxes.items():
            box.setChecked(
                self.settings.value(f"algorithms/{name}", name in DEFAULT_ALGORITHMS, type=bool)
            )
        for name, box in self.metadata_boxes.items():
            box.setChecked(self.settings.value(f"metadata/{name}", False, type=bool))
        value = self.settings.value("max_size", 0, type=int)
        self.max_size.setValue(value)
        unit = self.size_unit.findData(self.settings.value("size_unit", "MiB", type=str))
        if unit >= 0:
            self.size_unit.setCurrentIndex(unit)

    def _save_options(self):
        self.preferences.write(self.settings)
        self.settings.setValue("geometry", self.saveGeometry())
        self.settings.setValue("recursive", self.recursive.isChecked())
        for name, box in self.algorithm_boxes.items():
            self.settings.setValue(f"algorithms/{name}", box.isChecked())
        for name, box in self.metadata_boxes.items():
            self.settings.setValue(f"metadata/{name}", box.isChecked())
        self.settings.setValue("max_size", self.max_size.value())
        self.settings.setValue("size_unit", self.size_unit.currentData())
        self.settings.sync()

    def _settings(self):
        preferences = choose_preferences(self, self.tr_gui, self.preferences)
        if preferences is None:
            return
        if not preferences.history and self.preferences.history:
            try:
                self.history_path.unlink(missing_ok=True)
            except OSError:
                self._warning("settings", "history_remove_error")
        self.preferences = preferences
        self._apply_preferences()
        self._save_options()

    def _restore_history(self):
        try:
            paths, algorithms, metadata, results = load_history(self.history_path)
        except FileNotFoundError:
            return
        except (OSError, ValueError, TypeError, KeyError):
            self._fatal = "history_load_error"
            self._render_errors()
            return
        self.add_paths(paths)
        self.results.reset(algorithms, metadata)
        self._receive_batch(results)
        self._status = "history_restored"
        self._render_status()
        self.progress.setValue(100)

    def _persist_history(self):
        if not self.preferences.history:
            return
        try:
            save_history(
                self.history_path,
                self.input_paths(),
                self.results.algorithms,
                self.results.metadata,
                self.records,
            )
        except OSError:
            self._warning("settings", "history_save_error")

    def _pick_input(self, mode):
        if self._thread is not None:
            return
        dialog = PathDialog(self, self.tr_gui, mode, start_path=self._picker_path)
        if dialog.exec():
            self.add_paths(dialog.paths)
            self._picker_path = dialog.current

    def add_paths(self, paths):
        if self._thread is not None:
            return
        known = {os.path.normcase(name) for name in self.input_paths()}
        for path in paths:
            value = os.path.abspath(path)
            key = os.path.normcase(value)
            if key not in known:
                item = QListWidgetItem(display_text(value))
                item.setData(Qt.ItemDataRole.UserRole, value)
                item.setToolTip(f"<qt>{escape(display_text(value))}</qt>")
                self.sources.addItem(item)
                known.add(key)
        self._update_actions()

    def input_paths(self):
        return [
            self.sources.item(row).data(Qt.ItemDataRole.UserRole)
            for row in range(self.sources.count())
        ]

    def _remove_sources(self):
        for item in self.sources.selectedItems():
            self.sources.takeItem(self.sources.row(item))
        self._update_actions()

    def _clear_sources(self):
        self.sources.clear()
        self._update_actions()

    def dragEnterEvent(self, event):  # noqa: N802
        if self._thread is None and any(url.isLocalFile() for url in event.mimeData().urls()):
            event.acceptProposedAction()

    def dropEvent(self, event):  # noqa: N802
        urls = event.mimeData().urls()
        self.add_paths(url.toLocalFile() for url in urls if url.isLocalFile())
        event.acceptProposedAction()

    def _scan_options(self):
        filters = {
            key: tuple(part.strip() for part in field.text().split(",") if part.strip())
            for key, field in self.filters.items()
        }
        limit = self.max_size.value() * _METRIC[self.size_unit.currentData()] or None
        return ScanOptions(recursive=self.recursive.isChecked(), max_size=limit, **filters)

    def start_scan(self):
        paths = self.input_paths()
        if self._thread is not None or not paths:
            return
        algorithms = tuple(name for name in ALGORITHMS if self.algorithm_boxes[name].isChecked())
        if not algorithms:
            self._warning("calculate", "algorithms_required")
            return
        metadata = tuple(name for name in METADATA_FIELDS if self.metadata_boxes[name].isChecked())
        self.records.clear()
        self.counts.clear()
        self.results.reset(algorithms, metadata)
        if algorithms:
            self.table.setColumnWidth(0, 154)
        self.report.clear()
        self.errors.clear()
        self._fatal = None
        self._current_file = ""
        self._status = "discovering"
        self._started_at = monotonic()
        self._thread = ScanThread(
            paths,
            algorithms=algorithms,
            metadata=metadata,
            scan_options=self._scan_options(),
            workers=self.preferences.workers,
            parent=self,
        )
        self._thread.batch_ready.connect(self._receive_batch)
        self._thread.file_started.connect(self._file_started)
        self._thread.failed.connect(self._scan_failed)
        self._thread.finished.connect(self._scan_finished)
        self.status.setToolTip("")
        self.progress.setRange(0, 0)
        self.timer.start()
        self._thread.start()
        self._render_status()
        self._update_actions()

    def cancel_scan(self):
        if self._thread is not None:
            self._thread.cancel.set()
            self._status = "cancelling"
            self._render_status()
            self._update_actions()

    def _file_started(self, name, path):
        if self._status not in {"discovering", "scanning"}:
            return
        self._current_file = name
        self._status = "scanning"
        self.status.setToolTip(f"<qt>{escape(display_text(path))}</qt>")
        self._render_status()

    def _receive_batch(self, batch):
        self.records.extend(batch)
        self.counts.update(result.status for result in batch)
        available = [result for result in batch if result.status == "ok" or result.hashes]
        self.results.append(available)
        text = self._report_text(available)
        if text:
            scroll = self.report.verticalScrollBar()
            position = scroll.value()
            self.report.appendPlainText(text if self.report.document().isEmpty() else f"\n{text}")
            scroll.setValue(position)
        errors = self._error_lines(batch)
        if errors:
            self.errors.appendPlainText("\n".join(errors))
        self._render_status()
        self._update_actions()

    def _scan_failed(self, _failure):
        self._fatal = "scan_error"
        self._render_errors()

    def _scan_finished(self):
        thread = self._thread
        if thread is None:
            return
        self._status = (
            "scan_error" if self._fatal else "cancelled" if thread.cancel.is_set() else "complete"
        )
        self._elapsed = monotonic() - self._started_at
        self._started_at = None
        self._thread = None
        thread.deleteLater()
        self.timer.stop()
        self.progress.setRange(0, 100)
        self.progress.setValue(100 if self._status == "complete" else 0)
        self.status.setToolTip("")
        self._render_status()
        self._update_actions()
        if self._close_after_scan:
            self.close()

    def _render_status(self):
        elapsed = monotonic() - self._started_at if self._started_at is not None else self._elapsed
        self.status.setText(self.tr_gui(self._status, filename=display_text(self._current_file)))
        self.stats.setText(
            self.tr_gui(
                "stats",
                processed=self.counts["ok"] + self.counts["error"],
                skipped=self.counts["skipped"] + self.counts["cancelled"],
                errors=self.counts["error"],
                seconds=elapsed,
            )
        )

    def _report_text(self, rows):
        language = self.preferences.preferred_report_language
        return "\n\n".join(
            format_card(
                row,
                language=language,
                name_mode=self.preferences.name_mode,
                algorithms=self.results.algorithms,
                metadata=self.results.metadata,
            ).rstrip("\n")
            for row in rows
        )

    def _render_report(self):
        self.report.setPlainText(self._report_text(self.results.rows))

    def _error_lines(self, rows):
        language = self.preferences.language
        return [
            f"{display_text(str(result.target.path))}  |  "
            f"{translate(language, f'status.{result.status}')}  |  "
            f"{error_text(language, result.issue)}"
            for result in rows
            if result.issue is not None
        ]

    def _render_errors(self):
        errors = self._error_lines(self.records)
        if self._fatal:
            errors.append(self.tr_gui(self._fatal))
        self.errors.setPlainText("\n".join(errors))

    def _update_actions(self):
        if not hasattr(self, "cancel"):
            return
        running = self._thread is not None
        selected = self.table.selectionModel().hasSelection()
        self.add_files.setEnabled(not running)
        self.add_folders.setEnabled(not running)
        self.remove_sources.setEnabled(not running and bool(self.sources.selectedItems()))
        self.clear_sources.setEnabled(not running and bool(self.sources.count()))
        self.calculate.setEnabled(not running and bool(self.sources.count()))
        self.cancel.setEnabled(running and not self._thread.cancel.is_set())
        self.copy_report.setEnabled(bool(self.results.rows))
        self.copy_selected.setEnabled(selected)
        self.export_all.setEnabled(not running and bool(self.results.rows))
        self.export_selected.setEnabled(not running and selected)

    def _selected(self):
        selection = sorted(index.row() for index in self.table.selectionModel().selectedRows())
        return [
            self.results.rows[self.proxy.mapToSource(self.proxy.index(row, 0)).row()]
            for row in selection
        ]

    def _copy_report(self):
        QApplication.clipboard().setText(self._report_text(self.results.rows))

    def _copy_selected(self):
        rows = self._selected()
        if rows:
            QApplication.clipboard().setText(self._report_text(rows))

    def _row_menu(self, point):
        index = self.table.indexAt(point)
        if not index.isValid():
            return
        source = self.proxy.mapToSource(index)
        menu = QMenu(self)
        result = self.results.rows[source.row()]
        key = self.results.column_key(index.column())
        value = self.results.raw(source)
        if key in self.results.algorithms and value is not None:
            menu.addAction(
                self.tr_gui("copy_hash"), lambda: QApplication.clipboard().setText(str(value))
            )
        menu.addAction(self.tr_gui("copy_file"), lambda: self._copy_file_hashes(result))
        if self.table.selectionModel().hasSelection():
            menu.addAction(self.tr_gui("copy_selected"), self._copy_selected)
            if self._thread is None:
                menu.addAction(self.tr_gui("export_selected"), self._export_selected)
        menu.exec(self.table.viewport().mapToGlobal(point))
        menu.deleteLater()

    def _copy_file_hashes(self, result):
        QApplication.clipboard().setText(
            format_card(
                result,
                language=self.preferences.preferred_report_language,
                name_mode=self.preferences.name_mode,
                algorithms=self.results.algorithms,
            )
        )

    def _column_menu(self, point):
        header = self.table.horizontalHeader()
        column = header.logicalIndexAt(point)
        if column < 0 or not self.proxy.rowCount():
            return
        menu = QMenu(self)
        menu.addAction(self.tr_gui("copy_column"), lambda: self._copy_column(column))
        menu.exec(header.mapToGlobal(point))
        menu.deleteLater()

    def _copy_column(self, column):
        values = (
            self.results.raw(self.proxy.mapToSource(self.proxy.index(row, column)))
            for row in range(self.proxy.rowCount())
        )
        QApplication.clipboard().setText(
            "\n".join(str(value) for value in values if value is not None)
        )

    def _export_all(self):
        self._export(self.results.rows)

    def _export_selected(self):
        self._export(self._selected())

    def _export(self, rows):
        if self._thread is not None or not rows:
            return
        output_format = self.export_format.currentData()
        algorithms = self.results.algorithms
        if output_format == "hash-only" and len(algorithms) > 1:
            algorithm = self._choose_hash()
            if algorithm is None:
                return
            algorithms = (algorithm,)
        suffix = _SUFFIXES.get(output_format, output_format)
        dialog = PathDialog(
            self, self.tr_gui, "save", filename=f"malhash.{suffix}", start_path=self._picker_path
        )
        if not dialog.exec():
            return
        target = Path(dialog.paths[0])
        if not target.suffix:
            target = target.with_suffix(f".{suffix}")
        if target.exists() and not show_message(
            self,
            self.tr_gui,
            "save_report",
            self.tr_gui("overwrite", path=target),
            confirm=True,
        ):
            return
        protected = chain(self.input_paths(), (result.target.path for result in self.records))
        try:
            with report_file(target, protected_paths=protected) as stream:
                write_report(
                    rows,
                    stream,
                    format=output_format,
                    language=self.preferences.preferred_report_language,
                    name_mode=self.preferences.name_mode,
                    algorithms=algorithms,
                    metadata=self.results.metadata,
                )
        except ExportConflict as exc:
            show_message(
                self, self.tr_gui, "save_report", error_text(self.preferences.language, exc.code)
            )
        except (OSError, ValueError):
            self._warning("save_report", "export_error")
        else:
            self._picker_path = target.parent
            self.statusBar().showMessage(self.tr_gui("export_done", path=target), 5000)

    def _choose_hash(self):
        dialog = QDialog(self)
        dialog.setWindowTitle(self.tr_gui("hash_only"))
        layout = QVBoxLayout(dialog)
        layout.addWidget(QLabel(self.tr_gui("choose_hash")))
        choices = QComboBox()
        choices.addItems(self.results.algorithms)
        layout.addWidget(choices)
        buttons = QHBoxLayout()
        cancel = QPushButton(self.tr_gui("cancel"))
        cancel.clicked.connect(dialog.reject)
        accept = QPushButton(self.tr_gui("continue"))
        accept.clicked.connect(dialog.accept)
        buttons.addWidget(cancel)
        buttons.addWidget(accept)
        layout.addLayout(buttons)
        return choices.currentText() if dialog.exec() else None

    def _warning(self, title, key):
        show_message(self, self.tr_gui, title, self.tr_gui(key))

    def closeEvent(self, event):  # noqa: N802
        if self._thread is not None:
            self._close_after_scan = True
            self.cancel_scan()
            event.ignore()
            return
        self._save_options()
        self._persist_history()
        QApplication.instance().removeTranslator(self._qt_translator)
        event.accept()
