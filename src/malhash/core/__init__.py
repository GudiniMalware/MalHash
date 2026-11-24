"""Public API for offline hashing and file discovery."""

from malhash.algorithms import ALGORITHMS, CRYPTOGRAPHIC_ALGORITHMS, DEFAULT_ALGORITHMS

from .fileinfo import METADATA_FIELDS
from .hashing import hash_file
from .models import FileResult, FileTarget, HashValue
from .scanner import ScanOptions, scan_paths
from .workers import MAX_WORKERS, process_paths

__all__ = [
    "ALGORITHMS",
    "CRYPTOGRAPHIC_ALGORITHMS",
    "DEFAULT_ALGORITHMS",
    "MAX_WORKERS",
    "METADATA_FIELDS",
    "FileResult",
    "FileTarget",
    "HashValue",
    "ScanOptions",
    "hash_file",
    "process_paths",
    "scan_paths",
]
