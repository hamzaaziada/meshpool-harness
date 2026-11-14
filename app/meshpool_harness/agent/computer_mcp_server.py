"""Meshpool Computer: computer use for any harness, as an MCP server. Built in; nothing to install.

The agent sees the screen (screenshots) and uses the mouse and keyboard, in any app. Coordinates are pixels in the
latest screenshot; the server maps them to the real screen, so Retina and scaled displays just work.

macOS: Meshpool asks for Screen Recording (to see) and Accessibility (to click and type) in its own name.
Windows: no extra permissions.

Seeing the screen needs a model that accepts images. With a text-only model, use open_app, key shortcuts and
typing, or the browser tools for web pages.
"""
from __future__ import annotations

import io
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from meshpool_harness.agent.mcp_stdio import Tool, ToolFailed, image_png, serve, text

MAX_W = int(os.environ.get("MH_SCREEN_MAX_W", "1366"))
STATE = {"scale": 1.0}                       # screen units per screenshot pixel, from the latest screenshot

MODS = {"cmd": "cmd", "command": "cmd", "meta": "cmd", "super": "cmd", "win": "cmd", "windows": "cmd",
        "ctrl": "ctrl", "control": "ctrl", "alt": "alt", "option": "alt", "opt": "alt", "shift": "shift"}


def _parse_combo(combo: str) -> tuple[list[str], str]:
    parts = [p.strip().lower() for p in combo.replace("-", "+").split("+") if p.strip()]
    if not parts:
        raise ToolFailed("Give a key, for example enter, tab, cmd+c or ctrl+shift+t.")
    mods = [MODS[p] for p in parts[:-1] if p in MODS]
    unknown = [p for p in parts[:-1] if p not in MODS]
    if unknown:
        raise ToolFailed(f"Unknown modifier {unknown[0]}. Use cmd, ctrl, alt or shift.")
    return mods, parts[-1]


