"""Browser control for text-only models: a persistent Chromium (Playwright) behind a tiny local HTTP daemon.

The page is shown as a numbered list of what you can act on, e.g. `[3] button "Sign in"`, plus visible text,
so any model can drive it without screenshots (and with few tokens).

  python -m engines.browser.ctl start            # start the browser daemon (headless unless MH_HEADLESS=0)
  python -m engines.browser.ctl goto <url>
  python -m engines.browser.ctl snapshot         # numbered elements + page text (short)
  python -m engines.browser.ctl click <n>
  python -m engines.browser.ctl type <n> <text>  # replaces the field's value
  python -m engines.browser.ctl press <key>      # e.g. Enter, Tab
  python -m engines.browser.ctl hover <n>        # menus that open on hover
  python -m engines.browser.ctl click_text <text>  # click the visible element showing exactly this text
  python -m engines.browser.ctl select <n> <option text>
  python -m engines.browser.ctl back | screenshot <file.png> | stop
"""
from __future__ import annotations

import json
import os
import sys
import threading
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer

PORT = int(os.environ.get("MH_BROWSER_PORT", "8765"))
MARK = "data-mh-ref"
SNAP_JS = """(mark) => {
  const sel = 'a,button,input,select,textarea,[role=button],[role=link],[role=checkbox],[role=tab],[role=menuitem],[onclick],summary';
  const out = []; let n = 0;
  document.querySelectorAll('[' + mark + ']').forEach(e => e.removeAttribute(mark));
  const cand = new Set(document.querySelectorAll(sel));
  // elements made clickable by script (no tag/role hint) usually show a pointer cursor: include the innermost ones
  for (const e of document.querySelectorAll('body *')) {
    if (cand.has(e) || e.children.length > 3) continue;
    const c = getComputedStyle(e).cursor;
    if (c === 'pointer' && !(e.parentElement && getComputedStyle(e.parentElement).cursor === 'pointer')) cand.add(e);
    else if (/(^|[-_ ])(btn|button|icon|star|trash|reply|forward|more|share|like|retweet|close|delete|option|menu-item|tab)([-_ ]|$)/i.test(e.className || '')) cand.add(e);
  }
  for (const e of cand) {
    const r = e.getBoundingClientRect(), st = getComputedStyle(e);
    if (r.width === 0 || r.height === 0 || st.visibility === 'hidden' || st.display === 'none') continue;
    n += 1; e.setAttribute(mark, String(n));
    const tag = e.tagName.toLowerCase(), type = e.getAttribute('type') || '';
    let role = e.getAttribute('role') || (tag === 'a' ? 'link' : tag === 'select' ? 'select' : tag === 'textarea' ? 'textbox'
                 : tag === 'input' ? (['checkbox','radio','submit','button'].includes(type) ? type : 'textbox') : tag);
    if (window.__mhTyped && !e.getAttribute('role')) {   // typed roles: a textarea and an <input type=email> are not the same widget
      if (tag === 'textarea') role = 'textarea';
      else if (role === 'textbox' && type && type !== 'text') role = 'textbox[' + type + ']';
      const fmt = {date: 'YYYY-MM-DD', 'datetime-local': 'YYYY-MM-DDThh:mm', month: 'YYYY-MM', time: 'hh:mm'}[type];
      if (fmt && tag === 'input') role += ' (format ' + fmt + ')';
    }
    let fieldName = '';
    if (tag === 'input' || tag === 'textarea' || tag === 'select') {   // form fields: the label a person reads
      fieldName = (e.labels && e.labels.length ? e.labels[0].innerText : '') || e.getAttribute('aria-label') || e.getAttribute('placeholder') || '';
      if (!fieldName && e.previousElementSibling) fieldName = e.previousElementSibling.innerText || '';
      if (!fieldName && e.parentElement && e.parentElement.children.length <= 3) {   // parent text minus its buttons/links
        let t = e.parentElement.innerText || '';
        for (const b of e.parentElement.querySelectorAll('button,a,[role=button],input[type=submit],input[type=button]')) {
          const bt = (b.innerText || b.value || '').trim(); if (bt) t = t.replace(bt, '');
        }
        fieldName = t;
      }
      fieldName = fieldName.trim().replace(/\\s+/g, ' ').slice(0, 80);
      if (['submit','button'].includes(type)) fieldName = e.value || fieldName;
    }
    let name = fieldName || (e.getAttribute('aria-label') || e.innerText || e.value || e.getAttribute('placeholder') || e.getAttribute('name') || e.getAttribute('title') || e.getAttribute('alt') || '').trim().replace(/\\s+/g, ' ').slice(0, 80);
    if (!name && (tag === 'input' || tag === 'textarea' || tag === 'select')) name = e.getAttribute('name') || e.id || '';
    if (!name && !(tag === 'input' || tag === 'textarea' || tag === 'select')) {   // icons: what they are and which row
      const cls = (typeof e.className === 'string' ? e.className : '').trim().split(/\\s+/).slice(0, 2).join(' ');
      let row = e.parentElement, k = 0;
      while (row && k < 4 && (row.innerText || '').trim().length < 3) { row = row.parentElement; k++; }
      const ctx = row ? (row.innerText || '').trim().replace(/\\s+/g, ' ').slice(0, 50) : '';
      name = (cls ? cls + ' icon' : 'icon') + (ctx ? ' in "' + ctx + '"' : '');
    }
    let extra = '';
    if (tag === 'input' && !['checkbox','radio','submit','button'].includes(type) && e.value) extra = ' value="' + e.value.slice(0, 40) + '"';
    if (type === 'checkbox' || type === 'radio') extra = e.checked ? ' (checked)' : ' (unchecked)';
    out.push('[' + n + '] ' + role + ' "' + name + '"' + extra);
  }
  const text = (document.body ? document.body.innerText : '').replace(/\\n{3,}/g, '\\n\\n').slice(0, 3000);
  return {title: document.title, url: location.href, elements: out.slice(0, 150), text};
}"""


