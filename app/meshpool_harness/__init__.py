
__version__ = "0.0.1"
__author__ = "Meshpool"

from .presets import apply as _apply_preset  # noqa: E402

_apply_preset()     # MH_PRESET=v2|v3|v4 sets the tested flag bundle (explicit env vars win)

from .api import run_task  # noqa: E402,F401  safe entry point for apps (never raises)