# ======================================= macOS ======================================================================
class Mac:
    KEYS = {"a": 0, "s": 1, "d": 2, "f": 3, "h": 4, "g": 5, "z": 6, "x": 7, "c": 8, "v": 9, "b": 11, "q": 12, "w": 13,
            "e": 14, "r": 15, "y": 16, "t": 17, "1": 18, "2": 19, "3": 20, "4": 21, "6": 22, "5": 23, "=": 24, "9": 25,
            "7": 26, "-": 27, "8": 28, "0": 29, "]": 30, "o": 31, "u": 32, "[": 33, "i": 34, "p": 35, "l": 37, "j": 38,
            "'": 39, "k": 40, ";": 41, "\\": 42, ",": 43, "/": 44, "n": 45, "m": 46, ".": 47, "`": 50,
            "enter": 36, "return": 36, "tab": 48, "space": 49, "backspace": 51, "delete": 51, "escape": 53, "esc": 53,
            "forwarddelete": 117, "home": 115, "end": 119, "pageup": 116, "pagedown": 121,
            "left": 123, "right": 124, "down": 125, "up": 126,
            "f1": 122, "f2": 120, "f3": 99, "f4": 118, "f5": 96, "f6": 97, "f7": 98, "f8": 100, "f9": 101, "f10": 109,
            "f11": 103, "f12": 111}

    def __init__(self):
        import Quartz as Q
        self.Q = Q
        self.FLAGS = {"cmd": Q.kCGEventFlagMaskCommand, "ctrl": Q.kCGEventFlagMaskControl,
                      "alt": Q.kCGEventFlagMaskAlternate, "shift": Q.kCGEventFlagMaskShift}

    def screen_size(self) -> tuple[int, int]:
        b = self.Q.CGDisplayBounds(self.Q.CGMainDisplayID())
        return int(b.size.width), int(b.size.height)

    def grab(self) -> "Image":
        from PIL import Image
        fd, path = tempfile.mkstemp(suffix=".png")
        os.close(fd)
        try:
            r = subprocess.run(["screencapture", "-x", "-m", "-t", "png", path], capture_output=True, timeout=20)
            if r.returncode != 0 or os.path.getsize(path) == 0:
                raise ToolFailed("macOS blocked the screenshot. In Meshpool, open Settings and allow Screen Recording, "
                                 "then reopen Meshpool.")
            img = Image.open(path)
            img.load()
            return img
        finally:
            try:
                os.unlink(path)
            except OSError:
                pass

    def _post(self, ev):
        self.Q.CGEventPost(self.Q.kCGHIDEventTap, ev)

    def move(self, x, y):
        Q = self.Q
        self._post(Q.CGEventCreateMouseEvent(None, Q.kCGEventMouseMoved, (x, y), Q.kCGMouseButtonLeft))

    def click(self, x, y, button="left", count=1):
        Q = self.Q
        down, up, btn = {"left": (Q.kCGEventLeftMouseDown, Q.kCGEventLeftMouseUp, Q.kCGMouseButtonLeft),
                         "right": (Q.kCGEventRightMouseDown, Q.kCGEventRightMouseUp, Q.kCGMouseButtonRight),
                         "middle": (Q.kCGEventOtherMouseDown, Q.kCGEventOtherMouseUp, Q.kCGMouseButtonCenter)}[button]
        self.move(x, y)
        time.sleep(0.03)
        for i in range(1, count + 1):
            for kind in (down, up):
                ev = Q.CGEventCreateMouseEvent(None, kind, (x, y), btn)
                Q.CGEventSetIntegerValueField(ev, Q.kCGMouseEventClickState, i)
                self._post(ev)
            time.sleep(0.04)

    def drag(self, x1, y1, x2, y2):
        Q = self.Q
        self.move(x1, y1)
        self._post(Q.CGEventCreateMouseEvent(None, Q.kCGEventLeftMouseDown, (x1, y1), Q.kCGMouseButtonLeft))
        steps = 20
        for i in range(1, steps + 1):
            x, y = x1 + (x2 - x1) * i / steps, y1 + (y2 - y1) * i / steps
            self._post(Q.CGEventCreateMouseEvent(None, Q.kCGEventLeftMouseDragged, (x, y), Q.kCGMouseButtonLeft))
            time.sleep(0.01)
        self._post(Q.CGEventCreateMouseEvent(None, Q.kCGEventLeftMouseUp, (x2, y2), Q.kCGMouseButtonLeft))

    def scroll(self, x, y, dy, dx=0):
        Q = self.Q
        self.move(x, y)
        ev = Q.CGEventCreateScrollWheelEvent(None, Q.kCGScrollEventUnitLine, 2, int(-dy), int(-dx))
        self._post(ev)

    def type_text(self, s: str):
        Q = self.Q
        for i in range(0, len(s), 16):
            chunk = s[i:i + 16]
            for down in (True, False):
                ev = Q.CGEventCreateKeyboardEvent(None, 0, down)
                Q.CGEventKeyboardSetUnicodeString(ev, len(chunk.encode("utf-16-le")) // 2, chunk)
                self._post(ev)
            time.sleep(0.01)

    def key(self, combo: str):
        Q = self.Q
        mods, k = _parse_combo(combo)
        if k not in self.KEYS:
            raise ToolFailed(f"Unknown key {k}.")
        flags = 0
        for m in mods:
            flags |= self.FLAGS[m]
        for down in (True, False):
            ev = Q.CGEventCreateKeyboardEvent(None, self.KEYS[k], down)
            Q.CGEventSetFlags(ev, flags)
            self._post(ev)

    def open_app(self, name: str) -> str:
        r = subprocess.run(["open", "-a", name], capture_output=True, text=True, timeout=30)
        if r.returncode != 0:
            raise ToolFailed(f"Could not open {name}: {r.stderr.strip()}")
        return f"Opened {name}."

    def frontmost(self) -> str:
        try:
            from AppKit import NSWorkspace
            a = NSWorkspace.sharedWorkspace().frontmostApplication()
            return str(a.localizedName())
        except Exception:  # noqa: BLE001
            return ""

    def can_control(self) -> bool:
        try:
            import ctypes
            import ctypes.util
            ax = ctypes.cdll.LoadLibrary(ctypes.util.find_library("ApplicationServices"))
            ax.AXIsProcessTrusted.restype = ctypes.c_bool
            return bool(ax.AXIsProcessTrusted())
        except Exception:  # noqa: BLE001
            return True


# ======================================= Windows ====================================================================
class Win:
    VK = {"enter": 0x0D, "return": 0x0D, "tab": 0x09, "space": 0x20, "backspace": 0x08, "delete": 0x2E,
          "forwarddelete": 0x2E, "escape": 0x1B, "esc": 0x1B, "home": 0x24, "end": 0x23, "pageup": 0x21,
          "pagedown": 0x22, "left": 0x25, "up": 0x26, "right": 0x27, "down": 0x28, "insert": 0x2D,
          **{f"f{i}": 0x6F + i for i in range(1, 13)}}
    MODVK = {"ctrl": 0x11, "alt": 0x12, "shift": 0x10, "cmd": 0x5B}

    def __init__(self):
        import ctypes
        from ctypes import wintypes
        self.c, self.w = ctypes, wintypes
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except Exception:  # noqa: BLE001
            try:
                ctypes.windll.user32.SetProcessDPIAware()
            except Exception:  # noqa: BLE001
                pass
        self.u = ctypes.windll.user32

        class KEYBDINPUT(ctypes.Structure):
            _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD), ("dwFlags", wintypes.DWORD),
                        ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]

        class MOUSEINPUT(ctypes.Structure):
            _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG), ("mouseData", wintypes.DWORD),
                        ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]

        class _U(ctypes.Union):
            _fields_ = [("ki", KEYBDINPUT), ("mi", MOUSEINPUT), ("pad", ctypes.c_byte * 32)]

        class INPUT(ctypes.Structure):
            _fields_ = [("type", wintypes.DWORD), ("u", _U)]
        self.KEYBDINPUT, self.INPUT = KEYBDINPUT, INPUT

    def screen_size(self) -> tuple[int, int]:
        return self.u.GetSystemMetrics(0), self.u.GetSystemMetrics(1)

    def grab(self):
        import mss
        from PIL import Image
        with mss.mss() as m:
            mon = m.monitors[1]
            s = m.grab(mon)
            return Image.frombytes("RGB", s.size, s.bgra, "raw", "BGRX")

    def _send(self, *inputs):
        arr = (self.INPUT * len(inputs))(*inputs)
        self.u.SendInput(len(inputs), arr, self.c.sizeof(self.INPUT))

    def _key(self, vk=0, scan=0, flags=0):
        i = self.INPUT(type=1)
        i.u.ki = self.KEYBDINPUT(wVk=vk, wScan=scan, dwFlags=flags, time=0, dwExtraInfo=0)
        return i

    def move(self, x, y):
        self.u.SetCursorPos(int(x), int(y))

    def click(self, x, y, button="left", count=1):
        down, up = {"left": (0x02, 0x04), "right": (0x08, 0x10), "middle": (0x20, 0x40)}[button]
        self.move(x, y)
        time.sleep(0.03)
        for _ in range(count):
            self.u.mouse_event(down, 0, 0, 0, 0)
            self.u.mouse_event(up, 0, 0, 0, 0)
            time.sleep(0.05)

    def drag(self, x1, y1, x2, y2):
        self.move(x1, y1)
        self.u.mouse_event(0x02, 0, 0, 0, 0)
        for i in range(1, 21):
            self.move(x1 + (x2 - x1) * i / 20, y1 + (y2 - y1) * i / 20)
            time.sleep(0.01)
        self.u.mouse_event(0x04, 0, 0, 0, 0)

    def scroll(self, x, y, dy, dx=0):
        self.move(x, y)
        if dy:
            self.u.mouse_event(0x0800, 0, 0, self.c.c_uint32(int(-dy * 120)).value, 0)
        if dx:
            self.u.mouse_event(0x01000, 0, 0, self.c.c_uint32(int(dx * 120)).value, 0)

    def type_text(self, s: str):
        KEYUP, UNICODE = 0x0002, 0x0004
        data = s.encode("utf-16-le")
        for j in range(0, len(data), 2):
            code = int.from_bytes(data[j:j + 2], "little")
            self._send(self._key(scan=code, flags=UNICODE), self._key(scan=code, flags=UNICODE | KEYUP))

    def key(self, combo: str):
        mods, k = _parse_combo(combo)
        if k in self.VK:
            vk = self.VK[k]
        elif len(k) == 1:
            vk = self.u.VkKeyScanW(ord(k)) & 0xFF
        else:
            raise ToolFailed(f"Unknown key {k}.")
        seq = [self._key(vk=self.MODVK[m]) for m in mods] + [self._key(vk=vk), self._key(vk=vk, flags=0x0002)]
        seq += [self._key(vk=self.MODVK[m], flags=0x0002) for m in reversed(mods)]
        self._send(*seq)

    def open_app(self, name: str) -> str:
        name = _check_app_name(name)
        try:
            os.startfile(name)  # noqa: S606  ShellExecute: an app name, a file or a URL; no shell parses it
        except OSError as e:
            exe = shutil.which(name)           # a program on PATH that ShellExecute did not find by name
            if not exe or exe.lower().endswith((".bat", ".cmd")):     # scripts would run through cmd.exe
                raise ToolFailed(f"Could not open {name}: {e}")
            # an argument list, never through cmd; its own console, so a console program shows instead of hiding
            subprocess.Popen([exe], creationflags=0x00000010)
        return f"Opened {name}."

    def frontmost(self) -> str:
        h = self.u.GetForegroundWindow()
        buf = self.c.create_unicode_buffer(512)
        self.u.GetWindowTextW(h, buf, 512)
        return buf.value

    def can_control(self) -> bool:
        return True


