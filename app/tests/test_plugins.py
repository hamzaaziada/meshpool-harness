"""Plugins: install from a folder, a .zip, a git URL and a marketplace.json; list, enable, remove; built-in catalog."""
import io
import json
import shutil
import subprocess
import zipfile
from pathlib import Path

import pytest

FIX = Path(__file__).parent / "fixtures" / "plugins"


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("MESHPOOL_HOME", str(tmp_path))
    monkeypatch.delenv("MESHPOOL_MCP", raising=False)
    (tmp_path / "mcp.json").write_text(json.dumps({"mcpServers": {"keep": {"command": "x"}}}), encoding="utf-8")
    (tmp_path / "skills").mkdir()
    (tmp_path / "skills" / ".defaults-installed").write_text(json.dumps(
        sorted(d.name for d in (Path(__file__).parents[1] / "defaults" / "skills").iterdir())), encoding="utf-8")
    return tmp_path


def _mcp(home):
    return json.loads((home / "mcp.json").read_text(encoding="utf-8"))["mcpServers"]


def test_install_folder_adds_prefixed_skills_commands_and_disabled_servers(home):
    from meshpool_harness import plugins as P, skills as SK
    [rec] = P.install_plugin(str(FIX / "demo-plugin"))
    assert rec["name"] == "demo" and rec["version"] == "1.2.0"
    assert set(rec["skills"]) == {"demo-summarize", "demo-checklist", "demo-standup", "demo-review-contract",
                                  "demo-instructions"}
    sk = SK.get("demo-summarize")
    assert sk["name"] == "demo-summarize" and sk["description"] == "Summarize a document in five bullets."
    assert "allowed-tools: Read" in Path(sk["path"]).read_text()
    assert "refs/list.md" in SK.get("demo-checklist")["files"]
    assert "(arguments: [team])" in SK.get("demo-standup")["description"]
    assert "cite the source" in SK.instructions("demo-instructions")
    servers = _mcp(home)
    assert set(rec["servers"]) == {"demo-notes", "demo-remote"}
    assert all(servers[n]["disabled"] is True for n in rec["servers"])
    notes = servers["demo-notes"]
    assert notes["args"][0] == str(home / "plugins" / "demo").replace("\\", "/") + "/servers/notes.js"
    assert notes["env"] == {"NOTES_TOKEN": "${NOTES_TOKEN}"} and "node" in notes["description"]
    assert any("hooks" in n for n in rec["notes"])
    assert not list(home.rglob("SHOULD_NOT_RUN"))            # hooks never run
    rows = P.list_plugins()
    assert rows[0]["name"] == "demo" and rows[0]["enabled"] is True
    assert [s["enabled"] for s in rows[0]["server_status"]] == [False, False]


def test_remove_takes_away_only_what_the_plugin_added(home):
    from meshpool_harness import plugins as P, skills as SK
    SK.create("demo-mine", "My own skill.", "Do it.")          # a user skill that looks like the plugin's
    P.install_plugin(str(FIX / "demo-plugin"))
    assert P.remove_plugin("demo") == []
    assert SK.get("demo-summarize") is None and SK.get("demo-mine") is not None
    assert set(_mcp(home)) == {"keep"}
    assert not (home / "plugins" / "demo").exists()
    assert json.loads((home / "plugins.json").read_text())["plugins"] == {}
    with pytest.raises(ValueError):
        P.remove_plugin("demo")


def test_existing_skill_with_same_name_is_not_overwritten(home):
    from meshpool_harness import plugins as P, skills as SK
    SK.create("demo-summarize", "The user's own.", "Keep me.")
    [rec] = P.install_plugin(str(FIX / "demo-plugin"))
    assert "demo-summarize" not in rec["skills"] and any("demo-summarize" in n for n in rec["notes"])
    assert SK.get("demo-summarize")["description"] == "The user's own."
    P.remove_plugin("demo")
    assert SK.get("demo-summarize") is not None


def test_reinstall_replaces_the_old_version(home):
    from meshpool_harness import plugins as P
    P.install_plugin(str(FIX / "demo-plugin"))
    P.install_plugin(str(FIX / "demo-plugin"))
    assert [p["name"] for p in P.list_plugins()] == ["demo"]
    assert set(_mcp(home)) == {"keep", "demo-notes", "demo-remote"}


