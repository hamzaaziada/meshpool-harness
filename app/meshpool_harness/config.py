"""Settings file (meshpool.config) and local tool discovery.

The config file is plain KEY=VALUE lines. Values already set in the shell environment win, so a one-off
`MH_MODEL=... meshpool run ...` still works. The file path comes from MESHPOOL_CONFIG, else the folder above the app.
"""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

APP = Path(__file__).resolve().parents[1]

# Keys the web app shows and edits, in display order. (key, label, default, help)
FIELDS = [
    ("MESHPOOL_API_KEY", "API key", "", "Your Meshpool key (or any OpenAI-compatible key)."),
    ("MESHPOOL_BASE_URL", "Endpoint", "https://llm.meshpool.ai/v1", "OpenAI-compatible base URL."),
    ("MH_MODEL", "Model", "deepseek-v4.1-flash", "Any model id the endpoint serves."),
    ("MH_AGENT_PERMISSIONS", "Agent permissions", "ask", "ask = confirm edits and commands, auto = allow them."),
    ("MH_HARNESS_PROFILE", "Harness profile", "minimal", "minimal = the benchmarked v2 setup. Leave empty for v1."),
    ("MH_STREAM", "Streaming", "1", "1 = streamed calls with an idle cutoff (faster)."),
    ("MH_CACHE_KEY", "Cache key", "1", "1 = stable prompt_cache_key per conversation."),
    ("MH_WIRE", "Wire format", "chat", "chat or responses. Streaming only works with chat."),
    ("MH_BUDGET_USD", "Budget per task (USD)", "0", "Optional cap per task. 0 = off."),
    ("MH_AUTO_SUBSCRIPTIONS", "Auto may use subscriptions", "0",
     "1 = Meshpool Auto may pick Claude or ChatGPT on your own subscription (uses that plan's limits). "
     "0 = Auto only picks harnesses that run on your Meshpool key."),
    ("GITHUB_TOKEN", "GitHub token", "", "Optional. Connects GitHub (issues, pull requests, Actions runs) through "
                                       "the GitHub MCP server. Create one at github.com/settings/tokens."),
    ("BRAVE_SEARCH_API_KEY", "Brave Search API key", "", "Optional. Faster, steadier web search (free tier at brave.com/search/api)."),
    ("TAVILY_API_KEY", "Tavily API key", "", "Optional. Web search built for agents (free tier at tavily.com)."),
    ("SERPER_API_KEY", "Serper API key", "", "Optional. Google results through serper.dev."),
    ("SEARXNG_URL", "SearXNG address", "", "Optional. Your own SearXNG search server, for example http://localhost:8888."),
    ("INSTANTLY_API_KEY", "Instantly API key", "", "Set from Settings, Connectors."),
]
SECRET = {"MESHPOOL_API_KEY", "GITHUB_TOKEN", "BRAVE_SEARCH_API_KEY", "TAVILY_API_KEY", "SERPER_API_KEY",
          "INSTANTLY_API_KEY"}


def home() -> Path:
    """Where settings, MCP config and runs live. The zip install uses its own folder; the desktop apps set
    MESHPOOL_HOME to the user's app-data folder."""
    h = os.environ.get("MESHPOOL_HOME")
    return Path(h).expanduser() if h else APP.parent


def runs_dir() -> Path:
    return Path(os.environ["MH_RUNS_DIR"]) if os.environ.get("MH_RUNS_DIR") else home() / "runs"


def user_home_default() -> Path:
    """The per-user data folder the desktop apps use."""
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "Meshpool"
    if os.name == "nt":
        return Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming") / "Meshpool"
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "meshpool"


def ensure_home() -> Path:
    """Create the data folder and copy the default settings and MCP list into it the first time."""
    h = home()
    h.mkdir(parents=True, exist_ok=True)
    for name in ("meshpool.config", "mcp.json"):
        src, dst = APP / "defaults" / name, h / name
        if not dst.exists() and src.exists():
            dst.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
    runs_dir().mkdir(parents=True, exist_ok=True)
    _upgrade_mcp(h / "mcp.json")
    try:
        from meshpool_harness.harnesses import ensure_defaults
        ensure_defaults()
    except Exception:
        pass
    return h


def _upgrade_mcp(path: Path) -> None:
    """Older versions listed browser and computer use as npx servers (Node.js needed, off by default). Swap in the
    built-in ones and drop the other Node-only entries the user never turned on."""
    import json
    try:
        cfg = json.loads(path.read_text(encoding="utf-8"))
        new = json.loads((APP / "defaults" / "mcp.json").read_text(encoding="utf-8"))["mcpServers"]
    except (OSError, ValueError, KeyError):
        return
    servers = cfg.get("mcpServers")
    if not isinstance(servers, dict):
        return
    changed = False
    for name in ("browser", "computer-use"):
        cur = servers.get(name)
        if not isinstance(cur, dict) or cur.get("command") in ("npx", "npx.cmd"):
            servers[name] = new[name]
            changed = True
    for name in ("meshpool-web", "meshpool-assistant"):        # 2.4: shared web and assistant tools
        if name not in servers and name in new:
            servers[name] = new[name]
            changed = True
    if not cfg.get("_upgraded_24"):                          # 2.4: computer use is opt-in; research uses web tools
        cu = servers.get("computer-use")
        if isinstance(cu, dict) and not cu.get("disabled"):
            cu["disabled"] = True
        for name in ("browser", "computer-use"):
            if isinstance(servers.get(name), dict) and name in new:
                servers[name]["description"] = new[name]["description"]
        cfg["_upgraded_24"] = True
        changed = True
    gh = servers.get("github")
    if isinstance(gh, dict) and gh.get("disabled") and gh.get("url") == "https://api.githubcopilot.com/mcp/":
        del servers["github"]                      # GitHub is a connector now (Settings, Connectors)
        changed = True
    for name in ("memory", "sequential-thinking", "filesystem", "fetch"):
        cur = servers.get(name)
        if isinstance(cur, dict) and cur.get("disabled") and cur.get("command") in ("npx", "uvx"):
            del servers[name]
            changed = True
    if changed:
        order = {k: servers[k] for k in new if k in servers}
        order.update({k: v for k, v in servers.items() if k not in order})
        cfg["mcpServers"] = order
        path.write_text(json.dumps(cfg, indent=2) + "\n", encoding="utf-8")


