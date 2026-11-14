"""Meshpool Browser: browser use for any harness, as an MCP server. Built in; nothing to install.

The agent drives a real, visible browser window: Google Chrome or Microsoft Edge when installed, otherwise a
Chromium that Meshpool downloads once into its data folder. The browser keeps its own profile in the Meshpool data
folder, so sign-ins persist between sessions and never touch your everyday browser profile.

Pages are described as text: title, address, the visible text, and numbered interactive elements ([e12] button
"Sign in"). The agent acts on those numbers, so it works with any model, including ones that cannot see images.
"""
from __future__ import annotations

import os
import queue
import subprocess
import sys
import threading
import time
from pathlib import Path

from meshpool_harness.agent.mcp_stdio import Tool, ToolFailed, image_png, serve, text

SNAPSHOT_JS = r"""() => {
  const sel = 'a[href],button,input:not([type=hidden]),textarea,select,summary,[role=button],[role=link],[role=tab],' +
              '[role=menuitem],[role=checkbox],[role=radio],[role=option],[role=switch],[role=combobox],' +
              '[role=textbox],[contenteditable=""],[contenteditable=true],[onclick]';
  document.querySelectorAll('[data-mp-ref]').forEach(e => e.removeAttribute('data-mp-ref'));
  const out = []; let i = 0;
  for (const el of document.querySelectorAll(sel)) {
    const r = el.getBoundingClientRect(), st = getComputedStyle(el);
    if (r.width < 2 || r.height < 2 || st.visibility === 'hidden' || st.display === 'none' || st.opacity === '0') continue;
    if (r.bottom < -innerHeight || r.top > innerHeight * 2.5) continue;
    const ref = 'e' + (++i); el.setAttribute('data-mp-ref', ref);
    const tag = el.tagName.toLowerCase(), type = (el.getAttribute('type') || '').toLowerCase();
    const lab = (el.labels && el.labels.length) ? el.labels[0].innerText : '';
    let name = el.getAttribute('aria-label') || lab || el.getAttribute('title') || (tag === 'select' ? '' : el.innerText) || el.getAttribute('placeholder')
      || el.getAttribute('alt') || el.getAttribute('name') || (tag === 'input' && ['submit','button'].includes(type) ? el.value : '') || '';
    name = String(name).trim().replace(/\s+/g, ' ').slice(0, 90);
    let line = `[${ref}] ${el.getAttribute('role') || tag}${type ? ':' + type : ''} "${name}"`;
    if (tag === 'a' && el.getAttribute('href')) line += ' -> ' + el.getAttribute('href').slice(0, 90);
    if ((tag === 'input' || tag === 'textarea') && !['checkbox','radio','submit','button','password'].includes(type) && el.value)
      line += ` value="${String(el.value).slice(0, 60)}"`;
    if (type === 'checkbox' || type === 'radio') line += el.checked ? ' (checked)' : ' (unchecked)';
    if (tag === 'select') line += ` selected="${(el.options[el.selectedIndex] || {}).text || ''}" options=[${[...el.options].slice(0, 15).map(o => o.text.trim()).join(' | ')}]`;
    if (window.__mpStates) line += __STATES__(el).replace(' (disabled)', '');   // MH_BROWSER_STATES
    if (el.disabled) line += ' (disabled)';
    if (r.top > innerHeight || r.bottom < 0) line += ' (scroll to see)';
    out.push(line);
    if (out.length >= 300) break;
  }
  const body = document.body ? document.body.innerText : '';
  return {title: document.title, url: location.href, elements: out, text: body.replace(/\n{3,}/g, '\n\n').slice(0, 7000),
          scroll: Math.round(scrollY), height: document.documentElement.scrollHeight, vh: innerHeight};
}"""
from meshpool_harness.core.webfix import STATES_JS  # noqa: E402
# The app's browser runs with the fixes measured on MiniWoB++: read-only picker fields,
# late menus, invalid/disabled/read-only markers, key-driven widgets. An explicit "0" in the environment turns one off.
for _flag in ("MH_BROWSER_READONLY", "MH_BROWSER_SETTLE", "MH_BROWSER_STATES", "MH_BROWSER_KEYTYPE"):
    os.environ.setdefault(_flag, "1")
