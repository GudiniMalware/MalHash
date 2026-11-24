# Building and releasing MalHash

Use native CPython 3.11 or newer. Build each target on its own operating system and architecture. Release jobs use CPython 3.12. ARM64 builds and builds with Python 3.13 or newer compile the upstream TLSH extension. Install the Visual C++ Build Tools on Windows, the Xcode Command Line Tools on macOS, or `build-essential` on Ubuntu.

Windows GUI builds also require Inno Setup 6.3+ or 7. The build finds `ISCC.exe` on PATH or in a standard installation folder. For a portable compiler, set the `ISCC` environment variable to its full executable path. [Inno Setup downloads](https://jrsoftware.org/inno-setup/downloads)

```bash
python -m venv .venv
# Activate .venv for your shell.
python -m pip install '.[all,dev,build]'
python -m ruff check .
python -m ruff format --check .
python -m pytest --basetemp .pytest-tmp
python -m build
python -m twine check --strict dist/*
python scripts/build_release.py
python scripts/release_assets.py package dist release
python scripts/release_assets.py checksums release --archive
```

The build script hashes a synthetic file through the CLI and checks the SHA-256 shown by the GUI after both builds complete. On macOS, PyInstaller uses separate CLI and GUI directories to keep the CLI executable and app bundle distinct on case-insensitive volumes. Windows exercises the native Qt platform with only Windows directories on PATH. Other hosts use the offscreen platform. Pass `--components cli` for a headless build with `.[algorithms,build]` or `--components gui` for a desktop build. `--skip-smoke` is available for troubleshooting.

The Windows build writes the executables to `dist/`. The packaging command places each one at the top level of its own ZIP in `release/`.

| Release download | File to open after extraction | User experience |
| --- | --- | --- |
| `MalHash-Windows-x64-Portable.zip` | `MalHash.exe` | Double-click to open the desktop app. The bundled runtime expands into a temporary directory for the session. |
| `MalHash-Windows-x64-Setup.zip` | `MalHash-Setup.exe` | Install once for the current user, then open MalHash from Start. Windows Settings can uninstall it. The installed runtime stays under `%LOCALAPPDATA%\Programs\MalHash`. |

The Windows smoke test copies the portable EXE into an otherwise empty folder. It also runs the actual installer, starts the installed app, then uninstalls it. The isolated install uses a temporary destination and skips the Start shortcut and Windows app registration.

| Target | CLI | GUI |
| --- | --- | --- |
| Windows x64 | `malhash-windows-x64.exe` | `malhash-gui-windows-x64.exe` in a portable folder |
| Linux x64 and ARM64 | `malhash-linux-<arch>` | `malhash-gui-linux-<arch>` in a portable folder |
| macOS ARM64 and x64 | `malhash-macos-<arch>` | `MalHash-macos-<arch>.app` |

The archive for each target includes build versions and licenses. Extract the entire GUI folder or application bundle before running it. Keep the Qt libraries and platform plug-ins in their packaged directory structure. PyInstaller extracts the CLI's own runtime to a temporary directory on start. The CLI needs a writable temporary directory with permission to execute the extracted libraries.

## Platform requirements

- Windows builds are x64 and target Windows 10 1809 or newer and Windows 11. Qt may require the Microsoft Visual C++ x64 redistributable. Local and draft builds are unsigned and may produce SmartScreen prompts.
- The Linux x64 release builds on Ubuntu 22.04 (glibc 2.35). The ARM64 release builds on Ubuntu 24.04 (glibc 2.39). Older glibc and musl systems can use a compatible native source install. On Debian-based desktops, the GUI may need `libegl1`, `libgl1`, `libxkbcommon0`, `libxcb-cursor0` and a working X11/XWayland environment. Source-installed CLI needs no display or Qt libraries.
- Published macOS artifacts require a matching processor architecture. The Qt wheels used by the release workflow target macOS 13 or newer. PyInstaller signs Mach-O files ad hoc as needed. Developer ID signing and notarization are separate release steps. Gatekeeper may block unsigned or unnotarized downloads. On managed devices follow your organization's software approval process.

Platform builds embed the release host's native libraries. Validate minimum-platform support on actual target systems before advertising it as tested. Local smoke checks are separate from the CI runner matrix.

## Release procedure

The GitHub release workflow checks the version tag, builds and smoke-tests the applications, and attaches archives to a draft. The downloads include the two Windows ZIPs, the platform bundles, MalHash's source tarball, the wheel inside a `.whl.zip`, and the matching Qt and Qt for Python source tarballs. To install the wheel, extract the ZIP and pass the resulting `.whl` file to `python -m pip install`.

Each build job passes its target to the packager. For example, `python scripts/release_assets.py package dist release --target windows-x64` requires the platform ZIP and both Windows executables. The draft job verifies the downloadable archives against the checksum manifest before uploading them.

Extract `SHA256SUMS.zip` next to the downloaded archives to get `SHA256SUMS`. Its entries hash the downloadable archives themselves. On a system with GNU coreutils, run `sha256sum --check --ignore-missing SHA256SUMS` in that directory. On Windows, compare the output of `Get-FileHash .\MalHash-Windows-x64-Portable.zip -Algorithm SHA256` with the corresponding entry. Review the draft before publishing it.

For an equivalent manual redistribution, keep the exact dependency versions recorded in `BUILD_VERSIONS.md`, collect the Qt source archives, and publish them and the buildable MalHash source alongside the GUI binaries. The Qt download command is a build-time step and needs network access. It downloads the official source tarballs and verifies their upstream SHA-256 values before including them in `SHA256SUMS`.

```bash
python scripts/release_assets.py package dist release
python scripts/release_assets.py checksums release --archive --download-qt-source --qt-manifests dist
```

See [Third-party notices](THIRD_PARTY_NOTICES.md) for Qt replacement and release review details.

## Distribution names

On November 24, 2026, the [PyPI JSON endpoint](https://pypi.org/pypi/malhash/json) returned HTTP 404 for `malhash`. Availability can change and PyPI applies additional name rules during registration. Recheck before publishing. GitHub already has public repositories named MalHash, including [jheise/malhash](https://github.com/jheise/malhash) and [ZetaOrioniss/malhash](https://github.com/ZetaOrioniss/malhash). GitHub repository names are scoped to the owner. Confirm availability in the intended namespace and use that URL for CI and release badges when the project is hosted.