_BAD_APP = re.compile(r'[&|<>^%"\'`\r\n\x00]')


def _check_app_name(name: str) -> str:
    """An app name, file or web address to open. Shell metacharacters (x&calc) are refused, so a name can never run
    a second command whatever opens it."""
    name = str(name or "").strip()
    if not name:
        raise ToolFailed("Name the app, file or web address to open.")
    if not re.match(r"[A-Za-z][A-Za-z0-9+.-]*://", name) and _BAD_APP.search(name):   # URLs keep their & and %
        raise ToolFailed(f"Refused to open {name!r}: app names cannot contain & | < > ^ % or quotes.")
    return name


_OS = None


def os_() -> Mac | Win:
    global _OS
    if _OS is None:
        if sys.platform == "darwin":
            _OS = Mac()
        elif os.name == "nt":
            _OS = Win()
        else:
            raise ToolFailed("Computer use runs on macOS and Windows.")
    return _OS


def _pt(x, y) -> tuple[float, float]:
    s = STATE["scale"]
    return float(x) * s, float(y) * s


def _need_control():
    if not os_().can_control():
        raise ToolFailed("macOS has not allowed Meshpool to control the mouse and keyboard. In Meshpool, open Settings, "
                         "then Permissions, and allow Accessibility.")


# ---------- tools --------------------------------------------------------------------------------------------------
def screenshot() -> list:
    o = os_()
    img = o.grab().convert("RGB")
    sw, sh = o.screen_size()
    w = min(MAX_W, sw)                                    # never larger than the screen's own units
    h = round(img.height * w / img.width)
    if (img.width, img.height) != (w, h):
        from PIL import Image
        img = img.resize((w, h), Image.LANCZOS)
    STATE["scale"] = sw / w
    buf = io.BytesIO()
    img.save(buf, "PNG", optimize=True)
    front = o.frontmost()
    return [text(f"Screenshot {w}x{h}. Use pixel coordinates in this image for clicks."
                 + (f" Front app: {front}." if front else "")), image_png(buf.getvalue())]