SNAPSHOT_JS = SNAPSHOT_JS.replace("__STATES__", STATES_JS)


def _home() -> Path:
    return Path(os.environ.get("MESHPOOL_HOME") or Path.home() / ".meshpool")


class Browser:
    """One Playwright session on a dedicated thread (Playwright's sync API is bound to the thread that started it)."""

    def __init__(self):
        self.q: queue.Queue = queue.Queue()
        self.pw = self.ctx = self.page = None
        self.channel = ""
        threading.Thread(target=self._loop, daemon=True).start()

    def _loop(self):
        while True:
            fn, box, done = self.q.get()
            try:
                box["r"] = fn()
            except BaseException as e:  # noqa: BLE001
                box["e"] = e
            done.set()

    def run(self, fn, timeout: float = 240):
        box, done = {}, threading.Event()
        self.q.put((fn, box, done))
        if not done.wait(timeout):
            raise ToolFailed("The browser did not respond in time.")
        if "e" in box:
            raise box["e"]
        return box["r"]

    # ---------- start ----------------------------------------------------------------------------------------------
    def _install_chromium(self):
        env = dict(os.environ)
        env.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(_home() / "browsers"))
        r = subprocess.run([sys.executable, "-m", "playwright", "install", "chromium"], capture_output=True, text=True,
                           env=env, timeout=900)
        if r.returncode != 0:
            raise ToolFailed("Could not download the browser engine: " + (r.stderr or r.stdout)[-400:])

    def ensure(self):
        if self.ctx is not None:
            try:
                if self.page is None or self.page.is_closed():
                    self.page = self.ctx.pages[-1] if self.ctx.pages else self.ctx.new_page()
                return
            except Exception:  # noqa: BLE001  the window was closed by the user: start again
                self.ctx = self.page = None
        from playwright.sync_api import sync_playwright
        if self.pw is None:
            self.pw = sync_playwright().start()
        headless = os.environ.get("MH_BROWSER_HEADLESS") == "1"
        channels = ["chrome", "msedge", ""] if sys.platform != "linux" else ["chrome", ""]
        if os.environ.get("MH_BROWSER_CHANNEL") is not None:
            channels = [os.environ["MH_BROWSER_CHANNEL"]]
        last = None
        for attempt in range(2):
            for ch in channels:
                prof = _home() / "browser-profile" / (ch or "chromium")
                prof.mkdir(parents=True, exist_ok=True)
                try:
                    self.ctx = self.pw.chromium.launch_persistent_context(
                        str(prof), channel=ch or None, headless=headless, no_viewport=True,
                        args=["--no-first-run", "--no-default-browser-check", "--window-size=1280,900"])
                    self.channel = ch or "chromium"
                    break
                except Exception as e:  # noqa: BLE001
                    last = e
            if self.ctx is not None:
                break
            if attempt == 0:
                self._install_chromium()                      # no Chrome or Edge here: fetch Chromium once
                channels = [""]
        if self.ctx is None:
            raise ToolFailed(f"Could not start a browser: {last}")
        self.ctx.set_default_timeout(20000)
        self.page = self.ctx.pages[0] if self.ctx.pages else self.ctx.new_page()

    # ---------- helpers ------------------------------------------------------------------------------------------
    def settle(self):
        try:
            self.page.wait_for_load_state("domcontentloaded", timeout=15000)
        except Exception:  # noqa: BLE001
            pass
        try:
            self.page.wait_for_load_state("networkidle", timeout=2500)
        except Exception:  # noqa: BLE001
            pass
        from meshpool_harness.core.webfix import settle as _settle   # MH_BROWSER_SETTLE: late menus and pop-ups
        _settle(self.page)

    def snapshot(self, note: str = "") -> str:
        self.settle()
        if os.environ.get("MH_BROWSER_STATES") == "1":
            self.page.evaluate("() => { window.__mpStates = true; }")
        s = self.page.evaluate(SNAPSHOT_JS)
        tabs = len(self.ctx.pages)
        head = [note] if note else []
        head += [f"Page: {s['title'] or '(no title)'}", f"Address: {s['url']}",
                 f"Scroll: {s['scroll']} of {max(0, s['height'] - s['vh'])} px" + (f" | Tabs open: {tabs}" if tabs > 1 else "")]
        body = s["text"].strip()
        return ("\n".join(head) + "\n\nInteractive elements (use the [eN] number):\n" + ("\n".join(s["elements"]) or "(none)")
                + "\n\nVisible text:\n" + (body or "(empty)"))

    def el(self, ref: str):
        ref = str(ref).strip().strip("[]")
        loc = self.page.locator(f'[data-mp-ref="{ref}"]')
        if loc.count() == 0:
            raise ToolFailed(f"No element {ref} on the page now. Call browser_snapshot to get fresh numbers.")
        return loc.first


