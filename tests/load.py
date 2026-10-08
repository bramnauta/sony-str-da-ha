"""Load protocol.py and client.py without importing Home Assistant.

custom_components/sony_str_da/__init__.py imports Home Assistant, which the
protocol tests do not need, so the package is registered as an empty shell.
"""

import importlib
import sys
import types
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent.parent / "custom_components" / "sony_str_da"
NAME = "sony_str_da_under_test"

if NAME not in sys.modules:
    shell = types.ModuleType(NAME)
    shell.__path__ = [str(PACKAGE_DIR)]
    sys.modules[NAME] = shell

protocol = importlib.import_module(f"{NAME}.protocol")
client = importlib.import_module(f"{NAME}.client")