def click(x: float, y: float, button: str = "left", double: bool = False) -> str:
    _need_control()
    os_().click(*_pt(x, y), button=button if button in ("left", "right", "middle") else "left",
                count=2 if double else 1)
    time.sleep(0.25)
    return f"Clicked {button} at ({x}, {y}). Take a screenshot to see the result."


def move_mouse(x: float, y: float) -> str:
    _need_control()
    os_().move(*_pt(x, y))
    return f"Mouse at ({x}, {y})."


def drag(x1: float, y1: float, x2: float, y2: float) -> str:
    _need_control()
    a, b = _pt(x1, y1), _pt(x2, y2)
    os_().drag(a[0], a[1], b[0], b[1])
    return f"Dragged from ({x1}, {y1}) to ({x2}, {y2})."


def scroll(x: float, y: float, down: float = 3, right: float = 0) -> str:
    _need_control()
    os_().scroll(*_pt(x, y), dy=down, dx=right)
    time.sleep(0.2)
    return f"Scrolled {'down' if down >= 0 else 'up'} {abs(down)} at ({x}, {y})."


def type_text(text_: str = "", **kw) -> str:
    _need_control()
    value = text_ or kw.get("text", "")
    os_().type_text(value)
    return f"Typed {len(value)} characters."


def press_key(keys: str) -> str:
    _need_control()
    for combo in [k for k in keys.split(" ") if k.strip()]:
        os_().key(combo)
        time.sleep(0.08)
    return f"Pressed {keys}."


