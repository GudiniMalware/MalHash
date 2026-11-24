"""Headless command line for MalHash."""

import argparse
import os
import re
import signal
import sys
from collections import Counter
from collections.abc import Iterator, Sequence
from contextlib import closing, contextmanager, nullcontext
from pathlib import Path
from threading import Event, current_thread, main_thread
from time import monotonic

from malhash import __version__
from malhash.core import (
    ALGORITHMS,
    DEFAULT_ALGORITHMS,
    METADATA_FIELDS,
    FileResult,
    ScanOptions,
    process_paths,
)
from malhash.exporters import FORMATS, ExportConflict, display_text, report_file, write_report
from malhash.l10n import LANGUAGES, error_text, translate

_MAX_WORKERS = 64
_SIZE_UNITS = {
    "": 1,
    "b": 1,
    "kb": 1000,
    "mb": 1000**2,
    "gb": 1000**3,
    "tb": 1000**4,
    "kib": 1024,
    "mib": 1024**2,
    "gib": 1024**3,
    "tib": 1024**4,
}
_PARSER_ERRORS = (
    (r"unrecognized arguments: (.*)", "unknown_args"),
    (r"the following arguments are required: (.*)", "required_args"),
    (r"argument (.*): expected one argument", "expected_arg"),
    (r"argument (.*): not allowed with argument (.*)", "incompatible_args"),
    (r"argument (.*): ignored explicit argument (.*)", "unexpected_value"),
)


class _Parser(argparse.ArgumentParser):
    def __init__(self, language: str) -> None:
        super().__init__(
            prog="malhash",
            description=translate(language, "cli.description"),
            allow_abbrev=False,
            add_help=False,
        )
        self.language = language

    def format_usage(self) -> str:
        return super().format_usage().replace("usage: ", translate(self.language, "cli.usage"), 1)

    def format_help(self) -> str:
        return super().format_help().replace("usage: ", translate(self.language, "cli.usage"), 1)

    def error(self, message: str) -> None:
        for pattern, key in _PARSER_ERRORS:
            if match := re.fullmatch(pattern, message, re.DOTALL):
                values = match.groups()
                message = translate(self.language, f"cli.{key}", detail=values[0], other=values[-1])
                break
        self.print_usage(sys.stderr)
        prefix = translate(self.language, "status.error")
        self.exit(2, f"malhash: {prefix}: {display_text(message)}\n")


def _language(arguments: Sequence[str]) -> str:
    language = "en"
    for index, item in enumerate(arguments):
        if item == "--":
            break
        if item.startswith("--lang="):
            language = item.partition("=")[2]
        elif item == "--lang" and index + 1 < len(arguments):
            language = arguments[index + 1]
    return language if language in LANGUAGES else "en"


