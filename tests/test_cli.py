import csv
import hashlib
import io
import json
import os
import signal
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration


def cli(*args, cwd=None):
    return subprocess.run(
        [sys.executable, "-m", "malhash", *(str(arg) for arg in args)],
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
        timeout=30,
    )


@pytest.mark.parametrize(("language", "label"), [("en", "Name"), ("ru", "Имя"), ("es", "Nombre")])
def test_card_default_algorithms_and_unicode(tmp_path, language, label):
    sample = tmp_path / "данные-é.txt"
    sample.write_bytes(b"abc")
    run = cli(sample, "--lang", language)
    assert run.returncode == 0, run.stderr
    assert run.stderr == ""
    assert run.stdout.splitlines() == [
        f"{label}: {sample.name}",
        "md5: 900150983cd24fb0d6963f7d28e17f72",
        "sha1: a9993e364706816aba3e25717850c26c9cd0d89d",
        "sha256: ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad",
        "ssdeep: 3:uG:uG",
    ]


def test_language_is_independent_and_bad_input_is_partial(tmp_path):
    sample = tmp_path / "found.txt"
    sample.touch()
    run = cli(sample, "--lang", "es", tmp_path / "missing", "--report-lang", "ru")
    assert run.returncode == 1
    assert "Ruta no encontrada" in run.stderr
    assert run.stdout.startswith("Имя: found.txt\n")
    assert "missing" not in run.stdout
    assert "muestra" in cli("--lang", "es", "--help").stdout.lower()
    assert "Неизвестные аргументы" in cli(sample, "--lang", "ru", "--bogus").stderr


def test_globs_recursion_and_streamed_output(tmp_path):
    for folder in ("dir", "skip"):
        (tmp_path / folder).mkdir()
    for filename in ("a.bin", "dir/a.bin", "dir/blob.txt", "skip/a.bin"):
        (tmp_path / filename).write_bytes(filename.encode("ascii"))
    (tmp_path / "dir" / "big.bin").write_bytes(b"x" * 64)
    output = tmp_path / "report.jsonl"
    output.write_text("previous report", encoding="utf-8")
    run = cli(
        tmp_path,
        "-r",
        "--include",
        "*.bin",
        "--exclude-dir",
        "skip",
        "--max-size",
        "12B",
        "--workers",
        "2",
        "--relative-paths",
        "--format",
        "jsonl",
        "--metadata",
        "size",
        "-o",
        output,
    )
    assert run.returncode == 0, run.stderr
    assert run.stdout == ""
    records = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]
    assert [record["name"] for record in records] == ["a.bin", "dir/a.bin"]
    assert records[1]["metadata"]["size"] == 9
    assert records[1]["hashes"]["md5"]["status"] == "ok"
    assert not list(tmp_path.glob(".malhash-*.tmp"))


@pytest.mark.parametrize("format", ["txt", "json", "jsonl", "csv", "tsv", "markdown", "hash-only"])
def test_export_format(format, tmp_path):
    first = tmp_path / "=1+1.txt"
    second = tmp_path / "a_[x].txt"
    first.write_bytes(b"first")
    second.write_bytes(b"second")
    run = cli(tmp_path, "--format", format, "--algorithms", "sha256", "--lang", "ru")
    assert run.returncode == 0, run.stderr
    assert run.stderr == ""
    expected = [hashlib.sha256(sample.read_bytes()).hexdigest() for sample in (first, second)]
    if format in ("csv", "tsv"):
        rows = list(
            csv.DictReader(io.StringIO(run.stdout), delimiter="," if format == "csv" else "\t")
        )
        assert rows[0]["name"] == "'" + first.name
        assert [row["sha256"] for row in rows] == expected
    elif format in ("json", "jsonl"):
        records = (
            json.loads(run.stdout)
            if format == "json"
            else [json.loads(line) for line in run.stdout.splitlines()]
        )
        assert records[0]["name"] == first.name
        assert [record["hashes"]["sha256"]["value"] for record in records] == expected
    elif format == "hash-only":
        assert run.stdout.splitlines() == expected
    elif format == "txt":
        assert (
            run.stdout
            == f"Имя: {first.name}\nsha256: {expected[0]}\n\nИмя: {second.name}\nsha256: {expected[1]}\n"
        )
    else:
        assert run.stdout.startswith("| name | sha256 | sha256\\_status |\n")
        assert r"a\_\[x\]\.txt" in run.stdout
        assert all(value in run.stdout for value in expected)


