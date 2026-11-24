"""Bounded, read-only inspection and hashing of PE images."""

from __future__ import annotations

import hashlib
import struct
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import BinaryIO

IMPHASH_MAX_BYTES = 32 * 1024 * 1024
PE_HEADER_SIZE = 24
MAX_PE_SECTIONS = 96
ChunkReader = Callable[[BinaryIO, int], Iterable[bytes]]


class NotPE(ValueError):
    pass


class InvalidPE(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class PELayout:
    checksum: int
    security_entry: int | None
    headers_size: int
    sections: tuple[tuple[int, int], ...]
    certificate_size: int


def _read_at(stream: BinaryIO, offset: int, length: int, size: int) -> bytes:
    if offset < 0 or length < 0 or offset + length > size:
        raise InvalidPE
    stream.seek(offset)
    value = stream.read(length)
    if len(value) != length:
        raise InvalidPE
    return value


def pe_offset(stream: BinaryIO, size: int, prefix: bytes) -> int:
    if not prefix.startswith(b"MZ"):
        raise NotPE
    if len(prefix) < 64:
        raise InvalidPE
    offset = struct.unpack_from("<I", prefix, 60)[0]
    if offset == 0:
        raise NotPE
    if offset < 64:
        raise InvalidPE
    signature = _read_at(stream, offset, 4, size)
    if signature[:2] in (b"NE", b"LE", b"LX"):
        raise NotPE
    if signature != b"PE\0\0":
        raise InvalidPE
    return offset


def parse_layout(stream: BinaryIO, size: int, offset: int) -> PELayout:
    coff = _read_at(stream, offset, PE_HEADER_SIZE, size)
    section_count = struct.unpack_from("<H", coff, 6)[0]
    optional_size = struct.unpack_from("<H", coff, 20)[0]
    if not 1 <= section_count <= MAX_PE_SECTIONS or optional_size < 96:
        raise InvalidPE

    optional_offset = offset + PE_HEADER_SIZE
    optional = _read_at(stream, optional_offset, optional_size, size)
    magic = struct.unpack_from("<H", optional)[0]
    directory_base = {0x10B: 96, 0x20B: 112}.get(magic)
    if directory_base is None or directory_base > optional_size:
        raise InvalidPE
    headers_size = struct.unpack_from("<I", optional, 60)[0]
    directory_count = struct.unpack_from("<I", optional, directory_base - 4)[0]
    if directory_count > (optional_size - directory_base) // 8:
        raise InvalidPE

    table_offset = optional_offset + optional_size
    table_size = section_count * 40
    if not table_offset + table_size <= headers_size <= size:
        raise InvalidPE
    table = _read_at(stream, table_offset, table_size, size)
    ranges = []
    for section in range(section_count):
        length, start = struct.unpack_from("<II", table, section * 40 + 16)
        if length:
            ranges.append((start, start + length))
    ranges.sort()

    end = headers_size
    for start, stop in ranges:
        if start < end or stop > size:
            raise InvalidPE
        end = stop

    entry = certificate_size = None
    if directory_count >= 5:
        security = directory_base + 4 * 8
        entry = optional_offset + security
        certificate, certificate_size = struct.unpack_from("<II", optional, security)
        if certificate or certificate_size:
            if (
                certificate_size < 8
                or certificate < end
                or certificate % 8
                or certificate_size % 8
                or certificate + certificate_size != size
            ):
                raise InvalidPE
    return PELayout(optional_offset + 64, entry, headers_size, tuple(ranges), certificate_size or 0)


def authenticode_sha256(
    stream: BinaryIO, size: int, layout: PELayout, read_chunks: ChunkReader
) -> str:
    """Hash PE ranges in the order defined by Microsoft's Authenticode format."""
    digest = hashlib.sha256()
    ranges = [(0, layout.checksum)]
    header_start = layout.checksum + 4
    if layout.security_entry is not None:
        ranges.append((header_start, layout.security_entry))
        header_start = layout.security_entry + 8
    ranges.append((header_start, layout.headers_size))
    ranges.extend(layout.sections)

    hashed_size = layout.headers_size + sum(end - start for start, end in layout.sections)
    overlay_end = size - layout.certificate_size
    if hashed_size < overlay_end:
        ranges.append((hashed_size, overlay_end))

    for start, end in ranges:
        stream.seek(start)
        for chunk in read_chunks(stream, end - start):
            digest.update(chunk)
    return digest.hexdigest()


def imphash(stream: BinaryIO, size: int, read_chunks: ChunkReader) -> str | None:
    import pefile

    stream.seek(0)
    data = b"".join(read_chunks(stream, size))
    try:
        with pefile.PE(data=data, fast_load=True) as image:
            image.parse_data_directories(
                directories=[pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_IMPORT"]]
            )
            return image.get_imphash() or None
    except (pefile.PEFormatError, AttributeError, IndexError, struct.error, UnicodeError) as error:
        raise InvalidPE from error
