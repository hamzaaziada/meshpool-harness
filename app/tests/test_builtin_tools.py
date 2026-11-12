"""Built-in tool servers (browser, computer use) and screenshot handling in the agent."""
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from meshpool_harness import config as C  # noqa: E402
from meshpool_harness.agent import core  # noqa: E402

APP = Path(__file__).resolve().parents[1]


def _rpc(mod: str, calls: list, env=None) -> list:
    p = subprocess.Popen([sys.executable, "-m", mod], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True,
                         env=env, cwd=str(APP))
    out = []
    for i, (m, params) in enumerate(calls, 1):
        p.stdin.write(json.dumps({"jsonrpc": "2.0", "id": i, "method": m, "params": params}) + "\n")
        p.stdin.flush()
        out.append(json.loads(p.stdout.readline()))
    p.kill()
    return out


def test_servers_list_tools():
    for mod, must in (("meshpool_harness.agent.browser_mcp_server", {"browser_open", "browser_click", "browser_type"}),
                      ("meshpool_harness.agent.computer_mcp_server", {"screenshot", "click", "type_text", "press_key"})):
        r = _rpc(mod, [("initialize", {}), ("tools/list", {})])
        names = {t["name"] for t in r[1]["result"]["tools"]}
        assert must <= names, (mod, names)


def test_default_mcp_has_builtin_browser_and_computer():
    cfg = json.loads((APP / "defaults" / "mcp.json").read_text())["mcpServers"]
    for name in ("browser", "computer-use", "meshpool-web", "meshpool-assistant"):
        assert cfg[name]["command"] == "python"
    assert not cfg["browser"].get("disabled") and cfg["computer-use"].get("disabled")   # computer use is opt-in
    assert not cfg["meshpool-web"].get("disabled") and not cfg["meshpool-assistant"].get("disabled")


def test_upgrade_replaces_npx_entries(tmp_path):
    old = {"mcpServers": {"browser": {"command": "npx", "args": ["-y", "@playwright/mcp@latest"], "disabled": True},
                          "memory": {"command": "npx", "args": [], "disabled": True},
                          "mine": {"command": "my-server"}}}
    p = tmp_path / "mcp.json"
    p.write_text(json.dumps(old))
    C._upgrade_mcp(p)
    new = json.loads(p.read_text())["mcpServers"]
    assert new["browser"]["command"] == "python" and "computer-use" in new and "memory" not in new and "mine" in new


def test_images_attached_and_pruned():
    msgs = [{"role": "system", "content": "s"}]
    a = core.Agent.__new__(core.Agent)
    a.messages = msgs
    for _ in range(3):
        a._attach_images([("computer-use__screenshot", {"mime": "image/png", "data": "AAAA"})])
    imgs = [p for m in msgs if isinstance(m["content"], list) for p in m["content"] if p.get("type") == "image_url"]
    assert len(imgs) == 2
    assert "screenshot removed" in msgs[1]["content"]
    core._drop_images(msgs, keep=0)
    assert not core._has_images(msgs)
