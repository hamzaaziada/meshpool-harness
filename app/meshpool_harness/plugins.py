"""Plugins: bundles of skills, MCP servers and optional instructions, in the Claude Code plugin format.

    my-plugin/
      .claude-plugin/plugin.json    {"name", "version", "description", optional "skills"/"commands"/"mcpServers"}
      skills/<skill>/SKILL.md       skills (installed as <plugin>-<skill>)
      commands/*.md                 slash commands, installed as skills too
      .mcp.json                     MCP servers ({"mcpServers": {...}}), added switched OFF
      CLAUDE.md / AGENTS.md         optional instructions, installed as the skill <plugin>-instructions

install_plugin(source) takes a local folder, a .zip, a git URL (shallow clone into a temp folder) or a
marketplace.json (the Claude marketplace format: {"plugins": [{"name", "source": "./path" | {"source": "github",
"repo": "owner/repo"} | {"source": "url", "url": "..."}}]}). Nothing from a plugin runs at install time: files are
copied, hooks and agents are skipped, and its MCP servers are added disabled so the user sees each command and turns
it on. What each plugin added is recorded in <home>/plugins.json, and remove_plugin() takes exactly that away.

catalog() also offers the skills Meshpool ships in defaults/skills as built-in plugins (Anthropic's knowledge-work
plugins and Meshpool's finance kit), so a user switches on one group instead of sixty loose skills.
"""
from __future__ import annotations

import io
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
import urllib.request
import zipfile
from pathlib import Path

from meshpool_harness import config as C
from meshpool_harness import skills as SK

SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", ".DS_Store"}
MAX_BYTES = 100 * 1024 * 1024            # a plugin larger than this is almost certainly not a plugin
MARK = ".from-plugin"                    # in each skill folder a plugin added: the plugin's name
INSTRUCTION_FILES = ("CLAUDE.md", "AGENTS.md", "instructions.md", "INSTRUCTIONS.md")

KNOWLEDGE_WORK = "Anthropic knowledge-work plugins (Apache-2.0)"
FINANCE_KIT = ["finance-buy-side-deal-analysis", "finance-trading-comps", "finance-precedent-transactions",
               "finance-dcf-valuation", "finance-assumption-book"]
BUILTIN = [
    {"id": "finance-kit", "name": "Finance kit", "prefix": None, "skills": FINANCE_KIT,
     "source": "Meshpool", "license": "Apache-2.0",
     "description": "Buy-side valuation work: deal analysis, trading comps, precedent transactions, DCF and an "
                    "assumption book where every input has a driver and a source."},
    {"id": "finance", "name": "Finance (accounting)", "prefix": "finance-", "source": KNOWLEDGE_WORK,
     "license": "Apache-2.0",
     "description": "Month-end close, journal entries, reconciliations, financial statements, variance analysis, "
                    "SOX testing and audit support."},
    {"id": "legal", "name": "Legal", "prefix": "legal-", "source": KNOWLEDGE_WORK, "license": "Apache-2.0",
     "description": "Contract review, NDA triage, compliance checks, risk assessment, vendor checks, briefs and "
                    "signature requests."},
    {"id": "sales", "name": "Sales", "prefix": "sales-", "source": KNOWLEDGE_WORK, "license": "Apache-2.0",
     "description": "Account research, call prep and summaries, pipeline and forecast reviews, outreach, deal "
                    "reviews, renewals and CRM hygiene."},
    {"id": "customer-support", "name": "Customer support", "prefix": "customer-support-", "source": KNOWLEDGE_WORK,
     "license": "Apache-2.0",
     "description": "Ticket triage, draft responses, escalations, customer research and knowledge-base articles."},
]


# ---------- state -------------------------------------------------------------------------------------------------

def state_file() -> Path:
    return C.home() / "plugins.json"


def plugins_dir() -> Path:
    """Where installed plugins' files live (MCP servers may point into them with ${CLAUDE_PLUGIN_ROOT})."""
    return C.home() / "plugins"


def _load() -> dict:
    try:
        d = json.loads(state_file().read_text(encoding="utf-8"))
        return d if isinstance(d, dict) and isinstance(d.get("plugins"), dict) else {"plugins": {}}
    except (OSError, ValueError):
        return {"plugins": {}}


