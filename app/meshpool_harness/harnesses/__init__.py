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
     "models": "meshpool", "tagline": "Coding in your project, with browser, connectors and skills",
     "description": ("Best for coding in your project: edits code, runs commands and tests, and uses your "
                    "connected apps, skills and a browser.")},
    {"id": "meshpool-work", "name": "Meshpool Work", "mode": "cowork", "type": "builtin", "models": "meshpool",
     "tagline": "Documents, spreadsheets, decks and research",
     "description": ("Best for documents, spreadsheets, decks, data and research. Makes real Word, Excel and "
                    "PowerPoint files and checks each one before it reaches you.")},
]
PI = {"id": "pi", "name": "Pi", "mode": "code", "type": "pi", "models": "meshpool",
      "tagline": "Quick, low-cost code fixes",
      "description": ("Best for quick, low-cost coding. A small open-source coding agent, fast on everyday "
                      "fixes.")}
AUTO = {"id": "meshpool-auto", "name": "Meshpool Auto", "mode": "any", "type": "auto", "models": "meshpool",
        "tagline": "General tasks: picks the best agent for each one",
        "description": ("Best when you are not sure: picks a good agent for each task and tells you which one and "
                        "why.")}
# The official Claude Code, Codex and Gemini CLI agents, over the Agent Client Protocol. They are harnesses (which
# agent runs); the provider of the thread's model decides the credentials they run on (see credentials() below).
CLAUDE_ACP = "@agentclientprotocol/claude-agent-acp@0.106.0"
CODEX_ACP = "@agentclientprotocol/codex-acp@2.6.0"
GEMINI_CLI = "@google/gemini-cli@0.68.0"
NODE_NOTE = "It needs Node.js, which the Meshpool app includes; from the command line, install Node.js first."
# Claude Code on a Claude sign-in (the user's Pro or Max plan) or an Anthropic API key. Anthropic's terms let a
# claude.ai sign-in run only Anthropic's own agent, so the Claude subscription is used here and nowhere else.
CLAUDE_CODE = {"id": "claude-code", "name": "Claude Code", "mode": "code", "type": "acp", "command": "npx",
               "args": ["-y", CLAUDE_ACP], "models": "harness", "login": "claude", "share_mcp": True,
               "install": NODE_NOTE, "tagline": "Hard, multi-file coding, on Claude models",
               "description": ("Best for hard coding tasks. Anthropic's Claude Code agent, on your Claude subscription "
                               "(sign in) or an Anthropic API key. Uses your connected apps and skills.")}
# Codex: the same official agent on the Meshpool key, another API key (its own config in the Meshpool data folder
# points it at that endpoint) or the user's ChatGPT sign-in (Codex's own login in ~/.codex).
CODEX = {"id": "codex", "name": "Codex", "mode": "code", "type": "acp", "models": "meshpool", "login": "codex",
         # Codex's read-only mode refuses to edit rather than asking; in Workspace access it edits the project and
         # asks before anything outside it. Meshpool's Undo covers every change.
         "modes": {"ask": "workspace-write", "edits": "workspace-write", "auto": "agent-full-access"},
         "command": "npx", "args": ["-y", CODEX_ACP], "share_mcp": True, "install": NODE_NOTE,
         "tagline": "Terminal and command-line work, on GPT models",
         "description": ("Best for terminal and command-line work. OpenAI's open-source Codex agent, on your Meshpool "
                         "key, another API key or your ChatGPT sign-in. It edits files in your project without "
                         "asking and asks before anything outside it; Undo reverses its changes.")}
# Gemini CLI on a Google sign-in or a Gemini API key. Google's terms forbid using a Gemini CLI sign-in from other
# tools, so that sign-in runs here and nowhere else; Meshpool only tells Gemini CLI which of its own logins to use.
GEMINI = {"id": "gemini-cli", "name": "Gemini CLI", "mode": "code", "type": "acp", "command": "npx",
          "args": ["-y", GEMINI_CLI, "--acp"], "models": "harness", "login": "gemini", "share_mcp": True,
          "install": NODE_NOTE, "tagline": "Large codebases and long context, on Gemini models",
          "description": ("Best for large codebases and long files. Google's open-source Gemini CLI agent, on your "
                          "Google sign-in or a Gemini API key. Uses your connected apps and skills.")}
# Thread files and settings saved before harnesses and providers were separated name these ids.
LEGACY = {"claude-subscription": "claude-code", "chatgpt-subscription": "codex"}
MODES = ("code", "cowork")
RESERVED = {"meshpool-code", "meshpool-work", "hermes", "pi", "meshpool-auto", "claude-code", "codex", "gemini-cli",
            *LEGACY}
