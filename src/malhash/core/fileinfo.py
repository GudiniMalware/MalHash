"""Metadata derived from file bytes and the open descriptor's stat data."""

from __future__ import annotations

import math
import mimetypes
import os
from collections import Counter
from datetime import UTC, datetime

from .models import FileTarget, MetadataValue

METADATA_FIELDS = (
    "size",
    "relative_path",
    "absolute_path",
    "mime_type",
    "file_type",
    "magic_bytes",
    "entropy",
    "modified_at",
)

_SIGNATURES = (
    (b"\x7fELF", "ELF", "application/x-elf"),
    (b"\xfe\xed\xfa\xce", "Mach-O", "application/x-mach-binary"),
    (b"\xce\xfa\xed\xfe", "Mach-O", "application/x-mach-binary"),
    (b"\xfe\xed\xfa\xcf", "Mach-O", "application/x-mach-binary"),
    (b"\xcf\xfa\xed\xfe", "Mach-O", "application/x-mach-binary"),
    (b"%PDF-", "PDF", "application/pdf"),
    (b"PK\x03\x04", "ZIP", "application/zip"),
    (b"PK\x05\x06", "ZIP", "application/zip"),
    (b"PK\x07\x08", "ZIP", "application/zip"),
    (b"\x1f\x8b\x08", "GZIP", "application/gzip"),
    (b"\xfd7zXZ\x00", "XZ", "application/x-xz"),
    (b"7z\xbc\xaf\x27\x1c", "7-Zip", "application/x-7z-compressed"),
    (b"Rar!\x1a\x07", "RAR", "application/vnd.rar"),
    (b"\x89PNG\r\n\x1a\n", "PNG", "image/png"),
    (b"\xff\xd8\xff", "JPEG", "image/jpeg"),
    (b"GIF87a", "GIF", "image/gif"),
    (b"GIF89a", "GIF", "image/gif"),
    (b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1", "OLE Compound File", "application/x-ole-storage"),
    (b"SQLite format 3\x00", "SQLite", "application/vnd.sqlite3"),
    (b"\x00asm", "WebAssembly", "application/wasm"),
    (b"{\\rtf", "RTF", "application/rtf"),
    (b"\x4c\x00\x00\x00\x01\x14\x02\x00", "Windows Shortcut", "application/x-ms-shortcut"),
)


def _file_signature(prefix: bytes, *, is_pe: bool) -> tuple[str, str | None]:
    if not prefix:
        return "Empty", None
    if is_pe:
        return "PE", "application/vnd.microsoft.portable-executable"
    for magic, name, mime in _SIGNATURES:
        if prefix.startswith(magic):
            return name, mime
    if prefix[257:262] == b"ustar":
        return "TAR", "application/x-tar"
    if prefix.startswith(b"RIFF"):
        if prefix[8:12] == b"WEBP":
            return "WebP", "image/webp"
        if prefix[8:12] == b"WAVE":
            return "WAVE", "audio/wav"
    return "Unknown", None


def file_metadata(
    fields: tuple[str, ...],
    target: FileTarget,
    info: os.stat_result,
    prefix: bytes,
    frequencies: Counter[int],
    *,
    is_pe: bool,
) -> dict[str, MetadataValue]:
    result: dict[str, MetadataValue] = {}
    file_type, mime_type = _file_signature(prefix, is_pe=is_pe)
    for field in fields:
        if field == "size":
            result[field] = info.st_size
        elif field == "relative_path":
            result[field] = str(target.relative_path)
        elif field == "absolute_path":
            result[field] = str(target.path)
        elif field == "magic_bytes":
            result[field] = prefix[:16].hex()
        elif field == "modified_at":
            result[field] = datetime.fromtimestamp(info.st_mtime, UTC).isoformat()
        elif field == "file_type":
            result[field] = file_type
        elif field == "mime_type":
            result[field] = (
                mime_type or mimetypes.guess_type(target.name)[0] or "application/octet-stream"
            )
        elif field == "entropy":
            if not info.st_size:
                result[field] = 0.0
            else:
                result[field] = math.fsum(
                    -(count / info.st_size) * math.log2(count / info.st_size)
                    for count in frequencies.values()
                )
    return result
