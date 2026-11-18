"""Load shared platform rules without importing the Blender add-on entry point."""
import importlib.util
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    '_aventurine_release_platform', Path(__file__).resolve().parents[1] / 'utils/release_platform.py')
_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_module)

host_platform = _module.host_platform
wheel_platform = _module.wheel_platform
bundle_platform = _module.bundle_platform
native_module = _module.native_module