HERMES_NOTE = ("Best for ongoing work where memory matters: Hermes remembers what it learns across threads, uses "
               "the web, files and terminal, and makes Office files.")


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
           "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8", "HERMES_DISABLE_LAZY_INSTALLS": "1",
           "HERMES_ASK_EVERY_COMMAND": "1",   # every command asks Meshpool, which applies the user's permission mode
           "HERMES_ACP_APPROVAL_TIMEOUT": "86400"}   # an approval card waits for the user, as in every harness
    if os.environ.get("PYTHONPYCACHEPREFIX"):
        env["PYTHONPYCACHEPREFIX"] = os.environ["PYTHONPYCACHEPREFIX"]
    else:
        env["PYTHONDONTWRITEBYTECODE"] = "1"
    # Not the cowork default (office handoff, 13 Nov 2026): Meshpool Work passed every office task tested, was faster
    # on the Word proposal (42 s vs 64 s) and runs the forge deck/workbook checks; Hermes stays one click away.
    return {"id": "hermes", "name": "Hermes", "mode": "cowork", "type": "acp", "prepare": "hermes",
            "models": "meshpool", "command": sys.executable, "args": ["-m", "acp_adapter"], "env": env,
            "tagline": "Ongoing work: remembers what it learns across threads", "description": HERMES_NOTE}


def codex_home() -> Path:
    """Codex's config when it runs on an API key: in the Meshpool data folder, apart from the user's own Codex."""
    return C.home() / "codex"


def user_codex_home() -> Path:
    """The user's own Codex folder, where `codex login` keeps the ChatGPT sign-in (auth.json)."""
    return Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")


def prepare_codex(model: str | None = None) -> None:
    """Point Codex at the API key endpoint and model (its own config.toml in the Meshpool data folder). The key is
    read from MESHPOOL_API_KEY in the environment, never written to the file."""
    from meshpool_harness import providers as PV
    home = codex_home()
    home.mkdir(parents=True, exist_ok=True)
    base = PV.key_base()
    prov, bare = PV.split(model or os.environ.get("MH_MODEL") or "")
    model = bare if prov == "key" and bare else "deepseek-v4.1-flash"
    if not os.environ.get("MESHPOOL_API_KEY") and os.environ.get("OPENAI_API_KEY"):
        os.environ["MESHPOOL_API_KEY"] = os.environ["OPENAI_API_KEY"]
    q = lambda v: '"' + str(v).replace("\\", "\\\\").replace('"', '\\"') + '"'
    (home / "config.toml").write_text(
        f"model = {q(model)}\nmodel_provider = \"meshpool\"\nmodel_context_window = 128000\n"
        "\n"
        f"[model_providers.meshpool]\nname = \"Meshpool\"\nbase_url = {q(base)}\nenv_key = \"MESHPOOL_API_KEY\"\n"
        "wire_api = \"responses\"\nrequest_max_retries = 6\nstream_max_retries = 6\n", encoding="utf-8")


