"""Discovery and parallel processing tests using synthetic files."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from threading import Event

import pytest

from malhash.core import ScanOptions, hash_file, process_paths
from malhash.core._filesystem import native_path


def test_directory_depth_and_identical_names(tmp_path: Path) -> None:
    nested = tmp_path / "nested"
    nested.mkdir()
    (nested / "same.txt").write_text("nested", encoding="utf-8")
    (tmp_path / "same.txt").write_text("top", encoding="utf-8")
    nonrecursive = list(process_paths([tmp_path], algorithms=("sha256",)))
    assert len(nonrecursive) == 1
    options = ScanOptions(recursive=True)
    paths = [tmp_path, tmp_path / "same.txt", tmp_path]
    parallel = list(process_paths(paths, algorithms=("sha256",), scan_options=options, workers=3))
    serial = list(process_paths(paths, algorithms=("sha256",), scan_options=options))
    assert parallel == serial
    assert [result.target.name for result in serial] == ["same.txt", "same.txt"]
    assert [result.target.relative_path.as_posix() for result in serial] == [
        "nested/same.txt",
        "same.txt",
    ]
    assert serial[0].hashes["sha256"].value != serial[1].hashes["sha256"].value


def test_filters_and_ignores(tmp_path: Path) -> None:
    skip = tmp_path / "cache"
    skip.mkdir()
    data = {
        "a.EXE": b"x",
        "b.exe": b"too big",
        "c.dll": b"x",
        "d.exe": b"x",
        "e.exe": b"x",
        "report.exe": b"existing report",
        "cache/m.exe": b"x",
    }
    for name, contents in data.items():
        (tmp_path / name).write_bytes(contents)
    hardlink = tmp_path / "hardlink.exe"
    try:
        os.link(tmp_path / "report.exe", hardlink)
    except OSError:
        pass
    options = ScanOptions(
        recursive=True,
        extensions=(".exe",),
        include=("*.exe", "*.EXE"),
        exclude=("d.exe", "e.*"),
        exclude_dirs=("cache",),
        max_size=2,
        ignored_paths=(tmp_path / "report.exe",),
    )
    results = list(process_paths([tmp_path], algorithms=("sha256",), scan_options=options))
    assert [(result.target.name, result.status, result.issue) for result in results] == [
        ("a.EXE", "ok", None),
        ("b.exe", "skipped", "too_large"),
        ("c.dll", "skipped", "filtered"),
        ("cache", "skipped", "filtered"),
        ("d.exe", "skipped", "filtered"),
        ("e.exe", "skipped", "filtered"),
    ]


def test_missing_inputs_do_not_stop_run(tmp_path: Path) -> None:
    sample = tmp_path / "sample"
    sample.write_bytes(b"hello")
    paths = [sample, tmp_path / "absent"]
    first, second = process_paths(paths, algorithms=("md5",))
    assert first.issue == "not_found"
    assert second.status == "ok"


def test_symbolic_links_are_ignored(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.mkdir()
    child = target / "file"
    child.write_bytes(b"sample")
    directory_link = tmp_path / "linked-dir"
    file_link = tmp_path / "linked-file"
    try:
        directory_link.symlink_to(target, target_is_directory=True)
        file_link.symlink_to(child)
    except OSError as error:
        pytest.skip(f"Cannot create symbolic links on this host: {error}")
    results = list(
        process_paths([tmp_path], algorithms=("sha256",), scan_options=ScanOptions(recursive=True))
    )
    assert [
        result.target.relative_path.as_posix() for result in results if result.status == "ok"
    ] == ["target/file"]
    assert {result.target.name for result in results if result.issue == "unsafe_path"} == {
        "linked-dir",
        "linked-file",
    }
    assert hash_file(directory_link / "file", algorithms=("sha256",)).issue == "unsafe_path"
    assert hash_file(file_link, algorithms=("sha256",)).issue == "unsafe_path"


@pytest.mark.skipif(os.name != "nt", reason="Windows junctions")
def test_windows_junction_is_not_traversed(tmp_path: Path) -> None:
    source = tmp_path / "source"
    other = tmp_path / "elsewhere"
    source.mkdir()
    other.mkdir()
    (other / "sample").write_bytes(b"non-executable file")
    junction = source / "junction"
    command = ["cmd.exe", "/d", "/c", "mklink", "/J", str(junction), str(other)]
    create = subprocess.run(command, check=False, capture_output=True, timeout=10)
    if create.returncode:
        pytest.skip("The environment cannot create junctions")
    try:
        (result,) = process_paths(
            [source], algorithms=("sha256",), scan_options=ScanOptions(recursive=True)
        )
        assert result.issue == "unsafe_path"
        assert result.status == "skipped"
        assert hash_file(junction / "sample", algorithms=("sha256",)).issue == "unsafe_path"
    finally:
        os.rmdir(junction)


def test_long_unicode_path(tmp_path: Path) -> None:
    current = tmp_path
    directories = []
    while len(str(current)) < 275:
        current = current / ("muestras-образцы-樣本-" * 3)
        directories.append(current)
        os.mkdir(native_path(current))
    sample = current / "пример-样本"
    try:
        with open(native_path(sample), "wb") as stream:
            stream.write(b"data")
        result = hash_file(sample, algorithms=("sha256",))
        assert result.status == "ok"
        assert result.target.path == sample
    finally:
        os.unlink(native_path(sample))
        for directory in reversed(directories):
            os.rmdir(native_path(directory))


def test_pre_cancelled_process_is_empty(tmp_path: Path) -> None:
    cancel = Event()
    cancel.set()
    assert not list(process_paths([tmp_path], cancel=cancel, workers=2))
