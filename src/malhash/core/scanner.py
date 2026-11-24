"""Deterministic, streaming discovery of regular files."""

from __future__ import annotations

import os
import stat
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from fnmatch import fnmatchcase
from pathlib import Path
from threading import Event

from ._filesystem import (
    FileChanged,
    child_stat,
    is_link,
    issue_for,
    lexical_path,
    native_path,
    same_file,
    secure_directory,
)
from .models import FileResult, FileTarget


@dataclass(frozen=True, slots=True)
class ScanOptions:
    recursive: bool = False
    extensions: tuple[str, ...] = ()
    include: tuple[str, ...] = ()
    exclude: tuple[str, ...] = ()
    exclude_dirs: tuple[str, ...] = ()
    max_size: int | None = None
    ignored_paths: tuple[str | os.PathLike[str], ...] = ()

    def __post_init__(self) -> None:
        if self.max_size is not None and self.max_size < 0:
            raise ValueError("max_size must be nonnegative")


def _matches(path: Path, patterns: tuple[str, ...]) -> bool:
    relative = path.as_posix()
    return any(
        fnmatchcase(relative, pattern.replace("\\", "/")) or fnmatchcase(path.name, pattern)
        for pattern in patterns
    )


def _directory_entries(
    path: Path, expected: os.stat_result, cancel: Event | None
) -> Iterator[Path]:
    with secure_directory(path) as directory_fd:
        info = os.stat(native_path(path)) if directory_fd is None else os.fstat(directory_fd)
        if not same_file(expected, info):
            raise FileChanged
        reference = native_path(path) if directory_fd is None else directory_fd
        names = []
        with os.scandir(reference) as entries:
            for entry in entries:
                if cancel is not None and cancel.is_set():
                    return
                names.append(entry.name)
    for name in sorted(names):
        yield path / name


def _identity(info: os.stat_result) -> tuple[int, int]:
    return info.st_dev, info.st_ino


@dataclass(slots=True)
class _DirectoryCursor:
    report_base: Path
    directory: Path
    entries: Iterator[Path]
    identity: tuple[int, int] | None


def scan_paths(
    paths: Iterable[str | os.PathLike[str]],
    options: ScanOptions | None = None,
    *,
    cancel: Event | None = None,
) -> Iterator[FileTarget | FileResult]:
    """Yield files and per-path discovery diagnostics in stable lexical traversal order."""
    options = options or ScanOptions()
    roots = {lexical_path(path) for path in paths}
    ignored = {lexical_path(path) for path in options.ignored_paths}
    ignored_identities = set()
    for path in ignored:
        try:
            info = os.lstat(native_path(path))
        except (OSError, ValueError):
            continue
        if info.st_ino and not is_link(info):
            ignored_identities.add(_identity(info))
    extensions = {("." + part.lstrip(".")).casefold() for part in options.extensions}

    for root in sorted(roots, key=Path.as_posix):
        # An explicit directory's child paths are shown relative to that directory.
        stack = [_DirectoryCursor(root.parent, root, iter((root,)), None)]
        while stack:
            if cancel is not None and cancel.is_set():
                return
            cursor = stack[-1]
            report_base = cursor.report_base
            explicit = len(stack) == 1
            try:
                path = next(cursor.entries)
            except StopIteration:
                stack.pop()
                continue
            except OSError as error:
                stack.pop()
                directory = cursor.directory
                target = FileTarget(directory, directory.relative_to(report_base))
                yield FileResult(target, status="error", issue=issue_for(error))
                continue

            if path in ignored or (not explicit and path in roots):
                continue
            relative = path.relative_to(report_base)
            target = FileTarget(path, relative)
            try:
                if path == path.parent:
                    info = os.lstat(native_path(path))
                else:
                    with secure_directory(path.parent) as directory_fd:
                        info = child_stat(path, directory_fd)
            except OSError as error:
                yield FileResult(target, status="error", issue=issue_for(error))
                continue
            except ValueError:
                yield FileResult(target, status="error", issue="io_error")
                continue
            if _identity(info) in ignored_identities:
                continue
            if is_link(info):
                status = "error" if explicit else "skipped"
                yield FileResult(target, status=status, issue="unsafe_path")
                continue
            if stat.S_ISDIR(info.st_mode):
                directory_base = path if explicit else report_base
                identity = _identity(info)
                if _matches(relative, options.exclude_dirs) or _matches(relative, options.exclude):
                    yield FileResult(target, status="skipped", issue="filtered")
                elif info.st_ino and any(item.identity == identity for item in stack):
                    yield FileResult(target, status="skipped", issue="unsafe_path")
                elif explicit or options.recursive:
                    stack.append(
                        _DirectoryCursor(
                            directory_base, path, _directory_entries(path, info, cancel), identity
                        )
                    )
                continue
            if not stat.S_ISREG(info.st_mode):
                status = "error" if explicit else "skipped"
                yield FileResult(target, status=status, issue="not_regular")
                continue
            if (
                (extensions and path.suffix.casefold() not in extensions)
                or (options.include and not _matches(relative, options.include))
                or _matches(relative, options.exclude)
            ):
                yield FileResult(target, status="skipped", issue="filtered")
            elif options.max_size is not None and info.st_size > options.max_size:
                yield FileResult(target, status="skipped", issue="too_large")
            else:
                yield FileTarget(path, relative, info)