def prepare_hermes(model: str | None = None) -> None:
    """Point the bundled Hermes at the thread's provider (the key endpoint, Anthropic or Gemini), model and key
    (written to its own config in the Meshpool data folder, next to meshpool.config)."""
    from meshpool_harness import providers as PV
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
    prov = PV.for_harness("hermes", model or os.environ.get("MH_MODEL")) or "key"
    base, key = PV.endpoint(prov)
    p_, bare = PV.split(model or os.environ.get("MH_MODEL") or "")
    m.update({"provider": "custom", "base_url": base,
              "default": bare if p_ == prov and bare else "deepseek-v4.1-flash",
              "api_key": key})
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
    au = cfg.get("auth") if isinstance(cfg.get("auth"), dict) else {}
    au["adopt_external_logins"] = False         # never borrow the Claude Code or Codex sign-in: Hermes runs on the
    cfg["auth"] = au                            # key above, and a Claude sign-in is for Claude Code only
    ap = cfg.get("approvals") if isinstance(cfg.get("approvals"), dict) else {}
    ap["mode"] = "manual"                       # every command comes to Meshpool, which applies the user's setting;
    cfg["approvals"] = ap                       # Hermes's own "smart" mode auto-approved deletes
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
    out = [dict(BUILTIN[0], available=True, builtin=True, **credentials("meshpool-code"))]
    if pi_rpc.available():
        out.append(dict(PI, available=True, builtin=True, **credentials("pi")))
    bh = _bundled_hermes()
    if bh:
        out.append(dict(bh, available=True, builtin=True, **credentials("hermes")))
    out += [dict(b, available=True, builtin=True, **credentials(b["id"])) for b in BUILTIN[1:]]
    for h in (CODEX, CLAUDE_CODE, GEMINI):
        out.append(dict(h, available=_available(h), builtin=True, signed_in=signed_in(h["login"]),
                        **credentials(h["id"])))
    out.insert(0, dict(AUTO, available=True, builtin=True, **credentials("meshpool-auto")))
    if folder().exists():
        for p in sorted(folder().glob("*.json")):
            if p.stem in RESERVED:
                continue                   # an agent added from an example before it became built in (Gemini CLI)
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
            return (user_codex_home() / "auth.json").is_file()
        if which == "gemini":
            return (Path(os.environ.get("GEMINI_CLI_HOME") or Path.home()) / ".gemini" / "oauth_creds.json").is_file()
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
    if which in ("claude", "gemini"):
        line = (f'"{npx()}" -y @anthropic-ai/claude-code@latest' if which == "claude" else
                f'"{npx()}" -y {GEMINI_CLI}')
        if sys.platform == "darwin":
            script = line.replace("\\", "\\\\").replace('"', '\\"')
            subprocess.Popen(["osascript", "-e", f'tell application "Terminal" to do script "{script}"',
                              "-e", 'tell application "Terminal" to activate'])
        elif os.name == "nt":
            subprocess.Popen(["cmd", "/c", "start", f"{which.title()} sign-in", "cmd", "/k", line])
        else:
            for term in ("x-terminal-emulator", "gnome-terminal", "konsole", "xterm"):
                if shutil.which(term):
                    subprocess.Popen([term, "-e", line])
                    break
            else:
                return {"ok": False, "message": f"Open a terminal and run: {line}  then sign in."}
        if which == "gemini":
            return {"ok": True, "message": "A terminal window opened with Gemini CLI. Choose Sign in with Google, "
                                           "finish in your browser, then close the window and come back."}
        return {"ok": True, "message": "A terminal window opened with Claude Code. Type /login there, choose your "
                                       "Claude account, then close the window and come back."}
    raise ValueError("Unknown sign-in.")


def get(hid: str, model: str | None = None) -> dict:
    """A harness, with the credentials for the thread's model when one is given."""
    hid = LEGACY.get(hid, hid)
    for d in load_all():
        if d["id"] == hid:
            if d.get("error"):
                raise ValueError(f"{d['name']}: {d['error']}")
            if model and d.get("providers"):
                d = {k: v for k, v in d.items() if k not in ("env", "env_unset", "prepare", "auth", "plan", "setup")}
                d.update(credentials(hid, model))
            return d
    raise KeyError(hid)


def default_for(mode: str) -> str:
    want = (os.environ.get(f"MH_HARNESS_{mode.upper()}") or C.read().get(f"MH_HARNESS_{mode.upper()}") or "").strip()
    want = LEGACY.get(want, want)
    rows = {d["id"]: d for d in load_all()}

    def usable(hid: str) -> bool:
        d = rows.get(hid) or {}
        return bool(d.get("available") and not d.get("error") and d.get("ready", True))
    if want and usable(want):
        return want
    # Meshpool's own harnesses are the defaults: they passed every task in the practical check (13 Nov 2026) and
    # need nothing else installed. Hermes and the others stay one click away in the harness picker.
    own = "meshpool-code" if mode == "code" else "meshpool-work"
    if usable(own):
        return own
    alt = PROVIDER_AGENT.get(provider())         # e.g. a Claude subscription alone runs Claude Code only
    return alt if alt and usable(alt) else own


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


# ---------- providers: what the models run on -----------------------------------------------------------------------
# A harness is which agent runs; a provider is where its models run (providers.py). The model picker chooses the
# provider per thread; sign-up chooses the default one (MH_PROVIDER):
#   meshpool  the Meshpool key                  custom   another OpenAI-compatible key and URL
#   chatgpt   the user's ChatGPT sign-in        claude   the user's Claude sign-in (Claude Code only)
# Meshpool never reads or forwards a Claude or Google sign-in: those run only in Claude Code and Gemini CLI.
PROVIDER_NAMES = {"meshpool": "Meshpool API key", "custom": "Another API key",
                  "chatgpt": "ChatGPT subscription", "claude": "Claude subscription, runs Claude Code"}
LOGINS = {"claude": "claude", "chatgpt": "codex", "google": "gemini"}   # provider -> the official sign-in it uses
SIGNIN_PROVIDERS = tuple(LOGINS)
PROVIDER_AGENT = {"claude": "claude-code", "google": "gemini-cli"}     # a provider that only one harness can use


def provider() -> str:
    p = (C.read().get("MH_PROVIDER") or os.environ.get("MH_PROVIDER") or "meshpool").strip() or "meshpool"
    return p if p in PROVIDER_NAMES else "meshpool"


