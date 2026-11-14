"""ACP client and Pi gate regressions, against a fake ACP agent that keeps its sessions in a file (so a new process
can load them) and can exit on request."""
import json
import os
import shutil
import subprocess
import sys
import textwrap
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402

from meshpool_harness import config as C  # noqa: E402
from meshpool_harness.harnesses.acp import ACPAgent  # noqa: E402

FAKE = textwrap.dedent('''
    import json, os, subprocess, sys
    store, log = sys.argv[1], sys.argv[2]
    def send(o): sys.stdout.write(json.dumps(o) + "\\n"); sys.stdout.flush()
    def upd(sid, u): send({"jsonrpc": "2.0", "method": "session/update", "params": {"sessionId": sid, "update": u}})
    def known(): return json.load(open(store)) if os.path.exists(store) else []
    def note(m):
        with open(log, "a") as f: f.write(json.dumps(m) + "\\n")
    mine, waiting, kids = set(), {}, []
    for line in sys.stdin:
        m = json.loads(line)
        if "method" not in m:                         # reply to our permission request
            sid, rid = waiting.pop("perm")
            upd(sid, {"sessionUpdate": "agent_message_chunk",
                      "content": {"type": "text", "text": "chose " + m["result"]["outcome"].get("optionId", "cancelled")}})
            send({"jsonrpc": "2.0", "id": rid, "result": {"stopReason": "end_turn"}})
            continue
        meth, mid, p = m["method"], m.get("id"), m.get("params", {})
        note({"method": meth, "params": {k: v for k, v in p.items() if k != "mcpServers"}})
        if meth == "initialize":
            kids.append(subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"]))  # an MCP server
            send({"jsonrpc": "2.0", "id": mid, "result": {"protocolVersion": 1, "agentInfo": {"name": "fake"},
                  "agentCapabilities": {"loadSession": True}}})
        elif meth == "session/new":
            sid = "s%d" % (len(known()) + 1)
            json.dump(known() + [sid], open(store, "w"))
            mine.add(sid)
            send({"jsonrpc": "2.0", "id": mid, "result": {"sessionId": sid}})
        elif meth == "session/load":
            sid = p["sessionId"]
            if sid not in known():
                send({"jsonrpc": "2.0", "id": mid, "error": {"code": -32000, "message": "Session not found"}})
                continue
            upd(sid, {"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": "OLD REPLY "}})
            upd(sid, {"sessionUpdate": "tool_call", "toolCallId": "old", "title": "old", "kind": "read",
                      "status": "completed"})
            mine.add(sid)
            send({"jsonrpc": "2.0", "id": mid, "result": {}})
        elif meth == "session/set_model":
            send({"jsonrpc": "2.0", "id": mid, "result": {}})
        elif meth == "session/cancel":
            if "slow" in waiting:
                sid, rid = waiting.pop("slow")
                send({"jsonrpc": "2.0", "id": rid, "result": {"stopReason": "cancelled"}})
        elif meth == "session/prompt":
            sid = p["sessionId"]
            text = p["prompt"][0]["text"].split("\\n\\n")[-1]
            if sid not in mine:
                send({"jsonrpc": "2.0", "id": mid, "error": {"code": -32000, "message": "Session not found"}})
            elif text.startswith("perm:"):
                waiting["perm"] = (sid, mid)
                send({"jsonrpc": "2.0", "id": 99, "method": "session/request_permission", "params": {"sessionId": sid,
                      "toolCall": {"toolCallId": "t1", "title": "Do it?", "kind": text[5:]},
                      "options": [{"optionId": "yes1", "name": "Allow", "kind": "allow_once"},
                                  {"optionId": "no1", "name": "Deny", "kind": "reject_once"}]}})
            elif text == "slow":
                kids.append(subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"]))  # a command
                waiting["slow"] = (sid, mid)
            else:
                upd(sid, {"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": "fresh:" + text}})
                send({"jsonrpc": "2.0", "id": mid, "result": {"stopReason": "end_turn"}})
                if text == "exit":
                    sys.exit(0)
''')


