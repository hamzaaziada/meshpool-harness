"""Per-turn checkpoints of a project folder, for any harness: what each turn changed, and undo.

Before and after every turn Meshpool records the folder's files (content-addressed, deduplicated, compressed) in the
session's folder. That gives a diff per turn and for the whole thread, and a revert that puts every file back the way
it was before a chosen turn, including deleting files the agent created. No Git needed.

Skipped: .git and other tool folders, dependency and build folders, anything the project's .gitignore lists (simple
patterns), files over 2 MB, and projects with more than 20,000 files. Ignored, too-big and unreadable files, links and
ignored folders are still listed in each snapshot with a "not stored" marker, so a file that only became visible during
a turn (say the agent edited .gitignore) is never mistaken for one the agent created, and Undo never deletes or
overwrites a path whose earlier (or current) contents it did not store.
"""
from __future__ import annotations

import difflib
import fnmatch
import hashlib
import json
import os
import stat
import time
import zlib
from pathlib import Path

SKIP_DIRS = {".git", ".hg", ".svn", "node_modules", ".venv", "venv", "__pycache__", ".mypy_cache", ".pytest_cache",
             ".next", ".nuxt", "dist", "build", ".cache", ".turbo", ".gradle", "target", ".idea", ".DS_Store",
             "Pods", ".terraform", ".tox", "coverage"}
SKIP_FILES = {".DS_Store"}
MAX_FILE = 2_000_000
MAX_FILES = 20_000


def _ignore_rules(root: Path) -> list[tuple[str, bool]]:
    rules = []
    gi = root / ".gitignore"
    try:
        for line in gi.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or line.startswith("!"):
                continue
            rules.append((line.rstrip("/").lstrip("/"), line.endswith("/")))
    except OSError:
        pass
    return rules


def _ignored(rel: str, is_dir: bool, rules) -> bool:
    name = rel.rsplit("/", 1)[-1]
    for pat, dir_only in rules:
        if dir_only and not is_dir:
            continue
        if "/" in pat:
            if fnmatch.fnmatch(rel, pat) or rel.startswith(pat + "/"):
                return True
        elif fnmatch.fnmatch(name, pat):
            return True
    return False


# Manifest values: "<sha>:<octal mode>" for a stored file (old manifests: "<sha>"), or a "!..." marker for a path that
# exists but whose contents were not stored. Keys ending in "/" are folders that were not walked (ignored or a link):
# everything under them existed, contents unknown.
def _stored(v) -> bool:
    return isinstance(v, str) and not v.startswith("!")


def _sha(v) -> str | None:
    return v.partition(":")[0] if _stored(v) else None


def _mode(v) -> int | None:
    m = v.partition(":")[2] if _stored(v) else ""
    return int(m, 8) if m else None


def _same(x, y) -> bool:
    """Same recorded state. Unstored contents are never "the same" as anything."""
    if x is None or y is None:
        return x is None and y is None
    if not (_stored(x) and _stored(y)):
        return False
    mx, my = _mode(x), _mode(y)
    return _sha(x) == _sha(y) and (mx is None or my is None or mx == my)


def _unknown(path: str, manifest: dict) -> bool:
    """True if `path` existed in `manifest` without its contents stored (marker, or under an unwalked folder)."""
    v = manifest.get(path)
    if v is not None and not _stored(v):
        return True
    parts = path.split("/")
    return any("/".join(parts[:i]) + "/" in manifest for i in range(1, len(parts)))


