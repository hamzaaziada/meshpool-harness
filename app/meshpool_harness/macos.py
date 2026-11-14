"""What needs a newer macOS than the Intel app's minimum (10.15 Catalina).

The Intel Mac app opens on macOS 10.15, but the Node.js it bundles (Node 22, which Pi needs) and the Chromium
browser use drives need macOS 11 Big Sur or later. On an older Mac those parts say so instead of failing with a
loader error: the Node agents (Pi, Codex, OpenCode) show as unavailable with this note, and browser use answers
with it.
"""
from __future__ import annotations

import platform
import sys

NEEDS = (11, 0)
NODE_AGENTS = ("pi", "codex", "opencode")


def version() -> tuple[int, int] | None:
    """The running macOS as (major, minor), or None off a Mac or when it cannot be read."""
    if sys.platform != "darwin":
        return None
    try:
        parts = [int(x) for x in platform.mac_ver()[0].split(".")[:2]]
    except ValueError:
        return None
    if not parts or not parts[0]:
        return None
    return (parts + [0])[0], (parts + [0])[1]


def too_old() -> bool:
    """True on a Mac older than macOS 11. 10.16 is how Big Sur reports itself to programs built for older systems,
    so it counts as 11."""
    v = version()
    return bool(v and v < (10, 16))


def running() -> str:
    v = version()
    return f"macOS {v[0]}.{v[1]}" if v else "this macOS"


def note(what: str, instead: str = "Meshpool Code, Meshpool Work and Hermes work on this Mac.") -> str:
    """The message for a part that cannot run here, e.g. note("Pi")."""
    return f"{what} needs macOS 11 or later; this Mac runs {running()}. {instead}".strip()


BROWSER_INSTEAD = "To read public pages, use web_search, web_research and fetch_url, which need no browser."
