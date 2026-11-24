"""Calculate selected hashes from one safely opened input file."""

from __future__ import annotations

import os
from collections import Counter
from collections.abc import Iterable, Iterator
from pathlib import Path
from threading import Event
from typing import BinaryIO

from malhash.algorithms import ALGORITHMS, DEFAULT_ALGORITHMS, IncrementalHash, stream_hasher
from malhash.algorithms._periodic_ssdeep import find_period, hash_periodic
from malhash.algorithms.pe import (
    IMPHASH_MAX_BYTES,
    InvalidPE,
    NotPE,
    authenticode_sha256,
    imphash,
    parse_layout,
    pe_offset,
)

from ._filesystem import (
    CHUNK_SIZE,
    Cancelled,
    FileChanged,
    check_cancel,
    issue_for,
    lexical_path,
    read_file,
    same_version,
)
from .fileinfo import METADATA_FIELDS, file_metadata
from .models import FileResult, FileTarget, HashValue

_SPECIAL = {"ssdeep", "tlsh", "imphash", "authenticode-sha256"}
_PE_HASHES = {"imphash", "authenticode-sha256"}
_MAX_SSDEEP_BYTES = 3 * (1 << 30) * 64
_PREFIX_LENGTH = 512


def select(values: Iterable[str], supported: tuple[str, ...]) -> tuple[str, ...]:
    selected = tuple(dict.fromkeys(name.lower() for name in values))
    invalid = [name for name in selected if name not in supported]
    if invalid:
        raise ValueError(f"Unsupported selections: {', '.join(invalid)}")
    return selected


def _chunks(stream: BinaryIO, length: int, cancel: Event | None) -> Iterator[bytes]:
    while length:
        check_cancel(cancel)
        chunk = stream.read(min(CHUNK_SIZE, length))
        if not chunk:
            raise FileChanged
        length -= len(chunk)
        yield chunk


class _Accumulator:
    def __init__(self, algorithms: tuple[str, ...], metadata: tuple[str, ...]) -> None:
        self.hashers: dict[str, IncrementalHash] = {}
        self.hashes: dict[str, HashValue] = {}
        self.tlsh = None
        for name in algorithms:
            try:
                if name == "tlsh":
                    from tlsh import Tlsh

                    self.tlsh = Tlsh()
                elif name not in _SPECIAL:
                    self.hashers[name] = stream_hasher(name)
            except (ImportError, ValueError):
                self.hashes[name] = HashValue(None, "unavailable")
        self.collect_entropy = "entropy" in metadata
        self.frequencies: Counter[int] = Counter()
        self.prefix = b""
        self.observed = 0

    def update(self, chunk: bytes) -> None:
        self.observed += len(chunk)
        missing_prefix = _PREFIX_LENGTH - len(self.prefix)
        if missing_prefix:
            self.prefix += chunk[:missing_prefix]
        for hasher in self.hashers.values():
            hasher.update(chunk)
        if self.tlsh is not None:
            self.tlsh.update(chunk)
        if self.collect_entropy:
            self.frequencies.update(chunk)

    def finish(self) -> None:
        for name, hasher in self.hashers.items():
            self.hashes[name] = HashValue(hasher.hexdigest().lower())
        if self.tlsh is not None:
            try:
                self.tlsh.final()
                value = self.tlsh.hexdigest() if self.tlsh else None
            except (AssertionError, ValueError):
                value = None
            if not value or value == "TNULL":
                self.hashes["tlsh"] = HashValue(None, "not_applicable")
            else:
                # The Elastic wheel emits the same digest without the standard version prefix.
                if len(value) == 70:
                    value = "T1" + value
                self.hashes["tlsh"] = HashValue(value)


class _SSDEEPReader:
    """Adapt the pinned ppdeep stream API to our existing read-only handle."""

    def __init__(
        self,
        stream: BinaryIO,
        expected: os.stat_result,
        accumulator: _Accumulator,
        cancel: Event | None,
    ) -> None:
        self.stream = stream
        self.expected = expected
        self.accumulator = accumulator
        self.cancel = cancel
        self.position = 0
        self.pass_number = 0

    def seek(self, offset: int) -> None:
        check_cancel(self.cancel)
        if not same_version(self.expected, os.fstat(self.stream.fileno())):
            raise FileChanged
        self.pass_number += 1
        self.position = offset
        self.stream.seek(offset)

    def read(self, length: int) -> bytes:
        check_cancel(self.cancel)
        remaining = self.expected.st_size - self.position
        if remaining <= 0:
            return b""
        chunk = self.stream.read(min(length, remaining))
        if not chunk:
            raise FileChanged
        start = self.position
        self.position += len(chunk)
        if self.pass_number == 1 and self.position > self.accumulator.observed:
            self.accumulator.update(chunk[max(0, self.accumulator.observed - start) :])
        return chunk


