"""Harness registry: the built-in agents plus every ACP agent described by a .json file in <home>/harnesses."""
from __future__ import annotations

import json
import os
import re
import shutil
import sys
from pathlib import Path

from meshpool_harness import config as C

BUILTIN = [
    {"id": "meshpool-code", "name": "Meshpool Code", "mode": "code", "type": "builtin", "default": True,
     "models": "meshpool",
     "description": "Meshpool's coding agent. It reads and edits your code, runs commands and tests, and uses your "
                    "MCP servers. Runs Meshpool Harness v3: 18 of 20 on SWE-bench Verified and 97 of 102 on "
                    "computer use in Meshpool's benchmark."},
    {"id": "meshpool-work", "name": "Meshpool Work", "mode": "cowork", "type": "builtin", "models": "meshpool",
     "description": "Built-in work agent for research, writing, spreadsheets, decks and data analysis. It uses "
                    "Meshpool's office engines and needs nothing else installed."},
]
PI = {"id": "pi", "name": "Pi", "mode": "code", "type": "pi", "models": "meshpool",
      "description": "Pi, the minimal open-source coding agent by Mario Zechner, bundled with Meshpool. Reads, writes, "
                     "edits and runs commands, uses your MCP servers and connectors, and runs on your Meshpool key "
                     "and models."}
AUTO = {"id": "meshpool-auto", "name": "Meshpool Auto", "mode": "any", "type": "auto", "models": "meshpool",
        "description": "Picks the best harness for each new task, from Meshpool's benchmark results. You see which "
                       "one it picked and why."}
# Subscriptions: the official Claude Code and Codex agents, through the Agent Client Protocol adapters. They sign in
# with the user's own Claude or ChatGPT account, so usage comes out of that subscription, not the Meshpool key.
CLAUDE_ACP = "@agentclientprotocol/claude-agent-acp@0.106.0"
CODEX_ACP = "@agentclientprotocol/codex-acp@2.6.0"
SUBSCRIPTIONS = [
    {"id": "claude-subscription", "name": "Claude (your subscription)", "mode": "code", "type": "acp",
     "command": "npx", "args": ["-y", CLAUDE_ACP], "models": "harness", "login": "claude", "share_mcp": True,
     "env_unset": ["ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL"],
     "setup": "Sign in with your Claude account first: Settings, Harnesses, Claude, Sign in.",
     "description": "Claude Code by Anthropic on your Claude Pro or Max plan. Uses your Meshpool connectors, skills and "
                    "MCP servers."},
    {"id": "chatgpt-subscription", "name": "ChatGPT (your subscription)", "mode": "code", "type": "acp",
     "command": "npx", "args": ["-y", CODEX_ACP], "models": "harness", "login": "codex", "share_mcp": True,
     "env_unset": ["OPENAI_API_KEY", "OPENAI_BASE_URL"],
     "setup": "Sign in with your ChatGPT account first: Settings, Harnesses, ChatGPT, Sign in.",
     "description": "Codex by OpenAI on your ChatGPT Plus, Pro or Business plan. Uses your Meshpool connectors, skills "
                    "and MCP servers."},
]
MODES = ("code", "cowork")
RESERVED = {"meshpool-code", "meshpool-work", "hermes", "pi", "meshpool-auto", "claude-subscription",
            "chatgpt-subscription"}
HERMES_NOTE = ("Hermes by Nous Research, bundled with Meshpool: it remembers what it learns, builds its own skills, "
               "uses the web, files and terminal, and makes PowerPoint, Excel and Word files with Meshpool's office "
               "engines. Runs on your Meshpool key and model.")


def vendored_hermes() -> Path | None:
    p = C.APP / "vendor" / "hermes"
    return p if (p / "acp_adapter").is_dir() else None


def hermes_home() -> Path:
    return C.home() / "hermes"


def _bundled_hermes() -> dict | None:
    v = vendored_hermes()
    if not v:
        return None
    env = {"PYTHONPATH": str(v), "HERMES_HOME": str(hermes_home()), "HERMES_BUNDLED_SKILLS": str(v / "skills"),
           "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8", "HERMES_DISABLE_LAZY_INSTALLS": "1"}
    if os.environ.get("PYTHONPYCACHEPREFIX"):
        env["PYTHONPYCACHEPREFIX"] = os.environ["PYTHONPYCACHEPREFIX"]
    else:
        env["PYTHONDONTWRITEBYTECODE"] = "1"
    return {"id": "hermes", "name": "Hermes", "mode": "cowork", "type": "acp", "default": True, "prepare": "hermes",
            "models": "meshpool", "command": sys.executable, "args": ["-m", "acp_adapter"], "env": env, "description": HERMES_NOTE}


