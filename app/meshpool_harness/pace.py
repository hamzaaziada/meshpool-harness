"""Match the effort to the request: a quick question gets a quick answer.

Without this every request was handled like a deep job: a two-line "do some quick research and tell me X" ran 13 to
25 tool calls and took 70 to 100 s in the built-in agents (practical check, 13 Nov 2026). classify() sorts a message
into quick / normal / deep with plain rules (no model call, no delay). A quick request gets a hidden note with a small
budget, and the built-in agents are told to wrap up if they run past it. Deep and normal requests are unchanged.
"""
from __future__ import annotations

import re

QUICK_S = 25            # the built-in agents are asked to answer once a quick request runs this long
QUICK_STEPS = 3         # ... or after this many rounds of tool calls

_DEEP = re.compile(r"\b(deep|deeply|thorough(ly)?|comprehensive|in[- ]depth|detailed|exhaustive|full report|"
                   r"literature review|due diligence|step[- ]by[- ]step plan|take your time)\b", re.I)
_MAKE = re.compile(r"\b(make|create|build|write|draft|generate|produce|prepare|design|fix|implement|refactor|edit|"
                   r"change|update|add|remove|delete|rename|move|deploy|install|run|test|debug|commit|push|merge|"
                   r"migrate|convert|translate|summari[sz]e (this|the|my|these|that) (file|folder|doc|pdf|project))\b",
                   re.I)
_FILES = re.compile(r"\.(docx?|xlsx?|pptx?|pdf|csv|json|py|js|ts|tsx|md|html|css|sql)\b|\b(document|report|deck|"
                    r"slides?|spreadsheet|workbook|presentation|memo|file|folder|script|code|repo|app|website|"
                    r"function|class|bug|tests?)\b", re.I)
_ASK = re.compile(r"^\s*(what|which|who|whom|whose|when|where|why|how|is|are|was|were|can|could|does|do|did|should|"
                  r"would|will|tell me|explain|compare|define|give me|list|name|suggest|recommend|find out|look up|"
                  r"search|research|quick(ly)?|briefly|any idea|i wonder)\b|\?\s*$|\b(quick|short|brief|briefly|"
                  r"in (one|a few|two|three) (words?|sentences?|lines?))\b", re.I)

SEP = "\n\n\u200b"      # the separator every producer puts before notes only the model sees
NOTE = (SEP + "[Meshpool] This is a quick request. Answer within about 30 seconds: use only the tools you need. "
        "For research, run one web_research call with 2 or 3 queries; if its results do not answer the question, "
        "open the single most relevant page (for example the official site) and answer from that. Do not create or "
        "change files. Always end with a direct answer, briefly, with links for facts you looked up; offer to dig "
        "deeper if it would help.")
WRAP = ("[Meshpool] Time is up for this quick request. Answer now with what you have, briefly, with links for the "
        "facts you looked up. Do not call more tools.")


def classify(text: str, has_files: bool = False) -> str:
    t = str(text or "").split(SEP)[0].strip()   # model-only notes follow SEP; a pasted bare \u200b is user text
    if not t:
        return "normal"
    if _DEEP.search(t):
        return "deep"
    if has_files or len(t) > 400 or t.count("\n") > 4:
        return "normal"
    if _MAKE.search(t) and _FILES.search(t):
        return "normal"                       # a deliverable or a change to the project: not a quick question
    if re.search(r"\b(fix|implement|refactor|debug|deploy|install|commit|merge)\b", t, re.I):
        return "normal"
    return "quick" if _ASK.search(t) else "normal"
