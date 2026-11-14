"""What carries over between chats: personal instructions, memory, scheduled tasks and past-chat search.

Shared by every harness: the app adds the instructions and memory to the first message of each new thread, and the
meshpool-assistant MCP server lets any agent save to memory, schedule work and look up earlier chats.

Files in the Meshpool data folder:
  memory/instructions.md   how the user wants answers (the user edits it in Settings)
  memory/memory.md         facts agents saved, one per line
  schedules.json           scheduled tasks
"""
from __future__ import annotations

import contextlib
import difflib
import json
import os
import re
import tempfile
import threading
import time
import uuid
from datetime import datetime, timedelta
from pathlib import Path

from meshpool_harness import config as C

MAX_MEMORY = 12000
DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
WHEN = ("once", "hourly", "daily", "weekdays", "weekly", "every")
_LOCK = threading.Lock()
BAD = re.compile(r"ignore (all|previous|the above)|system prompt|disregard (all|previous)|you are now|api[_ ]?key|"
                 r"password|secret|token\s*[:=]", re.I)


def _dir() -> Path:
    d = C.home() / "memory"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _read(p: Path) -> str:
    try:
        return p.read_text(encoding="utf-8")
    except OSError:
        return ""


def _write(p: Path, text: str):
    """Write atomically through a temp file with a unique name, so two writers never share one temp file."""
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{p.name}.", suffix=".tmp", dir=str(p.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.replace(tmp, p)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


@contextlib.contextmanager
def _locked(lock: Path):
    """Hold a lock across threads and processes (the app's scheduler, other app windows and the assistant MCP server
    all change these files) for one read-modify-write."""
    with _LOCK:
        lock.parent.mkdir(parents=True, exist_ok=True)
        with open(lock, "a+b") as fh:
            if os.name == "nt":
                import msvcrt
                while True:
                    try:
                        fh.seek(0)
                        msvcrt.locking(fh.fileno(), msvcrt.LK_LOCK, 1)
                        break
                    except OSError:                  # LK_LOCK gives up after about 10 s: keep waiting
                        time.sleep(0.05)
            else:
                import fcntl
                fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                if os.name == "nt":
                    import msvcrt
                    with contextlib.suppress(OSError):
                        fh.seek(0)
                        msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(fh.fileno(), fcntl.LOCK_UN)


def _memory_lock():
    return _locked(_dir() / ".memory.lock")


def _tasks_lock():
    return _locked(C.home() / "schedules.lock")


def settings() -> dict:
    d = json.loads(_read(_dir() / "settings.json") or "{}")
    return {"memory_on": d.get("memory_on", True), "instructions_on": d.get("instructions_on", True)}


def save_settings(**kw) -> dict:
    d = settings()
    d.update({k: bool(v) for k, v in kw.items() if k in ("memory_on", "instructions_on")})
    _write(_dir() / "settings.json", json.dumps(d))
    return d


# ---------- instructions and memory --------------------------------------------------------------------------------

def instructions() -> str:
    return _read(_dir() / "instructions.md").strip()


def set_instructions(text: str):
    _write(_dir() / "instructions.md", (text or "").strip()[:8000] + "\n")


def memory() -> str:
    return _read(_dir() / "memory.md").strip()


def set_memory(text: str):
    _write(_dir() / "memory.md", (text or "").strip()[:MAX_MEMORY] + "\n")


def remember(fact: str) -> str:
    fact = re.sub(r"\s+", " ", (fact or "")).strip().lstrip("-• ").strip()
    if len(fact) < 4:
        raise ValueError("Say what to remember in a short sentence.")
    if len(fact) > 400:
        raise ValueError("Keep it under 400 characters: one fact per call.")
    if BAD.search(fact):
        raise ValueError("Memory does not store secrets or instructions about the system.")
    with _memory_lock():
        cur = memory()
        lines = [x for x in cur.splitlines() if x.strip()]
        key = _norm_fact(fact)
        if any(_norm_fact(x) == key for x in lines):    # whole lines: "likes tea" is not "Dislikes tea"
            return "Already in memory."
        lines.append(f"- {fact}")
        text = "\n".join(lines)
        while len(text) > MAX_MEMORY and len(lines) > 1:       # drop the oldest lines when full
            lines.pop(0)
            text = "\n".join(lines)
        set_memory(text)
    return "Saved to memory."


def _norm_fact(line: str) -> str:
    return re.sub(r"\s+", " ", line).strip().lstrip("-•* ").strip().rstrip(".").strip().lower()