class Checkpoints:
    def __init__(self, root: str, store: Path):
        self.root = Path(root).resolve()
        self.store = Path(store)
        self.objects = self.store / "objects"
        self.index_path = self.store / "turns.json"
        self.disabled = ""
        self._last: dict = {}                       # path -> (size, mtime, sha) from the latest scan

    # ---------- storage ------------------------------------------------------------------------------------------
    def _turns(self) -> list:
        try:
            return json.loads(self.index_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []

    def _save_turns(self, turns: list):
        self.store.mkdir(parents=True, exist_ok=True)
        self.index_path.write_text(json.dumps(turns), encoding="utf-8")

    def _put(self, data: bytes) -> str:
        sha = hashlib.sha1(data).hexdigest()
        p = self.objects / sha[:2] / sha[2:]
        if not p.exists():
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(zlib.compress(data, 6))
        return sha

    def _get(self, sha: str) -> bytes:
        return zlib.decompress((self.objects / sha[:2] / sha[2:]).read_bytes())

    def _manifest_path(self, mid: str) -> Path:
        return self.store / "manifests" / f"{mid}.json"

    def _load_manifest(self, mid: str) -> dict:
        try:
            return json.loads(self._manifest_path(mid).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    # ---------- snapshot ---------------------------------------------------------------------------------------
    def snapshot(self) -> str | None:
        """Record the folder now. Returns a manifest id, or None when the folder is too big to track."""
        if self.disabled:
            return None
        rules = _ignore_rules(self.root)
        files, count = {}, 0
        def note(rel: str, marker: str) -> bool:
            nonlocal count
            count += 1
            if count > MAX_FILES:
                self.disabled = ("This folder is too big to track changes in, so Undo is off here. "
                                 "Work in a smaller folder to turn it back on.")
                return False
            files[rel] = marker
            return True

        for dirpath, dirnames, filenames in os.walk(self.root):
            d = Path(dirpath)
            rel_d = "" if d == self.root else d.relative_to(self.root).as_posix()
            keep = []
            for n in dirnames:
                rel = f"{rel_d}/{n}" if rel_d else n
                if n in SKIP_DIRS or n.startswith(".meshpool"):
                    continue
                if (d / n).is_symlink():
                    if not note(rel + "/", "!link"):
                        return None
                    continue
                if _ignored(rel, True, rules):
                    if not note(rel + "/", "!ignored"):
                        return None
                    continue
                keep.append(n)
            dirnames[:] = keep
            for n in filenames:
                rel = f"{rel_d}/{n}" if rel_d else n
                if n in SKIP_FILES:
                    continue
                p = d / n
                try:
                    st = p.lstat()
                except OSError:
                    if not note(rel, "!unreadable"):
                        return None
                    continue
                marker = ("!link" if stat.S_ISLNK(st.st_mode) else "!special" if not stat.S_ISREG(st.st_mode)
                          else "!ignored" if _ignored(rel, False, rules) else "!big" if st.st_size > MAX_FILE else "")
                if marker:
                    if not note(rel, marker):
                        return None
                    continue
                mode = f":{stat.S_IMODE(st.st_mode):o}"
                prev = self._last.get(rel)
                if prev and prev[0] == st.st_size and prev[1] == st.st_mtime_ns:
                    if not note(rel, prev[2] + mode):
                        return None
                    continue
                try:
                    sha = self._put(p.read_bytes())
                except OSError:
                    sha = ""
                if not note(rel, sha + mode if sha else "!unreadable"):
                    return None
                if sha:
                    self._last[rel] = (st.st_size, st.st_mtime_ns, sha)
        mid = time.strftime("%Y%m%d%H%M%S") + hashlib.sha1(json.dumps(files, sort_keys=True).encode()).hexdigest()[:8]
        p = self._manifest_path(mid)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(files), encoding="utf-8")
        return mid

    def begin_turn(self, title: str, message_index: int | None = None) -> dict | None:
        mid = self.snapshot()
        if not mid:
            return None
        turns = self._turns()
        t = {"n": len(turns) + 1, "title": title[:120], "before": mid, "after": None, "time": time.time(),
             "message_index": message_index}
        turns.append(t)
        self._save_turns(turns)
        return t

    def end_turn(self):
        turns = self._turns()
        if not turns or turns[-1].get("after"):
            return
        mid = self.snapshot()
        turns[-1]["after"] = mid
        turns[-1]["changed"] = self._changes(self._load_manifest(turns[-1]["before"]), self._load_manifest(mid)) if mid else []
        self._save_turns(turns)

    # ---------- reading ------------------------------------------------------------------------------------------
    @staticmethod
    def _changes(a: dict, b: dict) -> list:
        out = []
        for path in sorted(set(a) | set(b)):
            if path.endswith("/") or a.get(path) == b.get(path):
                continue
            if path not in a:                    # "added" only when it truly did not exist before
                status = "modified" if _unknown(path, a) else "added"
            else:
                status = "deleted" if path not in b and not _unknown(path, b) else "modified"
            out.append({"path": path, "status": status})
        return out

    def turns(self) -> list:
        return [{k: t.get(k) for k in ("n", "title", "time", "changed")} | {"done": bool(t.get("after"))}
                for t in self._turns()]

    def _text(self, v: str | None) -> list[str] | None:
        if not v:
            return []
        if not _stored(v):
            return None                          # exists, contents not stored: shown like a binary file
        data = self._get(_sha(v))
        if b"\0" in data[:8000]:
            return None
        return data.decode("utf-8", "replace").splitlines()

    def diff(self, turn: int | None = None, max_chars: int = 400_000) -> dict:
        """Unified diff for one turn, or for the whole thread (turn None: first turn's start to now)."""
        turns = self._turns()
        if not turns:
            return {"files": [], "patch": "", "note": self.disabled or "No changes recorded yet."}
        if turn is None:
            # Only the files this thread's own replies changed: each from before its first change to after its
            # last. Changes made by other threads or by the user in the same folder are not this thread's.
            a, b, seen = {}, {}, set()
            for t in sorted(turns, key=lambda x: x["n"]):
                before = self._load_manifest(t["before"])
                if t.get("after"):
                    after, changed = self._load_manifest(t["after"]), [c["path"] for c in t.get("changed") or []]
                else:                                   # a reply still running: what it has changed so far
                    mid = self.snapshot()
                    after = self._load_manifest(mid) if mid else before
                    changed = [c["path"] for c in self._changes(before, after)]
                for path in changed:
                    if path not in seen:
                        seen.add(path)
                        if before.get(path) is not None:
                            a[path] = before[path]
                        elif _unknown(path, before):    # existed, under a folder that was not walked
                            a[path] = "!unknown"
                    b.pop(path, None)
                    if after.get(path) is not None:
                        b[path] = after[path]
        else:
            t = next((x for x in turns if x["n"] == turn), None)
            if not t:
                raise ValueError("No such turn.")
            a = self._load_manifest(t["before"])
            b = self._load_manifest(t["after"]) if t.get("after") else self._load_manifest(self.snapshot() or "")
        files, chunks, size = [], [], 0
        for ch in self._changes(a, b):
            path = ch["path"]
            old, new = self._text(a.get(path)), self._text(b.get(path))
            if old is None or new is None:
                files.append(ch | {"add": 0, "del": 0, "binary": True})
                chunks.append(f"diff --meshpool a/{path} b/{path}\nBinary file {ch['status']}\n")
                continue
            lines = list(difflib.unified_diff(old, new, f"a/{path}", f"b/{path}", n=3, lineterm=""))
            add = sum(1 for x in lines if x.startswith("+") and not x.startswith("+++"))
            rem = sum(1 for x in lines if x.startswith("-") and not x.startswith("---"))
            files.append(ch | {"add": add, "del": rem})
            text = "\n".join(lines) + "\n"
            if size + len(text) <= max_chars:
                chunks.append(text)
                size += len(text)
        return {"files": files, "patch": "".join(chunks), "note": ""}

    # ---------- undo ---------------------------------------------------------------------------------------------
    def revert_to_before(self, turn: int) -> dict:
        """Undo the file changes the agent made in `turn` and every later turn. Only files those turns changed are
        touched, and only while they still hold what the agent left: files the user (or another tool) created or
        edited since stay as they are and are listed in "kept". Returns what changed on disk."""
        turns = self._turns()
        t = next((x for x in turns if x["n"] == turn), None)
        if not t:
            raise ValueError("No such turn.")
        target = self._load_manifest(t["before"])
        now_id = self.snapshot()
        if not now_id:                            # can't see the folder now: undoing blind could clobber files
            raise ValueError(self.disabled or "Couldn't read the folder, so nothing was undone.")
        now = self._load_manifest(now_id)
        agent_left: dict = {}                     # path -> content sha the agent's last change left (None: deleted)
        for x in sorted((x for x in turns if x["n"] >= turn and x.get("after")), key=lambda x: x["n"]):
            after = self._load_manifest(x["after"])
            for ch in x.get("changed") or []:
                agent_left[ch["path"]] = after.get(ch["path"])
        restored, deleted, kept, notes = [], [], [], []

        def keep(path: str, why: str = ""):
            kept.append(path)
            if why:
                notes.append(f"{path}: {why}")

        # 1. decide every action first, touching nothing
        unlinks, writes = [], []
        for path, left in sorted(agent_left.items()):
            if _unknown(path, now):               # can't tell what it holds now (ignored, too big, a link)
                keep(path, "its current contents aren't tracked (ignored, over 2 MB or a link), so it was left as is")
            elif not _same(now.get(path), left):  # changed after the agent: not ours to undo
                keep(path)
            elif _unknown(path, target):          # its earlier state was never stored: never delete or overwrite it
                keep(path, "its earlier version wasn't saved (ignored or over 2 MB), so it was left as is")
            elif path in target:
                if not _same(now.get(path), target[path]):
                    writes.append(path)
            elif path in now:
                unlinks.append(path)
        if writes and self._case_insensitive():
            gone = {p.lower() for p in unlinks}
            now_lower = {p.lower(): p for p in now if not p.endswith("/")}
            for path in list(writes):
                other = now_lower.get(path.lower())
                if other and other != path and path.lower() not in gone:
                    writes.remove(path)
                    keep(path, f"{other} now takes its name on this disk (names ignore case here), so it was left as is")

        # 2. apply: removals first (so a case-only rename or a file<->folder swap can be put back), then writes
        for path in sorted(unlinks, reverse=True):
            p = self.root / path
            try:
                if not self._inside(p.parent):
                    keep(path, "it is behind a link that points outside the project, so it was left as is")
                    continue
                p.unlink()
                deleted.append(path)
            except OSError as e:
                keep(path, f"couldn't delete it ({e.strerror or e})")
        for path in writes:
            p = self.root / path
            try:
                if not self._inside(p.parent):
                    keep(path, "it is behind a link that points outside the project, so it was left as is")
                    continue
                if p.is_symlink():
                    p.unlink()                    # replace the link, never write through it
                elif p.is_dir():
                    # a folder now stands where the file was, so it was made after the turn began: remove it only
                    # if nothing but empty folders is left in it
                    if not self._remove_empty_tree(p):
                        keep(path, "a folder with other files in it now has its name, so it was left as is")
                        continue
                data = self._get(_sha(target[path]))
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_bytes(data)
                if _mode(target[path]) is not None:
                    os.chmod(p, _mode(target[path]))
                restored.append(path)
            except (OSError, zlib.error) as e:
                keep(path, f"couldn't put it back ({getattr(e, 'strerror', None) or e})")
        self._last.clear()
        self._save_turns([x for x in turns if x["n"] < turn])
        out = {"restored": restored, "deleted": deleted, "kept": kept, "message_index": t.get("message_index")}
        if notes:
            out["notes"] = notes
        return out

    def _inside(self, p: Path) -> bool:
        try:
            Path(os.path.realpath(p)).relative_to(self.root)
            return True
        except ValueError:
            return False

    @staticmethod
    def _remove_empty_tree(d: Path) -> bool:
        for dirpath, dirnames, filenames in os.walk(d, topdown=False):
            if filenames:
                return False
            try:
                os.rmdir(dirpath)
            except OSError:
                return False
        return True

    def _case_insensitive(self) -> bool:
        name = self.root.name
        if name.swapcase() != name:
            alt = self.root.parent / name.swapcase()
            try:
                return alt.exists() and os.path.samefile(alt, self.root)
            except OSError:
                return False
        try:
            self.store.mkdir(parents=True, exist_ok=True)
            probe = self.store / "CaseProbe.tmp"
            probe.write_bytes(b"")
            try:
                return (self.store / "caseprobe.tmp").exists()
            finally:
                probe.unlink()
        except OSError:
            return False
