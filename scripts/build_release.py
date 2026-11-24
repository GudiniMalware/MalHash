"""Build and smoke-test native MalHash applications for the current host."""

import argparse
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
from importlib import metadata
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "assets"
DIST = ROOT / "dist"
RUNTIME_DEPENDENCIES = ("ppdeep", "blake3", "pefile", "PyInstaller")
GUI_DEPENDENCIES = ("PySide6-Essentials", "shiboken6")
OS_NAMES = {"Windows": "windows", "Linux": "linux", "Darwin": "macos"}
ARCH_NAMES = {"amd64": "x64", "x86_64": "x64", "aarch64": "arm64", "arm64": "arm64"}


def run_pyinstaller(arguments: list[str], output: Path, component: str) -> None:
    command = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--clean",
        "--noconfirm",
        "--distpath",
        str(output),
        "--workpath",
        str(ROOT / "build" / "pyinstaller" / component / "work"),
        *arguments,
    ]
    environment = os.environ.copy()
    environment.pop("PYTHONPATH", None)
    environment.pop("PYTHONHOME", None)
    if sys.platform == "win32":
        windows = Path(os.environ["SystemRoot"])
        # Resolve native dependencies against Windows and the build environment.
        directories = (windows / "System32", windows, Path(sys.executable).parent)
        environment["PATH"] = os.pathsep.join(map(str, directories))
    subprocess.run(command, check=True, cwd=ROOT, env=environment)


def build(component: str, target: str, system: str, output: Path) -> Path:
    gui = component == "gui"
    mac_app = gui and system == "macos"
    name = f"{'MalHash' if mac_app else 'malhash-gui' if gui else 'malhash'}-{target}"
    if system == "macos":
        # The GUI's intermediate COLLECT directory case-folds to the CLI filename.
        output = output / component
    arguments = [
        "--noupx",
        "--name",
        name,
        "--specpath",
        str(ROOT / "build" / "pyinstaller" / component / "spec"),
        "--paths",
        str(ROOT / "src"),
        "--collect-data",
        "malhash",
        "--exclude-module",
        "ssdeep",
    ]
    for module in ("ppdeep", "blake3", "pefile", "tlsh"):
        arguments += ["--hidden-import", module]
    if gui:
        arguments += ["--onedir", "--windowed"]
        if mac_app:
            arguments += ["--icon", str(ASSETS / "malhash.icns")]
            arguments += ["--osx-bundle-identifier", "org.malhash.app"]
    else:
        arguments += ["--onefile", "--console", "--exclude-module", "malhash.gui"]
        for module in ("PySide6", "shiboken6", "tkinter"):
            arguments += ["--exclude-module", module]
    arguments.append(str(ROOT / "scripts" / f"{component}_entry.py"))
    run_pyinstaller(arguments, output, component)

    suffix = ".exe" if system == "windows" else ""
    if mac_app:
        return output / f"{name}.app" / "Contents" / "MacOS" / name
    if gui:
        return output / name / f"{name}{suffix}"
    return output / f"{name}{suffix}"


def smoke_test(executable: Path, component: str, *, single_file: bool = False) -> None:
    workspace = ROOT / "build"
    workspace.mkdir(exist_ok=True)
    with TemporaryDirectory(prefix="malhash-smoke-", dir=workspace) as temporary:
        sandbox = Path(temporary).resolve()
        env = os.environ.copy()
        env.pop("PYTHONPATH", None)
        env.pop("PYTHONHOME", None)
        if sys.platform == "win32":
            windows = Path(os.environ["SystemRoot"])
            env["PATH"] = os.pathsep.join(map(str, (windows / "System32", windows)))
            env.pop("QT_PLUGIN_PATH", None)
            env.pop("QT_QPA_PLATFORM_PLUGIN_PATH", None)
        if single_file:
            download = sandbox / "MalHash download"
            download.mkdir()
            executable = Path(shutil.copy2(executable, download))
        env.update(QT_QPA_PLATFORM="offscreen", MALHASH_SMOKE_SETTINGS_DIR=str(sandbox))
        if sys.platform == "win32" and component == "gui":
            env["QT_QPA_PLATFORM"] = "windows"
        command = [str(executable)]
        if component == "gui":
            try:
                subprocess.run(
                    [*command, "--smoke-test"], check=True, env=env, cwd=sandbox, timeout=60
                )
            except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
                log = sandbox / "gui-smoke.log"
                details = log.read_text(encoding="utf-8") if log.is_file() else str(error)
                raise RuntimeError(f"Frozen GUI smoke test failed:\n{details}") from error
            return

        subprocess.run([*command, "--version"], check=True, env=env, cwd=sandbox, timeout=60)
        sample_bytes = hashlib.shake_256(b"MalHash release smoke test").digest(8192)
        sample = sandbox / "sample.bin"
        sample.write_bytes(sample_bytes)
        result = subprocess.run(
            [
                *command,
                str(sample),
                "--quiet",
                "--format",
                "json",
                "--algorithms",
                "sha256,ssdeep,blake3,tlsh",
            ],
            env=env,
            cwd=sandbox,
            capture_output=True,
            encoding="utf-8",
            timeout=60,
        )
        if result.returncode:
            raise RuntimeError(
                f"CLI smoke test exited {result.returncode}: {result.stderr.strip()}"
            )
        import blake3
        import ppdeep
        import tlsh

        fuzzy = tlsh.Tlsh()
        fuzzy.update(sample_bytes)
        fuzzy.final()
        fuzzy_digest = fuzzy.hexdigest()
        if len(fuzzy_digest) == 70:
            fuzzy_digest = f"T1{fuzzy_digest}"
        expected = {
            "sha256": hashlib.sha256(sample_bytes).hexdigest(),
            "ssdeep": ppdeep.hash(sample_bytes),
            "blake3": blake3.blake3(sample_bytes).hexdigest(),
            "tlsh": fuzzy_digest,
        }
        report = json.loads(result.stdout)
        if len(report) != 1 or report[0]["name"] != sample.name:
            raise RuntimeError(f"Unexpected CLI smoke test report {report!r}")
        for algorithm, digest in expected.items():
            if report[0]["hashes"].get(algorithm) != {"value": digest, "status": "ok"}:
                raise RuntimeError(f"CLI smoke test returned an unexpected {algorithm} result")