def config_path() -> Path:
    p = os.environ.get("MESHPOOL_CONFIG")
    return Path(p) if p else home() / "meshpool.config"


def read(path: Path | None = None) -> dict:
    path = path or config_path()
    out = {}
    if not path.exists():
        return out
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        v = v.strip()
        if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
            v = v[1:-1]
        out[k.strip()] = v
    return out


def write(values: dict, path: Path | None = None) -> Path:
    """Update keys in place, keeping comments and unknown keys."""
    path = path or config_path()
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    done = set()
    for i, line in enumerate(lines):
        s = line.strip()
        if s and not s.startswith("#") and "=" in s:
            k = s.split("=", 1)[0].strip()
            if k in values:
                lines[i] = f"{k}={values[k]}"
                done.add(k)
    for k, v in values.items():
        if k not in done:
            lines.append(f"{k}={v}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def load_into_env(override: bool = False) -> dict:
    """Copy config values into os.environ (shell values win unless override). Empty values are skipped."""
    vals = read()
    for k, v in vals.items():
        if v == "":
            continue
        if override or k not in os.environ:
            os.environ[k] = v
    return vals


# ---------- local tools -------------------------------------------------------------------------------------------

def find_soffice() -> str | None:
    """LibreOffice binary: PATH first, then the usual install folders on Windows and macOS."""
    hit = shutil.which("soffice") or shutil.which("libreoffice")
    if hit:
        return hit
    cands = []
    if os.name == "nt":
        for base in (os.environ.get("ProgramFiles"), os.environ.get("ProgramFiles(x86)"), r"C:\Program Files"):
            if base:
                cands.append(Path(base) / "LibreOffice" / "program" / "soffice.exe")
    elif sys.platform == "darwin":
        cands.append(Path("/Applications/LibreOffice.app/Contents/MacOS/soffice"))
    for c in cands:
        if c.exists():
            return str(c)
    return None


def find_bash() -> str | None:
    """A real bash for the agent's shell commands. On Windows: Git Bash, never WSL's System32 bash."""
    if os.environ.get("MH_BASH"):
        return os.environ["MH_BASH"]
    if os.name != "nt":
        return shutil.which("bash")
    cands = []
    for base in (os.environ.get("ProgramFiles"), os.environ.get("ProgramW6432"), r"C:\Program Files",
                 os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs")):
        if base:
            cands.append(Path(base) / "Git" / "bin" / "bash.exe")
    for c in cands:
        if c.exists():
            return str(c)
    hit = shutil.which("bash")
    if hit and "system32" not in hit.lower():
        return hit
    return None


def system_check() -> list[dict]:
    """What is installed, and what each missing piece limits."""
    rows = [{"name": "Python", "ok": sys.version_info >= (3, 10), "detail": sys.version.split()[0],
             "fix": "Install Python 3.10 or newer."}]
    rows.append({"name": "Shell", "ok": bool(find_bash()), "detail": find_bash() or "not found",
                 "fix": "Agents run shell commands. The Windows app includes one; for the terminal-only harness on "
                        "Windows, install Git for Windows (git-scm.com)."})
    rows.append({"name": "Git", "optional": True, "ok": bool(shutil.which("git")), "detail": shutil.which("git") or "not found",
                 "fix": "Optional. Used to spot edited tests and for pull requests. On a Mac, run: xcode-select --install"})
    try:
        import playwright  # noqa: F401
        pw = True
    except ImportError:
        pw = False
    rows.append({"name": "Browser use", "ok": pw, "detail": "built in" if pw else "not available",
                 "fix": "Run: meshpool install-browser"})
    so = find_soffice()
    rows.append({"name": "LibreOffice", "optional": True, "ok": bool(so), "detail": so or "not installed (optional)",
                 "fix": "Optional. With it, Meshpool also checks spreadsheet formula results and renders slides to "
                        "review them. Files are complete without it."})
    try:
        import cairosvg  # noqa: F401
        cairosvg.svg2png(bytestring=b'<svg xmlns="http://www.w3.org/2000/svg" width="2" height="2"/>')
        cs = True
    except Exception:
        cs = False
    rows.append({"name": "Slide icons", "optional": True, "ok": cs, "detail": "cairo available" if cs else "cairo not available",
                 "fix": "Optional. Decks still build; icons are left out without the Cairo library."})
    return rows


def packaged() -> bool:
    """True inside the installed app (Meshpool.app or the Windows install), where Python is Meshpool's own."""
    return (APP.parent / "python").is_dir() or (APP.parent / "runtime").is_dir()


def user_env(base: dict | None = None) -> dict:
    """Environment for the user's own commands (the agent's shell, the terminal panel). Inside the app, drop the
    variables that point Python at Meshpool's bundled runtime, so the user's python3, pip and tools behave as in
    their own terminal."""
    env = dict(os.environ if base is None else base)
    if packaged():
        for k in ("PYTHONHOME", "PYTHONPATH", "PYTHONNOUSERSITE", "PYTHONPYCACHEPREFIX", "PYTHONDONTWRITEBYTECODE",
                  "PYTHONEXECUTABLE", "__PYVENV_LAUNCHER__"):
            env.pop(k, None)
    return env

