"""The Meshpool window as its own process (used on Windows): `python -m meshpool_harness.ui.window URL [ICON]`.

Running the WebView2 window apart from the server means a machine without a working WebView2 cannot take the app
down: the app sees this process fail and opens the interface as an Edge app window instead.
Exit code 0: the user closed the window. Anything else: the window could not open.
"""
from __future__ import annotations

import os
import sys


def window_size(webview) -> dict:
    """The same window on every computer: 1380x900 where it fits, otherwise 90% of the screen, centred, so it never
    opens larger than a small laptop screen and then jumps. Used on Mac and Windows."""
    w, h = 1380, 900
    try:
        sc = webview.screens[0]
        sw, sh = int(sc.width), int(sc.height)
        w, h = min(w, int(sw * 0.9)), min(h, int(sh * 0.9))
        return {"width": w, "height": h, "x": max(0, (sw - w) // 2), "y": max(0, (sh - h) // 2),
                "min_size": (min(940, w), min(620, h))}
    except Exception:  # noqa: BLE001  older pywebview or no screen information
        return {"width": w, "height": h, "min_size": (940, 620)}


def main(argv: list[str]) -> int:
    url = argv[0]
    icon = argv[1] if len(argv) > 1 else ""
    try:
        import webview
        webview.settings["ALLOW_DOWNLOADS"] = True
        webview.settings["OPEN_EXTERNAL_LINKS_IN_BROWSER"] = True
        dark = os.environ.get("MESHPOOL_DARK") == "1"
        webview.create_window("Meshpool", url, background_color="#0B131F" if dark else "#F5F8FC", text_select=True,
                              **window_size(webview))
        kw = {"gui": "edgechromium", "private_mode": False}
        if os.environ.get("MESHPOOL_WEBVIEW_DATA"):
            kw["storage_path"] = os.environ["MESHPOOL_WEBVIEW_DATA"]
        if icon and os.path.exists(icon):
            kw["icon"] = icon
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Meshpool.Desktop")
        except Exception:  # noqa: BLE001
            pass
        webview.start(**kw)
        return 0
    except Exception as e:  # noqa: BLE001
        import traceback
        msg = f"Native window failed: {type(e).__name__}: {e}\n{traceback.format_exc()}"
        try:
            with open(os.environ.get("MESHPOOL_WINDOW_LOG") or os.devnull, "a", encoding="utf-8") as f:
                f.write(msg)
        except OSError:
            pass
        return 3


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
