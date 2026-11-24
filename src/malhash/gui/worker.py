"""Run the shared scanning pipeline away from the Qt event loop."""

from threading import Event, Lock
from time import monotonic

from PySide6.QtCore import QThread, Signal

from malhash.core import process_paths


class ScanThread(QThread):
    batch_ready = Signal(object)
    file_started = Signal(str, str)
    failed = Signal(str)

    def __init__(self, paths, *, algorithms, metadata, scan_options, workers, parent=None):
        super().__init__(parent)
        self.paths = tuple(paths)
        self.algorithms = algorithms
        self.metadata = metadata
        self.scan_options = scan_options
        self.workers = workers
        self.cancel = Event()
        self._last_start = 0.0
        self._start_lock = Lock()

    def _on_start(self, target):
        with self._start_lock:
            now = monotonic()
            if now - self._last_start >= 0.1:
                self._last_start = now
                self.file_started.emit(target.name, str(target.path))

    def run(self):
        batch = []
        last_batch = monotonic()
        try:
            for result in process_paths(
                self.paths,
                algorithms=self.algorithms,
                metadata=self.metadata,
                scan_options=self.scan_options,
                workers=self.workers,
                cancel=self.cancel,
                on_start=self._on_start,
            ):
                batch.append(result)
                now = monotonic()
                if len(batch) >= 128 or now - last_batch >= 0.1:
                    self.batch_ready.emit(batch)
                    batch = []
                    last_batch = now
        except Exception as exc:
            self.failed.emit(type(exc).__name__)
        finally:
            if batch:
                self.batch_ready.emit(batch)
