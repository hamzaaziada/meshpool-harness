"""Browser tool flags on local pages (real Chromium, no model calls): MH_BROWSER_SETTLE, MH_BROWSER_READONLY,
MH_BROWSER_STATES, MH_BROWSER_KEYTYPE. Skipped when Playwright or Chromium is missing."""
import os
import socket
import subprocess
import sys
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


def _port():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p


@pytest.fixture(scope="module")
def browser(tmp_path_factory):
    page = tmp_path_factory.mktemp("pg") / "p.html"
    page.write_text(PAGE)
    env = dict(os.environ, MH_BROWSER_PORT=str(_port()), PYTHONPATH=str(APP), MH_BROWSER_DIFF="1",
               MH_BROWSER_SETTLE="1", MH_BROWSER_READONLY="1", MH_BROWSER_STATES="1", MH_BROWSER_KEYTYPE="1")

    def ctl(*a):
        r = subprocess.run([sys.executable, "-m", "engines.browser.ctl", *a], cwd=APP, env=env, capture_output=True,
                           text=True, timeout=90)
        return r.stdout + r.stderr
    if "started" not in ctl("start"):
        pytest.skip("browser did not start")
    ctl("goto", page.as_uri())
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