def open_app(name: str) -> str:
    out = os_().open_app(name)
    time.sleep(1.5)
    return out + " Take a screenshot to see it."


def screen_info() -> str:
    o = os_()
    w, h = o.screen_size()
    return (f"Screen {w}x{h} ({'macOS points' if sys.platform == 'darwin' else 'pixels'}). Screenshots are scaled to at "
            f"most {MAX_W} wide; always use the latest screenshot's pixel coordinates. Front app: {o.frontmost() or 'unknown'}."
            + ("" if o.can_control() else " Control is not allowed yet: ask the user to allow Accessibility in Meshpool's Settings."))


N = {"type": "number"}
TOOLS = {
    "screenshot": Tool(screenshot, "See the screen. Returns an image; click with pixel coordinates from it. "
                       "Take a new one after each action to check the result."),
    "click": Tool(click, "Click at a point in the latest screenshot. button: left, right or middle. double=true "
                  "double-clicks.", {"x": N, "y": N, "button": {"type": "string", "enum": ["left", "right", "middle"]},
                                     "double": {"type": "boolean"}}, ["x", "y"]),
    "move_mouse": Tool(move_mouse, "Move the mouse to a point (for hover menus).", {"x": N, "y": N}, ["x", "y"]),
    "drag": Tool(drag, "Drag with the left button from one point to another.",
                 {"x1": N, "y1": N, "x2": N, "y2": N}, ["x1", "y1", "x2", "y2"]),
    "scroll": Tool(scroll, "Scroll at a point. down is in lines (negative scrolls up); right scrolls sideways.",
                   {"x": N, "y": N, "down": N, "right": N}, ["x", "y"]),
    "type_text": Tool(type_text, "Type text into whatever has focus. Click the field first.", {"text": {"type": "string"}},
                      ["text"]),
    "press_key": Tool(press_key, "Press keys or shortcuts, separated by spaces: enter, tab, escape, cmd+c, ctrl+s, "
                      "alt+tab, cmd+shift+4, up, down, f5.", {"keys": {"type": "string"}}, ["keys"]),
    "open_app": Tool(open_app, "Open an app by name (Safari, Notes, Excel, Calculator) or a file or web address.",
                     {"name": {"type": "string"}}, ["name"]),
    "screen_info": Tool(screen_info, "Screen size, the front app, and whether control is allowed."),
}


def main():
    TOOLS["type_text"].fn = lambda text="": type_text(text_=text)
    serve("meshpool-computer", TOOLS, serial=True,
          instructions="Take a screenshot first, act with pixel coordinates from it, then take another to check.")


if __name__ == "__main__":
    main()
