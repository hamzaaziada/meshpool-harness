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
     "models": "meshpool", "tagline": "Coding in your project: more fixes solved, for less, than Codex CLI",
     "description": ("Best for coding in your project. On SWE-bench Verified it solved 18 of 20 tasks at $0.008 "
                     "each, more and cheaper than Codex CLI on the same model. Edits code, runs commands and tests, "
                     "and uses a browser, your connected apps and skills.")},
    {"id": "meshpool-work", "name": "Meshpool Work", "mode": "cowork", "type": "builtin", "models": "meshpool",
     "tagline": "Word, Excel, PowerPoint and PDF, checked before you get them",
     "description": ("Best for documents, spreadsheets, decks, data and research. Passed all 7 tasks in our office "
                     "run, from a live-formula workbook to editing a contract in place. Every deck and workbook is "
                     "checked against Meshpool's design and formula rules before it reaches you.")},
]
PI = {"id": "pi", "name": "Pi", "mode": "code", "type": "pi", "models": "meshpool",
      "tagline": "Quick, low-cost code fixes",
      "description": ("Best for quick, low-cost coding. A small open-source coding agent: 19 of 20 SWE-bench "
                      "Verified tasks at $0.007 each in our run.")}
AUTO = {"id": "meshpool-auto", "name": "Meshpool Auto", "mode": "any", "type": "auto", "models": "meshpool",
        "tagline": "Picks the best agent per task: quality first, then cost and speed",
        "description": ("Best when you are not sure. For each task it takes the agent with the best benchmark "
                        "quality for that kind of work, then the cheapest and fastest of the equally good ones, "
                        "and tells you which one and why.")}
# The official Codex agent, over the Agent Client Protocol. It is a harness (which agent runs); the provider of the
# thread's model decides the credentials it runs on (see credentials() below).
CODEX_ACP = "@agentclientprotocol/codex-acp@2.6.0"
NODE_NOTE = "It needs Node.js, which the Meshpool app includes; from the command line, install Node.js first."
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
# Thread files and settings saved by older versions name these ids: agents Meshpool no longer has open in
# Meshpool Code, so old chats still open.
LEGACY = {"chatgpt-subscription": "codex", "claude-subscription": "meshpool-code", "claude-code": "meshpool-code",
          "gemini-cli": "meshpool-code"}
MODES = ("code", "cowork")
RESERVED = {"meshpool-code", "meshpool-work", "hermes", "pi", "meshpool-auto", "codex", *LEGACY}
ID_RE = r"[A-Za-z0-9_.-]{1,60}"
HERMES_NOTE = ("Best for ongoing work where memory matters: Hermes remembers what it learns across chats, uses "
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
            "tagline": "Ongoing work: remembers what it learns across chats", "description": HERMES_NOTE}


def codex_home() -> Path:
    """Codex's config when it runs on an API key: in the Meshpool data folder, apart from the user's own Codex."""
    return C.home() / "codex"


def user_codex_home() -> Path:
    """The user's own Codex folder, where `codex login` keeps the ChatGPT sign-in (auth.json)."""
    return Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")


def prepare_codex(model: str | None = None) -> None:
    """Point Codex at the API key endpoint and model (its own config.toml in the Meshpool data folder). Codex reads
    the key from MESHPOOL_API_KEY in its own environment (set by _launch), never from the file."""
    from meshpool_harness import providers as PV
    home = codex_home()
    home.mkdir(parents=True, exist_ok=True)
    base = PV.key_base()
    prov, bare = PV.split(model or os.environ.get("MH_MODEL") or "")
    model = bare if prov == "key" and bare else "deepseek-v4.1-flash"
    q = lambda v: '"' + str(v).replace("\\", "\\\\").replace('"', '\\"') + '"'
    (home / "config.toml").write_text(
        f"model = {q(model)}\nmodel_provider = \"meshpool\"\nmodel_context_window = 128000\n"
        "\n"
        f"[model_providers.meshpool]\nname = \"Meshpool\"\nbase_url = {q(base)}\nenv_key = \"MESHPOOL_API_KEY\"\n"
        "wire_api = \"responses\"\nrequest_max_retries = 6\nstream_max_retries = 6\n", encoding="utf-8")


def write_private(p: Path, text: str) -> None:
    """Write a file only the user can read (it holds a key or tokens): created 0600, never readable by others first."""
    try:
        if p.exists():
            os.chmod(p, 0o600)               # narrow an older file before the new secret goes in
    except OSError:
        pass
    fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)


