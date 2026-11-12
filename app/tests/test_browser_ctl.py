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


def test_drive_local_page(monkeypatch):
    monkeypatch.setenv("MH_BROWSER_PORT", "8791")
    monkeypatch.setenv("MH_BROWSER_DIFF", "1")
    import importlib
    from engines.browser import ctl
    importlib.reload(ctl)
    d = tempfile.mkdtemp()
    Path(d, "p.html").write_text(PAGE)
    assert ctl.main(["start"]) == 0
    try:
        snap = ctl.call("goto", f"file://{d}/p.html")
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
    monkeypatch.setenv("MH_BROWSER_PORT", "8792")
    monkeypatch.setenv("MH_BROWSER_DIFF", "1")
    import importlib
    import json
    from engines.browser import ctl
    importlib.reload(ctl)
    d = tempfile.mkdtemp()
    Path(d, "f.html").write_text(FORM)
    assert ctl.main(["start"]) == 0
    try:
        snap = ctl.call("goto", f"file://{d}/f.html")
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
