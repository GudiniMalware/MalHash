"""Known-answer and file-safety tests for the hash engine."""

from __future__ import annotations

import hashlib
import os
import random
import shutil
import struct
import subprocess
import sys
import zlib
from pathlib import Path
from threading import Event

import pytest

from malhash.core import METADATA_FIELDS, hash_file, process_paths
from malhash.core import hashing as engine

_TEXT = b"Also called fuzzy hashes, Ctph can match inputs that have homologies."
_SSDEEP_CASES = [
    (b"", "3::"),
    (_TEXT, "3:AXGBicFlgVNhBGcL6wCrFQEv:AXGHsNhxLsr2C"),
    (b"\0" * 8192, "3::"),
]


@pytest.mark.parametrize(("data", "reference"), _SSDEEP_CASES)
def test_default_hashes(tmp_path: Path, data: bytes, reference: str) -> None:
    sample = tmp_path / "Muestra-образец-樣本.dat"
    sample.write_bytes(data)
    result = hash_file(sample)
    assert result.status == "ok"
    assert {name: value.value for name, value in result.hashes.items()} == {
        "md5": hashlib.md5(data, usedforsecurity=False).hexdigest(),
        "sha1": hashlib.sha1(data, usedforsecurity=False).hexdigest(),
        "sha256": hashlib.sha256(data).hexdigest(),
        "ssdeep": reference,
    }


def _fuzzy_inputs() -> tuple[bytes, ...]:
    return (
        b"",
        b"abc",
        _TEXT,
        b"\0" * 60_000,
        b"A" * 450_000,
        b"abc" * 230_000,
        bytes(range(256)) * 1800,
        random.Random(2026).randbytes(1024 * 1024 + 19),
        b"\0" * (2 * 1024 * 1024),
        b"\xf9hi" * 450_007 + b"\xf9",
        bytes(range(256)) * 4350,
    )


@pytest.mark.integration
def test_ssdeep_against_original_executable(tmp_path: Path) -> None:
    executable = os.environ.get("MALHASH_SSDEEP_REFERENCE") or shutil.which("ssdeep")
    if not executable:
        pytest.skip("Original ssdeep command is unavailable")
    files = []
    for index, data in enumerate(_fuzzy_inputs()):
        sample = tmp_path / f"input-{index}.bin"
        sample.write_bytes(data)
        files.append(sample)
    command = [executable, "-b", *[str(path) for path in files]]
    lines = subprocess.run(command, check=True, capture_output=True, text=True, timeout=45)
    original = [line.split(",", 1)[0] for line in lines.stdout.splitlines()[1:] if line]
    actual = [hash_file(path, algorithms=("ssdeep",)).hashes["ssdeep"].value for path in files]
    assert len(original) == len(files)
    assert actual == original


def test_streaming_hashes_and_metadata(tmp_path: Path) -> None:
    data = b"\x89PNG\r\n\x1a\n" + bytes(range(256)) * (2**14)
    sample = tmp_path / "photo.bin"
    sample.write_bytes(data)
    algorithms = ("sha512", "sha3-256", "sha3-512", "blake2b", "blake2s", "crc32")
    result = hash_file(sample, algorithms=algorithms, metadata=METADATA_FIELDS)
    assert result.status == "ok"
    for name in algorithms[:-1]:
        assert result.hashes[name].value == hashlib.new(name, data).hexdigest()
    assert result.hashes["crc32"].value == f"{zlib.crc32(data):08x}"
    assert result.metadata["size"] == len(data)
    assert result.metadata["relative_path"] == sample.name
    assert result.metadata["absolute_path"] == str(sample)
    assert result.metadata["mime_type"] == "image/png"
    assert result.metadata["file_type"] == "PNG"
    assert result.metadata["magic_bytes"] == data[:16].hex()
    assert result.metadata["entropy"] == pytest.approx(8.0)
    assert str(result.metadata["modified_at"]).endswith("+00:00")


def test_ssdeep_periodic_fallback_keeps_crypto_hashes(tmp_path: Path, monkeypatch) -> None:
    from ppdeep import hash as reference

    monkeypatch.setattr(engine, "CHUNK_SIZE", 32768)
    data = b"\0" * engine.CHUNK_SIZE + b"\x12\x23" * 37
    path = tmp_path / "repetition-changes"
    path.write_bytes(data)
    result = hash_file(path, algorithms=("sha256", "ssdeep"))
    assert result.hashes["sha256"].value == hashlib.sha256(data).hexdigest()
    assert result.hashes["ssdeep"].value == reference(data)