B = Browser()


def browser_open(url: str) -> str:
    def go():
        B.ensure()
        u = url.strip()
        if "://" not in u and not u.startswith(("about:", "file:")):
            u = "https://" + u
        B.page.goto(u, wait_until="domcontentloaded", timeout=45000)
        return B.snapshot()
    return B.run(go)


def browser_snapshot() -> str:
    def go():
        B.ensure()
        return B.snapshot()
    return B.run(go)


def browser_click(ref: str, double: bool = False) -> str:
    def go():
        B.ensure()
        pages = len(B.ctx.pages)
        e = B.el(ref)
        e.scroll_into_view_if_needed(timeout=5000)
        e.dblclick() if double else e.click()
        note = ""
        for _ in range(8):                                  # a click that opens a new tab does so a moment later
            B.page.wait_for_timeout(250)
            if len(B.ctx.pages) > pages:
                break
        if len(B.ctx.pages) > pages:                        # the click opened a new tab: follow it
            B.page = B.ctx.pages[-1]
            note = "The click opened a new tab; now on that tab."
        return B.snapshot(note)
    return B.run(go)


def browser_type(ref: str, text_: str = "", submit: bool = False, **kw) -> str:
    value = text_ or kw.get("text", "")

    def go():
        B.ensure()
        e = B.el(ref)
        note = ""
        try:
            from meshpool_harness.core.webfix import smart_fill   # read-only and key-driven fields (MH_BROWSER_*)
            note = smart_fill(e, value, 30000) or ""
        except Exception:  # noqa: BLE001  not a plain field (rich editors): type key by key
            e.click()
            B.page.keyboard.type(value, delay=15)
        if submit:
            e.press("Enter")
            B.page.wait_for_timeout(500)
        return B.snapshot(note)
    return B.run(go)


def browser_select(ref: str, option: str) -> str:
    def go():
        B.ensure()
        e = B.el(ref)
        try:
            e.select_option(label=option)
        except Exception:  # noqa: BLE001
            e.select_option(option)
        return B.snapshot()
    return B.run(go)


def browser_press(key: str) -> str:
    def go():
        B.ensure()
        B.page.keyboard.press(key)
        B.page.wait_for_timeout(400)
        return B.snapshot()
    return B.run(go)


def browser_scroll(direction: str = "down", pages: float = 1) -> str:
    def go():
        B.ensure()
        h = B.page.evaluate("innerHeight")
        dy = int(h * 0.85 * float(pages or 1)) * (-1 if str(direction).lower() in ("up", "top") else 1)
        B.page.mouse.wheel(0, dy)
        B.page.wait_for_timeout(400)
        return B.snapshot()
    return B.run(go)


def browser_back() -> str:
    def go():
        B.ensure()
        B.page.go_back(wait_until="domcontentloaded")
        return B.snapshot()
    return B.run(go)


