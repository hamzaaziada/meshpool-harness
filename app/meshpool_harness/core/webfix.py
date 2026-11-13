"""Browser fixes shared by the benchmark browser tool (engines/browser/ctl.py) and the app's browser tool
(agent/browser_mcp_server.py), so a fix lands in both. All behaviour changes are behind flags, off by default:
  MH_BROWSER_READONLY  set a read-only (picker) field at once, with a note, instead of waiting for a timeout
  MH_BROWSER_KEYTYPE   retype as key presses when a set value never shows on the page (terminal-style widgets)
  MH_BROWSER_SETTLE    wait until the page is quiet (max 1.5 s) before reading it, so late menus are seen
  MH_BROWSER_STATES    mark elements (invalid), (disabled), (read-only) in snapshots (see STATES_JS)
"""
from __future__ import annotations

import os
SETTLE_JS = """(ms) => new Promise(res => { let t = setTimeout(done, 350); const end = setTimeout(done, ms);
  const ob = new MutationObserver(() => { clearTimeout(t); t = setTimeout(done, 350); });
  function done() { ob.disconnect(); clearTimeout(end); res(true); }
  ob.observe(document.documentElement, {subtree: true, childList: true, attributes: true, characterData: true}); })"""

SET_VALUE_JS = """(el, v) => { el.value = v; el.dispatchEvent(new Event('input', {bubbles: true}));
  el.dispatchEvent(new Event('change', {bubbles: true})); return el.value; }"""


def smart_fill(loc, value: str, timeout: int) -> str | None:
    """fill(); with MH_BROWSER_READONLY=1 a read-only field (one a date or list picker fills) does not hang for the
    whole timeout: its value is set directly with input/change events, and the returned note says so, so the model
    can check that the page accepted it. Returns a note or None."""
    if os.environ.get("MH_BROWSER_READONLY") == "1":
        try:
            ro = loc.evaluate("e => !!(e.readOnly || e.getAttribute('aria-readonly') === 'true')", timeout=2000)
        except Exception:
            ro = False
        if ro:
            got = loc.evaluate(SET_VALUE_JS, value, timeout=2000)
            return (f"note: that field is read-only (a picker normally fills it); its value was set directly to "
                    f"{got!r}. Check that the page shows and accepts it.")
    loc.fill(value, timeout=timeout)
    if os.environ.get("MH_BROWSER_KEYTYPE") == "1" and value.strip():
        # Widgets that read key presses (terminals, some editors and search boxes) ignore a value set by fill(): the
        # text never shows on the page. Then clear it and type real key presses instead.
        try:
            shown = loc.evaluate("""(e, v) => { const t = document.body.innerText || '';
                if (t.includes(v)) return true;
                return ['INPUT', 'SELECT', 'TEXTAREA'].includes(e.tagName) && e.offsetHeight > 2 && e.value === v
                       && getComputedStyle(e).opacity !== '0' && getComputedStyle(e).color !== 'transparent'; }""",
                                 value, timeout=2000)
        except Exception:
            shown = True
        if not shown:
            loc.fill("", timeout=timeout)
            loc.press_sequentially(value, delay=5, timeout=timeout)
            return "note: typed as key presses (this field ignores pasted values)."
    return None



def settle(page, max_ms: int = 1500) -> None:
    """MH_BROWSER_SETTLE=1: return once the DOM has been quiet for 350 ms, or after max_ms. Never raises."""
    if os.environ.get("MH_BROWSER_SETTLE") != "1":
        return
    try:
        page.evaluate(SETTLE_JS, max_ms)
    except Exception:  # noqa: BLE001
        pass


# A JavaScript expression over an element `e` that gives the state suffix a person would see.
STATES_JS = """((e) => { let x = ''; const cls = typeof e.className === 'string' ? e.className : '';
  if (e.getAttribute('aria-invalid') === 'true' || /(^|[-_ ])(error|invalid|has-error|is-invalid)([-_ ]|$)/i.test(cls)
      || (e.validity && !e.validity.valid && e.value)) x += ' (invalid)';
  if (e.disabled || e.getAttribute('aria-disabled') === 'true') x += ' (disabled)';
  if ((e.tagName === 'INPUT' || e.tagName === 'TEXTAREA') && e.readOnly) x += ' (read-only)';
  return x; })"""
