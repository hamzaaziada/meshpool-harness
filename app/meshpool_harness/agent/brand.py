"""The Meshpool mark and colors for terminals.

MARK is brand/mark.png (the blue ribbon M) scaled to 22 x 14 pixels, one 6-digit hex color per pixel, "......" for
transparent. It is drawn with half-block characters, two pixels per character cell. Terminals with 24-bit color get
the exact colors; others (macOS Terminal before Tahoe, plain xterm) get the nearest of the 256 standard colors.
"""
from __future__ import annotations

import os
import sys

POOL = (22, 112, 232)        # #1670E8, the wordmark blue (ui/app.css --pool)
SKY = (94, 168, 255)         # #5EA8FF, the light ribbon (dark theme --pool)
MUTED = (141, 155, 176)      # #8D9BB0

MARK = [
    "........................2b95fb1987fd1180fc1882ff..............................8ddefcaae7fdcaf5ff....................................",
    "..................1e84fa0678ff0063fe005bfc025bff0a5eff..................70c8fc77d8ff7ed6ffa5ecffcdf4ff..............................",
    "..................0674ff0057fa0454f9044cf50343eb0142f00d4ae4......60b8fb5fc5ff5ebbfa6dc6fb83d2fab3eeffd7f5fd........................",
    "............0e70fb0159fd034cf60346ef0340e30339d70435c9002dc53d7ae04dacff47a7fb56b4fb64befb77cbfc90d7fbc9f7ffd8f0fc..................",
    "............0662ff0247f20241e6023bd80436cc0430c0032bb4052eac1f79f83798fe409efa4aa8fb57b3fb69c0fb7ecdfba3ddfcd6faff..................",
    "......1069fa0250ff033fe3033ad30333c20023af0022a500209c1354cb035bf71a72f63a9afd3d9dfb49a8fb58b3fb6dc3fc8bd2fbbeecfdd6f1fb............",
    "......085bfb013ddf0331ba0f38ac2355bd3572d8174bc20c3eb20d5be7003fde064be0308dfb3999fc3c9dfb4fadfb58b3fb55b0fb67bbfabbefffdff4fa......",
    "......0447e70128ab03269e0223940221931a46ae4694f8378dff1e68ed0844d40032c61e6ae43a9dff4aa8fb5ebafc50adfb4baafb48a9fa56b8fc9bdafc......",
    "1056ea0238d4022eb9032bac0326a20427a00021a41e57c45ba5ff3a82f62669e10d47ca033cc449a3f770c8fd5eb9fb52b0fb4cabfc4eadfc53b3fb70d0ff9ce1fd",
    "0b51f20243ea0339d00330bb022cb0002baf0a46cd............5ba5ff397df22869df084cd63e7fd481d5ff5eb8fb55b3fc4fadfc50affb57b6fa6dcaff86d9fb",
    "0765fb0052fa0246e6023cd00034c00746d82160d8..................589efd4284f23374e30742bc64b2ef68c3fd57b3fb53b1fc54b2fc5bbafc6fc8fc89dcfb",
    "1981fa0d72ff0b56f5084de6115bf4296ae6..........................................1355d32670e16cc8ff5cb9fb57b4fb58b6fb5fbbfa77d5ff88dcfc",
    "......348ffc2e7fff2d77fb3c83fa............................................................3a8cee69c9ff60befe5dbbfc68cbff76d3fd......",
    "................................................................................................4d9df768c1fb6ac8fb70d1fd............",
]


def depth(stream=None) -> int:
    """0 (no color), 256 or 24 (24-bit color) for this terminal."""
    stream = stream or sys.stdout
    if os.environ.get("NO_COLOR") or not getattr(stream, "isatty", lambda: False)():
        return 0
    if os.environ.get("TERM") == "dumb":
        return 0
    if os.environ.get("COLORTERM", "").lower() in ("truecolor", "24bit"):
        return 24
    tp = os.environ.get("TERM_PROGRAM", "")
    if tp in ("iTerm.app", "WezTerm", "vscode", "ghostty", "Hyper", "Tabby", "rio", "WarpTerminal", "zed"):
        return 24
    if os.name == "nt":
        os.system("")            # turn on escape sequences in the Windows console, which has 24-bit color
        return 24
    return 256


