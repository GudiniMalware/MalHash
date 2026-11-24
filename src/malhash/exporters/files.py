"""Atomic report writes and output-identity checks."""

import os
import stat
import tempfile
from collections.abc import Iterable, Iterator
from contextlib import contextmanager, suppress
from pathlib import Path
from typing import TextIO


class ExportConflict(OSError):
    """An output destination conflicts with an input or an unsafe path."""

    code = "output_conflict"


def _check_output(path: Path, protected_paths: tuple[Path, ...]) -> None:
    try:
        destination = path.lstat()
    except FileNotFoundError:
        destination = None
    if destination is not None and (
        not stat.S_ISREG(destination.st_mode)
        or getattr(destination, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
    ):
        raise ExportConflict(path)
    for protected in protected_paths:
        if path == protected:
            raise ExportConflict(path)
        if destination is not None:
            try:
                if os.path.samestat(destination, protected.stat()):
                    raise ExportConflict(path)
            except (FileNotFoundError, NotADirectoryError):
                continue


@contextmanager
def report_file(
    path: str | os.PathLike,
    *,
    protected_paths: Iterable[str | os.PathLike] = (),
) -> Iterator[TextIO]:
    """Write a UTF-8 report atomically without replacing a protected input."""
    requested = Path(os.path.abspath(path))
    parent = requested.parent.resolve(strict=True)
    destination = parent / requested.name
    protected = tuple(Path(os.path.abspath(item)) for item in protected_paths)
    parent_at_open = parent.stat()
    _check_output(destination, protected)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            errors="backslashreplace",
            newline="",
            prefix=".malhash-",
            suffix=".tmp",
            dir=parent,
            delete=False,
        ) as stream:
            temporary = Path(stream.name)
            yield stream
        if not os.path.samestat(parent_at_open, parent.stat()):
            raise ExportConflict(destination)
        _check_output(destination, protected)
        os.replace(temporary, destination)
        temporary = None
    finally:
        if temporary is not None:
            with suppress(FileNotFoundError):
                if os.path.samestat(parent_at_open, parent.stat()):
                    temporary.unlink()
