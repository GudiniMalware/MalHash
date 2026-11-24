import faulthandler
import os
import sys
import traceback
from pathlib import Path

from malhash.gui import main


def frozen_main() -> int:
    smoke_directory = os.environ.get("MALHASH_SMOKE_SETTINGS_DIR")
    if "--smoke-test" not in sys.argv or not smoke_directory:
        return main()

    # PyInstaller windowed executables have no console streams on Windows.
    log_path = Path(smoke_directory) / "gui-smoke.log"
    with log_path.open("w", encoding="utf-8", buffering=1) as log:
        sys.stdout = sys.stderr = log
        faulthandler.dump_traceback_later(15, file=log)
        try:
            return main()
        except Exception:
            traceback.print_exc(file=log)
            return 1
        finally:
            faulthandler.cancel_dump_traceback_later()
            sys.stdout, sys.stderr = sys.__stdout__, sys.__stderr__


raise SystemExit(frozen_main())