_DATE_FMT = {"date": "YYYY-MM-DD", "datetime-local": "YYYY-MM-DDThh:mm", "month": "YYYY-MM", "time": "hh:mm",
             "week": "YYYY-Www"}


def _fit_value(loc, text: str) -> str:
    """HTML date/time inputs only accept ISO values; a US-style date such as 04/03/2018 is silently dropped by fill().
    Convert the common forms so typing a date works the way a person would type it (MH_BROWSER_DATE_FIX=0: off)."""
    if os.environ.get("MH_BROWSER_DATE_FIX", "1") != "1":
        return text
    import re
    try:
        kind = (loc.get_attribute("type", timeout=2000) or "").lower()
    except Exception:
        return text
    t = text.strip()
    if kind == "date":
        m = re.fullmatch(r"(\d{1,2})[/.-](\d{1,2})[/.-](\d{4})", t)          # MM/DD/YYYY (US order)
        if m:
            return f"{m.group(3)}-{int(m.group(1)):02d}-{int(m.group(2)):02d}"
        m = re.fullmatch(r"(\d{4})[/.](\d{1,2})[/.](\d{1,2})", t)            # YYYY/MM/DD
        if m:
            return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    if kind == "month":
        m = re.fullmatch(r"(\d{1,2})[/.-](\d{4})", t)
        if m:
            return f"{m.group(2)}-{int(m.group(1)):02d}"
    return text