def _save(d: dict):
    state_file().parent.mkdir(parents=True, exist_ok=True)
    state_file().write_text(json.dumps(d, indent=2) + "\n", encoding="utf-8")


def list_plugins() -> list[dict]:
    """Installed plugins with what each added, plus whether its skills are on and its servers' commands."""
    off = SK._disabled()
    mcp = _mcp_servers()
    out = []
    for rec in _load()["plugins"].values():
        servers = [{"name": n, "enabled": isinstance(mcp.get(n), dict) and not mcp[n].get("disabled"),
                    "target": _target(mcp.get(n) or {}), "present": n in mcp} for n in rec.get("servers", [])]
        on = not any(s in off for s in rec["skills"]) if rec.get("skills") else any(x["enabled"] for x in servers)
        out.append(dict(rec, enabled=on, server_status=servers))
    return sorted(out, key=lambda r: r["name"])


# ---------- built-in catalog --------------------------------------------------------------------------------------

def _defaults() -> Path:
    return C.APP / "defaults" / "skills"


def _builtin_skills(group: dict) -> list[str]:
    if group["prefix"] is None:
        return [s for s in group["skills"] if (_defaults() / s / "SKILL.md").is_file()]
    kit = set(FINANCE_KIT)
    return sorted(d.name for d in _defaults().glob(group["prefix"] + "*")
                  if (d / "SKILL.md").is_file() and d.name not in kit)


def catalog() -> list[dict]:
    """The built-in plugins: each with a description, licence, the skills it enables and whether it is on."""
    SK.ensure_defaults()
    off = SK._disabled()
    out = []
    for g in BUILTIN:
        rows = []
        for sid in _builtin_skills(g):
            meta, _ = SK.parse((_defaults() / sid / "SKILL.md").read_text(encoding="utf-8", errors="replace"))
            rows.append({"id": sid, "description": meta.get("description", ""),
                         "installed": (SK.folder() / sid / "SKILL.md").is_file(), "enabled": sid not in off})
        on = bool(rows) and all(r["installed"] and r["enabled"] for r in rows)
        out.append({"id": g["id"], "name": g["name"], "description": g["description"], "source": g["source"],
                    "license": g["license"], "builtin": True, "skills": rows, "enabled": on,
                    "partly": not on and any(r["installed"] and r["enabled"] for r in rows)})
    return out


def enable_builtin(pid: str, on: bool = True) -> dict:
    """Switch a built-in plugin's skills on (copying back any the user removed) or off."""
    g = next((x for x in BUILTIN if x["id"] == pid), None)
    if not g:
        raise ValueError(f"No built-in plugin named {pid}.")
    SK.folder().mkdir(parents=True, exist_ok=True)
    off = SK._disabled()
    for sid in _builtin_skills(g):
        dst = SK.folder() / sid
        if on and not (dst / "SKILL.md").is_file():
            _copy_tree(_defaults() / sid, dst)
        (off.discard if on else off.add)(sid)
    SK._save_disabled(off)
    return next(x for x in catalog() if x["id"] == pid)


def enable_plugin(name: str, on: bool = True) -> dict:
    """Switch an installed plugin's skills on or off (built-in ids work too). Its MCP servers keep their own switch;
    switching a plugin off also switches its servers off."""
    if any(g["id"] == name for g in BUILTIN) and name not in _load()["plugins"]:
        return enable_builtin(name, on)
    rec = _load()["plugins"].get(name)
    if not rec:
        raise ValueError(f"No plugin named {name}.")
    off = SK._disabled()
    for sid in rec.get("skills", []):
        (off.discard if on else off.add)(sid)
    SK._save_disabled(off)
    if not on:
        _set_servers_disabled(rec.get("servers", []))
    return next(p for p in list_plugins() if p["name"] == name)


# ---------- reading a plugin --------------------------------------------------------------------------------------

def _read_json(p: Path) -> dict:
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _manifest(root: Path, entry: dict | None = None) -> dict:
    """plugin.json merged over the marketplace entry (an entry may describe a plugin that has no plugin.json)."""
    m = dict(entry or {})
    m.update(_read_json(root / ".claude-plugin" / "plugin.json"))
    m["name"] = SK.slug(str(m.get("name") or root.name))
    return m


