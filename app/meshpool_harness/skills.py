"""Skills: folders with a SKILL.md (name and description in YAML front matter, then instructions), the same format
Claude, Codex and Hermes use. They live in the Meshpool data folder under skills/. Agents see each skill's name and
description, and load the full instructions when a task matches (or when the user mentions $skill-name).

Meshpool ships a few skills in defaults/skills; the user adds their own by dropping a folder, a .zip or a SKILL.md
on the Skills page, or by writing one there.
"""
from __future__ import annotations

import io
import json
import re
import shutil
import zipfile
from pathlib import Path

from meshpool_harness import config as C

NAME_RE = re.compile(r"[^a-z0-9-]+")


def folder() -> Path:
    return C.home() / "skills"


def _state_path() -> Path:
    return folder() / ".disabled.json"


def _disabled() -> set:
    try:
        return set(json.loads(_state_path().read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return set()


def _save_disabled(s: set):
    folder().mkdir(parents=True, exist_ok=True)
    _state_path().write_text(json.dumps(sorted(s)), encoding="utf-8")


def slug(name: str) -> str:
    s = NAME_RE.sub("-", name.strip().lower()).strip("-")
    return s[:64] or "skill"


def parse(text: str) -> tuple[dict, str]:
    """Front matter (simple YAML: key: value lines) and body."""
    meta, body = {}, text
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            for line in text[3:end].splitlines():
                if ":" in line and not line.startswith((" ", "\t")):
                    k, v = line.split(":", 1)
                    meta[k.strip()] = v.strip().strip('"').strip("'")
            body = text[end + 4:].lstrip("\n")
    return meta, body


def ensure_defaults():
    src = C.APP / "defaults" / "skills"
    dst = folder()
    dst.mkdir(parents=True, exist_ok=True)
    marker = dst / ".defaults-installed"
    if not src.is_dir():
        return
    done: set = set()                               # names already offered once; a removed default stays removed
    if marker.exists():
        try:
            v = json.loads(marker.read_text(encoding="utf-8"))
        except ValueError:
            v = None
        # 2.2 wrote "1" after installing its three defaults; anything newer is offered once
        done = set(v) if isinstance(v, list) else {"code-review", "pull-request", "research-brief"}
    new = [d for d in src.iterdir() if d.is_dir() and d.name not in done]
    if not new:
        return
    for d in new:
        if not (dst / d.name).exists():
            shutil.copytree(d, dst / d.name, copy_function=shutil.copyfile)
    marker.write_text(json.dumps(sorted(done | {d.name for d in new})), encoding="utf-8")


def expand_mentions(text: str) -> str:
    """For harnesses with their own skill systems (Pi, Hermes): when the message names $skill, attach that skill's
    instructions so it is followed the same way in every harness."""
    import re
    out = []
    for m in dict.fromkeys(re.findall(r"(?<![\w$])\$([A-Za-z0-9][\w-]{1,63})", text)):
        sk = get(m)
        if sk and sk.get("enabled", True):
            try:
                out.append(instructions(m))
            except ValueError:
                pass
    if not out:
        return text
    return text + "\n\n\u200b---\nThe user asked for these skills. Follow their instructions:\n\n" + "\n\n---\n".join(out)


def load_all(include_disabled: bool = True) -> list[dict]:
    ensure_defaults()
    off = _disabled()
    out = []
    for d in sorted(folder().iterdir()) if folder().exists() else []:
        f = d / "SKILL.md"
        if not d.is_dir() or not f.is_file():
            continue
        try:
            meta, body = parse(f.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            continue
        sid = d.name
        row = {"id": sid, "name": meta.get("name") or sid, "description": meta.get("description", ""),
               "enabled": sid not in off, "path": str(f),
               "files": sorted(str(p.relative_to(d)) for p in d.rglob("*") if p.is_file() and p.name != "SKILL.md")[:50]}
        if include_disabled or row["enabled"]:
            out.append(row)
    return out


def enabled() -> list[dict]:
    return load_all(include_disabled=False)


def get(name: str) -> dict | None:
    key = slug(name)
    for s in load_all():
        if s["id"] == key or slug(s["name"]) == key:
            return s
    return None


def instructions(name: str) -> str:
    s = get(name)
    if not s:
        names = ", ".join(x["name"] for x in enabled()) or "none"
        raise ValueError(f"No skill named {name}. Available: {names}")
    text = Path(s["path"]).read_text(encoding="utf-8", errors="replace")
    extra = ""
    if s["files"]:
        extra = (f"\n\nFiles that come with this skill (read them with read_file using these full paths):\n"
                 + "\n".join(str(Path(s["path"]).parent / f) for f in s["files"]))
    return f"Skill: {s['name']}\nFolder: {Path(s['path']).parent}\n\n{text}{extra}"


def catalog_text() -> str:
    """The skills list for an agent's system prompt."""
    rows = enabled()
    if not rows:
        return ""
    lines = [f"- {s['name']}: {s['description']}" for s in rows]
    return ("\n\nSkills you can use (call use_skill with the name to load full instructions before doing a task "
            "that matches one; when the user writes $name, load that skill first):\n" + "\n".join(lines))


# ---------- changes from the app ---------------------------------------------------------------------------------
def create(name: str, description: str, body: str) -> dict:
    if not name.strip() or not description.strip():
        raise ValueError("A skill needs a name and a one-line description.")
    d = folder() / slug(name)
    if d.exists():
        raise ValueError(f"A skill named {slug(name)} already exists.")
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(f"---\nname: {slug(name)}\ndescription: {description.strip()}\n---\n\n{body.strip()}\n",
                                encoding="utf-8")
    return get(name)


def update(sid: str, text: str) -> dict:
    d = folder() / slug(sid)
    if not (d / "SKILL.md").exists():
        raise ValueError("No such skill.")
    meta, _ = parse(text)
    if not meta.get("description"):
        raise ValueError("SKILL.md needs front matter with a description line.")
    (d / "SKILL.md").write_text(text, encoding="utf-8")
    return get(sid)


def add_files(files: list[tuple[str, bytes]]) -> list[dict]:
    """Add skills from uploads: a .zip of one or more skill folders, a SKILL.md, or files of one folder
    (names like my-skill/SKILL.md, my-skill/scripts/x.py)."""
    added = []
    folder().mkdir(parents=True, exist_ok=True)
    loose: dict[str, list[tuple[str, bytes]]] = {}
    for name, data in files:
        name = name.replace("\\", "/").lstrip("/")
        if name.lower().endswith((".zip", ".skill")):
            added += _from_zip(data)
        elif name.endswith("SKILL.md") and "/" not in name:
            meta, _ = parse(data.decode("utf-8", "replace"))
            sid = slug(meta.get("name") or "skill")
            d = folder() / sid
            d.mkdir(parents=True, exist_ok=True)
            (d / "SKILL.md").write_bytes(data)
            added.append(sid)
        elif "/" in name:
            top, rest = name.split("/", 1)
            loose.setdefault(top, []).append((rest, data))
        elif name.lower().endswith(".md"):
            meta, body = parse(data.decode("utf-8", "replace"))
            sid = slug(meta.get("name") or Path(name).stem)
            d = folder() / sid
            d.mkdir(parents=True, exist_ok=True)
            text = data.decode("utf-8", "replace")
            if not meta.get("description"):
                text = f"---\nname: {sid}\ndescription: {Path(name).stem.replace('-', ' ')}\n---\n\n{text}"
            (d / "SKILL.md").write_text(text, encoding="utf-8")
            added.append(sid)
    for top, items in loose.items():
        if not any(r == "SKILL.md" for r, _ in items):
            continue
        meta, _ = parse(next(d for r, d in items if r == "SKILL.md").decode("utf-8", "replace"))
        d = folder() / slug(meta.get("name") or top)
        for rel, data in items:
            p = (d / rel).resolve()
            if d.resolve() not in p.parents and p != d.resolve():
                continue
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(data)
        added.append(d.name)
    if not added:
        raise ValueError("No skill found. Add a folder or .zip that contains a SKILL.md, or a SKILL.md file.")
    return [get(a) for a in added if get(a)]


def _from_zip(data: bytes) -> list[str]:
    z = zipfile.ZipFile(io.BytesIO(data))
    names = [n for n in z.namelist() if not n.endswith("/") and "__MACOSX" not in n]
    roots = sorted({n.rsplit("/SKILL.md", 1)[0] if "/" in n else "" for n in names if n.endswith("SKILL.md")})
    out = []
    for root in roots:
        prefix = (root + "/") if root else ""
        meta, _ = parse(z.read(prefix + "SKILL.md").decode("utf-8", "replace"))
        d = folder() / slug(meta.get("name") or (root.rsplit("/", 1)[-1] if root else "skill"))
        for n in names:
            if not n.startswith(prefix):
                continue
            rel = n[len(prefix):]
            p = (d / rel).resolve()
            if d.resolve() not in p.parents:
                continue
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(z.read(n))
        out.append(d.name)
    return out


def toggle(sid: str, on: bool):
    off = _disabled()
    (off.discard if on else off.add)(sid)
    _save_disabled(off)


def remove(sid: str):
    d = folder() / slug(sid)
    if d.is_dir() and d.parent == folder():
        shutil.rmtree(d)


def sync_to(dst: Path):
    """Mirror enabled skills into another harness's skills folder (Hermes reads HERMES_HOME/skills)."""
    dst.mkdir(parents=True, exist_ok=True)
    want = {s["id"]: Path(s["path"]).parent for s in enabled()}
    for d in dst.iterdir():
        if d.is_dir() and (d / ".from-meshpool").exists() and d.name not in want:
            shutil.rmtree(d, ignore_errors=True)
    for sid, src in want.items():
        t = dst / sid
        if t.exists() and not (t / ".from-meshpool").exists():
            continue                                # the harness has its own skill with this name: keep it
        shutil.rmtree(t, ignore_errors=True)
        shutil.copytree(src, t, copy_function=shutil.copyfile)
        (t / ".from-meshpool").write_text("1", encoding="utf-8")