def test_enable_toggles_skills_and_switches_servers_off(home):
    from meshpool_harness import plugins as P, skills as SK
    P.install_plugin(str(FIX / "demo-plugin"))
    from meshpool_harness.ui import agent_api as A
    A.mcp_save({"name": "demo-notes", "enabled": True})
    assert P.enable_plugin("demo", False)["enabled"] is False
    assert not any(s["id"].startswith("demo-") for s in SK.enabled())
    assert _mcp(home)["demo-notes"]["disabled"] is True
    assert P.enable_plugin("demo", True)["enabled"] is True


def test_install_zip(home, tmp_path):
    from meshpool_harness import plugins as P
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for f in (FIX / "demo-plugin").rglob("*"):
            if f.is_file():
                z.write(f, "demo-plugin-main/" + f.relative_to(FIX / "demo-plugin").as_posix())
        z.writestr("../escape.txt", "no")
    zp = tmp_path / "demo.zip"
    zp.write_bytes(buf.getvalue())
    [rec] = P.install_plugin(str(zp))
    assert rec["name"] == "demo" and "demo-summarize" in rec["skills"]
    assert not (tmp_path.parent / "escape.txt").exists()


def test_install_marketplace_json(home):
    from meshpool_harness import plugins as P, skills as SK
    recs = P.install_plugin(str(FIX / ".claude-plugin" / "marketplace.json"))
    assert [r["name"] for r in recs] == ["demo", "second"]
    second = recs[1]
    assert second["skills"] == ["second-hello"] and second["version"] == "0.1.0"
    assert SK.get("second-hello")["description"] == "Say hello politely."
    only = P.install_plugin(str(FIX / ".claude-plugin" / "marketplace.json"), names=["second"])
    assert [r["name"] for r in only] == ["second"]


def test_install_marketplace_folder(home):
    from meshpool_harness import plugins as P
    assert [r["name"] for r in P.install_plugin(str(FIX))] == ["demo", "second"]


@pytest.mark.skipif(shutil.which("git") is None, reason="needs git")
def test_install_git_url(home, tmp_path):
    from meshpool_harness import plugins as P
    repo = tmp_path / "src"
    shutil.copytree(FIX / "second-plugin", repo)
    run = lambda *a: subprocess.run(["git", "-C", str(repo), *a], check=True, capture_output=True)  # noqa: E731
    run("init", "-q")
    run("add", ".")
    run("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "x")
    [rec] = P.install_plugin(repo.as_uri())
    assert rec["skills"] == ["src-hello"] and rec["source"] == repo.as_uri()


def test_bad_sources(home):
    from meshpool_harness import plugins as P
    with pytest.raises(ValueError):
        P.install_plugin("")
    with pytest.raises(ValueError):
        P.install_plugin(str(home / "nope"))


def test_builtin_catalog_groups_bundled_skills(home):
    from meshpool_harness import plugins as P, skills as SK
    cat = {g["id"]: g for g in P.catalog()}
    assert set(cat) == {"finance-kit", "finance", "legal", "sales", "customer-support"}
    kit = [s["id"] for s in cat["finance-kit"]["skills"]]
    assert kit == P.FINANCE_KIT
    fin = [s["id"] for s in cat["finance"]["skills"]]
    assert "finance-close-management" in fin and not set(kit) & set(fin)
    assert len(cat["sales"]["skills"]) > 20 and all(g["license"] == "Apache-2.0" for g in cat.values())
    assert cat["legal"]["enabled"] is False                     # nothing installed in this fresh home
    on = P.enable_plugin("legal", True)
    assert on["enabled"] is True and SK.get("legal-review-contract")["enabled"]
    off = P.enable_builtin("legal", False)
    assert off["enabled"] is False and not SK.get("legal-review-contract")["enabled"]
    P.remove_plugin("legal")                                    # built-ins are switched off, not deleted
    assert SK.get("legal-review-contract") is not None


def test_folded_description_and_nested_plugin_folder(home, tmp_path):
    from meshpool_harness import plugins as P, skills as SK
    root = tmp_path / "download" / "wrapper" / "folded"
    (root / ".claude-plugin").mkdir(parents=True)
    (root / ".claude-plugin" / "plugin.json").write_text('{"name": "folded"}')
    (root / "skills" / "long").mkdir(parents=True)
    (root / "skills" / "long" / "SKILL.md").write_text(
        "---\nname: long\ndescription: >\n  Spread over\n  two lines.\n---\n\nDo it.\n")
    (tmp_path / "download" / "other").mkdir()
    [rec] = P.install_plugin(str(tmp_path / "download"))
    assert rec["name"] == "folded" and SK.get("folded-long")["description"] == "Spread over two lines."
