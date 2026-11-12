"""Cross-task lessons memory (ideas from Hermes Agent's bounded memory + FTS store, and Harness²'s per-task lessons).

- Store: SQLite FTS5 at memory/lessons.sqlite. Lessons are short (<= 300 chars), generic (no task-specific names or
  values), deduplicated, and screened for prompt-injection phrases before they are accepted.
- Write: after a DEV task is graded, one cheap DeepSeek call turns the run's failures and outcome into at most two
  generic lessons (runner flag MH_LEARN=1). Test runs never write.
- Read: at task start the engine retrieves the top-k lessons for its engine by BM25 over the task text and appends
  them to the task message (after the byte-stable system prefix, so provider caching is unaffected).
"""
from __future__ import annotations

import json
import re
import sqlite3
import time
from pathlib import Path

import os
DB = Path(os.environ.get("MH_MEMORY_DB") or Path(__file__).resolve().parents[1] / "memory" / "lessons.sqlite")
MAX_CHARS = 300
BAD = re.compile(r"ignore (all|previous|the above)|system prompt|disregard|you are now|api[_ ]?key|password", re.I)


def _db():
    DB.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(DB)
    c.execute("CREATE VIRTUAL TABLE IF NOT EXISTS lessons USING fts5(engine, text, source UNINDEXED, "
              "outcome UNINDEXED, ts UNINDEXED)")
    return c


def add(engine: str, text: str, source: str = "", outcome: str = "") -> bool:
    text = re.sub(r"\s+", " ", text).strip()[:MAX_CHARS]
    if len(text) < 20 or BAD.search(text):
        return False
    c = _db()
    words = [w for w in re.findall(r"[a-z]{4,}", text.lower())][:12]
    if words:   # near-duplicate: an existing lesson that matches most of the distinctive words
        q = " OR ".join(sorted(set(words)))
        for (old,) in c.execute("SELECT text FROM lessons WHERE engine=? AND lessons MATCH ? LIMIT 5", (engine, q)):
            ow = set(re.findall(r"[a-z]{4,}", old.lower()))
            if len(ow & set(words)) >= 0.7 * len(set(words)):
                return False
    c.execute("INSERT INTO lessons VALUES (?,?,?,?,?)", (engine, text, source, outcome,
                                                          time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())))
    c.commit()
    return True


def retrieve(engine: str, query: str, k: int = 3) -> list[str]:
    if not DB.exists():
        return []
    words = sorted(set(re.findall(r"[A-Za-z_]{4,}", query)))[:40]
    if not words:
        return []
    q = " OR ".join(f'"{w}"' for w in words)
    try:
        rows = _db().execute("SELECT text FROM lessons WHERE engine=? AND lessons MATCH ? ORDER BY bm25(lessons) LIMIT ?",
                             (engine, q, k)).fetchall()
    except sqlite3.OperationalError:
        return []
    return [r[0] for r in rows]


def block(engine: str, query: str, k: int = 3) -> str:
    ls = retrieve(engine, query, k)
    if not ls:
        return ""
    return "\n\n<lessons_from_past_tasks>\n" + "\n".join(f"- {x}" for x in ls) + "\n</lessons_from_past_tasks>"


REFLECT = """You improve an agent harness. Below is one finished task: the task summary, the outcome from an
independent grader, and the guard/failure events and last commands from the agent's run.
Write at most TWO lessons that would help the agent on OTHER tasks of the same kind. Each lesson: one sentence,
under 40 words, an actionable rule. No repository names, file names, function names, numbers or answers from this
task. If nothing generalises, return an empty list. Reply as JSON: {"lessons": ["...", "..."]}"""


def reflect(engine: str, task_text: str, outcome: str, events: str, client, source: str = "") -> list[str]:
    msgs = [{"role": "system", "content": REFLECT},
            {"role": "user", "content": f"<task>\n{task_text[:2500]}\n</task>\n<outcome>{outcome}</outcome>\n"
                                        f"<run_events>\n{events[-4000:]}\n</run_events>"}]
    try:
        r = client.chat(model="deepseek-v4.1-flash", messages=msgs, max_tokens=300, temperature=0.0,
                        task_id=f"reflect-{source}"[:60], role="verifier",
                        extra={"response_format": {"type": "json_object"}})
        txt = r["choices"][0]["message"].get("content") or "{}"
        ls = json.loads(txt[txt.find("{"): txt.rfind("}") + 1]).get("lessons", [])[:2]
    except Exception:
        return []
    return [x for x in ls if isinstance(x, str) and add(engine, x, source, outcome)]


def run_events(task_id: str, run_dir: str | None) -> str:
    """Failure-catalog rows for this task plus the tail of its command log: the reflection input."""
    out = []
    f = Path(__file__).resolve().parents[1] / "failures.csv"
    if f.exists():
        out += [l for l in f.read_text(errors="replace").splitlines() if task_id in l][-15:]
    if run_dir and (Path(run_dir) / "commands.log").exists():
        cl = (Path(run_dir) / "commands.log").read_text(errors="replace")
        out.append("--- last commands ---\n" + "\n".join(l for l in cl.splitlines() if l.startswith(("$ ", "[exit")))[-1500:])
    return "\n".join(out)
