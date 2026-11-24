"""Validate release tags and prepare checksums and exact Qt source assets."""

import argparse
import hashlib
import json
import os
import re
import shutil
import tomllib
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
QT_SOURCE = re.compile(
    r"https://download\.qt\.io/official_releases/"
    r"(?:qt/\d+\.\d+/\d+\.\d+\.\d+/single/qt-everywhere-src-\d+\.\d+\.\d+"
    r"|QtForPython/pyside6/PySide6-\d+\.\d+\.\d+(?:\.\d+)?-src/"
    r"pyside-setup-everywhere-src-\d+\.\d+\.\d+(?:\.\d+)?)\.tar\.xz"
)
HASH_FIELD = re.compile(r'<hash type="sha-256">([a-fA-F0-9]{64})</hash>')


def sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def download_qt_sources(directory: Path) -> None:
    manifests = sorted(directory.glob("*.qt-sources.json"))
    if not manifests:
        raise ValueError(f"No Qt source manifest in {directory}")
    source_urls = {url for manifest in manifests for url in json.loads(manifest.read_text("utf-8"))}
    if not source_urls:
        raise ValueError("Qt source manifests contain no URLs")
    for url in sorted(source_urls):
        if not QT_SOURCE.fullmatch(url):
            raise ValueError(f"Unexpected Qt source URL {url!r}")
        output = directory / url.rsplit("/", 1)[1]
        with urllib.request.urlopen(f"{url}.meta4", timeout=30) as metadata:
            metadocument = metadata.read(1_000_000).decode("utf-8")
        match = HASH_FIELD.search(metadocument)
        if not match:
            raise ValueError(f"No SHA-256 in metadata for {output.name}")
        expected = match[1].lower()
        if output.is_file() and sha256(output) == expected:
            continue
        temporary = output.with_suffix(output.suffix + ".part")
        try:
            with urllib.request.urlopen(url, timeout=120) as source, temporary.open("wb") as sink:
                shutil.copyfileobj(source, sink, length=1024 * 1024)
            if sha256(temporary) != expected:
                raise ValueError(f"Qt source checksum mismatch for {output.name}")
            temporary.replace(output)
        finally:
            temporary.unlink(missing_ok=True)


def write_checksums(directory: Path) -> None:
    extensions = (".zip", ".tar.gz", ".tar.xz", ".whl")
    archives = sorted(
        path for path in directory.iterdir() if path.is_file() and path.name.endswith(extensions)
    )
    if not archives:
        raise ValueError(f"No release assets in {directory}")
    sums = "".join(f"{sha256(path)}  {path.name}\n" for path in archives)
    (directory / "SHA256SUMS").write_text(sums, encoding="utf-8", newline="\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    verify = commands.add_parser("verify-tag", help="compare a release tag to the Python version")
    verify.add_argument("tag", nargs="?", default=os.environ.get("GITHUB_REF_NAME"))
    checksums = commands.add_parser("checksums", help="write SHA256SUMS for platform releases")
    checksums.add_argument("directory", type=Path)
    checksums.add_argument("--download-qt-source", action="store_true")
    args = parser.parse_args()
    if args.command == "verify-tag":
        with (ROOT / "pyproject.toml").open("rb") as project:
            expected = f"v{tomllib.load(project)['project']['version']}"
        if args.tag != expected:
            parser.error(f"release tag {args.tag!r} must match package version {expected!r}")
    else:
        if args.download_qt_source:
            download_qt_sources(args.directory)
        write_checksums(args.directory)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