def _try_periodic_ssdeep(
    stream: BinaryIO,
    length: int,
    accumulator: _Accumulator,
    cancel: Event | None,
) -> str | None:
    check_cancel(cancel)
    sample = stream.read(4096)
    pattern = find_period(sample)
    if not pattern:
        return None

    repeated = pattern * (CHUNK_SIZE // len(pattern) + 2)
    accumulator.update(sample)
    for chunk in _chunks(stream, length - len(sample), cancel):
        phase = accumulator.observed % len(pattern)
        accumulator.update(chunk)
        if chunk != repeated[phase : phase + len(chunk)]:
            return None
    return hash_periodic(pattern, length)


def _ssdeep(
    stream: BinaryIO,
    expected: os.stat_result,
    accumulator: _Accumulator,
    cancel: Event | None,
) -> HashValue:
    try:
        # The public ppdeep path API would reopen a path after validation.
        from ppdeep import _spamsum
    except ImportError:
        return HashValue(None, "unavailable")
    if expected.st_size > _MAX_SSDEEP_BYTES:
        return HashValue(None, "not_applicable")
    if expected.st_size >= CHUNK_SIZE:
        repeated = _try_periodic_ssdeep(stream, expected.st_size, accumulator, cancel)
        if repeated is not None:
            return HashValue(repeated)
    try:
        result = _spamsum(_SSDEEPReader(stream, expected, accumulator, cancel), expected.st_size)
    except (RuntimeError, ValueError):
        return HashValue(None, "error")
    return HashValue(result)


def _pe_hashes(
    stream: BinaryIO,
    size: int,
    prefix: bytes,
    selected: set[str],
    inspect_signature: bool,
    cancel: Event | None,
) -> tuple[bool, dict[str, HashValue]]:
    if not (selected or inspect_signature):
        return False, {}
    try:
        offset = pe_offset(stream, size, prefix)
    except NotPE:
        return False, dict.fromkeys(selected, HashValue(None, "not_applicable"))
    except InvalidPE:
        return False, dict.fromkeys(selected, HashValue(None, "error"))
    try:
        layout = parse_layout(stream, size, offset)
    except InvalidPE:
        return True, dict.fromkeys(selected, HashValue(None, "error"))

    def read_chunks(handle: BinaryIO, length: int) -> Iterator[bytes]:
        yield from _chunks(handle, length, cancel)

    values = {}
    if "authenticode-sha256" in selected:
        values["authenticode-sha256"] = HashValue(
            authenticode_sha256(stream, size, layout, read_chunks)
        )
    if "imphash" in selected:
        try:
            if size > IMPHASH_MAX_BYTES:
                values["imphash"] = HashValue(None, "unavailable")
            else:
                value = imphash(stream, size, read_chunks)
                values["imphash"] = (
                    HashValue(value.lower()) if value else HashValue(None, "not_applicable")
                )
        except ImportError:
            values["imphash"] = HashValue(None, "unavailable")
        except (InvalidPE, ValueError):
            values["imphash"] = HashValue(None, "error")
    return True, values


def hash_target(
    target: FileTarget,
    algorithms: tuple[str, ...],
    metadata: tuple[str, ...],
    cancel: Event | None,
) -> FileResult:
    try:
        check_cancel(cancel)
        with read_file(target.path, target._snapshot) as (stream, info):
            states = _Accumulator(algorithms, metadata)
            if "ssdeep" in algorithms:
                states.hashes["ssdeep"] = _ssdeep(stream, info, states, cancel)
            if states.observed < info.st_size:
                stream.seek(states.observed)
                for chunk in _chunks(stream, info.st_size - states.observed, cancel):
                    states.update(chunk)
            check_cancel(cancel)
            states.finish()

            is_pe, pe_hashes = _pe_hashes(
                stream,
                info.st_size,
                states.prefix,
                _PE_HASHES.intersection(algorithms),
                bool({"mime_type", "file_type"}.intersection(metadata)),
                cancel,
            )
            states.hashes.update(pe_hashes)
            hashes = {name: states.hashes[name] for name in algorithms}
            fields = file_metadata(
                metadata, target, info, states.prefix, states.frequencies, is_pe=is_pe
            )
    except Cancelled:
        return FileResult(target, status="cancelled", issue="cancelled")
    except OSError as error:
        return FileResult(target, status="error", issue=issue_for(error))
    except ValueError:
        return FileResult(target, status="error", issue="io_error")
    if any(value.status == "error" for value in hashes.values()):
        return FileResult(target, hashes, fields, status="error", issue="algorithm_error")
    return FileResult(target, hashes, fields)


def hash_file(
    path: str | os.PathLike[str],
    *,
    algorithms: Iterable[str] = DEFAULT_ALGORITHMS,
    metadata: Iterable[str] = (),
    cancel: Event | None = None,
) -> FileResult:
    """Hash a single regular file with an optional cancellation event."""
    absolute = lexical_path(path)
    target = FileTarget(absolute, Path(absolute.name))
    return hash_target(
        target, select(algorithms, ALGORITHMS), select(metadata, METADATA_FIELDS), cancel
    )
