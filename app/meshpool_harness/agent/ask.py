"""ask_user: the agent asks the user one to four multiple-choice questions and waits for the answers.

The agent emits {"type": "question", "id", "questions"} and blocks until a front end answers through
Agent.reply(question_id, answers) (the web app: POST /api/agent/<id>/reply), until MH_ASK_TIMEOUT_S passes (default
900), or until the user stops the agent. With no one to ask (headless runs, ACP, tests, scheduled tasks, or auto mode
with MH_ASK_AUTO=1) it goes on at once with the options the agent recommended, and says so in the tool result.
"""
from __future__ import annotations

import os
import re

from meshpool_harness.core import tools as T
from meshpool_harness.core.tools import ToolError

MAX_QUESTIONS, MIN_OPTIONS, MAX_OPTIONS = 4, 2, 5

SCHEMA = T._fn(
    "ask_user",
    "Ask the user 1 to 4 multiple-choice questions and wait for their answers. Use it when an answer would change "
    "the result and you cannot find it in the request, the files or by looking it up. Give each question 2 to 5 "
    "short options and mark the one you recommend (recommended: true); the user can always type their own answer "
    "instead. Put all the questions you need into one call, and do some work before you ask again.",
    {"questions": {"type": "array", "minItems": 1, "maxItems": MAX_QUESTIONS, "items": {
        "type": "object", "required": ["question", "options"], "properties": {
            "question": {"type": "string", "description": "The full question, one sentence"},
            "header": {"type": "string", "description": "A 1-3 word label for it, e.g. 'Audience'"},
            "multi": {"type": "boolean", "description": "true: the user may pick several options"},
            "options": {"type": "array", "minItems": MIN_OPTIONS, "maxItems": MAX_OPTIONS, "items": {
                "type": "object", "required": ["label"], "properties": {
                    "label": {"type": "string", "description": "A few words"},
                    "description": {"type": "string", "description": "What this choice means for the result"},
                    "recommended": {"type": "boolean"}}}}}}}},
    ["questions"])


def timeout_s() -> float:
    try:
        return max(1.0, float(os.environ.get("MH_ASK_TIMEOUT_S", "900")))
    except ValueError:
        return 900.0


def normalize(args: dict) -> list[dict]:
    """Check the arguments and return clean questions. Raises ToolError with a fix the model can act on."""
    qs = args.get("questions") if isinstance(args, dict) else None
    if isinstance(qs, dict):
        qs = [qs]
    if not isinstance(qs, list) or not qs:
        raise ToolError("ask_user: give 'questions', a list of 1 to 4 questions.")
    if len(qs) > MAX_QUESTIONS:
        raise ToolError(f"ask_user: {len(qs)} questions is too many. Ask at most {MAX_QUESTIONS}: keep the ones that "
                        "change the result most.")
    out = []
    for n, q in enumerate(qs, 1):
        if not isinstance(q, dict):
            raise ToolError(f"ask_user: question {n} must be an object with 'question' and 'options'.")
        text = str(q.get("question") or "").strip()
        if not text:
            raise ToolError(f"ask_user: question {n} has no 'question' text.")
        opts, seen = [], set()
        for o in q.get("options") or []:
            if isinstance(o, str):
                o = {"label": o}
            if not isinstance(o, dict):
                continue
            label = re.sub(r"\s+", " ", str(o.get("label") or "")).strip()[:80]
            if not label or label.lower() in seen or label.lower() in ("other", "other…", "other..."):
                continue                               # the app always adds its own "Other" text box
            seen.add(label.lower())
            opts.append({"label": label, "description": str(o.get("description") or "").strip()[:300],
                         "recommended": bool(o.get("recommended"))})
        if not MIN_OPTIONS <= len(opts) <= MAX_OPTIONS:
            raise ToolError(f"ask_user: question {n} needs {MIN_OPTIONS} to {MAX_OPTIONS} distinct options "
                            f"(it has {len(opts)}). The app adds a free-text 'Other' itself.")
        multi = bool(q.get("multi"))
        if not multi:                                  # one recommendation for a single choice
            first = next((i for i, o in enumerate(opts) if o["recommended"]), None)
            for i, o in enumerate(opts):
                o["recommended"] = i == first
        header = re.sub(r"\s+", " ", str(q.get("header") or "")).strip()[:24] or f"Question {n}"
        out.append({"question": text[:500], "header": header, "multi": multi, "options": opts})
    return out