def copy_licenses(distribution: metadata.Distribution, destination: Path) -> None:
    for source in distribution.files or ():
        is_notice = source.name.upper().startswith(("LICENSE", "COPYING", "NOTICE"))
        if not is_notice and not source.name.endswith(".cyclonedx.json"):
            continue
        if source.name.startswith("LicenseRef-Qt-Commercial"):
            continue
        if not any(part.endswith(".dist-info") for part in source.parts):
            continue
        target = destination / source.name
        destination.mkdir(parents=True, exist_ok=True)
        shutil.copy2(distribution.locate_file(source), target)


def qt_source_urls() -> list[str]:
    from PySide6 import QtCore
    from PySide6 import __version__ as binding_version

    qt_version = QtCore.qVersion()
    if not re.fullmatch(r"\d+\.\d+\.\d+(?:\.\d+)?", binding_version):
        raise ValueError(f"Unexpected PySide6 version {binding_version!r}")
    if not re.fullmatch(r"\d+\.\d+\.\d+", qt_version):
        raise ValueError(f"Unexpected Qt version {qt_version!r}")
    base = "https://download.qt.io/official_releases"
    qt_minor = qt_version.rsplit(".", 1)[0]
    return [
        f"{base}/qt/{qt_minor}/{qt_version}/single/qt-everywhere-src-{qt_version}.tar.xz",
        f"{base}/QtForPython/pyside6/PySide6-{binding_version}-src/"
        f"pyside-setup-everywhere-src-{binding_version}.tar.xz",
    ]


def runtime_packages() -> tuple[str, ...]:
    # The x64 binding's cp312-abi3 wheels contain CPython 3.12-specific extensions.
    use_wheel = sys.version_info < (3, 13) and platform.machine().lower() in {"x86_64", "amd64"}
    tls_dependencies = ("tlsh-python", "nanobind") if use_wheel else ("py-tlsh",)
    return (*RUNTIME_DEPENDENCIES, *tls_dependencies)


def add_licenses(destination: Path, gui: bool, qt_sources: list[str]) -> None:
    for file in ("LICENSE", "docs/THIRD_PARTY_NOTICES.md", "docs/BUILDING.md"):
        shutil.copy2(ROOT / file, destination / Path(file).name)
    licenses = destination / "licenses"
    licenses.mkdir()
    python_version = platform.python_version()
    python_license = Path(sys.base_prefix) / "LICENSE.txt"
    if not python_license.is_file():
        python_license = ROOT / "docs" / "licenses" / "Python.txt"
    shutil.copy2(python_license, licenses / "Python.txt")
    standard_licenses = ["Apache-2.0.txt"]
    if gui:
        standard_licenses.extend(("GPL-3.0-only.txt", "LGPL-3.0-only.txt"))
    for file in standard_licenses:
        shutil.copy2(ROOT / "docs" / "licenses" / file, licenses / file)
    runtime = [*runtime_packages(), "typing_extensions", "altgraph"]
    if gui:
        runtime.extend(GUI_DEPENDENCIES)
    versions = [f"CPython {python_version}"]
    for package in runtime:
        try:
            distribution = metadata.distribution(package)
        except metadata.PackageNotFoundError:
            if package == "typing_extensions":
                continue
            raise
        versions.append(f"{package} {distribution.version}")
        copy_licenses(distribution, licenses / package)
    notes = ["# Build versions", "", *(f"- {version}" for version in versions)]
    notes.extend(
        (
            "",
            "# Upstream source",
            "",
            f"- https://www.python.org/downloads/release/python-{python_version.replace('.', '')}/",
        )
    )
    notes.extend(f"- {source}" for source in qt_sources)
    (destination / "BUILD_VERSIONS.md").write_text("\n".join(notes) + "\n", encoding="utf-8")