def _serve():
    from playwright.sync_api import sync_playwright
    pw = sync_playwright().start()
    kw = {"headless": os.environ.get("MH_HEADLESS", "1") != "0", "args": ["--no-sandbox"]}
    exe = os.environ.get("MH_CHROMIUM_PATH") or next((p for p in ("/opt/pw-browsers/chromium-1243/chrome-linux64/chrome",)
                                                      if os.path.exists(p)), None)
    try:
        browser = pw.chromium.launch(**kw)
    except Exception:
        if not exe:
            raise
        browser = pw.chromium.launch(executable_path=exe, **kw)   # preinstalled Chromium when versions differ
    page = browser.new_page()
    lock = threading.Lock()
    state = {}

    def ref(n):
        return page.locator(f"[{MARK}='{int(n)}']").first

    def locate(t):
        """A batch step's target: {"n": 3} | {"role": "button", "name": "Save"} | {"label": "Email"} |
        {"placeholder": "Search"} | {"text": "Next"}. Names by role/label/text survive page changes, numbers do not."""
        if isinstance(t, (int, str)) and str(t).isdigit():
            return ref(t)
        if not isinstance(t, dict):
            raise ValueError(f"bad target {t!r}")
        if "n" in t:
            return ref(t["n"])
        if "field" in t:                          # a form field by the label text a person sees next to it
            name = str(t["field"])
            for loc in (page.get_by_label(name, exact=True), page.get_by_placeholder(name, exact=True),
                        page.locator(f"xpath=//*[normalize-space(text())={json.dumps(name)}]/following::*[self::input or self::textarea or self::select][1]")):
                if loc.count() and loc.first.is_visible():
                    return loc.first
            raise LookupError(f"no field labelled {name!r}")
        exact = t.get("exact", True)
        if "role" in t:
            loc = page.get_by_role(t["role"], name=t.get("name"), exact=exact) if t.get("name") else page.get_by_role(t["role"])
        elif "label" in t:
            loc = page.get_by_label(t["label"], exact=exact)
        elif "placeholder" in t:
            loc = page.get_by_placeholder(t["placeholder"], exact=exact)
        elif "text" in t:
            loc = page.get_by_text(t["text"], exact=exact)
        else:
            raise ValueError(f"bad target {t!r}")
        for i in range(min(loc.count(), 20)):     # first visible match, like a person would pick
            if loc.nth(i).is_visible():
                return loc.nth(i)
        if loc.count() == 0:
            raise LookupError(f"no element matches {t!r}")
        return loc.first

    def batch(steps):
        """Code-mode step: run several actions in one command and report each, stopping at the first failure, so
        a multi-field form or a menu path costs one model call instead of one per action."""
        done = []
        for i, s in enumerate(steps):
            op = s.get("do")
            try:
                if op in ("click", "dblclick", "hover", "check", "uncheck"):
                    getattr(locate(s["target"]), op)(timeout=5000)
                elif op == "type":
                    loc = locate(s["target"])
                    loc.fill(_fit_value(loc, str(s.get("text", ""))), timeout=5000)
                elif op == "select":
                    locate(s["target"]).select_option(label=str(s["option"]), timeout=5000)
                elif op == "press":
                    (locate(s["target"]).press if s.get("target") else page.keyboard.press)(s["key"])
                elif op == "wait_text":                # e.g. wait for a result to appear before the next step
                    page.get_by_text(s["text"], exact=s.get("exact", False)).first.wait_for(timeout=int(s.get("ms", 5000)))
                elif op == "expect_text":              # a check: fail the batch if this text is not on the page
                    if s["text"] not in page.evaluate("() => document.body ? document.body.innerText : ''"):
                        raise AssertionError(f"text not on page: {s['text']!r}")
                else:
                    raise ValueError(f"unknown step {op!r}")
                done.append(f"{i + 1}. {op} ok")
            except Exception as e:
                done.append(f"{i + 1}. {op} FAILED: {type(e).__name__}: {str(e).splitlines()[0][:160]}")
                return done, False
        return done, True

    def act(cmd, args):
        if cmd == "batch":
            try:
                steps = json.loads(" ".join(args))
                assert isinstance(steps, list) and all(isinstance(s, dict) for s in steps)
            except Exception:
                return {"error": 'batch takes a JSON list of steps, e.g. [{"do":"type","target":{"label":"Name"},'
                                 '"text":"Ann"},{"do":"click","target":{"role":"button","name":"Submit"}}]'}
            report, ok = batch(steps)
            state["batch"] = {"steps": report, "ok": ok}
            cmd = "batch_done"
        elif cmd == "goto":
            page.goto(args[0], wait_until="domcontentloaded", timeout=30000)
        elif cmd == "click":
            ref(args[0]).click(timeout=10000)
        elif cmd == "type":
            loc = ref(args[0])
            loc.fill(_fit_value(loc, " ".join(args[1:])), timeout=10000)
        elif cmd == "hover":
            ref(args[0]).hover(timeout=10000)
        elif cmd == "click_text":                 # smallest visible element whose text is exactly this
            txt = " ".join(args)
            loc = page.get_by_text(txt, exact=True)
            n = loc.count()
            if n == 0:
                return {"error": f"no visible element with text {txt!r}"}
            target = None
            for i in range(n):
                el = loc.nth(i)
                if el.is_visible():
                    target = el
                    break
            (target or loc.first).click(timeout=10000, force=target is None)
        elif cmd == "press":
            page.keyboard.press(args[0])
        elif cmd == "select":
            ref(args[0]).select_option(label=" ".join(args[1:]), timeout=10000)
        elif cmd == "back":
            page.go_back()
        elif cmd == "_lock":                      # grader: lock _eval behind a token only the grader holds
            state["token"] = args[0]
            return {"locked": True}
        elif cmd == "_eval":                      # grader/setup only (not documented for agents)
            if state.get("token") and (len(args) < 2 or args[1] != state["token"]):
                return {"error": "not available"}
            return {"value": page.evaluate(args[0])}
        elif cmd == "screenshot":
            page.screenshot(path=args[0], full_page=True)
            return {"saved": args[0]}
        elif cmd not in ("snapshot", "batch_done"):
            return {"error": f"unknown command {cmd}"}
        try:
            page.wait_for_load_state("domcontentloaded", timeout=5000)
        except Exception:
            pass
        if os.environ.get("MH_BROWSER_TYPED_ROLES", "1") == "1":
            page.evaluate("() => { window.__mhTyped = true; }")
        snap = page.evaluate(SNAP_JS, MARK)
        prev = state.get("last")
        state["last"] = snap
        b = state.pop("batch", None) if cmd == "batch_done" else None
        if cmd in ("snapshot", "goto") or os.environ.get("MH_BROWSER_DIFF", "0") != "1" or not prev or prev["url"] != snap["url"]:
            return {**snap, **({"batch": b} if b else {})}
        # after an action on the same page: send only what changed (fewer tokens per step)
        old_lines = set(prev["text"].splitlines())
        new_text = "\n".join(l for l in snap["text"].splitlines() if l.strip() and l not in old_lines)
        out = {"title": snap["title"], "url": snap["url"], "text_changed": new_text[:1500] or "(no visible text change)"}
        out["elements"] = snap["elements"] if snap["elements"] != prev["elements"] else None
        if b:
            out["batch"] = b
        return out

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_POST(self):
            req = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
            if req.get("cmd") == "stop":
                self._send({"stopped": True})
                threading.Thread(target=server.shutdown).start()
                return
            with lock:
                try:
                    res = act(req.get("cmd"), req.get("args", []))
                except Exception as e:
                    res = {"error": f"{type(e).__name__}: {str(e).splitlines()[0][:200]}"}
            self._send(res)

        def _send(self, obj):
            b = json.dumps(obj).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(b)))
            self.end_headers()
            self.wfile.write(b)

    server = HTTPServer(("127.0.0.1", PORT), H)
    server.serve_forever()
    browser.close()
    pw.stop()


