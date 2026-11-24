"""Check the files users receive from a release."""

import hashlib
import shutil
import subprocess
import sys
import tomllib
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "release_assets.py"


def test_public_archives_and_repeatable_checksums(tmp_path):
    build = tmp_path / "dist"
    release = tmp_path / "release"
    build.mkdir()
    with (ROOT / "pyproject.toml").open("rb") as project:
        stem = "malhash-" + tomllib.load(project)["project"]["version"]
    wheel = f"{stem}-py3-none-any.whl"
    members = {
        "MalHash-Windows-x64-Portable.zip": "MalHash.exe",
        "MalHash-Windows-x64-Setup.zip": "MalHash-Setup.exe",
        wheel + ".zip": wheel,
    }
    for name in members.values():
        (build / name).write_bytes(name.encode())
    platform = f"{stem}-windows-x64.zip"
    with zipfile.ZipFile(build / platform, "w") as archive:
        archive.writestr("test.txt", b"native bundle")
    (build / "private.qt-sources.json").write_text("[]", encoding="utf-8")
    subprocess.run([sys.executable, SCRIPT, "package", build, release], check=True)
    assert {path.name for path in release.iterdir()} == {*members, platform}
    assert (release / platform).read_bytes() == (build / platform).read_bytes()
    for name, member in members.items():
        with zipfile.ZipFile(release / name) as archive:
            assert archive.namelist() == [member]
            assert archive.read(member) == (build / member).read_bytes()

    expected = {
        name: hashlib.sha256((release / name).read_bytes()).hexdigest()
        for name in [*members, platform]
    }
    for _ in range(2):
        subprocess.run([sys.executable, SCRIPT, "checksums", release, "--archive"], check=True)
        with zipfile.ZipFile(release / "SHA256SUMS.zip") as archive:
            assert archive.namelist() == ["SHA256SUMS"]
            lines = archive.read("SHA256SUMS").decode().splitlines()
        entries = [line.split("  ", 1) for line in lines]
        assert {name: digest for digest, name in entries} == expected
    assert {path.name for path in release.iterdir()} == {*expected, "SHA256SUMS.zip"}


def test_incomplete_windows_build_creates_no_downloads(tmp_path):
    build = tmp_path / "dist"
    release = tmp_path / "release"
    build.mkdir()
    (build / "MalHash.exe").write_bytes(b"portable")
    result = subprocess.run(
        [sys.executable, SCRIPT, "package", build, release], capture_output=True
    )
    assert result.returncode != 0
    assert not list(release.glob("*"))


@pytest.mark.parametrize("arch", ["x64", "arm64"])
def test_macos_cli_survives_gui_build_and_ships_next_to_app(tmp_path, monkeypatch, arch):
    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    import build_release

    target = f"macos-{arch}"
    dist = tmp_path / "dist"
    native = dist / f"native-{target}"
    native.mkdir(parents=True)
    monkeypatch.setattr(build_release, "ROOT", tmp_path)
    monkeypatch.setattr(build_release, "DIST", dist)
    monkeypatch.setattr(build_release, "qt_source_urls", lambda: [])
    monkeypatch.setattr(build_release, "add_licenses", lambda *_: None)
    paths = []
    run = subprocess.run

    def build_fixture(command, **kwargs):
        if command[0] == "ditto":
            if sys.platform == "darwin":
                return run(command, **kwargs)
            folder, output = map(Path, command[-2:])
            shutil.make_archive(str(output.with_suffix("")), "zip", folder.parent, folder.name)
            return
        output, work, spec = (
            Path(command[command.index(flag) + 1])
            for flag in ("--distpath", "--workpath", "--specpath")
        )
        paths.append([str(p).casefold() for p in (output, work, spec)])
        name = command[command.index("--name") + 1]
        output.mkdir(parents=True, exist_ok=True)
        if "--windowed" in command:
            (output / name).mkdir()  # PyInstaller COLLECT precedes BUNDLE.
            inner = output / f"{name}.app" / "Contents" / "MacOS"
            inner.mkdir(parents=True)
            (inner / name).write_bytes(b"gui")
        else:
            (output / name).write_bytes(b"cli")

    monkeypatch.setattr(build_release.subprocess, "run", build_fixture)
    executables = {
        kind: build_release.build(kind, target, "macos", native) for kind in ("cli", "gui")
    }
    assert all(left != right for left, right in zip(*paths, strict=True))
    bundle = build_release.archive(executables, native, target, "macos", "1.0.0")
    with zipfile.ZipFile(bundle) as archive:
        assert archive.read(f"{bundle.stem}/malhash-{target}") == b"cli"
        assert (
            archive.read(f"{bundle.stem}/MalHash-{target}.app/Contents/MacOS/MalHash-{target}")
            == b"gui"
        )


def test_release_jobs_require_their_own_inputs(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    import release_assets

    stem = f"malhash-{release_assets.package_version()}"
    source = tmp_path / "dist"
    source.mkdir()
    for target, suffix in release_assets.NATIVE_SUFFIXES.items():
        (source / f"{stem}-{target}{suffix}").write_bytes(target.encode())
    with pytest.raises(ValueError, match="MalHash.exe"):
        release_assets.package_archives(source, tmp_path / "windows", "windows-x64")
    with pytest.raises(ValueError, match="wheel"):
        release_assets.package_archives(source, tmp_path / "python", "python")
    for name in ("MalHash.exe", "MalHash-Setup.exe"):
        (source / name).write_bytes(name.encode())
    for target, suffix in release_assets.NATIVE_SUFFIXES.items():
        out = tmp_path / target
        release_assets.package_archives(source, out, target)
        expected = {f"{stem}-{target}{suffix}"}
        if target == "windows-x64":
            expected.update(release_assets.WINDOWS_ZIPS.values())
        assert {path.name for path in out.iterdir()} == expected
    (source / f"{stem}-py3-none-any.whl").write_bytes(b"wheel")
    with pytest.raises(ValueError, match=r"\.tar\.gz"):
        release_assets.package_archives(source, tmp_path / "python", "python")