def _paths(root: Path, value, default: str) -> list[Path]:
    """Custom component paths from plugin.json ("./x" or ["./x", "./y"]) plus the default folder."""
    vals = value if isinstance(value, list) else [value] if isinstance(value, str) else []
    out = [root / default] + [(root / v).resolve() for v in vals]
    return [p for p in dict.fromkeys(out) if p.exists() and _inside(p, root)]


def _inside(p: Path, root: Path) -> bool:
    try:
        p.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def _skill_dirs(root: Path, m: dict) -> list[Path]:
    out = []
    for base in _paths(root, m.get("skills"), "skills"):
        if (base / "SKILL.md").is_file():
            out.append(base)
        elif base.is_dir():
            out += sorted(d for d in base.iterdir() if d.is_dir() and (d / "SKILL.md").is_file())
    return out


def _command_files(root: Path, m: dict) -> list[Path]:
    out = []
    for base in _paths(root, m.get("commands"), "commands"):
        out += [base] if base.is_file() else sorted(p for p in base.rglob("*.md") if p.is_file())
    return [p for p in out if not p.is_symlink()]


def _mcp_specs(root: Path, m: dict) -> dict:
    """{server name: raw spec} from .mcp.json and plugin.json's mcpServers (inline or a path)."""
    found: dict = {}
    for src in (_read_json(root / ".mcp.json"), m.get("mcpServers")):
        if isinstance(src, str) and _inside(root / src, root):
            src = _read_json(root / src)
        if isinstance(src, dict):
            servers = src.get("mcpServers") if isinstance(src.get("mcpServers"), dict) else src
            found.update({str(k): v for k, v in servers.items() if isinstance(v, dict)})
    return found


def _is_plugin(root: Path) -> bool:
    return (root / ".claude-plugin" / "plugin.json").is_file() or (root / "skills").is_dir() \
        or (root / "commands").is_dir() or (root / ".mcp.json").is_file()


def _marketplace_file(root: Path) -> Path | None:
    for p in (root / ".claude-plugin" / "marketplace.json", root / "marketplace.json"):
        if p.is_file():
            return p
    return None


# ---------- getting the files: folder, zip, git, marketplace --------------------------------------------------------

def _is_git(src: str) -> bool:
    s = src.strip()
    if s.startswith(("git@", "ssh://", "git://", "file://")) or (s.endswith(".git") and "://" in s):
        return True
    return bool(re.match(r"https?://(github\.com|gitlab\.com|bitbucket\.org|codeberg\.org)/[\w.-]+/[\w.-]+/?$", s))


def _clone(url: str, into: Path, ref: str = "") -> Path:
    """Shallow clone without hooks or submodules; nothing in the repository runs."""
    if shutil.which("git") is None:
        raise ValueError("Installing from a git URL needs git. Download the plugin as a .zip instead.")
    cmd = ["git", "clone", "--depth", "1", "--no-tags", "--template=", "-c", "core.hooksPath=" + os.devnull]
    cmd += (["--branch", ref] if ref else []) + ["--", url, str(into)]
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0", GIT_LFS_SKIP_SMUDGE="1")
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=180, env=env)
    if r.returncode != 0:
        raise ValueError(f"git could not clone {url}: {(r.stderr or r.stdout).strip()[-300:]}")
    return into


def _unzip(data: bytes, into: Path) -> Path:
    """Extract a .zip safely (no paths outside the folder, no symlinks) and return the plugin's root."""
    total = 0
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        for info in z.infolist():
            name = info.filename.replace("\\", "/")
            if info.is_dir() or "__MACOSX" in name or (info.external_attr >> 16) & 0o170000 == 0o120000:
                continue
            total += info.file_size
            if total > MAX_BYTES:
                raise ValueError("The .zip is larger than 100 MB; that is not a plugin.")
            p = (into / name).resolve()
            if not _inside(p, into):
                continue
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(z.read(info))
    return _find_root(into)