def call(cmd, *args):
    req = urllib.request.Request(f"http://127.0.0.1:{PORT}", data=json.dumps({"cmd": cmd, "args": list(args)}).encode(),
                                 method="POST", headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read())


def render(res: dict) -> str:
    if "error" in res:
        return "ERROR: " + res["error"]
    if res.get("batch"):                           # code-mode report first, then the page after the batch
        b = res["batch"]
        head = ("batch: all steps ok" if b["ok"] else "batch STOPPED at a failed step") + "\n" + "\n".join(b["steps"])
        return head + "\n" + render({k: v for k, v in res.items() if k != "batch"})
    if "text_changed" in res:                      # diff after an action
        els = ("\n".join(res["elements"]) if res.get("elements") else "(elements unchanged; same numbers)")
        return f"{res['title']} | {res['url']}\n{els}\n--- new text ---\n{res['text_changed']}"
    if "elements" not in res:
        return json.dumps(res)
    return (f"{res['title']} | {res['url']}\n" + "\n".join(res["elements"]) + "\n--- page text ---\n" + res["text"])


def main(argv=None):
    import subprocess
    import time
    a = list(argv if argv is not None else sys.argv[1:])
    if not a:
        print(__doc__)
        return 1
    if a[0] == "start":
        try:
            call("snapshot")
            print("browser already running")
            return 0
        except Exception:
            pass
        subprocess.Popen([sys.executable, "-m", "engines.browser.ctl", "_serve"], stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, start_new_session=True)
        for _ in range(60):
            try:
                call("snapshot")
                print("browser started")
                return 0
            except Exception:
                time.sleep(0.5)
        print("ERROR: browser did not start")
        return 1
    if a[0] == "_serve":
        _serve()
        return 0
    print(render(call(a[0], *a[1:])))
    return 0


if __name__ == "__main__":
    sys.exit(main())
