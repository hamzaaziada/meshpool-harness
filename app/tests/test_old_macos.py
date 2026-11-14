"""The Intel Mac app on macOS 10.15: the Node agents and browser use say they need macOS 11 instead of failing,
and the build check reads each architecture's slice of the bundled Mac files."""
import importlib.util
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402

from meshpool_harness import config as C  # noqa: E402
from meshpool_harness import harnesses as HR  # noqa: E402
from meshpool_harness import macos  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]


def mac(monkeypatch, release: str):
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(macos.platform, "mac_ver", lambda: (release, ("", "", ""), "x86_64"))


@pytest.fixture()
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("MESHPOOL_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("MESHPOOL_MCP", str(tmp_path / "none.json"))
    C.ensure_home()
    return tmp_path


@pytest.mark.parametrize("release, old", [("10.15.7", True), ("10.15", True), ("10.14.6", True),
                                          ("10.16", False), ("11.0", False), ("11.7.10", False), ("14.5", False),
                                          ("", False)])
def test_too_old_reads_mac_ver(monkeypatch, release, old):
    mac(monkeypatch, release)
    assert macos.too_old() is old


def test_never_too_old_off_a_mac(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(macos.platform, "mac_ver", lambda: ("10.15.7", ("", "", ""), ""))
    assert macos.version() is None and not macos.too_old()


def test_note_names_both_versions(monkeypatch):
    mac(monkeypatch, "10.15.7")
    n = macos.note("Pi")
    assert n.startswith("Pi needs macOS 11 or later") and "macOS 10.15" in n


def test_node_agents_unavailable_with_the_reason_on_catalina(home, monkeypatch):
    mac(monkeypatch, "10.15.7")
    from meshpool_harness.harnesses import pi_rpc
    monkeypatch.setattr(pi_rpc, "available", lambda: True)
    monkeypatch.setattr(HR, "_available", lambda d: True)            # Node and npx are in the app
    rows = {h["id"]: h for h in HR.load_all()}
    for hid in ("pi", "codex", "opencode"):
        h = rows[hid]
        assert h["available"] is False, hid
        assert "needs macOS 11 or later" in h["unsupported"] and h["install"] == h["unsupported"]
    for hid in ("meshpool-code", "meshpool-work", "meshpool-auto"):
        assert rows[hid]["available"] is True and "unsupported" not in rows[hid]
    # The defaults and Auto never land on an agent that cannot run here
    assert HR.default_for("code") == "meshpool-code" and HR.default_for("cowork") == "meshpool-work"


def test_pi_listed_on_catalina_even_without_its_files(home, monkeypatch):
    mac(monkeypatch, "10.15.7")
    from meshpool_harness.harnesses import pi_rpc
    monkeypatch.setattr(pi_rpc, "available", lambda: False)
    pi = next(h for h in HR.load_all() if h["id"] == "pi")
    assert pi["available"] is False and "macOS 11" in pi["unsupported"]


def test_node_agents_unchanged_on_big_sur_and_later(home, monkeypatch):
    mac(monkeypatch, "11.7.10")
    from meshpool_harness.harnesses import pi_rpc
    monkeypatch.setattr(pi_rpc, "available", lambda: True)
    monkeypatch.setattr(HR, "_available", lambda d: True)
    rows = {h["id"]: h for h in HR.load_all()}
    for hid in ("pi", "codex", "opencode"):
        assert rows[hid]["available"] is True and "unsupported" not in rows[hid]


def test_starting_a_node_agent_on_catalina_says_why(home, monkeypatch):
    mac(monkeypatch, "10.15.7")
    from meshpool_harness.harnesses.acp import ACPAgent, ACPError
    from meshpool_harness.harnesses.pi_rpc import PiAgent, PiError
    with pytest.raises(PiError, match="Pi needs macOS 11 or later"):
        PiAgent({"id": "pi"}, str(home))._start()
    codex = dict(HR.CODEX, builtin=True)
    with pytest.raises(ACPError, match="Codex needs macOS 11 or later"):
        ACPAgent(codex, str(home))._start()
    with pytest.raises(ValueError, match="Codex needs macOS 11 or later"):
        HR.login("codex")


def test_new_chat_with_a_node_agent_on_catalina_is_refused_clearly(home, monkeypatch):
    mac(monkeypatch, "10.15.7")
    from meshpool_harness.ui import agent_api
    proj = home / "proj"
    proj.mkdir()
    monkeypatch.setattr(HR, "credentials", lambda hid, model=None: {})
    with pytest.raises(ValueError, match="OpenCode needs macOS 11 or later"):
        agent_api.Session(str(proj), harness="opencode")


def test_browser_use_says_it_needs_macos_11(monkeypatch):
    mac(monkeypatch, "10.15.7")
    from meshpool_harness.agent import browser_mcp_server as BM
    from meshpool_harness.agent.mcp_stdio import ToolFailed
    with pytest.raises(ToolFailed, match="Browser use needs macOS 11 or later") as e:
        BM.Browser().ensure()
    assert "web_search" in str(e.value)
    row = next(r for r in C.system_check() if r["name"] == "Browser use")
    assert row["ok"] is False and row["optional"] and "macOS 11" in row["detail"]


# ---------- the build check (packaging/mac/check_minos.py) ---------------------------------------------------------
def _check_minos():
    spec = importlib.util.spec_from_file_location("check_minos", ROOT / "packaging/mac/check_minos.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def thin(cpu: int, minos: tuple, filetype: int = 6) -> bytes:
    """A 64-bit Mach-O header with one LC_BUILD_VERSION command (filetype 6: a dylib; 0xA: debug symbols)."""
    cmd = struct.pack("<IIIIII", 0x32, 24, 1, (minos[0] << 16) | (minos[1] << 8), 0, 0)
    return struct.pack("<IIIIIIII", 0xFEEDFACF, cpu, 3, filetype, 1, len(cmd), 0, 0) + cmd


def fat(*slices: bytes, cpus: tuple) -> bytes:
    head = struct.pack(">II", 0xCAFEBABE, len(slices))
    off, body, table = 4096, b"", b""
    for cpu, s in zip(cpus, slices):
        table += struct.pack(">IIIII", cpu, 3, off + len(body), len(s), 12)
        body += s + b"\0" * (4096 - len(s))
    return (head + table).ljust(4096, b"\0") + body


X86, ARM = 0x01000007, 0x0100000C


def test_minos_reads_the_slice_for_the_architecture(tmp_path):
    cm = _check_minos()
    u = tmp_path / "lib.so"
    u.write_bytes(fat(thin(X86, (10, 13)), thin(ARM, (11, 0)), cpus=(X86, ARM)))
    assert cm.minos(u, "x86_64") == (10, 13)
    assert cm.minos(u, "arm64") == (11, 0)
    assert cm.minos(u) == (11, 0)                            # no architecture: the highest slice
    d = tmp_path / "lib.dSYM"
    d.write_bytes(thin(X86, (11, 0), filetype=0xA))
    assert cm.minos(d, "x86_64") is None                    # debug symbols are never loaded
    t = tmp_path / "notes.txt"
    t.write_text("hello")
    assert cm.minos(t) is None


def _app(tmp_path, node: tuple, lib: tuple) -> Path:
    app = tmp_path / "Meshpool.app"
    (app / "Contents/MacOS").mkdir(parents=True)
    (app / "Contents/Resources/node/bin").mkdir(parents=True)
    (app / "Contents/Resources/python/bin").mkdir(parents=True)
    (app / "Contents/MacOS/Meshpool").write_bytes(thin(X86, (10, 15)))
    (app / "Contents/Resources/python/bin/python3").write_bytes(thin(X86, (10, 15)))
    (app / "Contents/Resources/node/bin/node").write_bytes(thin(X86, node))
    (app / "Contents/Resources/python/lib.so").write_bytes(fat(thin(X86, lib), thin(ARM, (11, 0)), cpus=(X86, ARM)))
    return app


def _run(cm, monkeypatch, *argv):
    monkeypatch.setattr(sys, "argv", ["check_minos.py", *map(str, argv)])
    with pytest.raises(SystemExit) as e:
        cm.main()
    return e.value.code


def test_check_minos_intel_exemptions(tmp_path, monkeypatch, capsys):
    cm = _check_minos()
    ex = ["--exempt", "Contents/Resources/node/*=11.0"]
    app = _app(tmp_path / "a", node=(11, 0), lib=(10, 13))
    assert _run(cm, monkeypatch, app, "10.15", "--arch", "x86_64", *ex) == 0
    assert _run(cm, monkeypatch, app, "10.15", "--arch", "x86_64") == 1             # Node is not exempt by itself
    assert "node/bin/node needs macOS 11.0" in capsys.readouterr().out
    app = _app(tmp_path / "b", node=(12, 0), lib=(10, 13))
    assert _run(cm, monkeypatch, app, "10.15", "--arch", "x86_64", *ex) == 1        # exempt only up to 11.0
    app = _app(tmp_path / "c", node=(11, 0), lib=(11, 0))
    assert _run(cm, monkeypatch, app, "10.15", "--arch", "x86_64", *ex) == 1        # any other file: 10.15
    assert "python/lib.so needs macOS 11.0" in capsys.readouterr().out