def _256(rgb) -> int:
    """Nearest xterm-256 color (the 6x6x6 cube or the gray ramp)."""
    steps = (0, 95, 135, 175, 215, 255)

    def near(v):
        return min(range(6), key=lambda i: abs(steps[i] - v))
    r, g, b = rgb
    ci = [near(r), near(g), near(b)]
    cube = tuple(steps[i] for i in ci)
    gray_i = max(0, min(23, round((sum(rgb) / 3 - 8) / 10)))
    gray = (8 + 10 * gray_i,) * 3

    def dist(c):
        return sum((c[k] - rgb[k]) ** 2 for k in range(3))
    return 232 + gray_i if dist(gray) < dist(cube) else 16 + 36 * ci[0] + 6 * ci[1] + ci[2]


def _rgb(h: str):
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def fg(rgb, d: int) -> str:
    if not d:
        return ""
    return f"\x1b[38;2;{rgb[0]};{rgb[1]};{rgb[2]}m" if d == 24 else f"\x1b[38;5;{_256(rgb)}m"


def bg(rgb, d: int) -> str:
    if not d:
        return ""
    return f"\x1b[48;2;{rgb[0]};{rgb[1]};{rgb[2]}m" if d == 24 else f"\x1b[48;5;{_256(rgb)}m"


def paint(text: str, rgb, d: int, bold: bool = False) -> str:
    if not d:
        return text
    return ("\x1b[1m" if bold else "") + fg(rgb, d) + text + "\x1b[0m"


def mark_lines(d: int) -> list[str]:
    """The mark as terminal lines. Without color it falls back to plain block shapes."""
    out = []
    for y in range(0, len(MARK), 2):
        top, bot = MARK[y], MARK[y + 1] if y + 1 < len(MARK) else "." * len(MARK[y])
        line = []
        for x in range(0, len(top), 6):
            t, b = top[x:x + 6], bot[x:x + 6]
            if t == "......" and b == "......":
                line.append(" ")
            elif not d:
                line.append("█" if t != "......" and b != "......" else ("▀" if t != "......" else "▄"))
            elif b == "......":
                line.append(fg(_rgb(t), d) + "▀\x1b[0m")
            elif t == "......":
                line.append(fg(_rgb(b), d) + "▄\x1b[0m")
            else:
                line.append(fg(_rgb(t), d) + bg(_rgb(b), d) + "▀\x1b[0m")
        out.append("".join(line))
    return out


def banner(d: int, lines: list[str] | None = None) -> list[str]:
    """The mark with the wordmark and up to five lines of text beside it, ready to print."""
    from meshpool_harness import __version__
    text = [paint("meshpool", POOL if d != 256 else SKY, d, bold=True),
            paint(f"v{__version__} · meshpool.ai", MUTED, d)] + list(lines or [])
    mark = mark_lines(d)
    top = max(0, (len(mark) - len(text)) // 2)
    width = len(MARK[0]) // 6
    rows = []
    for i in range(max(len(mark), top + len(text))):
        left = mark[i] if i < len(mark) else " " * width
        j = i - top
        rows.append("  " + left + ("   " + text[j] if 0 <= j < len(text) else ""))
    return rows


def prompt(text: str) -> str:
    """A colored prompt for input(). With readline loaded (GNU readline, or libedit on macOS), color codes must be
    marked as invisible with \\001 and \\002, or libedit drops the escape character and prints the code as text. The
    prompt should end in a plain character, which libedit needs to keep the final reset."""
    import re
    if "readline" not in sys.modules:
        return text
    return re.sub(r"(\x1b\[[0-9;]*m)", "\x01\\1\x02", text)