def _find_root(d: Path) -> Path:
    """The folder that holds the plugin (or marketplace): the top, a single sub-folder, or where .claude-plugin is."""
    if _is_plugin(d) or _marketplace_file(d):
        return d
    subs = [x for x in d.iterdir() if x.is_dir() and x.name not in SKIP_DIRS]
    if len(subs) == 1 and (_is_plugin(subs[0]) or _marketplace_file(subs[0])):
        return subs[0]
    for depth in ("*/.claude-plugin", "*/*/.claude-plugin", "*/*/*/.claude-plugin"):   # bounded: never a whole disk
        hits = sorted(p for p in d.glob(depth) if p.is_dir() and not any(x in SKIP_DIRS for x in p.parts))
        if hits:
            return hits[0].parent
    raise ValueError("No plugin found: expected .claude-plugin/plugin.json, a skills folder or a .mcp.json.")


def _fetch(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "Meshpool"})
    with urllib.request.urlopen(req, timeout=60) as r:  # noqa: S310 - https URL the user gave
        data = r.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise ValueError("The download is larger than 100 MB; that is not a plugin.")
    return data


def _entry_root(entry: dict, base: Path | None, tmp: Path) -> Path:
    """Where a marketplace entry's plugin files are: a path relative to the marketplace, or a repository."""
    src = entry.get("source")
    if isinstance(src, str) and not _is_git(src):
        if base is None:
            raise ValueError(f"{entry.get('name')}: a relative source needs the marketplace's folder; install "
                             "the marketplace from its folder or repository instead.")
        p = (base / src).resolve()
        if not _inside(p, base) or not p.is_dir():
            raise ValueError(f"{entry.get('name')}: source {src} is not a folder inside the marketplace.")
        return p
    if isinstance(src, str):
        src = {"source": "url", "url": src}
    if not isinstance(src, dict):
        raise ValueError(f"{entry.get('name')}: the source is missing.")
    kind = src.get("source")
    url = f"https://github.com/{src['repo']}.git" if kind == "github" and src.get("repo") else src.get("url")
    if not url:
        raise ValueError(f"{entry.get('name')}: unsupported source {kind!r}.")
    root = _clone(url, Path(tempfile.mkdtemp(dir=tmp)) / "repo", str(src.get("ref") or ""))
    sub = src.get("path")
    return (root / sub).resolve() if sub and _inside(root / sub, root) else root


def _install_marketplace(mfile: Path | None, data: dict, names: list | None, source: str, tmp: Path) -> list[dict]:
    """Install the plugins a marketplace lists (all, or the ones in `names`)."""
    base = None
    if mfile is not None:
        base = (mfile.parent.parent if mfile.parent.name == ".claude-plugin" else mfile.parent).resolve()
    proot = str((data.get("metadata") or {}).get("pluginRoot") or "").strip("/")
    entries = [e for e in data.get("plugins") or [] if isinstance(e, dict) and e.get("name")]
    if names:
        want = {SK.slug(n) for n in names}
        entries = [e for e in entries if SK.slug(e["name"]) in want]
    if not entries:
        raise ValueError("The marketplace lists no plugins" + (" with those names." if names else "."))
    out = []
    for e in entries:
        if proot and isinstance(e.get("source"), str) and not e["source"].startswith((".", "/")):
            e = dict(e, source=f"./{proot.lstrip('./')}/{e['source']}")       # metadata.pluginRoot + bare name
        root = _entry_root(e, base, tmp)
        out.append(_install_root(root, f"{source}#{e['name']}", entry=e))
    return out


