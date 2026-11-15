"""Guard switches, additive test guard, feature defaults, timing log. No model calls."""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from meshpool_harness.core import guards as G  # noqa: E402
from meshpool_harness.core.client import MockClient, tool_call  # noqa: E402
from meshpool_harness.core.loop import run  # noqa: E402
from meshpool_harness.core.tools import Workspace  # noqa: E402


def _serve_dir(d) -> str:
    """Serve folder d over local HTTP (the browser daemon refuses file:// URLs); returns the base URL."""
    import functools
    import http.server
    import threading
    h = functools.partial(type("Q", (http.server.SimpleHTTPRequestHandler,), {"log_message": lambda *a: None}),
                          directory=str(d))
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), h)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{srv.server_address[1]}"


def test_guard_defaults_and_overrides(monkeypatch):
    monkeypatch.delenv("MH_GUARDS", raising=False)
    monkeypatch.delenv("MH_TEST_GUARD", raising=False)
    assert G.enabled("repeat_failure") and G.enabled("test_guard") and G.enabled("deliverables")
    assert not G.enabled("progress") and not G.enabled("explore_lock") and not G.enabled("finish_check")
    assert G.test_guard_mode() == "additive"
    monkeypatch.setenv("MH_GUARDS", "progress")
    assert G.enabled("progress") and not G.enabled("repeat_failure")
    monkeypatch.setenv("MH_GUARDS", "none")
    assert not G.enabled("test_guard")
    monkeypatch.setenv("MH_GUARDS", "all")
    assert all(G.enabled(g) for g in G.ALL_GUARDS)


