"""Read-only, no-follow access to paths and file descriptors."""

from __future__ import annotations

import errno
import os
import stat
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from functools import cache
from pathlib import Path
from threading import Event
from typing import BinaryIO

CHUNK_SIZE = 1024 * 1024


class UnsafePath(OSError):
    pass


class FileChanged(OSError):
    pass


class NotRegular(OSError):
    pass


class Cancelled(Exception):
    pass


def check_cancel(cancel: Event | None) -> None:
    if cancel is not None and cancel.is_set():
        raise Cancelled


def is_link(info: os.stat_result) -> bool:
    return stat.S_ISLNK(info.st_mode) or bool(
        getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
    )


def same_file(first: os.stat_result, second: os.stat_result) -> bool:
    return os.path.samestat(first, second) and stat.S_IFMT(first.st_mode) == stat.S_IFMT(
        second.st_mode
    )


def same_version(first: os.stat_result, second: os.stat_result) -> bool:
    return same_file(first, second) and (
        first.st_size,
        first.st_mtime_ns,
        first.st_ctime_ns,
    ) == (
        second.st_size,
        second.st_mtime_ns,
        second.st_ctime_ns,
    )


def issue_for(error: OSError) -> str:
    if isinstance(error, UnsafePath) or error.errno == errno.ELOOP:
        return "unsafe_path"
    if isinstance(error, FileChanged):
        return "changed"
    if isinstance(error, NotRegular):
        return "not_regular"
    if isinstance(error, PermissionError) or getattr(error, "winerror", None) in (32, 33):
        return "permission_denied"
    if isinstance(error, FileNotFoundError):
        return "not_found"
    return "io_error"


def lexical_path(path: str | os.PathLike[str]) -> Path:
    return Path(os.path.abspath(path))


def native_path(path: Path) -> str:
    raw = str(path)
    if os.name != "nt":
        return raw
    if raw.upper().startswith(("\\\\.\\", "\\\\?\\GLOBALROOT\\")) or any(
        ":" in part for part in path.parts[1:]
    ):
        raise UnsafePath
    if raw.startswith("\\\\?\\"):
        return raw
    if raw.startswith("\\\\"):
        return "\\\\?\\UNC\\" + raw[2:]
    return "\\\\?\\" + raw


@contextmanager
def secure_directory(path: Path) -> Iterator[int | None]:
    """Open each path component without traversing a link or reparse point."""
    if os.name == "nt":
        with ExitStack() as locks:
            for parent in (*reversed(path.parents), path):
                locks.enter_context(_windows_handle(parent, directory=True))
            yield None
        return

    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    descriptor = os.open(path.anchor, flags)
    try:
        for part in path.parts[1:]:
            try:
                child = os.open(part, flags, dir_fd=descriptor)
            except NotADirectoryError as error:
                info = os.stat(part, dir_fd=descriptor, follow_symlinks=False)
                if is_link(info):
                    raise UnsafePath from error
                raise
            os.close(descriptor)
            descriptor = child
        yield descriptor
    finally:
        os.close(descriptor)


@cache
def _windows_functions():
    import ctypes
    from ctypes import wintypes

    class AttributeTag(ctypes.Structure):
        _fields_ = [("attributes", wintypes.DWORD), ("tag", wintypes.DWORD)]

    dll = ctypes.WinDLL("kernel32", use_last_error=True)
    open_handle = dll.CreateFileW
    open_handle.argtypes = (
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    )
    open_handle.restype = wintypes.HANDLE
    get_info = dll.GetFileInformationByHandleEx
    get_info.argtypes = (wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD)
    get_info.restype = wintypes.BOOL
    close_handle = dll.CloseHandle
    close_handle.argtypes = (wintypes.HANDLE,)
    close_handle.restype = wintypes.BOOL
    return AttributeTag, open_handle, get_info, close_handle


@contextmanager
def _windows_handle(path: Path, *, directory: bool) -> Iterator[int]:
    import ctypes

    AttributeTag, open_handle, get_info, close_handle = _windows_functions()

    # Deny renames of ancestors and deny writers on the file during hashing.
    access = 0 if directory else 0x80000000  # GENERIC_READ
    sharing = 0x03 if directory else 0x01  # FILE_SHARE_READ | FILE_SHARE_WRITE
    flags = 0x00200000  # FILE_FLAG_OPEN_REPARSE_POINT
    flags |= 0x02000000 if directory else 0x08000000  # BACKUP_SEMANTICS / SEQUENTIAL_SCAN
    handle = open_handle(native_path(path), access, sharing, None, 3, flags, None)
    if handle == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        info = AttributeTag()
        if not get_info(handle, 9, ctypes.byref(info), ctypes.sizeof(info)):
            raise ctypes.WinError(ctypes.get_last_error())
        if info.attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT:
            raise UnsafePath
        if directory and not info.attributes & stat.FILE_ATTRIBUTE_DIRECTORY:
            raise NotADirectoryError(errno.ENOTDIR, "Expected a directory", path)
        yield handle
    finally:
        close_handle(handle)


def child_stat(path: Path, directory_fd: int | None) -> os.stat_result:
    if directory_fd is None:
        return os.lstat(native_path(path))
    return os.stat(path.name, dir_fd=directory_fd, follow_symlinks=False)


@contextmanager
def _file_descriptor(path: Path, directory_fd: int | None) -> Iterator[int]:
    if directory_fd is None:
        import msvcrt

        with _windows_handle(path, directory=False) as handle:
            # The CRT owns the duplicate. The original remains available to the context.
            import _winapi

            process = _winapi.GetCurrentProcess()
            duplicate = _winapi.DuplicateHandle(
                process, handle, process, 0, False, _winapi.DUPLICATE_SAME_ACCESS
            )
            try:
                descriptor = msvcrt.open_osfhandle(duplicate, os.O_RDONLY | os.O_BINARY)
            except OSError:
                _winapi.CloseHandle(duplicate)
                raise
            try:
                yield descriptor
            finally:
                os.close(descriptor)
        return

    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
    descriptor = os.open(path.name, flags, dir_fd=directory_fd)
    try:
        yield descriptor
    finally:
        os.close(descriptor)


@contextmanager
def read_file(
    path: Path, expected: os.stat_result | None
) -> Iterator[tuple[BinaryIO, os.stat_result]]:
    with secure_directory(path.parent) as directory_fd:
        original = child_stat(path, directory_fd)
        if is_link(original):
            raise UnsafePath
        if not stat.S_ISREG(original.st_mode):
            raise NotRegular
        if expected is not None and not same_version(expected, original):
            raise FileChanged
        with _file_descriptor(path, directory_fd) as descriptor:
            opened = os.fstat(descriptor)
            if not same_version(original, opened):
                raise FileChanged
            with open(descriptor, "rb", closefd=False) as stream:
                yield stream, opened
            try:
                current = child_stat(path, directory_fd)
                # A POSIX directory can be renamed while its descriptor remains open.
                named = os.lstat(native_path(path))
            except FileNotFoundError as error:
                raise FileChanged from error
            if (
                is_link(current)
                or is_link(named)
                or not same_version(opened, os.fstat(descriptor))
                or not same_version(opened, current)
                or not same_version(opened, named)
            ):
                raise FileChanged