def prepare_hermes(model: str | None = None) -> None:
    """Point the bundled Hermes at the thread's provider (the key endpoint or the ChatGPT relay), model and key
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
    au["adopt_external_logins"] = False         # never borrow another app's sign-in: Hermes runs on the key above
    cfg["auth"] = au
    ap = cfg.get("approvals") if isinstance(cfg.get("approvals"), dict) else {}
    ap["mode"] = "manual"                       # every command comes to Meshpool, which applies the user's setting;
    cfg["approvals"] = ap                       # Hermes's own "smart" mode auto-approved deletes
    try:
        from meshpool_harness import skills as SK
        SK.sync_to(home / "skills" / "meshpool")           # the user's Meshpool skills, for Hermes too
    except Exception:
        pass
    text = yaml.safe_dump(cfg, sort_keys=False)
    write_private(p, text)
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
            write_private(dst, text)
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
        if not isinstance(d.get(k), str) or not d[k].strip():
            raise ValueError(f"The harness file needs \"{k}\" (text).")
    if not re.fullmatch(ID_RE, d["id"]):
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
    out.append(dict(CODEX, available=_available(CODEX), builtin=True, signed_in=signed_in(CODEX["login"]),
                    **credentials(CODEX["id"])))
    out.insert(0, dict(AUTO, available=True, builtin=True, **credentials("meshpool-auto")))
    if folder().exists():
        for p in sorted(folder().glob("*.json")):
            if p.stem in RESERVED:
                continue                   # an agent added from an example before it became built in, or one
                                           # Meshpool no longer has
            try:
                d = _validate(json.loads(p.read_text(encoding="utf-8")))
                out.append(dict(d, available=_available(d), builtin=False, file=str(p)))
            except (ValueError, TypeError, OSError) as e:
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
            except (ValueError, TypeError, OSError):
                continue
    return rows


def signed_in(which: str) -> bool:
    """Whether the Codex (ChatGPT) sign-in already exists on this computer (it never reads the secret)."""
    try:
        if which == "codex":
            return (user_codex_home() / "auth.json").is_file()
    except Exception:  # noqa: BLE001
        return False
    return False


def npx() -> str:
    return shutil.which("npx") or "npx"


def login(which: str) -> dict:
    """Start the official ChatGPT sign-in (Codex's own login), which opens the browser by itself."""
    import subprocess
    if which == "codex":
        cmd = [npx(), "-y", "@openai/codex@latest", "login"]
        subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL,
                         **({"creationflags": 0x08000000} if os.name == "nt" else {"start_new_session": True}))
        return {"ok": True, "message": "Your browser opens for the ChatGPT sign-in. Come back here when it says you "
                                       "are signed in."}
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
                cred = credentials(hid, model)
                # The harness's own environment (bundled Hermes: PYTHONPATH, HERMES_HOME ...) plus the provider's.
                env = {**(_manifest_env(hid)), **(cred.get("env") or {})}
                d.update(cred)
                if env:
                    d["env"] = env
            return d
    raise KeyError(hid)


def _manifest_env(hid: str) -> dict:
    """The environment a built-in harness itself needs, apart from any provider's credentials."""
    if hid == "hermes":
        return dict((_bundled_hermes() or {}).get("env") or {})
    h = next((h for h in (*BUILTIN, PI, AUTO, CODEX) if h["id"] == hid), None)
    return dict((h or {}).get("env") or {})


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


def _check_id(hid: str) -> str:
    if not isinstance(hid, str) or not re.fullmatch(ID_RE, hid):
        raise KeyError(hid)
    return hid


def add_example(hid: str) -> dict:
    _check_id(hid)
    p = folder() / "examples" / f"{hid}.json"
    if not p.exists():
        raise KeyError(hid)
    return add(p.read_text(encoding="utf-8"))


def remove(hid: str) -> None:
    if hid in RESERVED:
        raise ValueError("Built-in harnesses cannot be removed.")
    _check_id(hid)
    p = folder() / f"{hid}.json"
    if not p.exists():
        raise KeyError(hid)
    p.unlink()


# ---------- providers: what the models run on -----------------------------------------------------------------------
# A harness is which agent runs; a provider is where its models run (providers.py). The model picker chooses the
# provider per thread; sign-up chooses the default one (MH_PROVIDER):
#   meshpool  the Meshpool key                  custom   another OpenAI-compatible key and URL
#   chatgpt   the user's ChatGPT sign-in
PROVIDER_NAMES = {"meshpool": "Meshpool API key", "custom": "Another API key", "chatgpt": "ChatGPT subscription"}
LOGINS = {"chatgpt": "codex"}   # provider -> the official sign-in it uses
SIGNIN_PROVIDERS = tuple(LOGINS)


def provider() -> str:
    p = (C.read().get("MH_PROVIDER") or os.environ.get("MH_PROVIDER") or "meshpool").strip() or "meshpool"
    return p if p in PROVIDER_NAMES else "meshpool"


def api_key() -> str:
    from meshpool_harness import providers as PV
    return PV.key()


def base_url() -> str:
    from meshpool_harness import providers as PV
    return PV.key_base()


def _launch(hid: str, prov: str | None) -> dict:
    """How a harness process starts on a provider: env, config to write, how Meshpool picks its models."""
    from meshpool_harness import providers as PV
    if hid == "codex":
        if prov == "key":
            return {"prepare": "codex", "models": "meshpool",
                    "env": {"CODEX_HOME": str(codex_home()), "MESHPOOL_API_KEY": PV.key()},
                    "env_unset": ["OPENAI_API_KEY", "OPENAI_BASE_URL"]}
        return {"models": "harness", "env": {"CODEX_HOME": str(user_codex_home())},
                "env_unset": ["OPENAI_API_KEY", "OPENAI_BASE_URL"]}
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
    if prov == "chatgpt":
        out["plan"] = prov                      # a subscription: Auto uses it only when it is the chosen provider
    if prov is None:
        out["setup"] = PV.setup(sup[0])
    out.update(_launch(hid, prov))
    return out


def use_provider(which: str) -> None:
    """Save the provider chosen at sign-up and pick the defaults from it: its models become the default model, and
    Meshpool Code and Work stay the default agents."""
    if which not in PROVIDER_NAMES:
        raise ValueError(f"Unknown provider: {which}")
    vals = {"MH_PROVIDER": which, "MH_HARNESS_CODE": "", "MH_HARNESS_COWORK": ""}
    cur = (C.read().get("MH_MODEL") or "").strip()
    if which == "chatgpt":
        from meshpool_harness.core import chatgpt as CG
        if not cur.startswith("chatgpt:"):
            vals["MH_MODEL"] = "chatgpt:" + CG.default_model()
    elif which in ("meshpool", "custom") and cur.startswith("chatgpt:"):
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
    if provider() == "chatgpt":
        return signed_in(LOGINS["chatgpt"])
    return PV.ready("key")
