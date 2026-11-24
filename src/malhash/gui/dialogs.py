"""Localized dialogs that do not ask the shell to inspect samples."""

import os
from pathlib import Path

from PySide6.QtCore import QAbstractListModel, QDir, QModelIndex, Qt
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListView,
    QMessageBox,
    QPushButton,
    QStyle,
    QVBoxLayout,
)

from malhash.core._filesystem import lexical_path, native_path, secure_directory
from malhash.exporters import display_text

_ROOT = QModelIndex()


def show_message(parent, tr, title, message, *, confirm=False):
    dialog = QMessageBox(parent)
    dialog.setWindowTitle(tr(title))
    dialog.setTextFormat(Qt.TextFormat.PlainText)
    dialog.setText(message)
    if confirm:
        dialog.setIcon(QMessageBox.Icon.Question)
        accepted = dialog.addButton(tr("replace"), QMessageBox.ButtonRole.AcceptRole)
        canceled = dialog.addButton(tr("cancel"), QMessageBox.ButtonRole.RejectRole)
        dialog.setDefaultButton(canceled)
    else:
        dialog.setIcon(QMessageBox.Icon.Warning)
        accepted = dialog.addButton(tr("ok"), QMessageBox.ButtonRole.AcceptRole)
    dialog.exec()
    return dialog.clickedButton() == accepted


class DirectoryList(QAbstractListModel):
    """A list of names and generic icons backed solely by directory entries."""

    def __init__(self, parent):
        super().__init__(parent)
        style = QApplication.style()
        self.directory = style.standardIcon(QStyle.StandardPixmap.SP_DirIcon)
        self.file = style.standardIcon(QStyle.StandardPixmap.SP_FileIcon)
        self.entries: list[tuple[bool, str]] = []

    def load(self, folder):
        with (
            secure_directory(folder) as descriptor,
            os.scandir(descriptor if descriptor is not None else native_path(folder)) as entries,
        ):
            names = [(entry.is_dir(follow_symlinks=False), entry.name) for entry in entries]
        names.sort(key=lambda entry: (not entry[0], entry[1].casefold(), entry[1]))
        self.beginResetModel()
        self.entries = names
        self.endResetModel()

    def rowCount(self, parent=_ROOT):  # noqa: N802
        return 0 if parent.isValid() else len(self.entries)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        is_directory, name = self.entries[index.row()]
        if role == Qt.ItemDataRole.DisplayRole:
            return display_text(name)
        if role == Qt.ItemDataRole.DecorationRole:
            return self.directory if is_directory else self.file
        return None


class PathDialog(QDialog):
    """A local-filesystem picker with multi-selection and no sample previews."""

    def __init__(self, parent, tr, mode, *, filename="", start_path=None):
        super().__init__(parent)
        self.tr = tr
        self.mode = mode
        self.paths: list[str] = []
        self.setWindowTitle(
            tr({"files": "pick_files", "folders": "pick_folders", "save": "save_report"}[mode])
        )
        self.resize(650, 450)

        body = QVBoxLayout(self)
        self.model = DirectoryList(self)
        self.view = QListView()
        self.view.setModel(self.model)
        self.view.setUniformItemSizes(True)
        self.view.setSelectionMode(
            QListView.SelectionMode.SingleSelection
            if mode == "save"
            else QListView.SelectionMode.ExtendedSelection
        )
        self.view.doubleClicked.connect(self._open)

        navigation = QHBoxLayout()
        self.up = QPushButton(tr("up"))
        self.up.clicked.connect(self._up)
        self.address = QLineEdit()
        self.address.setAccessibleName(tr("folder_path"))
        self.address.returnPressed.connect(self._open_address)
        navigation.addWidget(self.up)
        navigation.addWidget(self.address, 1)
        go = QPushButton(tr("go"))
        go.clicked.connect(self._open_address)
        navigation.addWidget(go)
        roots = QComboBox()
        roots.setAccessibleName(tr("roots"))
        roots.addItem(tr("locations"), None)
        roots.addItem(tr("home"), str(Path.home()))
        for drive in QDir.drives():
            roots.addItem(drive.absoluteFilePath(), drive.absoluteFilePath())
        roots.activated.connect(lambda index: self._go(roots.itemData(index)))
        navigation.addWidget(roots)
        body.addLayout(navigation)
        body.addWidget(self.view, 1)

        self.name = None
        if mode == "save":
            name_row = QHBoxLayout()
            self.name = QLineEdit(filename)
            label = QLabel(tr("filename"))
            label.setBuddy(self.name)
            name_row.addWidget(label)
            name_row.addWidget(self.name, 1)
            self.view.clicked.connect(self._pick_name)
            body.addLayout(name_row)
        body.addWidget(
            QLabel(
                tr(
                    {
                        "files": "pick_files_help",
                        "folders": "pick_folders_help",
                        "save": "save_help",
                    }[mode]
                )
            )
        )

        buttons = QHBoxLayout()
        buttons.addStretch()
        cancel = QPushButton(tr("cancel"))
        cancel.clicked.connect(self.reject)
        buttons.addWidget(cancel)
        accept = QPushButton(tr("save") if mode == "save" else tr("add"))
        accept.setDefault(True)
        accept.clicked.connect(self._choose)
        buttons.addWidget(accept)
        body.addLayout(buttons)
        self.shortcut = QShortcut(QKeySequence("Ctrl+L"), self)
        self.shortcut.activated.connect(self._focus_address)

        self.current = None
        for path in (start_path, Path.home(), Path(Path.cwd().anchor)):
            if path is not None and self._go(str(path), warn=False):
                break

    def _focus_address(self):
        self.address.setFocus()
        self.address.selectAll()

    def _go(self, path, *, warn=True):
        if not path:
            return False
        try:
            folder = lexical_path(os.path.expanduser(path))
            self.model.load(folder)
        except (OSError, ValueError):
            if warn:
                show_message(self, self.tr, "pick_error", self.tr("folder_unavailable"))
            return False
        self.current = folder
        self.address.setText(str(folder))
        self.up.setEnabled(folder.parent != folder)
        return True

    def _open_address(self):
        self._go(self.address.text())

    def _up(self):
        if self.current is not None:
            self._go(str(self.current.parent))

    def _open(self, index):
        is_directory, name = self.model.entries[index.row()]
        if is_directory:
            self._go(str(self.current / name))
        elif self.mode == "save":
            self._pick_name(index)
        elif self.mode == "files":
            self._choose()

    def _pick_name(self, index):
        is_directory, name = self.model.entries[index.row()]
        if not is_directory:
            self.name.setText(name)

    def _choose(self):
        if self.current is None:
            return
        if self.mode == "save":
            filename = self.name.text()
            if (
                filename in ("", ".", "..")
                or "/" in filename
                or "\\" in filename
                or (os.name == "nt" and any(char in filename for char in '<>:"|?*'))
            ):
                show_message(self, self.tr, "pick_error", self.tr("invalid_filename"))
                return
            self.paths = [str(self.current / filename)]
        else:
            want_dirs = self.mode == "folders"
            selected = self.view.selectedIndexes()
            self.paths = [
                str(self.current / name)
                for is_directory, name in (self.model.entries[index.row()] for index in selected)
                if is_directory == want_dirs
            ]
            if not selected and want_dirs:
                self.paths = [str(self.current)]
        if self.paths:
            self.accept()