def install_plugin(source: str, names: list | None = None) -> list[dict]:
    """Install from a folder, a .zip (path or URL), a git URL or a marketplace.json (path or URL). `names` picks
    plugins from a marketplace. Returns the record of each plugin installed."""
    source = str(source or "").strip()
    if not source:
        raise ValueError("Give a folder, a .zip, a git URL or a marketplace.json.")
    with tempfile.TemporaryDirectory(prefix="meshpool-plugin-") as t:
        tmp, hint = Path(t), None
        local = Path(source).expanduser()
        if local.exists():
            if local.is_file() and local.suffix.lower() == ".json":
                return _install_marketplace(local.resolve(), _read_json(local), names, source, tmp)
            if local.is_file() and local.suffix.lower() == ".zip":
                root = _unzip(local.read_bytes(), tmp / "zip")
            elif local.is_dir():
                root = _find_root(local.resolve())
            else:
                raise ValueError("A plugin is a folder, a .zip, a git URL or a marketplace.json.")
        elif re.match(r"https?://", source) and source.lower().split("?")[0].endswith(".json"):
            return _install_marketplace(None, json.loads(_fetch(source)), names, source, tmp)
        elif re.match(r"https?://", source) and source.lower().split("?")[0].endswith(".zip"):
            root = _unzip(_fetch(source), tmp / "zip")
        elif _is_git(source) or re.fullmatch(r"[\w.-]+/[\w.-]+", source):
            url = source if _is_git(source) else f"https://github.com/{source}.git"
            clone = _clone(url, tmp / "repo")
            root = _find_root(clone)
            if root == clone:                      # a plugin without plugin.json is named after its repository
                hint = {"name": re.sub(r"\.git$", "", url.rstrip("/").rsplit("/", 1)[-1].rsplit(":", 1)[-1])}
        else:
            raise ValueError(f"Not found: {source}. Give a folder, a .zip, a git URL or a marketplace.json.")
        mf = _marketplace_file(root)
        if mf and not (root / ".claude-plugin" / "plugin.json").is_file():
            return _install_marketplace(mf, _read_json(mf), names, source, tmp)
        return [_install_root(root, source, entry=hint)]


# ---------- installing one plugin ---------------------------------------------------------------------------------