def prepare_hermes() -> None:
    """Point the bundled Hermes at the Meshpool endpoint, model and key (written to its own config in the
    Meshpool data folder, next to meshpool.config)."""
    import yaml
    home = hermes_home()
    home.mkdir(parents=True, exist_ok=True)
    p = home / "config.yaml"
    try:
        cfg = yaml.safe_load(p.read_text(encoding="utf-8")) if p.exists() else {}
    except Exception:
        cfg = {}
    cfg = cfg if isinstance(cfg, dict) else {}
    m = cfg.get("model") if isinstance(cfg.get("model"), dict) else {}
    m.update({"provider": "custom",
              "base_url": os.environ.get("MESHPOOL_BASE_URL") or "https://llm.meshpool.ai/v1",
              "default": os.environ.get("MH_MODEL") or "deepseek-v4.1-flash",
              "api_key": os.environ.get("MESHPOOL_API_KEY") or os.environ.get("OPENAI_API_KEY") or ""})
    m.pop("key_env", None)
    cfg["model"] = m
    tools = cfg.get("tools") if isinstance(cfg.get("tools"), dict) else {}
    ts = tools.get("tool_search") if isinstance(tools.get("tool_search"), dict) else {}
    ts["enabled"] = "off"                       # show Meshpool's office tools directly, not behind a search
    tools["tool_search"] = ts
    cfg["tools"] = tools
    sec = cfg.get("security") if isinstance(cfg.get("security"), dict) else {}
    sec["allow_lazy_installs"] = False          # no pip downloads at runtime; never write into the signed app
    cfg["security"] = sec
    try:
        from meshpool_harness import skills as SK
        SK.sync_to(home / "skills" / "meshpool")           # the user's Meshpool skills, for Hermes too
    except Exception:
        pass
    text = yaml.safe_dump(cfg, sort_keys=False)
    p.write_text(text, encoding="utf-8")
    try:
        os.chmod(p, 0o600)
    except OSError:
        pass
    # Hermes keeps a "last known good" copy of config.yaml and skips the copy when the newest one matches.
    # Writing it here keeps Hermes off shutil.copy2 at startup (CopyFile2 is missing on some Windows
    # compatibility layers) and keeps the backup folder from filling with one copy per launch.
    try:
        b = home / "backups" / "config"
        b.mkdir(parents=True, exist_ok=True)
        olds = sorted((f for f in b.iterdir() if f.name.startswith("config.yaml.good.")), reverse=True)
        if not olds or olds[0].read_text(encoding="utf-8", errors="replace") != text:
            import time
            dst = b / f"config.yaml.good.{time.strftime('%Y%m%d-%H%M%S')}"
            dst.write_text(text, encoding="utf-8")
            for stale in [dst, *olds][5:]:
                stale.unlink(missing_ok=True)
    except OSError:
        pass


def folder() -> Path:
    return C.home() / "harnesses"


def ensure_defaults() -> None:
    """First run: copy the shipped manifests (Hermes) and the examples into the user's harness folder."""
    src = C.APP / "defaults" / "harnesses"
    dst = folder()
    if dst.exists() or not src.exists():
        return
    tmp = dst.with_name(dst.name + ".part")         # copy then rename, so a failed first run never leaves half a folder
    shutil.rmtree(tmp, ignore_errors=True)
    shutil.copytree(src, tmp, copy_function=shutil.copyfile)
    tmp.rename(dst)


def _validate(d: dict) -> dict:
    if not isinstance(d, dict):
        raise ValueError("A harness file must be a JSON object.")
    for k in ("id", "name", "command"):
        if not str(d.get(k, "")).strip():
            raise ValueError(f"The harness file needs \"{k}\".")
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,60}", d["id"]):
        raise ValueError("\"id\" may only use letters, numbers, dots, dashes and underscores.")
    if d["id"] in RESERVED:
        raise ValueError(f"\"{d['id']}\" is a built-in harness id. Pick another.")
    d.setdefault("type", "acp")
    if d["type"] != "acp":
        raise ValueError("Only ACP harnesses (\"type\": \"acp\") can be added.")
    d.setdefault("mode", "cowork")
    if d["mode"] not in MODES:
        raise ValueError("\"mode\" must be \"code\" or \"cowork\".")
    if not isinstance(d.get("args", []), list):
        raise ValueError("\"args\" must be a list.")
    return d


def _available(d: dict) -> bool:
    cmd = d.get("command", "")
    return bool(shutil.which(cmd) or (cmd and Path(cmd).exists()))


def load_all() -> list[dict]:
    from meshpool_harness.harnesses import pi_rpc
    out = [dict(BUILTIN[0], available=True, builtin=True)]
    if pi_rpc.available():
        out.append(dict(PI, available=True, builtin=True))
    bh = _bundled_hermes()
    if bh:
        out.append(dict(bh, available=True, builtin=True))
    out += [dict(b, available=True, builtin=True) for b in BUILTIN[1:]]
    for sub in SUBSCRIPTIONS:
        out.append(dict(sub, available=_available(sub), builtin=True, subscription=True,
                        signed_in=signed_in(sub["login"])))
    out.insert(0, dict(AUTO, available=True, builtin=True))
    if folder().exists():
        for p in sorted(folder().glob("*.json")):
            try:
                d = _validate(json.loads(p.read_text(encoding="utf-8")))
                out.append(dict(d, available=_available(d), builtin=False, file=str(p)))
            except (ValueError, OSError) as e:
                out.append({"id": p.stem, "name": p.stem, "mode": "cowork", "type": "acp", "available": False,
                            "builtin": False, "error": str(e), "file": str(p)})
    return out