def inno_compiler() -> Path:
    configured = os.environ.get("ISCC") or shutil.which("ISCC.exe")
    if configured:
        return Path(configured)
    for location in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA"):
        if location not in os.environ:
            continue
        base = Path(os.environ[location])
        if location == "LOCALAPPDATA":
            base /= "Programs"
        for folder in ("Inno Setup 7", "Inno Setup 6"):
            candidate = base / folder / "ISCC.exe"
            if candidate.is_file():
                return candidate
    raise SystemExit("Windows GUI builds require Inno Setup 6.3+ or 7. Set ISCC to ISCC.exe.")


def smoke_installer(setup: Path) -> None:
    with TemporaryDirectory(prefix="installer-", dir=ROOT / "build") as temporary:
        target = Path(temporary).resolve() / "MalHash Desktop"
        quiet = ["/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART"]
        subprocess.run(
            [
                str(setup),
                *quiet,
                "/MALHASHBUILDTEST=1",
                "/NOICONS",
                "/NOCLOSEAPPLICATIONS",
                f"/DIR={target}",
            ],
            check=True,
            timeout=120,
        )
        uninstaller = target / "unins000.exe"
        try:
            smoke_test(target / "MalHash.exe", "gui")
        finally:
            subprocess.run([str(uninstaller), *quiet], check=True, timeout=120)
        if (target / "MalHash.exe").exists() or (target / "_internal").exists():
            raise RuntimeError("The Windows uninstaller left application files behind")


def build_windows_gui(output: Path, version: str, *, smoke: bool) -> Path:
    compiler = inno_compiler()
    with TemporaryDirectory(prefix="notices-", dir=output) as temporary:
        add_licenses(Path(temporary), True, qt_source_urls())
        run_pyinstaller(
            [str(ROOT / "scripts" / "windows_gui.spec"), "--", temporary], output, "gui"
        )
    folder = output / "malhash-gui-windows-x64"
    portable = output / "MalHash.exe"
    setup = output / "MalHash-Setup.exe"
    subprocess.run(
        [
            str(compiler),
            "/Q",
            f"/O{output}",
            f"/DAppVersion={version}",
            f"/DGuiDir={folder}",
            str(ROOT / "scripts" / "windows_setup.iss"),
        ],
        check=True,
        cwd=ROOT,
    )
    if smoke:
        smoke_test(portable, "gui", single_file=True)
        smoke_installer(setup)
    for artifact in (portable, setup):
        artifact.replace(DIST / artifact.name)
        print(DIST / artifact.name, flush=True)
    return folder / "malhash-gui-windows-x64.exe"


def archive(
    executables: dict[str, Path], output: Path, target: str, system: str, version: str
) -> Path:
    gui = "gui" in executables
    qt_sources = qt_source_urls() if gui else []
    suffix = "" if len(executables) == 2 else f"-{next(iter(executables))}"
    archive_stem = f"malhash-{version}-{target}{suffix}"
    with TemporaryDirectory(prefix="archive-", dir=output) as temporary:
        staging = Path(temporary) / archive_stem
        staging.mkdir()
        for component, executable in executables.items():
            if component == "cli":
                shutil.copy2(executable, staging)
            else:
                bundle = executable.parents[2] if system == "macos" else executable.parent
                shutil.copytree(bundle, staging / bundle.name, symlinks=True)
        add_licenses(staging, gui, qt_sources)
        if system == "macos":
            output_archive = DIST / f"{archive_stem}.zip"
            subprocess.run(
                ["ditto", "-c", "-k", "--keepParent", str(staging), str(output_archive)],
                check=True,
            )
        else:
            archive_format = "zip" if system == "windows" else "gztar"
            output_archive = Path(
                shutil.make_archive(
                    str(DIST / archive_stem), archive_format, temporary, archive_stem
                )
            )
    if gui:
        (DIST / f"{archive_stem}.qt-sources.json").write_text(
            json.dumps(qt_sources, indent=2) + "\n", encoding="utf-8"
        )
    return output_archive


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--components", choices=("cli", "gui", "both"), default="both")
    parser.add_argument(
        "--skip-smoke", action="store_true", help="skip tests of frozen executables"
    )
    args = parser.parse_args()
    system = OS_NAMES.get(platform.system())
    arch = ARCH_NAMES.get(platform.machine().lower())
    if not system or not arch or (system == "windows" and arch != "x64"):
        parser.error("use a supported native Windows x64, Linux x64/ARM64, or macOS x64/ARM64 host")
    target = f"{system}-{arch}"
    version = metadata.version("malhash")
    output = DIST / f"native-{target}"
    output.mkdir(parents=True, exist_ok=True)
    for package in runtime_packages():
        metadata.version(package)
    components = ("cli", "gui") if args.components == "both" else (args.components,)
    executables = {}
    for component in components:
        if system == "windows" and component == "gui":
            executable = build_windows_gui(output, version, smoke=not args.skip_smoke)
        else:
            executable = build(component, target, system, output)
        executables[component] = executable
    if not args.skip_smoke:
        for component, executable in executables.items():
            smoke_test(executable, component)
    print(archive(executables, output, target, system, version))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
