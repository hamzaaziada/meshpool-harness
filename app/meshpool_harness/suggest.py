"""Suggestions while the user types: the connector, skill, agent or command that fits what they are asking for.

No model call: names and keywords only, so it is instant, free and runs on every keystroke pause.
- connect:  a service named in the message that is not connected yet (Connect Jira)
- enable:   a connected service that is turned off (Turn on Notion)
- skill:    an enabled skill whose name, or most of whose key words, the message mentions (Use $dcf-model)
- agent:    for a new chat, the agent with the best benchmark quality for this kind of task, when it is not the one
            picked (the same ranking Meshpool Auto uses)
- command:  /goal, /review or /qa when the message reads like one of them
"""
from __future__ import annotations

import re

# Other names people use for a catalog connector
ALIASES = {"jira": "atlassian", "confluence": "atlassian", "gmail": "google-gmail", "google mail": "google-gmail",
           "google calendar": "google-calendar", "gcal": "google-calendar", "google drive": "google-drive",
           "gdrive": "google-drive", "google docs": "google-drive", "google sheets": "google-drive",
           "hugging face": "huggingface", "monday.com": "monday", "click up": "clickup"}
# Catalog names too common as plain words to count as a mention on their own
AMBIGUOUS = {"close", "make", "box", "neon", "jam", "miro", "granola", "otter", "linear", "monday", "attio", "slack"}

COMMANDS = [
    ("/review", "Review the changes on this branch for bugs",
     r"\b(review|check)\b.{0,40}\b(pr|pull request|branch|diff|changes|code)\b|\bcode review\b"),
    ("/qa", "Click through the app and report UI and UX problems",
     r"\b(ui|ux|user interface|design)\b.{0,30}\b(review|audit|crawl|check|qa|test|pass)\b|\b(crawl|click through)\b"),
    ("/goal", "Keep going until it is done",
     r"\b(until|keep going|don'?t stop|all of (them|these)|every (single )?(one|test|file|page))\b"),
]
_WORD = re.compile(r"[a-z0-9]+")
# Prefixes that group skills (finance-, sales-, customer-support-) say little about a single request
_BROAD = {"finance", "sales", "customer", "support", "legal", "design", "data", "code", "review", "analysis",
          "document", "report", "plan", "research"}
_STOP = {"the", "and", "for", "with", "from", "that", "this", "your", "you", "into", "when", "use", "using", "make",
         "how", "what", "who", "are", "can", "will", "about", "any", "all", "out", "its", "our", "their", "them",
         "skill", "skills", "file", "files", "user", "users", "work", "help", "helps", "write", "create", "new",
         # everyday verbs: a skill named after one (pull-request) is not meant by "pull the tickets"
         "pull", "draft", "build", "turn", "find", "check", "run", "get", "fix", "update", "send", "post", "read",
         "open", "show", "give", "take", "add", "set", "list", "request", "reply", "write"}


def _stem(w: str) -> str:
    return w[:6] if len(w) > 6 else w                     # reconcile ~ reconciliation, valuations ~ valuation


def _mentions(text: str, name: str) -> bool:
    return bool(re.search(r"(?<![\w-])" + re.escape(name.lower()) + r"(?![\w-])", text))


def _connectors(low: str) -> list[dict]:
    from meshpool_harness import mcp_apps as CN
    try:
        rows = {c["id"]: c for c in CN.listing()}
    except Exception:  # noqa: BLE001
        return []
    hits: dict[str, str] = {}
    for alias, cid in ALIASES.items():
        if cid in rows and _mentions(low, alias):
            hits.setdefault(cid, alias)
    for cid, c in rows.items():
        name = c["name"].lower()
        if cid in hits:
            continue
        if name in AMBIGUOUS or cid in AMBIGUOUS:
            # only with a word that shows the service is meant ("in Linear", "my Slack", "Close CRM")
            if re.search(r"(?:\b(in|on|from|to|my|our|the)\s+)" + re.escape(name) + r"\b|\b" + re.escape(name)
                         + r"\s+(crm|workspace|board|channel|issues?|tickets?|account)\b", low):
                hits[cid] = name
        elif _mentions(low, name) or _mentions(low, cid):
            hits[cid] = name
    out = []
    for cid in hits:
        c = rows[cid]
        if not c["connected"]:
            out.append({"type": "connect", "id": cid, "label": f"Connect {c['name']}",
                        "why": f"so the agent can use {c['name']} in this chat"})
        elif not c["enabled"]:
            out.append({"type": "enable", "id": cid, "label": f"Turn on {c['name']}",
                        "why": f"{c['name']} is connected but turned off"})
    return out


def _skills(low: str) -> list[dict]:
    from meshpool_harness import skills as SK
    words = {_stem(w) for w in _WORD.findall(low) if w not in _STOP}
    out = []
    for s in SK.load_all(include_disabled=False):
        name = s["name"].lower()
        if "$" + name in low:
            continue                                        # already in the message
        # the specific words of the name count most ("dcf" in finance-dcf-valuation), the description's words less
        parts = {_stem(p) for p in re.split(r"[-_\s]+", name) if len(p) > 2 and p not in _STOP and p not in _BROAD}
        key = {_stem(w) for w in _WORD.findall((s.get("description") or "").lower()) if len(w) > 3 and w not in _STOP}
        score = 10 if _mentions(low, name) else 4 * len(parts & words) + len((key - parts) & words)
        if score >= 4:
            out.append({"type": "skill", "id": s["id"], "name": s["name"], "label": f"Use ${s['name']}",
                        "why": (s.get("description") or "")[:120], "score": score})
    out.sort(key=lambda x: -x.pop("score"))
    return out[:2]


_QUESTION = {"what is", "what are", "who is", "how to", "how do", "how does", "why", "explain", "tell me"}


def _agent(text: str, harness: str, kind: str) -> list[dict]:
    from meshpool_harness import harness_router as HRT
    if harness in ("", "auto"):
        return []                                           # Auto picks for itself
    try:
        r = HRT.route(text, kind if kind in ("code", "cowork") else "cowork")
    except Exception:  # noqa: BLE001
        return []
    if r.get("category") == "general" or not r.get("ranking") or r["harness"] == harness:
        return []
    if not [h for h in HRT.classify(text)[1] if h not in _QUESTION]:
        return []                                           # "what is ..." alone is a question, not a research job
    ranked = {x["harness"]: x for x in r["ranking"]}
    cur = ranked.get(harness)
    band = 5.0
    if cur and cur["score"] >= r.get("score", 0) - band:
        return []                                           # the picked agent is as good for this: no nudge
    return [{"type": "agent", "id": r["harness"], "label": f"Use {r['name']}",
             "why": f"Best benchmark quality for a {r['category']} task"}]


def suggest(text: str, harness: str = "", kind: str = "cowork", new_chat: bool = True) -> list[dict]:
    text = (text or "").strip()
    if len(text) < 8 or text.startswith("/"):
        return []
    low = " " + re.sub(r"\s+", " ", text.lower()) + " "
    out = _connectors(low) + _skills(low)
    if new_chat:
        out += _agent(text, harness, kind)
    for cmd, why, pat in COMMANDS:
        if re.search(pat, low):
            out.append({"type": "command", "id": cmd, "label": cmd, "why": why})
            break
    return out[:3]                                         # a short row: easy to scan, easy to ignore