def api_key() -> str:
    from meshpool_harness import providers as PV
    return PV.key()


def base_url() -> str:
    from meshpool_harness import providers as PV
    return PV.key_base()


def anthropic_key() -> str:
    from meshpool_harness import providers as PV
    return PV.anthropic_key()


def _launch(hid: str, prov: str | None) -> dict:
    """How a harness process starts on a provider: env, config to write, how Meshpool picks its models."""
    from meshpool_harness import providers as PV
    if hid == "codex":
        if prov == "key":
            return {"prepare": "codex", "models": "meshpool", "env": {"CODEX_HOME": str(codex_home())},
                    "env_unset": ["OPENAI_API_KEY", "OPENAI_BASE_URL"]}
        return {"models": "harness", "env": {"CODEX_HOME": str(user_codex_home())},
                "env_unset": ["OPENAI_API_KEY", "OPENAI_BASE_URL"]}
    if hid == "claude-code":
        unset = ["ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL"]
        if prov == "anthropic":
            return {"env": {"ANTHROPIC_API_KEY": PV.anthropic_key()}, "env_unset": unset[1:]}
        return {"env_unset": unset}
    if hid == "gemini-cli":
        unset = ["GEMINI_API_KEY", "GOOGLE_API_KEY", "GOOGLE_GENAI_USE_VERTEXAI", "GOOGLE_CLOUD_PROJECT"]
        if prov == "gemini":
            k = PV.gemini_key()
            return {"env": {"GEMINI_API_KEY": k}, "env_unset": unset[1:],
                    "auth": {"methodId": "gemini-api-key", "_meta": {"api-key": k}}}
        return {"env_unset": unset, "auth": {"methodId": "oauth-personal"}}
    if hid == "hermes":
        return {"prepare": "hermes"}
    return {}


def credentials(hid: str, model: str | None = None) -> dict:
    """What a built-in harness runs on: {providers, provider, ready, runs_on, setup} for the pickers, plus the
    environment and config its process starts with. model: the thread's model, whose provider wins when the
    harness can use it."""
    from meshpool_harness import providers as PV
    sup = PV.SUPPORTS.get(hid)
    if sup is None:
        return {}
    prov = PV.for_harness(hid, model)
    out = {"providers": sup, "provider": prov, "ready": prov is not None,
           "runs_on": PV.name(prov) if prov else ""}
    if prov in ("chatgpt", "claude", "google"):
        out["plan"] = prov                      # a subscription: Auto uses it only when it is the chosen provider
    if prov is None:
        if provider() == "claude" and "claude" not in sup:
            out["setup"] = ("Your Claude subscription runs Claude Code only. For this agent, add an API key or sign "
                            "in with ChatGPT: Settings, Account.")
        else:
            out["setup"] = PV.setup(sup[0]) if len(sup) <= 2 else \
                f"Runs on {', '.join(PV.name(p) for p in sup[:-1])} or {PV.name(sup[-1])}. {PV.setup(sup[0])}"
    out.update(_launch(hid, prov))
    return out


def use_provider(which: str) -> None:
    """Save the provider chosen at sign-up and pick the defaults from it: its models become the default model, and
    a Claude subscription makes Claude Code the default agent (Meshpool Code and Work otherwise)."""
    if which not in PROVIDER_NAMES:
        raise ValueError(f"Unknown provider: {which}")
    hid = PROVIDER_AGENT.get(which, "")
    vals = {"MH_PROVIDER": which, "MH_HARNESS_CODE": hid, "MH_HARNESS_COWORK": hid}
    cur = (C.read().get("MH_MODEL") or "").strip()
    if which == "chatgpt":
        from meshpool_harness.core import chatgpt as CG
        if not cur.startswith("chatgpt:"):
            vals["MH_MODEL"] = "chatgpt:" + CG.default_model()
    elif which in ("meshpool", "custom") and ":" in cur and cur.split(":", 1)[0] in ("chatgpt", "anthropic", "gemini"):
        vals["MH_MODEL"] = ""                   # back to the key endpoint's default model
    C.write(vals)
    os.environ["MH_PROVIDER"] = which
    for k in ("MH_HARNESS_CODE", "MH_HARNESS_COWORK", "MH_MODEL"):
        if k in vals:
            if vals[k]:
                os.environ[k] = vals[k]
            else:
                os.environ.pop(k, None)


def ready() -> bool:
    """Whether Meshpool has something to run on: an API key, or a signed-in subscription chosen as the provider."""
    from meshpool_harness import providers as PV
    p = provider()
    if p in ("chatgpt", "claude"):
        return signed_in(LOGINS[p])
    return PV.ready("key") or PV.ready("anthropic") or PV.ready("gemini")