def forget(text: str) -> str:
    """Remove the memory lines that say exactly this; if none does, the one line that matches it best."""
    key = _norm_fact(text or "")
    if not key:
        raise ValueError("Say what to forget.")
    with _memory_lock():
        lines = memory().splitlines()
        facts = [(i, _norm_fact(x)) for i, x in enumerate(lines) if _norm_fact(x)]
        drop = {i for i, f in facts if f == key}
        if not drop:
            near = [(difflib.SequenceMatcher(None, key, f).ratio(), i) for i, f in facts if key in f]
            if not near:
                near = [(r, i) for r, i in ((difflib.SequenceMatcher(None, key, f).ratio(), i) for i, f in facts)
                        if r >= 0.6]
            if near:
                drop = {max(near)[1]}
        if not drop:
            return "Nothing in memory matched that."
        removed = [lines[i].strip() for i in sorted(drop)]
        set_memory("\n".join(x for i, x in enumerate(lines) if i not in drop))
    return f"Removed {len(removed)} line(s) from memory:\n" + "\n".join(removed)


def context_block() -> str:
    """Text the app adds, out of sight, to the first message of a new thread."""
    st = settings()
    now = datetime.now().astimezone()
    parts = [f"Today is {now.strftime('%A %d %B %Y')}, {now.strftime('%H:%M')} local time "
             f"(UTC{now.strftime('%z')[:3]}:{now.strftime('%z')[3:]})."]
    ins = instructions() if st["instructions_on"] else ""
    mem = memory() if st["memory_on"] else ""
    if ins:
        parts.append("The user's instructions for every chat (follow them):\n" + ins[:6000])
    if mem:
        parts.append("What you remember about the user from earlier chats (use it only where it changes the answer; "
                     "do not mention that it came from memory):\n" + mem[-6000:])
    parts.append("Notes from Meshpool: look things up with the web search, research and fetch tools, not the browser. "
                 "Cite sources with links. "
                 + ("If the user asks you to remember something, or states a lasting fact or preference about "
                    "themselves or their work, save it with memory_save. " if st["memory_on"] else "")
                 + "If the user asks for something to happen later or on a schedule, use schedule_task. "
                 "For a large job with independent parts (research many companies, review many files), use "
                 "run_subtasks to work on the parts in parallel.")
    return "\n\n".join(parts)


# ---------- scheduled tasks ------------------------------------------------------------------------------------------

def _sfile() -> Path:
    return C.home() / "schedules.json"


def tasks() -> list[dict]:
    try:
        d = json.loads(_read(_sfile()) or "[]")
        return d if isinstance(d, list) else []
    except ValueError:
        return []


def _save_tasks(rows: list[dict]):
    _write(_sfile(), json.dumps(rows, indent=1))


def _hm(s: str) -> tuple[int, int]:
    m = re.fullmatch(r"\s*(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\s*", (s or "9:00").lower())
    if not m:
        raise ValueError("Give the time as HH:MM, for example 08:30.")
    h, mi = int(m.group(1)), int(m.group(2) or 0)
    if m.group(3) == "pm" and h < 12:
        h += 12
    if m.group(3) == "am" and h == 12:
        h = 0
    if not (0 <= h < 24 and 0 <= mi < 60):
        raise ValueError("Give the time as HH:MM, for example 08:30.")
    return h, mi


def next_run(t: dict, after: datetime | None = None) -> float | None:
    now = (after or datetime.now()).replace(second=0, microsecond=0)
    w = t.get("when")
    if w == "once":
        try:
            at = datetime.fromisoformat(t["at"])
        except (KeyError, ValueError):
            return None
        return at.timestamp() if not t.get("last_run") else None
    if w == "hourly":
        return (now.replace(minute=0) + timedelta(hours=1)).timestamp()
    if w == "every":
        mins = max(15, int(t.get("minutes") or 60))
        return (now + timedelta(minutes=mins)).timestamp()
    h, mi = _hm(t.get("time", "09:00"))
    cand = now.replace(hour=h, minute=mi)
    for i in range(0, 8):
        d = cand + timedelta(days=i)
        if d <= now:
            continue
        if w == "weekdays" and d.weekday() > 4:
            continue
        if w == "weekly" and DAYS[d.weekday()] != (t.get("day") or "mon")[:3].lower():
            continue
        return d.timestamp()
    return None