def recommended(qs: list[dict]) -> list[dict]:
    """The recommended options as answers (the first option where none is marked)."""
    ans = []
    for q in qs:
        pick = [o["label"] for o in q["options"] if o["recommended"]] or [q["options"][0]["label"]]
        ans.append({"selected": pick, "other": "", "default": True})
    return ans


def coerce(qs: list[dict], raw) -> list[dict]:
    """Turn what a front end sent into one {"selected": [labels], "other": text} per question.
    Accepts a list (one entry per question: a dict, a label, or a list of labels), a dict keyed by question number
    or header. Questions left blank get the recommended option, marked "default"."""
    if isinstance(raw, dict):
        if "selected" in raw or "other" in raw:
            raw = [raw]
        else:
            raw = [raw.get(str(i), raw.get(i, raw.get(q["header"]))) for i, q in enumerate(qs)]
    if not isinstance(raw, list):
        raw = []
    rec = recommended(qs)
    out = []
    for i, q in enumerate(qs):
        a = raw[i] if i < len(raw) else None
        if isinstance(a, str):
            a = {"selected": [a]}
        elif isinstance(a, list):
            a = {"selected": a}
        elif not isinstance(a, dict):
            a = {}
        sel = a.get("selected") or []
        sel = [sel] if isinstance(sel, str) else [str(s) for s in sel if isinstance(s, (str, int, float))]
        labels = {o["label"].lower(): o["label"] for o in q["options"]}
        picked = [labels[s.strip().lower()] for s in sel if s.strip().lower() in labels]
        unknown = [s.strip() for s in sel if s.strip() and s.strip().lower() not in labels]
        picked = list(dict.fromkeys(picked))
        if not q["multi"]:
            picked = picked[:1]
        other = " ".join([str(a.get("other") or "").strip(), *unknown]).strip()[:2000]
        if not picked and not other:
            out.append(rec[i])                         # left blank: the recommendation stands
        else:
            out.append({"selected": picked, "other": other, "default": False})
    return out


def summary(qs: list[dict], answers: list[dict]) -> str:
    """One line per question, as shown in the chat and in the tool result."""
    lines = []
    for n, (q, a) in enumerate(zip(qs, answers), 1):
        parts = ", ".join(a["selected"])
        if a.get("other"):
            parts = (parts + "; " if parts else "") + f'"{a["other"]}"'
        lines.append(f"{n}. {q['header']}: {q['question']} -> {parts}"
                     + (" (recommended; not answered)" if a.get("default") else ""))
    return "\n".join(lines)


REASONS = {
    "headless": "no one is watching this session to answer",
    "auto": "the session runs in auto mode",
    "timeout": "the user did not answer within {mins} minutes",
}


def result_text(qs: list[dict], answers: list[dict], source: str, timeout: float = 0) -> str:
    body = summary(qs, answers)
    if source == "user":
        partly = any(a.get("default") for a in answers)
        return ("The user answered:\n" + body + "\nFollow these answers"
                + (" (where a question was left blank, your recommendation stands)" if partly else "")
                + ". Text in quotes is what the user typed themselves.")
    why = REASONS.get(source, "no answer came").format(mins=max(1, round(timeout / 60)))
    return (f"No answer: {why}. Go ahead with your recommended options:\n" + body
            + "\nIn your final reply, list these assumptions so the user can change them.")


def finish(qs: list[dict], raw, source: str, timeout: float = 0) -> tuple[list[dict], str, str]:
    """(answers, what the chat shows, the tool result) for an answer from a front end (raw), or for none
    (raw None: the recommendations, with source saying why). Plain text is a reply typed in the message box
    instead of picking options."""
    if raw is not None and source == "user" and isinstance(raw, str):
        said = raw.strip()[:4000]
        answers = recommended(qs)
        return answers, f'You replied: "{said}"', (
            f'The user replied in their own words instead of picking options: "{said}"\nUse what they said. Where '
            "it does not settle a question, go with your recommendation:\n" + summary(qs, answers))
    answers = coerce(qs, raw) if raw is not None else recommended(qs)
    return answers, summary(qs, answers), result_text(qs, answers, source, timeout)


def replay_text(out: str) -> str:
    """The answer lines of a saved ask_user result, for a thread reopened after a restart."""
    lines = [ln for ln in str(out or "").splitlines() if re.match(r"^\d+\. ", ln)]
    return "\n".join(lines) or str(out or "")[:400]