def test_sparse_large_file_uses_bounded_reads(tmp_path: Path, monkeypatch) -> None:
    size = 16 * 1024 * 1024 + 17
    path = tmp_path / "large.bin"
    with path.open("wb") as stream:
        stream.seek(size - 1)
        stream.write(b"\x01")
    original = engine._Accumulator.update
    observed = []

    def record(state, chunk: bytes) -> None:
        observed.append(len(chunk))
        original(state, chunk)

    monkeypatch.setattr(engine._Accumulator, "update", record)
    result = hash_file(path, algorithms=("sha256", "sha512"), metadata=("size",))
    assert result.status == "ok"
    assert result.metadata["size"] == size
    assert sum(observed) == size
    assert max(observed) <= engine.CHUNK_SIZE
    with path.open("rb") as stream:
        assert result.hashes["sha256"].value == hashlib.file_digest(stream, "sha256").hexdigest()


def test_blake3_known_answer(tmp_path: Path) -> None:
    pytest.importorskip("blake3")
    sample = tmp_path / "data"
    sample.write_bytes(b"abc")
    value = hash_file(sample, algorithms=("blake3",)).hashes["blake3"]
    assert value.value == "6437b3ac38465133ffb63b75273a8db548c558465d79db03fd359c6cd5bd9d85"
    assert value.status == "ok"


def test_tlsh_reference_and_low_variance(tmp_path: Path) -> None:
    pytest.importorskip("tlsh")
    sample = tmp_path / "sample.dat"
    sample.write_bytes(random.Random(17).randbytes(4096))
    value = hash_file(sample, algorithms=("tlsh",)).hashes["tlsh"]
    assert value.value == "T169818E2668C3C58771F62B13BD7B77D1D294AF027E25326D1796D73DC4D453C24A4805"
    assert value.status == "ok"
    for data in (b"", b"a" * 100, b"\0" * 16384):
        sample.write_bytes(data)
        value = hash_file(sample, algorithms=("tlsh",)).hashes["tlsh"]
        assert value.status == "not_applicable"
        assert value.value is None


def _synthetic_pe(with_certificate: bool, *, pe64: bool = False) -> tuple[bytes, bytes]:
    image = bytearray(1024)
    image[:2] = b"MZ"
    pe = 128
    struct.pack_into("<I", image, 60, pe)
    image[pe : pe + 4] = b"PE\0\0"
    optional_size = 240 if pe64 else 224
    machine = 0x8664 if pe64 else 0x14C
    struct.pack_into("<HHIIIHH", image, pe + 4, machine, 1, 0, 0, 0, optional_size, 0x0002)
    opt = pe + 24
    struct.pack_into("<H", image, opt, 0x20B if pe64 else 0x10B)
    struct.pack_into("<IIII", image, opt + 32, 0x1000, 0x200, 0, 0)
    struct.pack_into("<III", image, opt + 56, 0x2000, 0x200, 0xDEADBEEF)
    directories = opt + (112 if pe64 else 96)
    struct.pack_into("<I", image, directories - 4, 16)
    struct.pack_into("<II", image, directories + 8, 0x1000, 40)
    section = opt + optional_size
    image[section : section + 8] = b".idata\0\0"
    struct.pack_into("<IIII", image, section + 8, 0x200, 0x1000, 0x200, 0x200)

    struct.pack_into("<IIIII", image, 0x200, 0x1040, 0, 0, 0x1030, 0x1050)
    image[0x230:0x23D] = b"KERNEL32.dll\0"
    thunk_format = "<Q" if pe64 else "<I"
    struct.pack_into(thunk_format, image, 0x240, 0x1060)
    struct.pack_into(thunk_format, image, 0x250, 0x1060)
    image[0x262:0x26E] = b"ExitProcess\0"
    image.extend(b"overlay!")
    certificate = directories + 32
    if with_certificate:
        struct.pack_into("<II", image, certificate, len(image), 16)
        image.extend(struct.pack("<IHH8s", 16, 0x0200, 2, b"testcert"))
    digest_data = image[: opt + 64] + image[opt + 68 : certificate] + image[certificate + 8 : 1032]
    return bytes(image), bytes(digest_data)