def _check_task(t: dict) -> dict:
    """Validate and normalise a task's schedule fields in place (shared by add_task and update_task)."""
    when = t.get("when")
    if when not in WHEN:
        raise ValueError("when must be one of: " + ", ".join(WHEN))
    if not str(t.get("prompt") or "").strip():
        raise ValueError("Write what the task should do.")
    t["prompt"] = str(t["prompt"]).strip()
    if when == "once":
        try:
            datetime.fromisoformat(str(t.get("at") or ""))
        except ValueError:
            raise ValueError("For a one-time task give `at` as a local date and time, like 2026-11-20T09:00.")
    if when in ("daily", "weekdays", "weekly"):
        h, mi = _hm(t.get("time") or "09:00")
        t["time"] = f"{h:02d}:{mi:02d}"
    t["day"] = (t.get("day") or "mon")[:3].lower()
    if when == "weekly" and t["day"] not in DAYS:
        raise ValueError("day must be mon, tue, wed, thu, fri, sat or sun.")
    try:
        t["minutes"] = max(15, int(t.get("minutes") or 60))
    except (TypeError, ValueError):
        raise ValueError("minutes must be a whole number.")
    if t.get("kind") not in ("cowork", "code"):
        t["kind"] = "cowork"
    t["name"] = str(t.get("name") or t["prompt"].splitlines()[0])[:80]
    return t


def add_task(name: str, prompt: str, when: str, time_: str = "09:00", day: str = "mon", at: str = "",
             minutes: int = 60, folder: str = "", harness: str = "", kind: str = "cowork") -> dict:
    t = _check_task({"id": uuid.uuid4().hex[:8], "name": name, "prompt": prompt, "when": when, "time": time_,
                     "day": day, "at": at, "minutes": minutes, "folder": folder, "harness": harness, "kind": kind,
                     "enabled": True, "created": time.time(), "last_run": None, "last_thread": None})
    t["next_run"] = next_run(t)
    with _tasks_lock():
        rows = tasks()
        rows.append(t)
        _save_tasks(rows)
    return t


def update_task(tid: str, **kw) -> dict:
    with _tasks_lock():
        rows = tasks()
        for n, t in enumerate(rows):
            if t["id"] == tid:
                new = dict(t)
                for k in ("name", "prompt", "when", "time", "day", "at", "minutes", "folder", "harness", "kind", "enabled"):
                    if k in kw and kw[k] is not None:
                        new[k] = kw[k]
                _check_task(new)                                     # same rules as add_task; nothing saved on error
                if new.get("when") != t.get("when") or new.get("at") != t.get("at"):
                    new["last_run"] = None                           # a new time: a fired one-time task runs again
                new["next_run"] = next_run(new) if new.get("enabled") else None
                rows[n] = new
                _save_tasks(rows)
                return new
    raise KeyError(tid)


def delete_task(tid: str) -> bool:
    with _tasks_lock():
        rows = tasks()
        keep = [t for t in rows if t["id"] != tid and t.get("name", "").lower() != tid.lower()]
        _save_tasks(keep)
    return len(keep) != len(rows)


def due() -> list[dict]:
    """Tasks whose time has come; marks them as run and works out their next time. The claim (re-read, check, mark,
    save) happens under the cross-process lock, so two app instances never both run the same task."""
    now = time.time()
    out = []
    with _tasks_lock():
        rows = tasks()
        changed = False
        for t in rows:
            if t.get("enabled") and t.get("next_run") and t["next_run"] <= now:
                late = now - t["next_run"] > 6 * 3600         # the computer was off: run once, not for every miss
                t["last_run"] = now
                t["next_run"] = next_run(t)
                if t["when"] == "once":
                    t["enabled"] = False
                changed = True
                out.append(dict(t, late=late))
        if changed:
            _save_tasks(rows)
    return out


def describe(t: dict) -> str:
    w = t.get("when")
    if w == "once":
        s = f"once at {t.get('at', '').replace('T', ' ')}"
    elif w == "hourly":
        s = "every hour"
    elif w == "every":
        s = f"every {t.get('minutes')} minutes"
    elif w == "weekly":
        s = f"every {t.get('day', 'mon').title()} at {t.get('time')}"
    else:
        s = f"{'every weekday' if w == 'weekdays' else 'every day'} at {t.get('time')}"
    nr = t.get("next_run")
    return s + (f", next {datetime.fromtimestamp(nr).strftime('%a %d %b %H:%M')}" if nr and t.get("enabled") else
                ", paused" if not t.get("enabled") else "")


def set_thread(tid: str, sid: str):
    with _tasks_lock():
        rows = tasks()
        for t in rows:
            if t["id"] == tid:
                t["last_thread"] = sid
        _save_tasks(rows)