def examples() -> list[dict]:
    rows = []
    ex = folder() / "examples"
    if ex.exists():
        for p in sorted(ex.glob("*.json")):
            try:
                d = _validate(json.loads(p.read_text(encoding="utf-8")))
                if not (folder() / p.name).exists():
                    rows.append(dict(d, available=_available(d), file=str(p)))
            except (ValueError, OSError):
                continue
    return rows


def signed_in(which: str) -> bool:
    """Whether the Claude Code or Codex sign-in already exists on this computer (it never reads the secret)."""
    try:
        if which == "codex":
            return (Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex") / "auth.json").is_file()
        if which == "claude":
            cfg = Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude")
            if (cfg / ".credentials.json").is_file():
                return True
            if sys.platform == "darwin":
                import subprocess
                r = subprocess.run(["security", "find-generic-password", "-s", "Claude Code-credentials"],
                                   capture_output=True, timeout=5)
                return r.returncode == 0
    except Exception:  # noqa: BLE001
        return False
    return False


def npx() -> str:
    return shutil.which("npx") or "npx"


def login(which: str) -> dict:
    """Start the official sign-in for a subscription. Codex opens the browser by itself; Claude Code's sign-in runs
    in a terminal window, where the user types /login."""
    import subprocess
    if which == "codex":
        cmd = [npx(), "-y", "@openai/codex@latest", "login"]
        subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL,
                         **({"creationflags": 0x08000000} if os.name == "nt" else {"start_new_session": True}))
        return {"ok": True, "message": "Your browser opens for the ChatGPT sign-in. Come back here when it says you "
                                       "are signed in."}
    if which == "claude":
        line = f'"{npx()}" -y @anthropic-ai/claude-code@latest'
        if sys.platform == "darwin":
            script = line.replace("\\", "\\\\").replace('"', '\\"')
            subprocess.Popen(["osascript", "-e", f'tell application "Terminal" to do script "{script}"',
                              "-e", 'tell application "Terminal" to activate'])
        elif os.name == "nt":
            subprocess.Popen(["cmd", "/c", "start", "Claude sign-in", "cmd", "/k", line])
        else:
            for term in ("x-terminal-emulator", "gnome-terminal", "konsole", "xterm"):
                if shutil.which(term):
                    subprocess.Popen([term, "-e", line])
                    break
            else:
                return {"ok": False, "message": f"Open a terminal and run: {line}  then type /login."}
        return {"ok": True, "message": "A terminal window opened with Claude Code. Type /login there, choose your "
                                       "Claude account, then close the window and come back."}
    raise ValueError("Unknown sign-in.")


def get(hid: str) -> dict:
    for d in load_all():
        if d["id"] == hid:
            if d.get("error"):
                raise ValueError(f"{d['name']}: {d['error']}")
            return d
    raise KeyError(hid)


def default_for(mode: str) -> str:
    want = (os.environ.get(f"MH_HARNESS_{mode.upper()}") or C.read().get(f"MH_HARNESS_{mode.upper()}") or "").strip()
    if want and any(d["id"] == want and d.get("available") and not d.get("error") for d in load_all()):
        return want
    rows = [d for d in load_all() if d.get("mode") == mode and not d.get("error")]
    for d in sorted(rows, key=lambda d: not d.get("builtin")):
        if d.get("default") and d.get("available") and d["id"] not in ("meshpool-code", "meshpool-work", "pi"):
            return d["id"]
    return "meshpool-code" if mode == "code" else "meshpool-work"


def add(text: str) -> dict:
    try:
        d = _validate(json.loads(text))
    except json.JSONDecodeError as e:
        raise ValueError(f"That file is not valid JSON: {e}")
    folder().mkdir(parents=True, exist_ok=True)
    p = folder() / f"{d['id']}.json"
    p.write_text(json.dumps(d, indent=2) + "\n", encoding="utf-8")
    return dict(d, available=_available(d), builtin=False, file=str(p))


def add_example(hid: str) -> dict:
    p = folder() / "examples" / f"{hid}.json"
    if not p.exists():
        raise KeyError(hid)
    return add(p.read_text(encoding="utf-8"))


def remove(hid: str) -> None:
    if hid in RESERVED:
        raise ValueError("Built-in harnesses cannot be removed.")
    p = folder() / f"{hid}.json"
    if not p.exists():
        raise KeyError(hid)
    p.unlink()