@pytest.fixture()
def fake(tmp_path, monkeypatch):
    monkeypatch.setenv("MESHPOOL_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("MESHPOOL_MCP", str(tmp_path / "none.json"))
    C.ensure_home()
    script = tmp_path / "fake_acp.py"
    script.write_text(FAKE)
    proj = tmp_path / "proj"
    proj.mkdir()
    log = tmp_path / "calls.jsonl"
    manifest = {"id": "fake", "name": "Fake", "mode": "cowork", "type": "acp", "command": sys.executable,
                "args": [str(script), str(tmp_path / "store.json"), str(log)], "office": False, "share_mcp": False}
    agents = []

    def make(**kw):
        a = ACPAgent(manifest, str(proj), **kw)
        agents.append(a)
        return a

    def calls():
        return [json.loads(x) for x in log.read_text().splitlines()] if log.exists() else []
    yield make, calls
    for a in agents:
        a.close()


def test_resume_does_not_replay_old_text_and_reapplies_the_model(fake):
    make, calls = fake
    a = make(mode="ask")
    assert a.send("hi") == "fresh:hi"
    a.save()
    a.close()
    data = json.loads((a.dir / "session.json").read_text())
    b = ACPAgent.resume(data, a.manifest, mode="ask")
    try:
        b.model = "some-model"
        events = []
        b.emit_cb = events.append
        reply = b.send("again")
    finally:
        b.close()
    assert reply == "fresh:again"                                    # not "OLD REPLY fresh:again"
    assert not [e for e in events if e["type"] == "tool_start"]
    meths = [c["method"] for c in calls()]
    load = meths.index("session/load")
    assert "session/set_model" in meths[load:]                       # the thread's model after session/load


def test_a_harness_that_exited_gets_its_session_back(fake):
    make, calls = fake
    events = []
    a = make(mode="ask", emit=events.append)
    assert a.send("exit") == "fresh:exit"
    for _ in range(100):
        if a._p.poll() is not None:
            break
        time.sleep(0.05)
    assert a._p.poll() is not None
    assert a.send("next") == "fresh:next"                            # not "Session not found"
    assert not [e for e in events if e["type"] == "error"]
    loads = [c for c in calls() if c["method"] == "session/load"]
    assert loads and loads[0]["params"]["sessionId"] == "s1"


@pytest.mark.parametrize("kind", ["edit", "execute", "delete", "move"])
def test_plan_mode_rejects_changes_even_on_full_access(fake, kind):
    make, _ = fake
    a = make(mode="auto")
    a.plan = True
    assert a.send("perm:" + kind) == "chose no1"


def test_plan_mode_still_reads_and_auto_still_allows(fake):
    make, _ = fake
    a = make(mode="auto")
    a.plan = True
    assert a.send("perm:read") == "chose yes1"
    a.plan = False
    assert a.send("perm:edit") == "chose yes1"


def test_stop_on_the_first_turn_keeps_servers_started_with_the_harness(fake):
    psutil = pytest.importorskip("psutil")
    make, _ = fake
    a = make(mode="auto")
    t = threading.Thread(target=a.send, args=("slow",))
    t.start()
    kids = []
    for _ in range(200):
        if a._p:
            try:
                kids = psutil.Process(a._p.pid).children(recursive=True)
            except psutil.Error:
                kids = []
            if len(kids) >= 2:
                break
        time.sleep(0.05)
    assert len(kids) >= 2
    server = min(kids, key=lambda k: k.create_time())
    a.stop()
    t.join(20)
    time.sleep(1.3)                                                  # the second sweep after Stop
    assert server.is_running() and server.status() != psutil.STATUS_ZOMBIE


# ---------- Pi --------------------------------------------------------------------------------------------------

def _pi(tmp_path, monkeypatch, **kw):
    monkeypatch.setenv("MESHPOOL_HOME", str(tmp_path / "home"))
    C.ensure_home()
    from meshpool_harness.harnesses.pi_rpc import PiAgent
    return PiAgent({"id": "pi"}, str(tmp_path), **kw)


def _gate(a, name, inp=None):
    return a._gate({"title": "meshpool:" + name, "message": json.dumps({"name": name, "input": inp or {}, "id": "t"})})


def test_pi_plan_mode_allows_only_read_only_tools(tmp_path, monkeypatch):
    a = _pi(tmp_path, monkeypatch, mode="auto")
    a.plan = True
    assert not _gate(a, "bash", {"command": "rm -rf build"})
    assert not _gate(a, "edit", {"path": "x"})
    assert not _gate(a, "mcp__meshpool_office__make_deck")
    assert _gate(a, "read", {"path": "x"}) and _gate(a, "mcp__meshpool_web__web_search")
    a.plan = False
    assert _gate(a, "bash", {"command": "ls"})


def test_pi_safe_tools_come_from_one_list(tmp_path, monkeypatch):
    from meshpool_harness.harnesses import pi_rpc
    from meshpool_harness.harnesses.safe_tools import SAFE_MCP
    from meshpool_harness.agent import core
    assert SAFE_MCP <= pi_rpc.READ_ONLY and set(core.SAFE_MCP) <= SAFE_MCP
    a = _pi(tmp_path, monkeypatch, mode="ask")                       # no approver: anything that asks is denied
    assert _gate(a, "mcp__meshpool_office__check_deck") and _gate(a, "mcp__meshpool_office__find_icon")
    from meshpool_harness.harnesses import acp
    assert pi_rpc._kill_tree is acp._kill_tree


@pytest.mark.skipif(not shutil.which("node"), reason="needs node")
def test_pi_gate_reads_the_safe_list_from_meshpool(tmp_path):
    from meshpool_harness.harnesses import pi_rpc
    gate = tmp_path / "gate.mjs"
    gate.write_text(Path(pi_rpc.__file__).with_name("pi_gate.js").read_text())
    probe = tmp_path / "probe.mjs"
    probe.write_text(textwrap.dedent('''
        import gate from "./gate.mjs";
        let h; gate({on: (_, f) => { h = f; }});
        const asked = [];
        const ctx = {ui: {confirm: async (t) => { asked.push(t); return false; }}};
        for (const n of ["mcp__meshpool_office__check_deck", "read", "bash"]) await h({toolName: n, input: {}}, ctx);
        console.log(JSON.stringify(asked));
    '''))
    env = dict(os.environ, MESHPOOL_PI_SAFE=json.dumps(sorted(pi_rpc.READ_ONLY)))
    out = subprocess.run(["node", str(probe)], capture_output=True, text=True, env=env, timeout=30)
    assert json.loads(out.stdout) == ["meshpool:bash"], out.stderr
