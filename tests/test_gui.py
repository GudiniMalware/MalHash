"""Desktop integration tests using the offscreen Qt platform."""

import hashlib
from pathlib import Path
from time import monotonic

import pytest

pytest.importorskip("PySide6")
from PySide6.QtCore import QItemSelectionModel, QSettings, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from malhash.gui import window as desktop
from malhash.gui.dialogs import PathDialog
from malhash.gui.preferences import Preferences


def _wait_until(predicate):
    deadline = monotonic() + 8
    while not predicate() and monotonic() < deadline:
        QTest.qWait(15)
    assert predicate()


@pytest.fixture(scope="module")
def application():
    app = QApplication.instance() or QApplication(["MalHash tests", "-platform", "offscreen"])
    app.setStyle("Fusion")
    yield app


@pytest.fixture
def main_window(application, tmp_path):
    settings = QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)
    window = desktop.MainWindow(settings, tmp_path / "history.json")
    window.show()
    yield window
    window.close()
    _wait_until(lambda: not window.isVisible())


def test_scan_selection_report_and_localization(main_window, tmp_path, monkeypatch):
    tmp_path = tmp_path.resolve()
    source = tmp_path / "evidence"
    source.mkdir()
    payload = b"Synthetic file used by the GUI tests.\n"
    for name in ("a.txt", "b.txt"):
        (source / name).write_bytes(payload)
    main_window.add_paths((source, source / "missing.txt"))
    assert len(main_window.input_paths()) == 2
    main_window.calculate.click()
    _wait_until(lambda: main_window._thread is None)
    assert main_window.counts == {"ok": 2, "error": 1}
    assert "missing.txt" in main_window.errors.toPlainText()
    assert main_window.report.toPlainText().count("\n\nName: ") == 1
    assert main_window.results.rowCount() == 2
    assert main_window.results.index(0, 1).data(Qt.ItemDataRole.BackgroundRole).isValid()
    sha256 = hashlib.sha256(payload).hexdigest()
    assert sha256 in main_window.report.toPlainText()

    main_window.tabs.setCurrentIndex(1)
    main_window.search.setText("b.txt")
    assert main_window.proxy.rowCount() == 1
    main_window.table.selectionModel().select(
        main_window.proxy.index(0, 0),
        QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows,
    )
    main_window.copy_selected.click()
    assert QApplication.clipboard().text().startswith("Name: b.txt\n")
    main_window._copy_column(main_window.results.algorithms.index("sha256") + 1)
    assert QApplication.clipboard().text() == sha256

    monkeypatch.setattr(
        desktop,
        "choose_preferences",
        lambda *args: Preferences(language="ru", report_language="es", theme="light"),
    )
    main_window._settings()
    assert main_window.tabs.tabText(0) == "Отчёт"
    assert main_window.report.toPlainText().startswith("Nombre: a.txt\n")
    assert sha256 in main_window.report.toPlainText()
    assert main_window.preferences.theme == "light"
    assert not main_window.history_path.exists()


def test_picker_multiselect_and_export(main_window, tmp_path, monkeypatch):
    tmp_path = tmp_path.resolve()
    for name in ("one", "two"):
        (tmp_path / name).mkdir()
    dialog = PathDialog(main_window, main_window.tr_gui, "folders", start_path=tmp_path)
    dialog.show()
    for row, (is_directory, _) in enumerate(dialog.model.entries):
        if is_directory:
            index = dialog.model.index(row, 0)
            dialog.view.selectionModel().select(index, QItemSelectionModel.SelectionFlag.Select)
    dialog._choose()
    assert {Path(path).name for path in dialog.paths} == {"one", "two"}
    dialog.close()

    sample = tmp_path / "one" / "sample.txt"
    sample.write_bytes(b"hello")
    main_window.add_paths([sample])
    main_window.start_scan()
    _wait_until(lambda: main_window._thread is None)
    main_window.tabs.setCurrentIndex(1)
    main_window.table.selectRow(0)
    main_window.export_format.setCurrentIndex(main_window.export_format.findData("jsonl"))
    target = tmp_path / "selected.jsonl"

    class ChooseDestination:
        def __init__(self, *_args, **_kwargs):
            self.paths = [str(target)]

        def exec(self):
            return True

    monkeypatch.setattr(desktop, "PathDialog", ChooseDestination)
    main_window.export_selected.click()
    assert '"name": "sample.txt"' in target.read_text(encoding="utf-8")
    assert sample.read_bytes() == b"hello"


def test_history_requires_opt_in(main_window, tmp_path, monkeypatch):
    tmp_path = tmp_path.resolve()
    sample = tmp_path / "input.txt"
    sample.write_text("Test file.", encoding="utf-8")
    main_window.add_paths([sample])
    main_window.start_scan()
    _wait_until(lambda: main_window._thread is None)
    main_window._persist_history()
    assert not main_window.history_path.exists()
    main_window.preferences.history = True
    main_window._save_options()
    main_window._persist_history()
    assert main_window.history_path.exists()
    restored = desktop.MainWindow(main_window.settings, main_window.history_path)
    try:
        assert restored.input_paths() == [str(sample)]
        assert restored.report.toPlainText() == main_window.report.toPlainText()
        monkeypatch.setattr(desktop, "choose_preferences", lambda *args: Preferences())
        restored._settings()
        assert not main_window.history_path.exists()
    finally:
        restored.close()
        main_window.preferences.history = False