@pytest.mark.parametrize("pe64", [False, True])
def test_authenticode_synthetic_pe(tmp_path: Path, pe64: bool) -> None:
    values = []
    for with_certificate in (False, True):
        image, hashed_bytes = _synthetic_pe(with_certificate, pe64=pe64)
        sample = tmp_path / "pe.data"
        sample.write_bytes(image)
        result = hash_file(sample, algorithms=("authenticode-sha256",), metadata=("file_type",))
        assert result.status == "ok"
        assert result.metadata["file_type"] == "PE"
        value = result.hashes["authenticode-sha256"].value
        assert value == hashlib.sha256(hashed_bytes).hexdigest()
        values.append(value)
    assert values[0] == values[1]


def test_imphash_synthetic_pe(tmp_path: Path) -> None:
    pytest.importorskip("pefile")
    image, _ = _synthetic_pe(False)
    sample = tmp_path / "pe.data"
    sample.write_bytes(image)
    result = hash_file(sample, algorithms=("imphash",))
    assert result.status == "ok"
    assert (
        result.hashes["imphash"].value
        == hashlib.md5(b"kernel32.exitprocess", usedforsecurity=False).hexdigest()
    )


def test_non_pe_and_damaged_pe(tmp_path: Path) -> None:
    sample = tmp_path / "bad.exe"
    algorithms = ("sha256", "imphash", "authenticode-sha256")
    sample.write_bytes(b"plain")
    result = hash_file(sample, algorithms=algorithms)
    assert result.status == "ok"
    assert (
        result.hashes["imphash"].status
        == result.hashes["authenticode-sha256"].status
        == ("not_applicable")
    )
    sample.write_bytes(b"MZ\0\0")
    result = hash_file(sample, algorithms=algorithms)
    assert result.status == "error"
    assert result.issue == "algorithm_error"
    assert result.hashes["imphash"].status == result.hashes["authenticode-sha256"].status == "error"
    assert result.hashes["sha256"].value == hashlib.sha256(b"MZ\0\0").hexdigest()


def test_missing_optional_dependency(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setitem(sys.modules, "blake3", None)
    sample = tmp_path / "data"
    sample.write_bytes(b"abc")
    result = hash_file(sample, algorithms=("sha256", "blake3"))
    assert result.status == "ok"
    assert result.hashes["blake3"].status == "unavailable"
    assert result.hashes["blake3"].value is None
    assert result.hashes["sha256"].value


def test_detects_file_changed_after_discovery(tmp_path: Path) -> None:
    sample = tmp_path / "changing"
    sample.write_bytes(b"initial")

    def modify(_target) -> None:
        sample.write_bytes(b"modified after discovery")

    (result,) = process_paths([sample], algorithms=("sha256",), on_start=modify)
    assert result.status == "error"
    assert result.issue == "changed"
    assert not result.hashes


def test_detects_file_changed_during_read(tmp_path: Path, monkeypatch) -> None:
    sample = tmp_path / "changing"
    sample.write_bytes(b"initial")
    original = engine._Accumulator.update
    denied = []

    def update_and_modify(state, chunk: bytes) -> None:
        original(state, chunk)
        try:
            sample.write_bytes(b"modified while read")
        except PermissionError:
            denied.append(True)

    monkeypatch.setattr(engine._Accumulator, "update", update_and_modify)
    result = hash_file(sample, algorithms=("sha256",))
    if os.name == "nt":
        assert denied == [True]
        assert result.status == "ok"
        assert result.hashes["sha256"].value == hashlib.sha256(b"initial").hexdigest()
    else:
        assert not denied
        assert result.status == "error"
        assert result.issue == "changed"
        assert not result.hashes


@pytest.mark.skipif(os.name != "nt", reason="Windows file-sharing semantics")
def test_locked_file_is_reported(tmp_path: Path) -> None:
    sample = tmp_path / "locked"
    with sample.open("wb") as writer:
        writer.write(b"locked")
        writer.flush()
        result = hash_file(sample, algorithms=("sha256",))
    assert result.status == "error"
    assert result.issue == "permission_denied"


def test_cancellation_during_read(tmp_path: Path, monkeypatch) -> None:
    cancel = Event()
    sample = tmp_path / "long"
    sample.write_bytes(b"abc" * 500_000)
    original = engine._Accumulator.update

    def update_and_cancel(state, chunk: bytes) -> None:
        original(state, chunk)
        cancel.set()

    monkeypatch.setattr(engine._Accumulator, "update", update_and_cancel)
    result = hash_file(sample, cancel=cancel)
    assert result.status == "cancelled"
    assert not result.hashes
