"""Launch the optional MalHash desktop interface."""

import hashlib
import os
import sys
from pathlib import Path
from tempfile import TemporaryDirectory


def main(argv=None):
    from PySide6.QtCore import QSettings, Qt
    from PySide6.QtWidgets import QApplication

    from malhash.gui.window import MainWindow

    arguments = list(sys.argv[1:] if argv is None else argv)
    smoke_test = "--smoke-test" in arguments
    if smoke_test:
        arguments.remove("--smoke-test")
    application = QApplication([sys.argv[0], *arguments])
    application.setApplicationName("MalHash")
    application.setOrganizationName("MalHash")
    application.setStyle("Fusion")
    if smoke_test:
        staging = os.environ.get("MALHASH_SMOKE_SETTINGS_DIR")
        with TemporaryDirectory(dir=staging) as directory:
            settings = QSettings(str(Path(directory) / "settings.ini"), QSettings.Format.IniFormat)
            window = MainWindow(settings, Path(directory) / "history.json")
            window.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen)
            window.show()
            sample = Path(directory).resolve() / "sample.txt"
            payload = b"MalHash desktop release test\n"
            sample.write_bytes(payload)
            window.add_paths([sample])
            window._close_after_scan = True
            window.start_scan()
            status = application.exec()
            if hashlib.sha256(payload).hexdigest() not in window.report.toPlainText().lower():
                raise RuntimeError("The desktop report did not show the expected SHA-256")
            return status
    window = MainWindow()
    window.show()
    return application.exec()