def test_output_cannot_replace_explicit_input_or_hardlink(tmp_path):
    sample = tmp_path / "evidence"
    sample.write_bytes(b"evidence")
    assert cli(sample, "-o", sample).returncode == 2
    alias = tmp_path / "alias"
    try:
        os.link(sample, alias)
    except OSError as error:
        pytest.skip(str(error))
    assert cli(sample, "-o", alias).returncode == 2
    assert sample.read_bytes() == alias.read_bytes() == b"evidence"


def test_repeated_output_in_scan_directory_is_ignored(tmp_path):
    sample = tmp_path / "evidence.bin"
    sample.write_bytes(b"evidence")
    report = sample.with_name("report.json")
    for _ in range(2):
        run = cli(tmp_path, "--format", "json", "-o", report)
        assert run.returncode == 0, run.stderr
        assert [record["name"] for record in json.loads(report.read_bytes())] == [sample.name]


def test_hash_only_validates_algorithm_count(tmp_path):
    run = cli(tmp_path, "--format", "hash-only")
    assert run.returncode == 2
    assert "Select exactly one algorithm" in run.stderr
    assert not run.stdout


def test_cli_never_imports_pyside():
    run = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; from malhash.cli import main; "
            "assert main(['--version']) == 0; "
            "assert not any(name.startswith('PySide6') for name in sys.modules)",
        ],
        capture_output=True,
        check=False,
        text=True,
        timeout=30,
    )
    assert run.returncode == 0, run.stderr
    assert run.stdout == "MalHash 1.0.0\n"


def test_argv_terminator_keeps_option_like_filenames(tmp_path):
    sample = tmp_path / "--lang"
    sample.touch()
    (tmp_path / "es").touch()
    run = cli("--algorithms", "md5", "--", sample.name, "es", cwd=tmp_path)
    assert run.returncode == 0, run.stderr
    assert run.stdout.startswith("Name: --lang\n")


def test_interrupt_keeps_completed_records_in_valid_json(tmp_path, capsys, monkeypatch):
    import malhash.cli as entry
    from malhash.core import FileResult, FileTarget, HashValue

    output = tmp_path / "partial.json"
    sample = tmp_path / "sample"
    digest = hashlib.sha256(b"").hexdigest()

    def timed_results(*args, cancel, **kwargs):
        yield FileResult(FileTarget(sample, Path(sample.name)), {"sha256": HashValue(digest)})
        signal.raise_signal(signal.SIGINT)
        assert cancel.is_set()

    monkeypatch.setattr(entry, "process_paths", timed_results)
    code = entry.main(
        [str(sample), "--algorithms", "sha256", "--format", "json", "-o", str(output)]
    )
    assert code == 130
    assert json.loads(output.read_bytes())[0]["hashes"]["sha256"]["value"] == digest
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "Processing cancelled" in captured.err


def test_jsonl_writes_a_record_before_fetching_the_next(tmp_path):
    from malhash.core import FileResult, FileTarget
    from malhash.exporters import write_report

    stream = io.StringIO()

    def records():
        yield FileResult(FileTarget(tmp_path / "one", Path("one")))
        assert json.loads(stream.getvalue())["name"] == "one"
        yield FileResult(FileTarget(tmp_path / "two", Path("two")))

    write_report(records(), stream, format="jsonl")
    assert [json.loads(line)["name"] for line in stream.getvalue().splitlines()] == ["one", "two"]
