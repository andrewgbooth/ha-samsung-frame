"""Load the integration's pure-Python modules without importing Home Assistant."""

import sys
import types
from pathlib import Path

PKG = "custom_components.samsung_frame"
ROOT = Path(__file__).resolve().parent.parent / "custom_components" / "samsung_frame"

for name, path in (("custom_components", ROOT.parent), (PKG, ROOT)):
    if name not in sys.modules:
        mod = types.ModuleType(name)
        mod.__path__ = [str(path)]
        sys.modules[name] = mod
