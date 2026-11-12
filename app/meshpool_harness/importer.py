"""Bring your setup: find the skills and MCP servers already set up in Claude, Codex, Cursor and other apps on this
computer, and import the ones the user picks into Meshpool. Every harness then shares them.

Nothing is read until the user opens the import step, and nothing is copied until they choose.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import sys
from pathlib import Path

from meshpool_harness import config as C

HOME = Path.home()


def _appdata() -> Path:
    if sys.platform == "darwin":
        return HOME / "Library" / "Application Support"
    if os.name == "nt":
        return Path(os.environ.get("APPDATA") or HOME / "AppData" / "Roaming")
    return Path(os.environ.get("XDG_CONFIG_HOME") or HOME / ".config")


# ---------- skills ----------------------------------------------------------------------------------------------------

def skill_sources() -> list[tuple[str, Path]]:
    rows = [("Claude Code", HOME / ".claude" / "skills"),
            ("Codex", HOME / ".codex" / "skills"),
            ("Agent skills", HOME / ".agents" / "skills"),
            ("Cursor", HOME / ".cursor" / "skills"),
            ("Gemini CLI", HOME / ".gemini" / "skills")]
    plugins = HOME / ".claude" / "plugins"
    if plugins.is_dir():
        for d in sorted(plugins.glob("**/skills")):
            if d.is_dir() and "node_modules" not in d.parts:
                rows.append(("Claude Code plugin", d))
    try:
        from meshpool_harness.ui import workbench as W
        for p in W.projects():
            for sub, label in ((".claude/skills", "Project (Claude)"), (".codex/skills", "Project (Codex)"),
                               (".agents/skills", "Project")):
                rows.append((f"{label}: {p['name']}", Path(p["path"]) / sub))
    except Exception:  # noqa: BLE001 - projects are optional here
        pass
    return rows


def find_skills() -> list[dict]:
    from meshpool_harness import skills as SK
    have = {s["id"] for s in SK.load_all()}
    seen, out = set(), []
    for source, root in skill_sources():
        if not root.is_dir():
            continue
        for f in sorted(root.glob("*/SKILL.md")) + sorted(root.glob("*/*/SKILL.md")):
            d = f.parent
            if d.name.startswith(".") or str(d) in seen:
                continue
            seen.add(str(d))
            try:
                meta, _ = SK.parse(f.read_text(encoding="utf-8", errors="replace"))
            except OSError:
                continue
            sid = SK.slug(meta.get("name") or d.name)
            out.append({"key": "skill:" + str(d), "kind": "skill", "source": source, "name": meta.get("name") or d.name,
                        "description": meta.get("description", "")[:200], "path": str(d), "id": sid,
                        "exists": sid in have,
                        # plugin skills come in bundles of dozens: listed, but not ticked by default
                        "suggested": source != "Claude Code plugin"})
    return out


def import_skill(path: str) -> str:
    from meshpool_harness import skills as SK
    src = Path(path)
    if not (src / "SKILL.md").is_file():
        raise ValueError(f"No SKILL.md in {path}")
    meta, _ = SK.parse((src / "SKILL.md").read_text(encoding="utf-8", errors="replace"))
    dst = SK.folder() / SK.slug(meta.get("name") or src.name)
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(src, dst, ignore=shutil.ignore_patterns(".git", "node_modules", "__pycache__", ".DS_Store"),
                    symlinks=False)
    return dst.name


# ---------- MCP servers -----------------------------------------------------------------------------------------------

def _json(p: Path) -> dict:
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _norm(spec: dict) -> dict | None:
    """One server entry from any app's config, in Meshpool's mcp.json shape."""
    if not isinstance(spec, dict):
        return None
    if spec.get("disabled") is True or spec.get("enabled") is False:
        return None
    url = spec.get("url") or spec.get("serverUrl") or spec.get("httpUrl")
    if url:
        out = {"url": url}
        if isinstance(spec.get("headers"), dict):
            out["headers"] = {str(k): str(v) for k, v in spec["headers"].items()}
        if spec.get("bearer_token_env_var"):
            out.setdefault("headers", {})["Authorization"] = "Bearer ${" + spec["bearer_token_env_var"] + "}"
        return out
    cmd = spec.get("command")
    if not cmd:
        return None
    out = {"command": str(cmd), "args": [str(a) for a in spec.get("args") or []]}
    if isinstance(spec.get("env"), dict) and spec["env"]:
        out["env"] = {str(k): str(v) for k, v in spec["env"].items()}
    if spec.get("cwd"):
        out["cwd"] = str(spec["cwd"])
    return out


def mcp_sources() -> list[tuple[str, dict]]:
    """(app name, {server name: raw spec}) for every app config found on this computer."""
    found = []
    ad = _appdata()
    for app, p, key in (("Claude Desktop", ad / "Claude" / "claude_desktop_config.json", "mcpServers"),
                        ("Cursor", HOME / ".cursor" / "mcp.json", "mcpServers"),
                        ("Windsurf", HOME / ".codeium" / "windsurf" / "mcp_config.json", "mcpServers"),
                        ("Gemini CLI", HOME / ".gemini" / "settings.json", "mcpServers"),
                        ("VS Code", ad / "Code" / "User" / "mcp.json", "servers"),
                        ("Cline", ad / "Code" / "User" / "globalStorage" / "saoudrizwan.claude-dev" / "settings"
                         / "cline_mcp_settings.json", "mcpServers")):
        servers = _json(p).get(key)
        if isinstance(servers, dict) and servers:
            found.append((app, servers))
    cc = _json(HOME / ".claude.json")
    if isinstance(cc.get("mcpServers"), dict) and cc["mcpServers"]:
        found.append(("Claude Code", cc["mcpServers"]))
    for proj, conf in (cc.get("projects") or {}).items():
        if isinstance(conf, dict) and isinstance(conf.get("mcpServers"), dict) and conf["mcpServers"]:
            found.append((f"Claude Code: {Path(proj).name}", conf["mcpServers"]))
    codex = HOME / ".codex" / "config.toml"
    if codex.is_file():
        try:
            import tomllib
            servers = tomllib.loads(codex.read_text(encoding="utf-8")).get("mcp_servers") or {}
            if servers:
                found.append(("Codex", servers))
        except Exception:  # noqa: BLE001 - a broken config file is skipped
            pass
    return found


def find_mcp() -> list[dict]:
    from meshpool_harness.agent import mcp as M
    try:
        have = M.load_config()
    except Exception:  # noqa: BLE001
        have = {}
    have_sig = {json.dumps(_norm(v) or {}, sort_keys=True) for v in have.values() if isinstance(v, dict)}
    out, seen = [], set()
    for app, servers in mcp_sources():
        for name, raw in servers.items():
            spec = _norm(raw)
            if not spec:
                continue
            sig = json.dumps(spec, sort_keys=True)
            if sig in seen:
                continue
            seen.add(sig)
            what = spec.get("url") or " ".join([spec["command"]] + spec.get("args", []))[:140]
            out.append({"key": f"mcp:{app}:{name}", "kind": "mcp", "source": app, "name": str(name),
                        "description": what, "spec": spec, "exists": sig in have_sig,
                        "has_secrets": bool(spec.get("env") or spec.get("headers"))})
    return out


def import_mcp(name: str, spec: dict) -> str:
    from meshpool_harness.agent import mcp as M
    p = M.config_file()
    try:
        cfg = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {"mcpServers": {}}
    except ValueError:
        raise ValueError("mcp.json is not valid JSON. Fix it under Settings, MCP servers, then import again.")
    servers = cfg.setdefault("mcpServers", {})
    base = re.sub(r"[^A-Za-z0-9_-]+", "-", name).strip("-")[:40] or "server"
    n, i = base, 2
    while n in servers and json.dumps(_norm(servers[n]) or {}, sort_keys=True) != json.dumps(spec, sort_keys=True):
        n, i = f"{base}-{i}", i + 1
    servers[n] = dict(spec, description=f"Imported from another app ({name}).")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    return n


# ---------- both --------------------------------------------------------------------------------------------------------

def scan() -> dict:
    return {"skills": find_skills(), "mcp": find_mcp()}


def run(keys: list[str]) -> dict:
    keys = set(keys or [])
    found = scan()
    done = {"skills": [], "mcp": [], "errors": []}
    for s in found["skills"]:
        if s["key"] in keys:
            try:
                done["skills"].append(import_skill(s["path"]))
            except (OSError, ValueError) as e:
                done["errors"].append(f"{s['name']}: {e}")
    for m in found["mcp"]:
        if m["key"] in keys:
            try:
                done["mcp"].append(import_mcp(m["name"], m["spec"]))
            except (OSError, ValueError) as e:
                done["errors"].append(f"{m['name']}: {e}")
    return done