def _copy_tree(src: Path, dst: Path):
    """Copy a folder without symlinks, VCS folders or dependencies; refuse anything over MAX_BYTES."""
    total = 0
    for p in sorted(src.rglob("*")):
        rel = p.relative_to(src)
        if p.is_symlink() or any(part in SKIP_DIRS for part in rel.parts) or not p.is_file():
            continue
        total += p.stat().st_size
        if total > MAX_BYTES:
            raise ValueError(f"{src.name} is larger than 100 MB; that is not a plugin.")
        (dst / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(p, dst / rel)


def _prefixed(plugin: str, name: str) -> str:
    n = SK.slug(name)
    return n if n == plugin or n.startswith(plugin + "-") else SK.slug(f"{plugin}-{n}")


def _front_blocks(text: str) -> list[tuple[str, list[str]]]:
    """Front-matter lines grouped by key: (key, [the key line and its indented continuation lines])."""
    end = text.find("\n---", 3) if text.startswith("---") else -1
    blocks: list[tuple[str, list[str]]] = []
    for ln in (text[3:end].splitlines() if end != -1 else []):
        if ln.strip() and not ln.startswith((" ", "\t")) and ":" in ln:
            blocks.append((ln.split(":", 1)[0].strip(), [ln]))
        elif ln.strip() and blocks:
            blocks[-1][1].append(ln)
    return blocks


def _set_front(text: str, **kv) -> str:
    """Replace or add front-matter keys (name, description) in a SKILL.md; a folded or block value (description: >)
    becomes one line."""
    _, body = SK.parse(text)
    blocks = _front_blocks(text)
    for k, v in list(kv.items()):
        old = next((lines for key, lines in blocks if key == k), None)
        if old and str(v).strip() in (">", "|", ">-", "|-", ">+", "|+"):
            kv[k] = " ".join(x.strip() for x in old[1:]) or k
    keep = [ln for key, lines in blocks if key not in kv for ln in lines]
    lines = [f"{k}: {v}" for k, v in kv.items() if v] + keep
    return "---\n" + "\n".join(lines) + "\n---\n\n" + body


def _owned(sid: str, plugin: str) -> bool:
    try:
        return (SK.folder() / sid / MARK).read_text(encoding="utf-8").strip() == plugin
    except OSError:
        return False


def _claim(sid: str, plugin: str, notes: list) -> Path | None:
    """The folder for a new skill, or None when a skill of that name that is not this plugin's is already there."""
    d = SK.folder() / sid
    if d.exists() and not _owned(sid, plugin):
        notes.append(f"Skipped skill {sid}: a skill with that name is already installed.")
        return None
    shutil.rmtree(d, ignore_errors=True)
    d.mkdir(parents=True)
    return d


def _add_skill(src: Path, plugin: str, notes: list) -> str | None:
    meta, _ = SK.parse((src / "SKILL.md").read_text(encoding="utf-8", errors="replace"))
    sid = _prefixed(plugin, meta.get("name") or src.name)
    d = _claim(sid, plugin, notes)
    if d is None:
        return None
    _copy_tree(src, d)
    text = (d / "SKILL.md").read_text(encoding="utf-8", errors="replace")
    (d / "SKILL.md").write_text(_set_front(text, name=sid, description=meta.get("description") or sid),
                                encoding="utf-8")
    (d / MARK).write_text(plugin, encoding="utf-8")
    return sid


def _add_text_skill(plugin: str, name: str, description: str, body: str, notes: list) -> str | None:
    sid = _prefixed(plugin, name)
    d = _claim(sid, plugin, notes)
    if d is None:
        return None
    desc = re.sub(r"\s+", " ", description).strip()[:300] or sid
    (d / "SKILL.md").write_text(f"---\nname: {sid}\ndescription: {desc}\n---\n\n{body.strip()}\n", encoding="utf-8")
    (d / MARK).write_text(plugin, encoding="utf-8")
    return sid


def _add_command(path: Path, base: Path, plugin: str, notes: list) -> str | None:
    """A slash command (commands/*.md) becomes a skill; $ARGUMENTS stays for the agent to fill from the request."""
    meta, body = SK.parse(path.read_text(encoding="utf-8", errors="replace"))
    rel = path.relative_to(base).with_suffix("") if path.is_relative_to(base) else Path(path.stem)
    name = "-".join(rel.parts)
    first = next((ln.strip("# ").strip() for ln in body.splitlines() if ln.strip()), name)
    desc = meta.get("description") or first
    if meta.get("argument-hint"):
        desc += f" (arguments: {meta['argument-hint']})"
    return _add_text_skill(plugin, name, desc, body, notes)


def _target(spec: dict) -> str:
    return spec.get("url") or " ".join([str(spec.get("command", ""))] + [str(a) for a in spec.get("args") or []])


def _add_server(plugin: str, name: str, raw: dict, pdir: Path, notes: list) -> str | None:
    """Add one MCP server, switched off, with ${CLAUDE_PLUGIN_ROOT} pointing at the installed plugin's files."""
    from meshpool_harness import importer
    raw = json.loads(json.dumps(raw).replace("${CLAUDE_PLUGIN_ROOT}", str(pdir).replace("\\", "/")))
    raw.pop("disabled", None)
    raw.pop("enabled", None)
    spec = importer._norm(raw)
    if not spec:
        notes.append(f"Skipped MCP server {name}: no command or url.")
        return None
    spec["disabled"] = True
    sname = _prefixed(plugin, name)
    desc = f"From the {plugin} plugin. Off until you turn it on. Runs: {_target(spec)}"
    return importer.import_mcp(sname, spec, description=desc)


def _install_root(root: Path, source: str, entry: dict | None = None) -> dict:
    """Install one plugin folder: copy its files, add its skills, commands, instructions and MCP servers."""
    m = _manifest(root, entry)
    name = m["name"]
    if _load()["plugins"].get(name):
        remove_plugin(name)                               # reinstall: take away the old version first
    pdir = plugins_dir() / name
    shutil.rmtree(pdir, ignore_errors=True)
    _copy_tree(root, pdir)
    notes: list[str] = []
    SK.folder().mkdir(parents=True, exist_ok=True)
    skills = [s for s in (_add_skill(d, name, notes) for d in _skill_dirs(pdir, m)) if s]
    cbases = _paths(pdir, m.get("commands"), "commands")
    for f in _command_files(pdir, m):
        base = next((b for b in cbases if _inside(f, b)), f.parent)
        sid = _add_command(f, base, name, notes)
        if sid and sid not in skills:
            skills.append(sid)
    for fn in INSTRUCTION_FILES:
        if (pdir / fn).is_file():
            text = (pdir / fn).read_text(encoding="utf-8", errors="replace")
            sid = _add_text_skill(name, "instructions", f"How to work with the {name} plugin: "
                                  + (m.get("description") or "its conventions and rules."), text, notes)
            skills += [sid] if sid else []
            break
    servers = [s for s in (_add_server(name, k, v, pdir, notes) for k, v in _mcp_specs(pdir, m).items()) if s]
    for skipped in ("hooks", "agents"):
        if (pdir / skipped).exists() or m.get(skipped):
            notes.append(f"Skipped {skipped}: Meshpool does not run plugin {skipped}.")
    rec = {"name": name, "version": str(m.get("version") or ""), "description": str(m.get("description") or ""),
           "source": source, "installed": time.strftime("%Y-%m-%d %H:%M"), "dir": str(pdir), "skills": skills,
           "servers": servers, "notes": notes}
    d = _load()
    d["plugins"][name] = rec
    _save(d)
    return rec


# ---------- removing ----------------------------------------------------------------------------------------------

def _mcp_servers() -> dict:
    from meshpool_harness.agent import mcp as M
    try:
        return M.load_config()
    except (OSError, ValueError):
        return {}


def _edit_mcp(fn):
    """Load mcp.json, let fn change the servers dict, write it back."""
    from meshpool_harness.agent import mcp as M
    p = M.config_file()
    if not p.exists():
        return
    cfg = json.loads(p.read_text(encoding="utf-8"))
    key = "mcpServers" if "mcpServers" in cfg else "servers"
    fn(cfg.setdefault(key, {}))
    p.write_text(json.dumps(cfg, indent=2) + "\n", encoding="utf-8")


def _set_servers_disabled(names: list):
    def off(servers):
        for n in names:
            if isinstance(servers.get(n), dict):
                servers[n]["disabled"] = True
    _edit_mcp(off)


def remove_plugin(name: str) -> list[dict]:
    """Take away exactly what a plugin added: its skills, its MCP servers and its files. Built-in plugins are
    switched off instead."""
    d = _load()
    rec = d["plugins"].pop(name, None)
    if rec is None:
        if any(g["id"] == name for g in BUILTIN):
            enable_builtin(name, False)
            return list_plugins()
        raise ValueError(f"No plugin named {name}.")
    for sid in rec.get("skills", []):
        if _owned(sid, name):
            SK.remove(sid)

    def drop(servers):
        for n in rec.get("servers", []):
            if isinstance(servers.get(n), dict) and f"the {name} plugin" in str(servers[n].get("description", "")):
                del servers[n]
    _edit_mcp(drop)
    pdir = Path(rec.get("dir") or "")
    if pdir.parent == plugins_dir() and pdir.is_dir():
        shutil.rmtree(pdir, ignore_errors=True)
    _save(d)
    return list_plugins()


# ---------- app API (Settings, Plugins) ---------------------------------------------------------------------------

def api_view() -> dict:
    return {"plugins": list_plugins(), "catalog": catalog(), "file": str(state_file())}


def api_action(body: dict) -> dict:
    """POST /api/plugins: {action: install, source, names?} | {action: upload, file: {name, data(base64)}} |
    {action: enable, name, on} | {action: remove, name}."""
    act = body.get("action")
    done: list[dict] = []
    if act == "install":
        done = install_plugin(body.get("source", ""), body.get("names") or None)
    elif act == "upload":
        import base64
        f = body.get("file") or {}
        name = Path(str(f.get("name") or "plugin.zip")).name
        if not name.lower().endswith((".zip", ".json")):
            raise ValueError("Drop a plugin .zip or a marketplace.json.")
        with tempfile.TemporaryDirectory(prefix="meshpool-upload-") as t:
            p = Path(t) / name
            p.write_bytes(base64.b64decode(f.get("data") or ""))
            done = install_plugin(str(p), body.get("names") or None)
    elif act == "enable":
        enable_plugin(body.get("name", ""), bool(body.get("on")))
    elif act == "remove":
        remove_plugin(body.get("name", ""))
    else:
        raise ValueError(f"Unknown plugin action {act!r}.")
    return dict(api_view(), installed=done)