def browser_tabs(switch_to: int | None = None, close: int | None = None) -> str:
    def go():
        B.ensure()
        pages = B.ctx.pages
        if close is not None and 0 <= int(close) < len(pages) and len(pages) > 1:
            pages[int(close)].close()
            pages = B.ctx.pages
            B.page = pages[-1]
        if switch_to is not None and 0 <= int(switch_to) < len(pages):
            B.page = pages[int(switch_to)]
            B.page.bring_to_front()
            return B.snapshot(f"Switched to tab {int(switch_to)}.")
        return "Tabs:\n" + "\n".join(f"{i}: {p.title() or '(no title)'} - {p.url}" + (" (current)" if p == B.page else "")
                                    for i, p in enumerate(pages))
    return B.run(go)


def browser_screenshot(full_page: bool = False) -> list:
    def go():
        B.ensure()
        B.settle()
        png = B.page.screenshot(full_page=bool(full_page), type="png")
        out = Path(os.environ.get("MESHPOOL_PROJECT") or os.getcwd()) / "Meshpool outputs" / "screenshots"
        try:
            out.mkdir(parents=True, exist_ok=True)
            f = out / f"browser-{time.strftime('%Y%m%d-%H%M%S')}.png"
            f.write_bytes(png)
            where = f"Saved to {f}."
        except OSError:
            where = ""
        return [text(f"Screenshot of {B.page.url}. {where}"), image_png(png)]
    return B.run(go)


def browser_wait(seconds: float = 2) -> str:
    def go():
        B.ensure()
        B.page.wait_for_timeout(1000 * min(30.0, max(0.0, float(seconds))))
        return B.snapshot()
    return B.run(go, timeout=60)


def browser_close() -> str:
    def go():
        if B.ctx is not None:
            try:
                B.ctx.close()
            except Exception:  # noqa: BLE001
                pass
        B.ctx = B.page = None
        return "Browser closed."
    return B.run(go)


S = {"type": "string"}
TOOLS = {
    "browser_open": Tool(browser_open, "Open a web address in the browser window and describe the page.", {"url": S}, ["url"]),
    "browser_snapshot": Tool(browser_snapshot, "Describe the current page: address, visible text and numbered "
                             "interactive elements. Call it again after the page changes."),
    "browser_click": Tool(browser_click, "Click an element by its number from the latest snapshot, for example e12.",
                          {"ref": S, "double": {"type": "boolean"}}, ["ref"]),
    "browser_type": Tool(browser_type, "Type into a field by its number. Replaces what is there. submit=true presses Enter.",
                         {"ref": S, "text": S, "submit": {"type": "boolean"}}, ["ref", "text"]),
    "browser_select": Tool(browser_select, "Choose an option in a drop-down by its number and the option's text.",
                           {"ref": S, "option": S}, ["ref", "option"]),
    "browser_press": Tool(browser_press, "Press a key or shortcut on the page, such as Enter, Escape, Tab, "
                          "ArrowDown or Control+A.", {"key": S}, ["key"]),
    "browser_scroll": Tool(browser_scroll, "Scroll the page up or down by screens.",
                           {"direction": {"type": "string", "enum": ["down", "up"]}, "pages": {"type": "number"}}),
    "browser_back": Tool(browser_back, "Go back to the previous page."),
    "browser_tabs": Tool(browser_tabs, "List open tabs, switch to one by index, or close one by index.",
                         {"switch_to": {"type": "integer"}, "close": {"type": "integer"}}),
    "browser_screenshot": Tool(browser_screenshot, "Take a screenshot of the page (saved in the project's "
                               "Meshpool outputs folder). Useful to check layout or for models that can see images.",
                               {"full_page": {"type": "boolean"}}),
    "browser_wait": Tool(browser_wait, "Wait a few seconds for the page to finish something, then describe it.",
                         {"seconds": {"type": "number"}}),
    "browser_close": Tool(browser_close, "Close the browser window."),
}


def _fix_type_args():
    # MCP clients send {"ref":..,"text":..}; the function's parameter is text_ to avoid shadowing text()
    t = TOOLS["browser_type"]
    t.fn = lambda ref, text="", submit=False: browser_type(ref, text_=text, submit=submit)


def main():
    _fix_type_args()
    serve("meshpool-browser", TOOLS, instructions="Use browser_open, then act on the [eN] numbers from the snapshot.")


if __name__ == "__main__":
    main()