# ---------- past chats ----------------------------------------------------------------------------------------------

def search_chats(query: str, limit: int = 8) -> str:
    from meshpool_harness.agent.core import SESSIONS
    words = [w for w in re.findall(r"\w{3,}", (query or "").lower())]
    if not words:
        raise ValueError("Give a few words to search for.")
    hits = []
    for p in SESSIONS.glob("*/session.json") if SESSIONS.exists() else []:
        if p.parent.name.startswith("sub-"):
            continue
        try:
            raw = p.read_text(encoding="utf-8")
            d = json.loads(raw)
        except (OSError, ValueError):
            continue
        meta = {}
        try:
            meta = json.loads((p.parent / "meta.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pass
        if meta.get("deleted"):
            continue
        texts = []
        for m in d.get("messages") or []:
            c = m.get("content")
            if isinstance(c, list):
                c = " ".join(x.get("text", "") for x in c if isinstance(x, dict))
            if isinstance(c, str) and m.get("role") in ("user", "assistant"):
                texts.append((m["role"], c.split("\n\n​")[0]))
        for e in d.get("events") or []:
            if e.get("type") in ("user", "assistant") and e.get("text"):
                texts.append((e["type"], e["text"]))
        low = " ".join(t for _, t in texts).lower()
        score = sum(low.count(w) for w in words)
        if not score or not all(w in low for w in words[:3]):
            continue
        snip = ""
        for role, t in texts:
            i = t.lower().find(words[0])
            if i >= 0:
                snip = f"{role}: " + re.sub(r"\s+", " ", t[max(0, i - 120): i + 280])
                break
        hits.append((score, d.get("updated", 0), meta.get("title") or d.get("title") or "Untitled", d.get("id"),
                     d.get("workdir"), snip))
    if not hits:
        return "No earlier chats matched."
    hits.sort(key=lambda h: (-h[0], -h[1]))
    return "\n\n".join(f"{i}. {h[2]} ({datetime.fromtimestamp(h[1]).strftime('%d %b %Y') if h[1] else ''}, "
                       f"folder {h[4]})\n   {h[5]}" for i, h in enumerate(hits[:limit], 1))


# ---------- parallel sub-agents -------------------------------------------------------------------------------------

def run_subtasks(items: list, folder: str = "", max_parallel: int = 4, max_steps: int = 30) -> str:
    """Run independent research or reading tasks in parallel with Meshpool's own agent (read-only plus web tools)."""
    import os
    import shutil
    from concurrent.futures import ThreadPoolExecutor
    from meshpool_harness.agent import core as K
    C.load_into_env(override=False)
    rows = []
    for it in items or []:
        if isinstance(it, str):
            it = {"title": it[:60], "prompt": it}
        if isinstance(it, dict) and str(it.get("prompt", "")).strip():
            rows.append({"title": str(it.get("title") or it["prompt"])[:80], "prompt": str(it["prompt"])})
    if not rows:
        raise ValueError("Give at least one task with a prompt.")
    rows = rows[:12]
    root = folder or os.environ.get("MESHPOOL_PROJECT") or str(Path.home())
    if not Path(root).is_dir():
        root = str(Path.home())
    tools = ["read_file", "grep", "glob", "fetch_url", "web_search", "web_research"]

    class Sub(K.Agent):
        def _tool_names(self):
            return tools

    def one(r):
        a = None
        t0 = time.time()
        try:
            a = Sub(root, mode="auto", session_id="sub-" + uuid.uuid4().hex[:10], max_steps=max_steps, profile="work")
            a.messages[0]["content"] += ("\n\nYou are a sub-agent working on one part of a larger job. You can read "
                                         "files and the web but not change anything. Do the task fully, then reply "
                                         "with a complete, self-contained report: findings first, with a link or "
                                         "file path for every fact, and say what you could not find.")
            out = a.send(r["prompt"]) or "(no reply)"
        except Exception as e:                                # noqa: BLE001 - one failed part must not sink the rest
            out = f"This part failed: {e}"
        finally:
            if a is not None:
                shutil.rmtree(a.dir, ignore_errors=True)
        return f"## {r['title']}\n({round(time.time() - t0)} s)\n\n{out.strip()}"

    with ThreadPoolExecutor(max(1, min(int(max_parallel or 4), 6))) as ex:
        res = list(ex.map(one, rows))
    return f"{len(rows)} parts finished.\n\n" + "\n\n".join(res)
