"""Streaming report formats shared by CLI, GUI and Python callers."""

import csv
import json
import re
from collections.abc import Iterable, Iterator, Sequence
from typing import Literal, TextIO

from malhash.core import DEFAULT_ALGORITHMS, FileResult
from malhash.l10n import translate

from .files import ExportConflict, report_file

__all__ = ["FORMATS", "ExportConflict", "NameMode", "format_card", "report_file", "write_report"]

FORMATS = ("txt", "csv", "json", "jsonl", "tsv", "markdown", "hash-only")
NameMode = Literal["filename", "relative", "absolute"]

_UNSAFE_TEXT = re.compile(r"[\x00-\x1f\x7f-\x9f\u2028-\u202e\u2066-\u2069\ud800-\udfff]")
_MARKDOWN = re.compile(r"([\\`*_{}\[\]()<>#+.!|~&-])")


def display_text(value: str) -> str:
    """Keep a filename or diagnostic on one line and inert in a terminal."""
    return _UNSAFE_TEXT.sub(lambda match: match[0].encode("unicode_escape").decode("ascii"), value)


def _name(result: FileResult, mode: NameMode) -> str:
    if mode == "absolute":
        return str(result.target.path)
    if mode == "relative":
        return result.target.relative_path.as_posix()
    return result.target.name


def format_card(
    result: FileResult,
    *,
    language: str = "en",
    name_mode: NameMode = "filename",
    algorithms: Sequence[str] = DEFAULT_ALGORITHMS,
    metadata: Sequence[str] = (),
) -> str:
    """Render one card without a final newline."""
    lines = [f"{translate(language, 'report.name')}: {display_text(_name(result, name_mode))}"]
    for algorithm in algorithms:
        digest = result.hashes.get(algorithm)
        if digest is not None:
            value = digest.value
            if value is None:
                value = translate(language, f"status.{digest.status}")
            lines.append(f"{algorithm}: {value}")
    for field in metadata:
        if field in result.metadata:
            label = translate(language, f"metadata.{field}")
            lines.append(f"{label}: {display_text(str(result.metadata[field]))}")
    return "\n".join(lines)


def _records(
    results: Iterable[FileResult],
    name_mode: NameMode,
    algorithms: Sequence[str],
    metadata: Sequence[str],
) -> Iterator[dict]:
    for result in results:
        if result.status != "ok" and not result.hashes:
            continue
        yield {
            "name": _name(result, name_mode),
            "hashes": {
                algorithm: {"value": digest.value, "status": digest.status}
                for algorithm in algorithms
                if (digest := result.hashes.get(algorithm)) is not None
            },
            "metadata": {
                field: result.metadata[field] for field in metadata if field in result.metadata
            },
        }


def _spreadsheet_safe(value: object) -> object:
    if isinstance(value, str) and (
        value.startswith(("\t", "\r", "\n"))
        or value.lstrip().startswith(("=", "+", "-", "@", "＝", "＋", "－", "＠"))
    ):
        return "'" + value
    return value


def _markdown_cell(value: object) -> str:
    if value is None:
        return ""
    return _MARKDOWN.sub(r"\\\1", display_text(str(value)))


def _write_table(
    records: Iterable[dict],
    stream: TextIO,
    format: str,
    algorithms: Sequence[str],
    metadata: Sequence[str],
) -> None:
    header = ["name", *algorithms, *(f"{name}_status" for name in algorithms), *metadata]
    if format == "markdown":

        def write_row(row):
            stream.write("| " + " | ".join(map(_markdown_cell, row)) + " |\n")

        write_row(header)
        stream.write("| " + " | ".join(["---"] * len(header)) + " |\n")
    else:
        writer = csv.writer(
            stream, delimiter="," if format == "csv" else "\t", lineterminator="\r\n"
        )
        write_row = writer.writerow
        write_row(header)

    for record in records:
        hashes = record["hashes"]
        fields = [record["name"], *(record["metadata"].get(field) for field in metadata)]
        if format in ("csv", "tsv"):
            fields = [_spreadsheet_safe(field) for field in fields]
        write_row(
            [
                fields[0],
                *(hashes.get(name, {}).get("value") for name in algorithms),
                *(hashes.get(name, {}).get("status") for name in algorithms),
                *fields[1:],
            ]
        )


def write_report(
    results: Iterable[FileResult],
    stream: TextIO,
    *,
    format: str = "txt",
    language: str = "en",
    name_mode: NameMode = "filename",
    algorithms: Sequence[str] = DEFAULT_ALGORITHMS,
    metadata: Sequence[str] = (),
) -> None:
    """Write results incrementally to a text stream (open with newline='' for CSV/TSV)."""
    if format not in FORMATS:
        raise ValueError(f"Unknown report format: {format}")
    if name_mode not in ("filename", "relative", "absolute"):
        raise ValueError(f"Unknown name mode: {name_mode}")
    if format == "hash-only":
        if len(algorithms) != 1:
            raise ValueError("hash-only requires one algorithm")
        algorithm = algorithms[0]
        for result in results:
            digest = result.hashes.get(algorithm)
            if digest is not None and digest.status == "ok" and digest.value is not None:
                stream.write(digest.value + "\n")
        return
    if format == "txt":
        separator = ""
        for result in results:
            if result.status != "ok" and not result.hashes:
                continue
            card = format_card(
                result,
                language=language,
                name_mode=name_mode,
                algorithms=algorithms,
                metadata=metadata,
            )
            stream.write(separator + card + "\n")
            separator = "\n"
        return
    records = _records(results, name_mode, algorithms, metadata)
    if format == "jsonl":
        for record in records:
            stream.write(json.dumps(record, allow_nan=False) + "\n")
    elif format == "json":
        stream.write("[")
        separator = "\n"
        for record in records:
            stream.write(separator + "  " + json.dumps(record, allow_nan=False))
            separator = ",\n"
        stream.write("\n]\n" if separator == ",\n" else "]\n")
    else:
        _write_table(records, stream, format, algorithms, metadata)
