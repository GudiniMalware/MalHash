"""Build the Windows download and the installed GUI from the same runtime."""

import argparse
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files

parser = argparse.ArgumentParser()
parser.add_argument("notices")
args = parser.parse_args()
root = Path(SPECPATH).parent
folder_name = "malhash-gui-windows-x64"
analysis = Analysis(
    [str(root / "scripts" / "gui_entry.py")],
    pathex=[str(root / "src")],
    datas=[*collect_data_files("malhash"), (args.notices, "notices")],
    hiddenimports=["ppdeep", "blake3", "pefile", "tlsh"],
    excludes=["ssdeep", "tkinter"],
)
# Qt uses the ICU ABI shipped by Windows. Third-party ICU DLLs can shadow it.
system_icu = {"icu.dll", "icuin.dll", "icuuc.dll"}
binaries = [entry for entry in analysis.binaries if entry[0].lower() not in system_icu]
modules = PYZ(analysis.pure)
options = {"console": False, "upx": False, "icon": str(root / "assets" / "malhash.ico")}

launcher = EXE(modules, analysis.scripts, exclude_binaries=True, name=folder_name, **options)
COLLECT(launcher, binaries, analysis.datas, upx=False, name=folder_name)
EXE(modules, analysis.scripts, binaries, analysis.datas, name="MalHash", **options)