def _parser(language: str) -> _Parser:
    parser = _Parser(language)

    def t(key: str, **values: object) -> str:
        return translate(language, f"cli.{key}", **values)

    parser._positionals.title = t("positionals")
    parser._optionals.title = t("options")
    parser.add_argument("paths", metavar=t("paths_label"), nargs="*", help=t("paths"))
    parser.add_argument("-h", "--help", action="help", help=t("help"))
    parser.add_argument(
        "--version", action="version", version=f"MalHash {__version__}", help=t("version")
    )
    parser.add_argument("-r", "--recursive", action="store_true", help=t("recursive"))
    parser.add_argument(
        "--algorithms",
        metavar="LIST",
        default=",".join(DEFAULT_ALGORITHMS),
        help=t("algorithms", names=", ".join(ALGORITHMS)),
    )
    parser.add_argument(
        "--metadata",
        metavar="LIST",
        default="",
        help=t("metadata", names=", ".join(METADATA_FIELDS)),
    )
    parser.add_argument(
        "--format", metavar="FORMAT", default="txt", help=t("format", names=", ".join(FORMATS))
    )
    parser.add_argument("-o", "--output", metavar="FILE", type=Path, help=t("output"))
    parser.add_argument("--lang", metavar="LANG", default="en", help=t("lang"))
    parser.add_argument("--report-lang", metavar="LANG", help=t("report_lang"))
    names = parser.add_mutually_exclusive_group()
    names.add_argument(
        "--relative-paths",
        dest="name_mode",
        action="store_const",
        const="relative",
        help=t("relative_paths"),
    )
    names.add_argument(
        "--absolute-paths",
        dest="name_mode",
        action="store_const",
        const="absolute",
        help=t("absolute_paths"),
    )
    parser.set_defaults(name_mode="filename")
    parser.add_argument(
        "--extension", metavar="EXT", action="append", default=[], help=t("extension")
    )
    parser.add_argument("--include", metavar="GLOB", action="append", default=[], help=t("include"))
    parser.add_argument("--exclude", metavar="GLOB", action="append", default=[], help=t("exclude"))
    parser.add_argument(
        "--exclude-dir", metavar="GLOB", action="append", default=[], help=t("exclude_dir")
    )
    parser.add_argument("--max-size", metavar="SIZE", help=t("max_size"))
    parser.add_argument(
        "--workers", metavar="N", default="1", help=t("workers", limit=_MAX_WORKERS)
    )
    verbosity = parser.add_mutually_exclusive_group()
    verbosity.add_argument("--quiet", action="store_true", help=t("quiet"))
    verbosity.add_argument("--verbose", action="store_true", help=t("verbose"))
    parser.add_argument("--no-color", action="store_true", help=t("no_color"))
    return parser


def _validate(parser: _Parser, args: argparse.Namespace) -> None:
    def fail(key: str, **values: object) -> None:
        parser.error(translate(parser.language, f"cli.{key}", **values))

    if not args.paths:
        fail("required_args", detail=translate(parser.language, "cli.paths_label"))
    for language in (args.lang, args.report_lang if args.report_lang is not None else args.lang):
        if language not in LANGUAGES:
            fail("invalid_language", value=language)
    if args.format not in FORMATS:
        fail("invalid_format", value=args.format)
    algorithms = tuple(dict.fromkeys(part.strip().lower() for part in args.algorithms.split(",")))
    if any(algorithm not in ALGORITHMS for algorithm in algorithms):
        fail("invalid_algorithms", value=args.algorithms)
    if args.format == "hash-only" and len(algorithms) != 1:
        fail("hash_only_count")
    args.algorithms = algorithms
    if args.metadata == "all":
        args.metadata = METADATA_FIELDS
    elif args.metadata:
        fields = tuple(dict.fromkeys(part.strip().lower() for part in args.metadata.split(",")))
        if any(field not in METADATA_FIELDS for field in fields):
            fail("invalid_metadata", value=args.metadata)
        args.metadata = fields
    else:
        args.metadata = ()
    if not re.fullmatch(r"[0-9]{1,2}", args.workers) or not 1 <= int(args.workers) <= _MAX_WORKERS:
        fail("invalid_workers", limit=_MAX_WORKERS)
    args.workers = int(args.workers)
    if args.max_size is not None:
        size = re.fullmatch(r"\s*([0-9]{1,19})\s*([a-zA-Z]*)\s*", args.max_size)
        if not size or size[2].lower() not in _SIZE_UNITS:
            fail("invalid_size", value=args.max_size)
        args.max_size = int(size[1]) * _SIZE_UNITS[size[2].lower()]


@contextmanager
def _interrupt_event() -> Iterator[Event]:
    cancel = Event()
    if current_thread() is not main_thread():
        yield cancel
        return
    previous = signal.signal(signal.SIGINT, lambda *_: cancel.set())
    try:
        yield cancel
    finally:
        signal.signal(signal.SIGINT, previous)


def _diagnostic(args: argparse.Namespace, message: str, level: str = "") -> None:
    if level:
        label = translate(args.lang, f"status.{level}")
        if not args.no_color and "NO_COLOR" not in os.environ and sys.stderr.isatty():
            color = "31" if level == "error" else "33"
            label = f"\x1b[{color}m{label}\x1b[0m"
        message = f"{label}: {message}"
    print(message, file=sys.stderr)


