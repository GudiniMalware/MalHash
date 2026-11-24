"""Qt model for a scan's results."""

from collections import Counter
from html import escape

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt
from PySide6.QtGui import QColor, QFontDatabase

from malhash.core import FileResult
from malhash.exporters import display_text
from malhash.l10n import translate

_CRYPTOGRAPHIC = frozenset(
    {
        "md5",
        "sha1",
        "sha256",
        "sha512",
        "sha3-256",
        "sha3-512",
        "blake2b",
        "blake2s",
        "blake3",
        "authenticode-sha256",
    }
)
_DISPLAY = Qt.ItemDataRole.DisplayRole
_SORT = Qt.ItemDataRole.UserRole
_ROOT = QModelIndex()


class ResultsModel(QAbstractTableModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.rows: list[FileResult] = []
        self.algorithms: tuple[str, ...] = ()
        self.metadata: tuple[str, ...] = ()
        self.language = "en"
        self.name_mode = "filename"
        self.duplicate_color = QColor("#203d50")
        self.mono = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
        self._counts: Counter[tuple[str, str]] = Counter()

    def reset(self, algorithms, metadata):
        self.beginResetModel()
        self.rows.clear()
        self._counts.clear()
        self.algorithms = tuple(algorithms)
        self.metadata = tuple(metadata)
        self.endResetModel()

    def append(self, results):
        if not results:
            return
        beginning = len(self.rows)
        refresh = False
        self.beginInsertRows(QModelIndex(), beginning, beginning + len(results) - 1)
        for result in results:
            for name in _CRYPTOGRAPHIC.intersection(result.hashes):
                digest = result.hashes[name].value
                if digest is None:
                    continue
                entry = (name, digest)
                refresh |= self._counts[entry] == 1
                self._counts[entry] += 1
        self.rows.extend(results)
        self.endInsertRows()
        if refresh and beginning:
            self.dataChanged.emit(
                self.index(0, 1),
                self.index(beginning - 1, len(self.algorithms)),
                [Qt.ItemDataRole.BackgroundRole],
            )

    def set_display(self, language, name_mode, theme):
        self.language = language
        self.name_mode = name_mode
        self.duplicate_color = QColor("#d4e9fd" if theme == "light" else "#203d50")
        self.headerDataChanged.emit(Qt.Orientation.Horizontal, 0, self.columnCount() - 1)
        if self.rows:
            self.dataChanged.emit(
                self.index(0, 0), self.index(len(self.rows) - 1, self.columnCount() - 1)
            )

    def rowCount(self, parent=_ROOT):  # noqa: N802
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent=_ROOT):  # noqa: N802
        return 0 if parent.isValid() else 1 + len(self.algorithms) + len(self.metadata)

    def column_key(self, column):
        return ("", *self.algorithms, *self.metadata)[column]

    def headerData(self, section, orientation, role=_DISPLAY):  # noqa: N802
        if (
            orientation != Qt.Orientation.Horizontal
            or role != _DISPLAY
            or not 0 <= section < self.columnCount()
        ):
            return None
        key = self.column_key(section)
        if key in self.algorithms:
            return key
        return translate(self.language, f"metadata.{key}" if key else "report.name")

    def raw(self, index):
        row = self.rows[index.row()]
        key = self.column_key(index.column())
        if key in self.algorithms:
            value = row.hashes.get(key)
            return None if value is None else value.value
        if key:
            return row.metadata.get(key)
        if self.name_mode == "relative":
            return row.target.relative_path.as_posix()
        if self.name_mode == "absolute":
            return str(row.target.path)
        return row.target.name

    def data(self, index, role=_DISPLAY):
        if not index.isValid():
            return None
        row = self.rows[index.row()]
        key = self.column_key(index.column())
        if role == Qt.ItemDataRole.FontRole:
            return self.mono if key in self.algorithms else None
        if role == Qt.ItemDataRole.ToolTipRole:
            text = display_text(str(row.target.path)) if not key else self.data(index, _DISPLAY)
            return f"<qt>{escape(str(text))}</qt>"
        if role not in (_DISPLAY, _SORT, Qt.ItemDataRole.BackgroundRole):
            return None
        value = self.raw(index)
        if role == Qt.ItemDataRole.BackgroundRole:
            return self.duplicate_color if self._counts[key, value] > 1 else None
        if role == _SORT:
            return value
        if value is not None:
            return (
                display_text(value)
                if key not in self.algorithms and isinstance(value, str)
                else value
            )
        hash_value = row.hashes.get(key)
        status = "unavailable" if hash_value is None else hash_value.status
        return translate(self.language, f"status.{status}")