def _repo():
    d = Path(tempfile.mkdtemp())
    (d / "tests").mkdir()
    (d / "tests" / "test_a.py").write_text("def test_one():\n    assert 1 == 1\n")
    (d / "a.py").write_text("x = 1\n")
    for c in (["git", "init", "-q"], ["git", "add", "-A"],
              ["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "init"]):
        subprocess.run(c, cwd=d, check=True)
    return d


@pytest.mark.parametrize("mode,change,blocked", [
    ("additive", "append", False), ("additive", "modify", True), ("strict", "append", True), ("off", "modify", False)])
def test_test_guard_modes(monkeypatch, mode, change, blocked):
    monkeypatch.setenv("MH_TEST_GUARD", mode)
    monkeypatch.delenv("MH_GUARDS", raising=False)
    d = _repo()
    t = d / "tests" / "test_a.py"
    if change == "append":
        t.write_text(t.read_text() + "\n\ndef test_two():\n    assert 2 == 2\n")
    else:
        t.write_text("def test_one():\n    assert True\n")
    ws = Workspace(str(d), str(d / ".run"))
    g = G.Guards({"task_id": "t"}, ws, require_check=False).check_finish([])
    assert (g is not None and "test_tampering" in g) == blocked


def test_feature_defaults(monkeypatch):
    from engines.code.engine import feature
    monkeypatch.delenv("MH_ESCALATE", raising=False)
    monkeypatch.delenv("MH_REGRESSION_GATE", raising=False)
    monkeypatch.delenv("MH_TESTCMD", raising=False)
    assert not feature("MH_ESCALATE") and not feature("MH_REGRESSION_GATE") and feature("MH_TESTCMD")
    monkeypatch.setenv("MH_ESCALATE", "1")                          # each feature can be added back on its own
    assert feature("MH_ESCALATE")


def test_timing_log(monkeypatch, tmp_path):
    log = tmp_path / "timing.jsonl"
    monkeypatch.setenv("MH_TIMING_LOG", str(log))
    d = tmp_path / "w"
    d.mkdir()
    (d / "f.txt").write_text("hello\n")
    client = MockClient([tool_call("read_file", {"path": "f.txt"}),
                         tool_call("finish", {"answer": "done", "evidence": []})])
    ws = Workspace(str(d), str(d / ".run"))
    r = run("read f.txt", client=client, model="m", profile=None, ws=ws, ctx={"task_id": "tt"}, system_text="s",
            require_check=False)
    assert r.ok
    row = json.loads(log.read_text().splitlines()[-1])
    assert row["task_id"] == "tt" and row["model_calls"] == 2
    assert row["model_s"] >= 0 and row["tool_s"] >= 0 and row["wall_s"] >= row["model_s"]


def test_typed_roles(monkeypatch):
    pytest.importorskip("playwright")
    monkeypatch.delenv("MH_BROWSER_PORT", raising=False)   # a free port, chosen by the daemon
    monkeypatch.setenv("MESHPOOL_HOME", tempfile.mkdtemp())
    monkeypatch.setenv("MH_BROWSER_TYPED_ROLES", "1")
    import importlib
    from engines.browser import ctl
    importlib.reload(ctl)
    d = tempfile.mkdtemp()
    Path(d, "w.html").write_text('<html><body><input value="a"><textarea>b</textarea><input type="email" value="c">'
                                 '</body></html>')
    assert ctl.main(["start"]) == 0
    try:
        els = "\n".join(ctl.call("goto", f"{_serve_dir(d)}/w.html")["elements"])
        assert '[1] textbox "' in els and '[2] textarea "' in els and '[3] textbox[email] "' in els
    finally:
        ctl.call("stop")


def test_date_inputs_accept_us_dates(monkeypatch):
    pytest.importorskip("playwright")
    monkeypatch.delenv("MH_BROWSER_PORT", raising=False)   # a free port, chosen by the daemon
    monkeypatch.setenv("MESHPOOL_HOME", tempfile.mkdtemp())
    import importlib
    from engines.browser import ctl
    importlib.reload(ctl)
    d = tempfile.mkdtemp()
    Path(d, "d.html").write_text('<html><body><label>When <input type="date" id="w"></label></body></html>')
    assert ctl.main(["start"]) == 0
    try:
        els = "\n".join(ctl.call("goto", f"{_serve_dir(d)}/d.html")["elements"])
        assert "textbox[date] (format YYYY-MM-DD)" in els
        ctl.call("type", "1", "04/03/2018")
        assert ctl.call("_eval", "() => document.getElementById('w').value")["value"] == "2018-04-03"
        ctl.call("batch", json.dumps([{"do": "type", "target": {"n": 1}, "text": "12/31/2019"}]))
        assert ctl.call("_eval", "() => document.getElementById('w').value")["value"] == "2019-12-31"
    finally:
        ctl.call("stop")


def test_test_guard_clears_after_revert_without_git(tmp_path):
    """No git repo: the guard judges written test files by whether they still differ from what the agent first saw,
    so a revert clears it (it used to block every finish for the rest of the run)."""
    (tmp_path / "tests").mkdir()
    t = tmp_path / "tests" / "test_one.py"
    t.write_text("def test_one():\n    assert True\n")
    ws = Workspace(str(tmp_path), str(tmp_path / ".run"))
    ws.read_file("tests/test_one.py")
    ws.edit("tests/test_one.py", "assert True", "assert 1")
    g = G.Guards({"task_id": "t"}, ws, require_check=False)
    assert "test_tampering" in (g.check_finish([]) or "")
    ws.write_file("tests/test_one.py", "def test_one():\n    assert True\n")
    assert g.check_finish([]) is None


def _docker_ok():
    import shutil
    import subprocess
    return bool(shutil.which("docker")) and subprocess.run(["docker", "info"], capture_output=True).returncode == 0


@pytest.mark.skipif(not _docker_ok(), reason="needs a running Docker daemon")
def test_test_guard_reads_git_inside_an_unmounted_container(tmp_path):
    """Files only in the container: the guard runs git there, so additions are allowed and a revert clears it."""
    import subprocess
    img = subprocess.run(["docker", "images", "-q"], capture_output=True, text=True).stdout.split()
    base = next((i for i in img if subprocess.run(["docker", "run", "--rm", i, "git", "--version"],
                                                  capture_output=True).returncode == 0), None)
    if not base:
        pytest.skip("no local image with git")
    name = "mh-guard-test"
    subprocess.run(["docker", "rm", "-f", name], capture_output=True)
    subprocess.run(["docker", "run", "-d", "--name", name, base, "sleep", "300"], check=True, capture_output=True)
    try:
        subprocess.run(["docker", "exec", name, "bash", "-c",
                        "mkdir -p /r/tests && cd /r && git init -q && printf 'def test_a():\\n    assert True\\n' "
                        "> tests/test_a.py && git add -A && git -c user.email=x@y -c user.name=x commit -qm i"],
                       check=True, capture_output=True)
        ws = Workspace(str(tmp_path), str(tmp_path / ".run"), container=name, container_dir="/r", mounted=False)
        ws.read_file("tests/test_a.py")
        ws.edit("tests/test_a.py", "assert True\n", "assert True\n\ndef test_b():\n    assert 2\n")
        g = G.Guards({"task_id": "t"}, ws, require_check=False)
        assert g.check_finish([]) is None                                   # added a test: allowed
        ws.read_file("tests/test_a.py")
        ws.edit("tests/test_a.py", "def test_a():\n    assert True", "def test_a():\n    assert 0")
        assert "test_tampering" in (g.check_finish([]) or "")              # changed an existing line
        ws.bash("git checkout -- tests/test_a.py")
        assert g.check_finish([]) is None                                   # reverted: cleared
    finally:
        subprocess.run(["docker", "rm", "-f", name], capture_output=True)
