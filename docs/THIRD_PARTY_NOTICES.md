# Third-party notices

MalHash's original code and artwork are MIT-licensed. Components shipped with frozen applications retain their own terms. Build archives include license files from the installed package metadata, standard license texts, and exact build versions. The same release provides the MalHash source and Qt corresponding source.

| Component | License and source |
| --- | --- |
| CPython and standard library | [PSF License and bundled software notices](https://docs.python.org/3/license.html) |
| ppdeep, by Marcin Ulikowski | [Apache-2.0](https://github.com/elceef/ppdeep/blob/master/ppdeep.py) |
| pefile, by Ero Carrera | [MIT](https://github.com/erocarrera/pefile/blob/master/LICENSE) |
| BLAKE3 bindings and BLAKE3 library | [CC0-1.0 or Apache-2.0](https://github.com/oconnor663/blake3-py/blob/master/LICENSE). The Rust dependency inventory and license expressions are recorded in the wheel's CycloneDX SBOM when provided. |
| TLSH and Python bindings | [Apache-2.0 or BSD-3-Clause for upstream TLSH](https://github.com/trendmicro/tlsh/blob/master/LICENSE). The [tlsh-python repackaging](https://pypi.org/project/tlsh-python/) advertises MIT and includes the upstream TLSH terms in its wheel. |
| nanobind, with the x64 TLSH bindings | [BSD-3-Clause](https://github.com/wjakob/nanobind/blob/master/LICENSE) |
| typing_extensions, when bundled | [PSF License](https://github.com/python/typing_extensions/blob/main/LICENSE) |
| Qt for Python Essentials and Shiboken, by The Qt Company | [LGPL-3.0 for this distribution](https://doc.qt.io/qtforpython-6/licenses.html). Qt also ships components under their own licenses. Sources and license notices for the bundled Qt version are available as release assets. |
| PyInstaller bootloader and frozen runtime hooks | [GPL-2.0-or-later with the PyInstaller exception, with some Apache-2.0 files](https://pyinstaller.org/en/stable/license.html) |
| altgraph | [MIT](https://pypi.org/project/altgraph/) |
| Windows installer engine | [Inno Setup](https://jrsoftware.org/inno-setup) by Jordan Russell and Martijn Laan. The installed app includes the compiler version's license in its notices folder. |

Build and test tools include [setuptools](https://github.com/pypa/setuptools), [build](https://github.com/pypa/build), [Twine](https://github.com/pypa/twine), [pytest](https://github.com/pytest-dev/pytest), [Ruff](https://github.com/astral-sh/ruff) and the [original ssdeep reference](https://github.com/ssdeep-project/ssdeep). The reference is used only for compatibility tests. It is excluded from frozen applications.

## Qt source and replacement

The GUI loads Qt as shared libraries. You may replace Qt or PySide with an ABI-compatible modified version and debug your modifications. On Windows, **Settings → Licenses (Qt / LGPL)** opens the bundled notices, license texts and build versions. You can copy these files while the app is open.

In the Windows installation and in Windows and Linux folder builds, replace the libraries and plug-ins under the GUI's `_internal` directory. On macOS, replace the corresponding Qt frameworks and libraries under `Contents/Frameworks`, then sign the modified bundle for your target environment.

The single-file Windows download extracts its runtime on each launch. To build your own `MalHash.exe`, unpack MalHash's source distribution from the same release, follow the build guide to create its Windows environment, then install your ABI-compatible PySide6-Essentials and Shiboken wheels there. Run `python scripts/build_release.py --components gui` to produce a new `dist/MalHash.exe` with your libraries. MalHash's MIT license permits rebuilding and redistributing the application. The release includes matching Qt and PySide source for building modified wheels.

`BUILD_VERSIONS.md` lists the official matching Qt and Qt for Python source URLs for each binary archive. The automated draft also attaches the original verified source archives to the same release as the GUI binaries. See the [build instructions](BUILDING.md) and [Qt open-source distribution guidance](https://www.qt.io/development/open-source-lgpl-obligations).

Before public redistribution, review the precise collected files on each target against the corresponding source and license inventory, including Qt's third-party code, any native system libraries copied into the bundle and Rust components statically linked into BLAKE3. Keep the required terms and copyright notices with the distributed files.
