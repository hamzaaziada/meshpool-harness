"""Browser tool flags on local pages (real Chromium, no model calls): MH_BROWSER_SETTLE, MH_BROWSER_READONLY,
MH_BROWSER_STATES, MH_BROWSER_KEYTYPE. Skipped when Playwright or Chromium is missing."""
import functools
import http.server
import os
import shlex
import subprocess
import sys
import threading
from pathlib import Path

import pytest

pytest.importorskip("playwright")
APP = Path(__file__).resolve().parents[1]

PAGE = """<!doctype html><html><body>
<label>Name <input id="name"></label>
<label>Notes <textarea id="notes"></textarea></label>
<label>Date <input id="date" readonly></label>
<input id="bad" class="error" value="x" aria-label="Bad">
<input id="keys" style="opacity:0;width:1px;height:1px" aria-label="Keys"><div id="line"></div>
<button id="go" onclick="setTimeout(()=>{const b=document.createElement('button');b.textContent='Late';document.body.appendChild(b)},300)">Go</button>
<script>
document.getElementById('keys').addEventListener('keydown', e => {
  if (e.key.length === 1) document.getElementById('line').textContent += e.key; });
</script></body></html>"""


def _serve_dir(d) -> str:
    h = functools.partial(type("Q", (http.server.SimpleHTTPRequestHandler,), {"log_message": lambda *a: None}),
                          directory=str(d))
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), h)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{srv.server_address[1]}"


@pytest.fixture(scope="module")
def browser(tmp_path_factory):
    pg = tmp_path_factory.mktemp("pg")
    (pg / "p.html").write_text(PAGE)
    # the command the browser engine gives the model: ctl.py by its path, no PYTHONPATH, run from another folder
    env = {k: v for k, v in os.environ.items() if k not in ("PYTHONPATH", "MH_BROWSER_PORT")}
    env.update(MESHPOOL_HOME=str(tmp_path_factory.mktemp("home")), MH_BROWSER_DIFF="1", MH_BROWSER_SETTLE="1",
               MH_BROWSER_READONLY="1", MH_BROWSER_STATES="1", MH_BROWSER_KEYTYPE="1")
    sys.path.insert(0, str(APP))
    from engines.browser.engine import ctl_command
    cmd = ctl_command()

    def ctl(*a):
        r = subprocess.run(cmd + " " + " ".join(shlex.quote(x) for x in a), shell=True, cwd=pg, env=env,
                           capture_output=True, text=True, timeout=90)
        return r.stdout + r.stderr
    if "started" not in ctl("start"):
        pytest.skip("browser did not start")
    ctl("goto", f"{_serve_dir(pg)}/p.html")
    yield ctl
    ctl("stop")


def _n(snapshot, label):
    return next(line.split("]")[0][1:] for line in snapshot.splitlines() if f'"{label}' in line)


def test_flags_together(browser):
    snap = browser("snapshot")
    assert "(read-only)" in snap and "(invalid)" in snap
    out = browser("type", _n(snap, "Name"), "Ann")
    assert "note:" not in out                                      # a normal input keeps the fast path
    out = browser("type", _n(snap, "Notes"), "hello there")
    assert "note:" not in out                                      # so does a normal textarea
    out = browser("type", _n(snap, "Date"), "10/23/2016")
    assert "read-only" in out and 'value="10/23/2016"' in out      # set at once, no 10 s hang
    out = browser("type", _n(snap, "Keys"), "ls")
    assert "key presses" in out and "ls" in out                    # key listeners see the text
    out = browser("click", _n(snap, "Go"))
    assert '"Late"' in out                                         # element added 300 ms later is listed
