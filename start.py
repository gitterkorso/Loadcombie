"""Launcher shared by the source distribution and the macOS application bundle."""

import os
from pathlib import Path
import shutil
import sys

from lastkomb.__main__ import main


if __name__ == "__main__":
    if getattr(sys, "frozen", False) and "MPLCONFIGDIR" in os.environ:
        cache = Path(os.environ["MPLCONFIGDIR"])
        for index in Path(sys._MEIPASS).glob("fontlist-v*.json"):
            shutil.copyfile(index, cache / index.name)
    raise SystemExit(main())
