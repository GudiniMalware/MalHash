"""Hash algorithm names and stream-based implementations."""

from __future__ import annotations

import hashlib
import zlib
from typing import Protocol

DEFAULT_ALGORITHMS = ("md5", "sha1", "sha256", "ssdeep")
ALGORITHMS = (
    *DEFAULT_ALGORITHMS,
    "sha512",
    "sha3-256",
    "sha3-512",
    "blake2b",
    "blake2s",
    "blake3",
    "crc32",
    "tlsh",
    "imphash",
    "authenticode-sha256",
)

CRYPTOGRAPHIC_ALGORITHMS = frozenset(
    {"md5", "sha1", "sha256", "sha512", "sha3-256", "sha3-512", "blake2b", "blake2s", "blake3"}
)


class IncrementalHash(Protocol):
    def update(self, chunk: bytes) -> None: ...

    def hexdigest(self) -> str: ...


class CRC32:
    def __init__(self) -> None:
        self._checksum = 0

    def update(self, chunk: bytes) -> None:
        self._checksum = zlib.crc32(chunk, self._checksum)

    def hexdigest(self) -> str:
        return f"{self._checksum:08x}"


def stream_hasher(name: str) -> IncrementalHash:
    if name == "crc32":
        return CRC32()
    if name == "blake3":
        from blake3 import blake3

        return blake3(max_threads=1)
    return hashlib.new(name, usedforsecurity=False)
