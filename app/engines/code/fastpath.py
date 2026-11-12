"""Single-request first draft (opt-in, MH_FASTPATH=1): one model call reads the issue plus the most likely source
code and returns exact search/replace edits. They are applied before the agent loop, which then only has to verify
(and correct) the fix instead of exploring from scratch. Cheap: one call, no tool history."""
from __future__ import annotations

import json
import re
from pathlib import Path

PROMPT = """You fix bugs in a repository in ONE reply. You get the issue and the most relevant source code.
Return JSON only: {"edits": [{"path": "<repo-relative path>", "search": "<exact existing lines>", "replace": "<new lines>"}],
"why": "<one sentence>"}
Rules: "search" must be copied exactly from the code shown (whole lines, enough to be unique). Make the smallest
complete fix at the root cause; update every place the issue needs. If you cannot see where the fix goes, return
{"edits": [], "why": "need to explore"}."""


def candidate_files(hints_text: str, limit: int = 3) -> list[str]:
    out = []
    for m in re.finditer(r"^\s{2}([\w./-]+\.\w+)(?::\d+:| \()", hints_text, re.M):
        p = m.group(1)
        if p not in out and not re.search(r"(^|/)tests?/|test_", p):
            out.append(p)
    return out[:limit]


def context(workdir: str, files: list[str], max_chars: int = 24000) -> str:
    parts, used = [], 0
    for f in files:
        try:
            txt = Path(workdir, f).read_text(errors="replace")
        except OSError:
            continue
        txt = txt[: max(0, max_chars - used)]
        used += len(txt)
        parts.append(f"=== {f} ===\n{txt}")
        if used >= max_chars:
            break
    return "\n\n".join(parts)


def apply_edits(workdir: str, edits: list[dict]) -> list[str]:
    """Apply exact search/replace edits; returns the applied paths (skips edits whose search text is not unique)."""
    done = []
    for e in edits or []:
        p = Path(workdir, str(e.get("path", "")))
        try:
            src = p.read_text()
        except OSError:
            continue
        s, r = e.get("search", ""), e.get("replace", "")
        if s and src.count(s) == 1:
            p.write_text(src.replace(s, r))
            done.append(str(e.get("path")))
    return done


def draft(issue: str, hints_text: str, workdir: str, client, model: str, task_id: str) -> str:
    """Returns a note for the agent describing the applied draft, or "" if nothing was applied."""
    files = candidate_files(hints_text)
    if not files:
        return ""
    msgs = [{"role": "system", "content": PROMPT},
            {"role": "user", "content": f"<issue>\n{issue}\n</issue>\n\n{context(workdir, files)}"}]
    try:
        r = client.chat(model=model, messages=msgs, max_tokens=4000, temperature=0.0, task_id=task_id + "-draft",
                        role="worker", extra={"response_format": {"type": "json_object"}})
        txt = r["choices"][0]["message"].get("content") or "{}"
        plan = json.loads(txt[txt.find("{"): txt.rfind("}") + 1])
    except Exception:
        return ""
    applied = apply_edits(workdir, plan.get("edits", []))
    if not applied:
        return ""
    return (f"<draft_fix>\nA first-draft fix is ALREADY APPLIED to {', '.join(applied)} ({plan.get('why', '')}). "
            f"Do not start over: write a reproduction test, run it and the related tests, fix the draft if needed, "
            f"and finish as soon as they pass.\n</draft_fix>")
