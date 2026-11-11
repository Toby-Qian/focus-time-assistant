"""Compatibility entry for the current desktop integration check."""
from pathlib import Path
import runpy
runpy.run_path(str(Path(__file__).with_name("check_qt_desktop.py")),run_name="__main__")
