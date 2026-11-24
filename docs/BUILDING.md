# Building and releasing MalHash

Use native CPython 3.11 or newer. Build each target on its own operating system and architecture. Release jobs use CPython 3.12. Building the TLSH dependency on ARM64 requires a C++ toolchain. On macOS this means the Xcode Command Line Tools. On Ubuntu install `build-essential`.

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
python scripts/release_assets.py checksums dist
```

The build script runs the CLI against a synthetic file and starts the GUI using Qt's offscreen platform. These smoke tests launch the packaged programs from an unrelated temporary directory. Pass `--components cli` for a headless build with `.[algorithms,build]` or `--components gui` for only the portable GUI. `--skip-smoke` is available for troubleshooting. Treat its output as unverified until the executable passes a smoke test on the target.

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

The GitHub release workflow checks that the version tag agrees with `pyproject.toml`, builds wheel and sdist, builds and smoke-tests native archives, and generates a draft with `SHA256SUMS`. The draft includes matching Qt and Qt for Python source archives plus MalHash's sdist. It does not publish to a package index. Review the asset list, licenses, smoke output and release notes before making the draft public.

For an equivalent manual redistribution, keep the exact dependency versions recorded in `BUILD_VERSIONS.md`, collect the Qt source archives, and publish them and the buildable MalHash source alongside the GUI binaries. The Qt download command is a build-time step and needs network access. It downloads the official source tarballs and verifies their upstream SHA-256 values before including them in `SHA256SUMS`.

```bash
python scripts/release_assets.py checksums dist --download-qt-source
```

See [Third-party notices](THIRD_PARTY_NOTICES.md) for Qt replacement and release review details.

## Distribution names

On November 24, 2026, the [PyPI JSON endpoint](https://pypi.org/pypi/malhash/json) returned HTTP 404 for `malhash`. Availability can change and PyPI applies additional name rules during registration. Recheck before publishing. GitHub already has public repositories named MalHash, including [jheise/malhash](https://github.com/jheise/malhash) and [ZetaOrioniss/malhash](https://github.com/ZetaOrioniss/malhash). GitHub repository names are scoped to the owner. Confirm availability in the intended namespace and use that URL for CI and release badges when the project is hosted.
