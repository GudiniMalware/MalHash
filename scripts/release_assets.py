"""Package GitHub release archives and verify their versions and checksums."""

import argparse
import hashlib
import json
import os
import re
import shutil
import tomllib
import urllib.request
import zipfile
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[1]
ARCHIVE_SUFFIXES = (".zip", ".tar.gz", ".tar.xz")
CHECKSUM_ZIP = "SHA256SUMS.zip"
WINDOWS_ZIPS = {
    "MalHash.exe": "MalHash-Windows-x64-Portable.zip",
    "MalHash-Setup.exe": "MalHash-Windows-x64-Setup.zip",
}
NATIVE_SUFFIXES = {
    "windows-x64": ".zip",
    "linux-x64": ".tar.gz",
    "linux-arm64": ".tar.gz",
    "macos-x64": ".zip",
    "macos-arm64": ".zip",
}
QT_SOURCE = re.compile(
    r"https://download\.qt\.io/official_releases/"
    r"(?:qt/\d+\.\d+/\d+\.\d+\.\d+/single/qt-everywhere-src-\d+\.\d+\.\d+"
    r"|QtForPython/pyside6/PySide6-\d+\.\d+\.\d+(?:\.\d+)?-src/"
    r"pyside-setup-everywhere-src-\d+\.\d+\.\d+(?:\.\d+)?)\.tar\.xz"
)
HASH_FIELD = re.compile(r'<hash type="sha-256">([a-fA-F0-9]{64})</hash>')


def package_version() -> str:
    with (ROOT / "pyproject.toml").open("rb") as project:
        return tomllib.load(project)["project"]["version"]


def package_archives(source: Path, destination: Path, target: str | None = None) -> None:
    if source.resolve() == destination.resolve():
        raise ValueError("Choose a release directory separate from the build directory")
    stem = f"malhash-{package_version()}"
    archives = [
        *source.glob(f"{stem}.tar.gz"),
        *source.glob(f"{stem}-*.tar.gz"),
        *source.glob(f"{stem}-*.zip"),
    ]
    wheels = sorted(source.glob(f"{stem}-*.whl"))
    if target == "python":
        archives = [source / f"{stem}.tar.gz"]
        if not wheels:
            raise ValueError(f"No {stem} wheel in {source}")
    elif target is not None:
        archives = [source / f"{stem}-{target}{NATIVE_SUFFIXES[target]}"]
        wheels = []
    wrap = {wheel: wheel.name + ".zip" for wheel in wheels}
    windows_inputs = (source / f"{stem}-windows-x64.zip").is_file() or any(
        (source / name).is_file() for name in WINDOWS_ZIPS
    )
    if target == "windows-x64" or (target is None and windows_inputs):
        wrap.update({source / name: archive for name, archive in WINDOWS_ZIPS.items()})
    missing = [path.name for path in (*archives, *wrap) if not path.is_file()]
    if missing:
        raise ValueError(f"Missing release inputs in {source}: {', '.join(missing)}")
    if not archives and not wrap:
        raise ValueError(f"No {stem} release inputs in {source}")
    destination.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=destination) as temporary:
        staging = Path(temporary)
        for path in archives:
            shutil.copy2(path, staging / path.name)
        for path, name in wrap.items():
            with zipfile.ZipFile(staging / name, "w", zipfile.ZIP_DEFLATED) as bundle:
                bundle.write(path, arcname=path.name)
        for path in sorted(staging.iterdir()):
            output = destination / path.name
            path.replace(output)
            print(output)


def sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def download_qt_sources(directory: Path, manifest_directory: Path | None = None) -> None:
    manifest_directory = manifest_directory or directory
    manifests = sorted(manifest_directory.glob("*.qt-sources.json"))
    if not manifests:
        raise ValueError(f"No Qt source manifest in {manifest_directory}")
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


def write_checksums(directory: Path, *, as_zip: bool = False) -> None:
    extensions = ARCHIVE_SUFFIXES if as_zip else (*ARCHIVE_SUFFIXES, ".exe", ".whl")
    archives = sorted(
        path
        for path in directory.iterdir()
        if path.is_file() and path.name != CHECKSUM_ZIP and path.name.endswith(extensions)
    )
    if not archives:
        raise ValueError(f"No release assets in {directory}")
    sums = "".join(f"{sha256(path)}  {path.name}\n" for path in archives)
    if as_zip:
        with TemporaryDirectory(dir=directory) as temporary:
            output = Path(temporary) / CHECKSUM_ZIP
            with zipfile.ZipFile(output, "w") as bundle:
                bundle.writestr(zipfile.ZipInfo("SHA256SUMS"), sums, zipfile.ZIP_DEFLATED)
            output.replace(directory / CHECKSUM_ZIP)
    else:
        (directory / "SHA256SUMS").write_text(sums, encoding="utf-8", newline="\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    verify = commands.add_parser("verify-tag", help="compare a release tag to the Python version")
    verify.add_argument("tag", nargs="?", default=os.environ.get("GITHUB_REF_NAME"))
    package = commands.add_parser("package", help="stage distributable archives for GitHub")
    package.add_argument("source", type=Path)
    package.add_argument("output", type=Path)
    package.add_argument(
        "--target", choices=(*NATIVE_SUFFIXES, "python"), help="require all downloads for this job"
    )
    checksums = commands.add_parser("checksums", help="write SHA256SUMS for platform releases")
    checksums.add_argument("directory", type=Path)
    checksums.add_argument("--download-qt-source", action="store_true")
    checksums.add_argument(
        "--qt-manifests", type=Path, help="directory containing Qt source manifests"
    )
    checksums.add_argument(
        "--archive", action="store_true", help="write SHA256SUMS.zip for archives"
    )
    args = parser.parse_args()
    if args.command == "verify-tag":
        expected = f"v{package_version()}"
        if args.tag != expected:
            parser.error(f"release tag {args.tag!r} must match package version {expected!r}")
    elif args.command == "package":
        package_archives(args.source, args.output, args.target)
    else:
        if args.download_qt_source:
            download_qt_sources(args.directory, args.qt_manifests)
        write_checksums(args.directory, as_zip=args.archive)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