def _collect(
    results: Iterator[FileResult], args: argparse.Namespace, counts: Counter[str]
) -> Iterator[FileResult]:
    unavailable: set[str] = set()
    for result in results:
        counts[result.status] += 1
        path = display_text(str(result.target.path))
        if result.status == "error":
            _diagnostic(
                args, f"{path}: {error_text(args.lang, result.issue or 'io_error')}", "error"
            )
        elif args.verbose:
            disposition = (
                error_text(args.lang, result.issue)
                if result.issue
                else translate(args.lang, f"status.{result.status}")
            )
            _diagnostic(args, f"{path}: {disposition}")
        for algorithm, digest in result.hashes.items():
            if digest.status == "unavailable" and algorithm not in unavailable and not args.quiet:
                unavailable.add(algorithm)
                _diagnostic(
                    args,
                    translate(args.lang, "cli.algorithm_unavailable", name=algorithm),
                    "warning",
                )
        yield result


def _run(args: argparse.Namespace) -> int:
    counts: Counter[str] = Counter()
    started = monotonic()
    paths = [Path(path) for path in args.paths]
    destination = (
        report_file(args.output, protected_paths=paths) if args.output else nullcontext(sys.stdout)
    )
    with _interrupt_event() as cancel, destination as stream:
        ignored = (args.output, Path(stream.name)) if args.output else ()
        scan = ScanOptions(
            recursive=args.recursive,
            extensions=tuple(args.extension),
            include=tuple(args.include),
            exclude=tuple(args.exclude),
            exclude_dirs=tuple(args.exclude_dir),
            max_size=args.max_size,
            ignored_paths=ignored,
        )
        results = process_paths(
            paths,
            algorithms=args.algorithms,
            metadata=args.metadata,
            scan_options=scan,
            workers=args.workers,
            cancel=cancel,
        )
        with closing(results):
            write_report(
                _collect(results, args, counts),
                stream,
                format=args.format,
                language=args.report_lang or args.lang,
                name_mode=args.name_mode,
                algorithms=args.algorithms,
                metadata=args.metadata,
            )
        stream.flush()
    if args.verbose:
        _diagnostic(
            args,
            translate(
                args.lang,
                "cli.summary",
                ok=counts["ok"],
                skipped=counts["skipped"],
                errors=counts["error"],
                seconds=monotonic() - started,
            ),
        )
    if cancel.is_set():
        if not args.quiet:
            _diagnostic(args, error_text(args.lang, "cancelled"), "warning")
        return 130
    return int(bool(counts["error"]))


def main(argv: Sequence[str] | None = None) -> int:
    """Run the command line and return a process exit code."""
    if argv is None:
        for stream in (sys.stdout, sys.stderr):
            if hasattr(stream, "reconfigure"):
                stream.reconfigure(encoding="utf-8", errors="backslashreplace", newline="")
    arguments = list(sys.argv[1:] if argv is None else argv)
    parser = _parser(_language(arguments))
    split = arguments.index("--") if "--" in arguments else len(arguments)
    try:
        args = parser.parse_intermixed_args(arguments[:split])
        args.paths.extend(arguments[split + 1 :])
        _validate(parser, args)
    except SystemExit as exit_request:
        return int(exit_request.code)
    try:
        return _run(args)
    except BrokenPipeError:
        if sys.stdout is sys.__stdout__:
            with open(os.devnull, "w") as sink:
                os.dup2(sink.fileno(), sys.stdout.fileno())
        return 0
    except KeyboardInterrupt:
        return 130
    except ExportConflict:
        _diagnostic(
            args, error_text(args.lang, "output_conflict", display_text(str(args.output))), "error"
        )
        return 2
    except OSError as error:
        code = "permission_denied" if isinstance(error, PermissionError) else "io_error"
        path = error.filename or args.output
        _diagnostic(
            args, error_text(args.lang, code, display_text(str(path)) if path else ""), "error"
        )
        return 2
