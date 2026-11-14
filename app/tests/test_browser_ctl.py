"""Browser control: numbered snapshot, type, click, select, on a local page. No model calls."""
import os
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
pytest.importorskip("playwright")

PAGE = """<html><head><title>Signup</title></head><body>
<input name="email" placeholder="Email">
<select name="plan"><option>Free</option><option>Pro</option></select>
<button onclick="document.getElementById('out').innerText='Thanks '+document.querySelector('[name=email]').value+' ('+document.querySelector('[name=plan]').value+')'">Sign up</button>
<p id="out"></p></body></html>"""


def _serve_dir(d) -> str:
    """Serve folder d over local HTTP (the daemon refuses file:// URLs); returns the base URL."""
    import functools
    import http.server
    import threading
    h = functools.partial(type("Q", (http.server.SimpleHTTPRequestHandler,), {"log_message": lambda *a: None}),
                          directory=str(d))
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), h)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{srv.server_address[1]}"


def _env(monkeypatch):
    monkeypatch.delenv("MH_BROWSER_PORT", raising=False)       # the daemon picks a free port itself
    monkeypatch.setenv("MESHPOOL_HOME", tempfile.mkdtemp())     # private state file per test
    monkeypatch.setenv("MH_BROWSER_DIFF", "1")


def test_drive_local_page(monkeypatch):
    _env(monkeypatch)
    import importlib
    from engines.browser import ctl
    importlib.reload(ctl)
    d = tempfile.mkdtemp()
    Path(d, "p.html").write_text(PAGE)
    assert ctl.main(["start"]) == 0
    try:
        snap = ctl.call("goto", f"{_serve_dir(d)}/p.html")
        els = "\n".join(snap["elements"])
        assert '[1] textbox "Email"' in els and "select" in els and 'button "Sign up"' in els
        ctl.call("type", "1", "a@b.co")
        ctl.call("select", "2", "Pro")
        res = ctl.call("click", "3")
        assert "Thanks a@b.co (Pro)" in res["text_changed"] and res["elements"] is None   # diff after an action
        assert "Thanks a@b.co (Pro)" in ctl.call("snapshot")["text"]                    # full view on request
    finally:
        ctl.call("stop")


FORM = """<html><head><title>Login</title></head><body>
<div><label>Username</label><input id="u"></div>
<div><label>Password</label><input id="p" type="password"></div>
<label><input type="checkbox" id="r"> Remember me</label>
<button onclick="document.getElementById('out').innerText='Hi '+document.getElementById('u').value+(document.getElementById('r').checked?' (remembered)':'')">Log in</button>
<p id="out"></p></body></html>"""


def test_batch_runs_steps_in_one_call(monkeypatch):
    _env(monkeypatch)
    import importlib
    import json
    from engines.browser import ctl
    importlib.reload(ctl)
    d = tempfile.mkdtemp()
    Path(d, "f.html").write_text(FORM)
    assert ctl.main(["start"]) == 0
    try:
        snap = ctl.call("goto", f"{_serve_dir(d)}/f.html")
        els = "\n".join(snap["elements"])
        assert 'textbox "Username"' in els and 'checkbox "Remember me"' in els   # fields named by their visible label
        steps = [{"do": "type", "target": {"field": "Username"}, "text": "ann"},
                 {"do": "type", "target": {"field": "Password"}, "text": "pw1"},
                 {"do": "check", "target": {"label": "Remember me"}},
                 {"do": "click", "target": {"role": "button", "name": "Log in"}},
                 {"do": "expect_text", "text": "Hi ann (remembered)"}]
        res = ctl.call("batch", json.dumps(steps))
        assert res["batch"]["ok"] and len(res["batch"]["steps"]) == 5
        assert "batch: all steps ok" in ctl.render(res)
        bad = ctl.call("batch", json.dumps([{"do": "click", "target": {"text": "Nope"}},
                                            {"do": "click", "target": {"role": "button", "name": "Log in"}}]))
        assert not bad["batch"]["ok"] and len(bad["batch"]["steps"]) == 1 and "FAILED" in bad["batch"]["steps"][0]
        assert "error" in ctl.call("batch", "not json")
    finally:
        ctl.call("stop")


def test_control_port_is_private(monkeypatch):
    """Token, exact Host and a JSON body on every request; http(s) only; screenshots inside the start folder;
    a free port (not the UI's 8765) recorded in a state file only this user can read."""
    import json
    import stat
    import urllib.error
    import urllib.request
    _env(monkeypatch)
    import importlib
    from engines.browser import ctl
    importlib.reload(ctl)
    d = tempfile.mkdtemp()
    Path(d, "p.html").write_text(PAGE)
    work = tempfile.mkdtemp()
    assert ctl.main(["start", work]) == 0
    try:
        st = json.loads(ctl.state_path().read_text())
        assert st["port"] != 8765 and len(st["token"]) >= 32 and st["outdir"] == os.path.realpath(work)
        if os.name != "nt":
            assert stat.S_IMODE(ctl.state_path().stat().st_mode) == 0o600
        url = f"http://127.0.0.1:{st['port']}"
        body = json.dumps({"cmd": "snapshot", "args": []}).encode()
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

        def status(headers):
            try:
                with opener.open(urllib.request.Request(url, data=body, method="POST", headers=headers), timeout=30) as r:
                    return r.status
            except urllib.error.HTTPError as e:
                return e.code
        good = {"Content-Type": "application/json", "X-MH-Token": st["token"]}
        assert status(good) == 200
        assert status({"Content-Type": "application/json"}) == 403                     # no token
        assert status(dict(good, **{"X-MH-Token": "x" * 43})) == 403                   # wrong token
        assert status(dict(good, **{"Content-Type": "text/plain"})) == 403             # no-cors form post
        assert status(dict(good, Host=f"evil.example:{st['port']}")) == 403            # DNS rebinding
        assert "error" in ctl.call("goto", f"file://{d}/p.html")
        assert "error" in ctl.call("goto", "javascript:alert(1)")
        assert "error" in ctl.call("goto", "chrome://version")
        ctl.call("goto", f"{_serve_dir(d)}/p.html")
        assert "error" in ctl.call("screenshot", os.path.join(d, "out.png"))           # outside the start folder
        assert "error" in ctl.call("screenshot", "../out.png")
        saved = ctl.call("screenshot", "shot.png")["saved"]
        assert saved == os.path.join(os.path.realpath(work), "shot.png") and os.path.getsize(saved) > 0
    finally:
        ctl.call("stop")
