"""Where the model calls go, from engine transcripts (.run/transcript-*.jsonl or swe_ab.py's OUT/logs).

    python testkit/trace_stats.py OUT/logs            # per-arm averages (arm = the task id's last "-" part)
    python testkit/trace_stats.py OUT/logs --tasks    # one line per transcript

Per transcript: model calls, calls before the first edit, re-reads (read_file of a file already read), reads right
after a failing command that named that file (what error context saves), failing commands, finish attempts that a
guard refused, and guard notes by kind. These are the numbers the harness changes target.
"""
from __future__ import annotations

import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path


def stats(path: Path) -> dict:
    rows = [json.loads(l) for l in path.read_text(errors="replace").splitlines() if l.strip()]
    s = Counter()
    seen, first_edit, calls, last_fail_text = set(), None, 0, ""
    for m in rows:
        role = m.get("role")
        if role == "assistant":
            calls += 1
            for c in m.get("tool_calls") or []:
                f = c.get("function") or {}
                name = f.get("name", "")
                try:
                    a = json.loads(f.get("arguments") or "{}")
                except ValueError:
                    a = {}
                s["tool:" + name] += 1
                if name in ("edit", "write_file") and first_edit is None:
                    first_edit = calls
                if name == "read_file":
                    p = str(a.get("path", "")).replace("/testbed/", "")
                    if p in seen:
                        s["re_reads"] += 1
                    seen.add(p)
                    if p and p.rsplit("/", 1)[-1] in last_fail_text:
                        s["read_after_fail"] += 1
                if name == "finish":
                    s["finish_calls"] += 1
        elif role == "tool":
            t = str(m.get("content") or "")
            if t.startswith("[exit ") and not t.startswith("[exit 0]"):
                s["failed_cmds"] += 1
                last_fail_text = t
            elif t.startswith("[exit 0]"):
                last_fail_text = ""
            for g in re.findall(r"GUARD (\w+)", t):
                s["guard:" + g] += 1
            if "where it failed" in t:
                s["error_context_shown"] += 1
        elif role == "user":
            for g in re.findall(r"GUARD (\w+)", str(m.get("content") or "")):
                s["guard:" + g] += 1
    s["calls"] = calls
    s["calls_before_first_edit"] = first_edit - 1 if first_edit else calls
    s["finish_refused"] = max(0, s["finish_calls"] - 1)
    return dict(s)


def main():
    root = Path(sys.argv[1] if len(sys.argv) > 1 else ".")
    files = sorted(root.rglob("transcript-*.jsonl"))
    by_arm = defaultdict(list)
    for f in files:
        st = stats(f)
        arm = f.stem.rsplit("-", 1)[-1]
        by_arm[arm].append(st)
        if "--tasks" in sys.argv:
            print(f"{f.stem[11:]:<50} calls={st['calls']:<3} pre_edit={st['calls_before_first_edit']:<3} "
                  f"re_reads={st.get('re_reads', 0):<3} failed={st.get('failed_cmds', 0):<3} "
                  f"refused={st['finish_refused']}")
    keys = ["calls", "calls_before_first_edit", "re_reads", "read_after_fail", "failed_cmds", "finish_refused",
            "error_context_shown"]
    for arm, sts in sorted(by_arm.items()):
        n = len(sts)
        print(f"\n[{arm}] {n} transcripts")
        for k in keys:
            print(f"  {k:<26}{sum(x.get(k, 0) for x in sts) / n:>7.2f}")
        guards = Counter()
        for x in sts:
            guards.update({k: v for k, v in x.items() if k.startswith("guard:")})
        tools = Counter()
        for x in sts:
            tools.update({k[5:]: v for k, v in x.items() if k.startswith("tool:")})
        print("  guards  " + ", ".join(f"{k[6:]} {v}" for k, v in guards.most_common()) if guards else "  guards  none")
        print("  tools   " + ", ".join(f"{k} {v / n:.1f}" for k, v in tools.most_common(10)))


if __name__ == "__main__":
    main()
