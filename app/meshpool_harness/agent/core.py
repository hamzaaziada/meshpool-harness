"""Meshpool coding agent: an interactive conversation in a project folder.

Unlike the one-shot harness (`meshpool run`), the agent keeps the whole conversation, answers in plain text when it
is done with a request, and waits for the next one. It reuses the harness's file and shell tools (Workspace), its
transport (any OpenAI-compatible endpoint, streamed), its model profiles, and adds MCP tools and permission prompts.

Front ends (terminal, web app) talk to it through two callbacks:
  emit(event: dict)                      every step, as it happens (text deltas, tool calls, usage, errors)
  approve(kind, title, detail) -> (decision, note)   decision in {"yes", "always", "no"}
"""
from __future__ import annotations

import copy
import difflib
import http.client
import itertools
import json
import os
import platform
import re
import threading
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

from meshpool_harness import pace as PACE
from meshpool_harness.agent import ask as ASK
from meshpool_harness import config as C
from meshpool_harness.core import gateway as GW
from meshpool_harness.core import tools as T
from meshpool_harness.core.client import _withhold_latest_output, classify_error, rate_limit_wait
from meshpool_harness.core.loop import _compact, _sanitize_calls
from meshpool_harness.core.tools import ToolError, Workspace, clean, validate
from meshpool_harness.core.wire import sse_to_chat
from meshpool_harness.harnesses.safe_tools import SAFE_MCP

SESSIONS = C.runs_dir() / "sessions"
DEFAULT_MODEL = "deepseek-v4.1-flash"

READ_ONLY = {"read_file", "grep", "glob", "fetch_url", "office_task", "web_search", "web_research", "todo_write", "use_skill",
             "ask_user"}
# MCP tools that change nothing: the only ones plan mode runs (SAFE_MCP, which skips the approval prompt, also has
# tools that save or schedule things).
PLAN_MCP = {"mcp__meshpool_web__web_search", "mcp__meshpool_web__web_fetch", "mcp__meshpool_web__web_research",
            "mcp__meshpool_assistant__memory_view", "mcp__meshpool_assistant__list_scheduled_tasks",
            "mcp__meshpool_assistant__search_chats", "mcp__meshpool_office__read_document",
            "mcp__meshpool_office__office_job_status", "mcp__meshpool_tools__current_time",
            "mcp__meshpool_tools__calculate", "mcp__meshpool_office__check_deck",
            "mcp__meshpool_office__check_workbook", "mcp__meshpool_office__measure_office_file",
            "mcp__meshpool_office__check_document", "mcp__meshpool_office__find_icon"}
PLAN_TOOLS = ["read_file", "grep", "glob", "fetch_url", "web_search", "web_research", "todo_write", "use_skill",
              "ask_user"]
BUILTIN = ["read_file", "grep", "glob", "edit", "write_file", "bash", "bash_many", "fetch_url", "web_search",
           "web_research", "todo_write", "use_skill", "ask_user"]
# Harness v3 (benchmarked 2026-11-12, run C): bash_many, todo follow-through, loop and stall breakers, a turn-budget
# note and an environment snapshot. MH_AGENT_V3=0 turns them off.
V3 = os.environ.get("MH_AGENT_V3", "1") != "0"
if not V3:
    BUILTIN.remove("bash_many")
if os.environ.get("MH_OBSERVE") == "1":           # probe/trace: see the bug before editing (core/observe.py)
    BUILTIN[BUILTIN.index("bash") + 1:BUILTIN.index("bash") + 1] = ["probe", "trace"]
WORK_TOOLS = BUILTIN + ["office_task"]
PROJECT_FILES = ("MESHPOOL.md", "AGENTS.md", "CLAUDE.md")

T.SCHEMAS.setdefault("ask_user", ASK.SCHEMA)
T.SCHEMAS.setdefault("use_skill", T._fn(
    "use_skill", "Load a skill's full instructions by name (from the skills list in your instructions). Do this before "
    "a task that matches a skill, and whenever the user writes $skill-name.", {"name": T.S}, ["name"]))
_REC = {"type": "string", "enum": ["", "day", "week", "month", "year"], "description": "Only recent results"}
T.SCHEMAS["fetch_url"] = T._fn(
    "fetch_url", "Read a web page, PDF or Office file from a URL as clean text (title, date, main content). Long pages "
    "come in parts: call again with start to read on. Use this, not the browser, for public pages.",
    {"url": T.S, "start": {"type": "integer", "description": "Character offset to read from"},
     "max_chars": {"type": "integer", "description": "Default 15000"},
     "links": {"type": "boolean", "description": "Also list the page's links"}}, ["url"])
T.SCHEMAS["web_research"] = T._fn(
    "web_research", "Research a question in one step: runs several searches in parallel, reads the best sources in "
    "parallel and returns the passages that answer it, numbered with links for citing. Give 2 to 5 varied queries for "
    "broad questions; call again with new queries to fill gaps.",
    {"question": T.S, "queries": {"type": "array", "items": T.S},
     "max_sources": {"type": "integer", "description": "2 to 12, default 6"}, "recency": _REC, "site": T.S}, ["question"])

SYSTEM = """You are Meshpool, a coding agent. You work in the user's project folder on their own computer, through \
tools. The user sees every tool call you make.

Environment
- Project folder: {root}
- Operating system: {os_name}
- Shell for the bash tool: {shell}

How to work
1. Look before you change anything: use glob, grep and read_file to understand the code.
2. For work with several steps, keep a short plan with todo_write and update it as you go.
3. Make focused changes. Use edit for existing files: read the file first and copy `old` exactly, without the \
line-number prefix. Use write_file for new files.
4. Check your work: run the tests, the build, or the script with bash, and fix what fails.
5. Do what was asked, nothing more. If the request is unclear in a way that changes what you build, ask with \
ask_user (short options, your recommendation marked) instead of guessing; look up what you can find yourself first.
6. When you finish a request, reply with a short summary of what you changed and how you checked it, without \
calling a tool. That reply ends your turn; the user then answers or gives the next request.

Rules
- Paths are relative to the project folder. You can only write inside it.
- Never print or store secrets such as API keys or tokens.
- Ask before destructive steps (deleting files, force-pushing, dropping data) unless the user asked for them.
- If the user denies a tool call, do not retry it. Take another approach or ask.
- Long outputs are cut. The full text is saved to a file you can read.
- Tools whose names start with mcp__ come from MCP servers the user connected. Use them when they fit.
- To look things up, use web_search, web_research and fetch_url. They are fast and need no browser. Match the depth to the request: a quick question gets one web_research call and a short answer with links; for a research job or report, run web_research with several varied queries, read key sources in full with fetch_url, and cite every fact with its link.
- Use the browser tools (if enabled) only for pages that need a sign-in, clicks or forms. Use computer use only for desktop apps, and only when the user asks for it. Files, commands and APIs beat clicking. Before anything irreversible on a website or in an app (sending, paying, deleting, submitting), stop and confirm with the user.
- Write plainly: short sentences, no filler. Use Markdown code blocks for code."""

V3_RULES = """

Working efficiently
- When you need several independent facts (file ranges, greps, versions, listings), get them in ONE bash_many call \
instead of one command per step.
- For any request with more than one step, list its concrete requirements and your steps with todo_write first, and \
mark items completed as you go. Before you end your turn, every item should be completed, or say why it is not."""


T.SCHEMAS["web_search"] = T._fn(
    "web_search", "Search the web and get the top results (title, link, snippet). Then read pages with fetch_url, or "
    "use web_research to search and read in one step.",
    {"query": T.S, "max_results": {"type": "integer"}, "recency": _REC,
     "site": {"type": "string", "description": "Limit to one site"}}, ["query"])
T.SCHEMAS.setdefault("todo_write", T._fn(
    "todo_write", "Keep a short plan for multi-step work. Send the whole list each time with each item's status. "
    "The user sees it as a checklist.",
    {"items": {"type": "array", "items": {"type": "object", "properties": {
        "content": T.S, "status": {"type": "string", "enum": ["pending", "in_progress", "completed"]}},
        "required": ["content", "status"]}}}, ["items"]))
T.SCHEMAS.setdefault("office_task", T._fn(
    "office_task", "Hand a document job to Meshpool's office engines and get the finished file back. Use it for "
    "slide decks (.pptx), spreadsheets and financial models (.xlsx), and data analysis with charts. Describe the "
    "whole deliverable in `request`; list input files from the project folder in `files`.",
    {"engine": {"type": "string", "enum": ["slides", "sheets", "data"]}, "request": T.S,
     "files": {"type": "array", "items": T.S},
     # the deck brief (engine slides; engines/forge/brief.py): what is not given is asked, or recommended
     "audience": {"type": "string", "description": "slides: who the deck is for"}, "purpose": T.S,
     "density": {"type": "string", "enum": ["present", "balanced", "read"],
                 "description": "slides: present (visual, few words, to present live), balanced, read (dense pre-read)"},
     "slides": {"type": "integer", "description": "slides: number of slides"},
     "theme": {"type": "string", "description": "slides: navy, ocean, forest, burgundy, graphite or mono"},
     "opening": T.S, "closing": T.S, "sources": T.S,
     "brief_ok": {"type": "boolean", "description": "slides: build now with the recommended answers to anything "
                                                    "not given, without asking"}}, ["engine", "request"]))

WORK_SYSTEM = """You are Meshpool Cowork, an assistant that does real work for the user on their own computer: \
research, writing, analysis, spreadsheets, presentations and file housekeeping. You work through tools, and the user \
sees every tool call.

Environment
- Working folder: {root}
- Operating system: {os_name}
- Shell for the bash tool: {shell}

How to work
1. Understand the job, then ask before you build (see "Asking the user" below).
2. For a multi-step job, keep a short plan with todo_write and update it as you go.
3. Gather material: read the user's files (read_file, glob, grep), research the web with web_research and \
web_search, read pages and PDFs with fetch_url, and use connected apps (mcp__ tools) when they fit. For a big \
research job, split it into parts, research each, then write up.
4. Produce real deliverables as files in the working folder. For decks, spreadsheets, models and data analysis use \
office_task: it returns a finished, checked file. For memos, reports, letters and proposals make a Word document \
(.docx) with the make_document tool (or python-docx through bash for edits to an existing .docx); write Markdown only \
when the user asks for it. Use Python through bash for quick calculations.
5. Check the result: open what you made, verify numbers against their sources, and fix problems before you finish. \
Research budget: when the request or its files already hold sourced research, use it and only look up what they \
mark as a gap; otherwise prefer a few authoritative sources (filings, the company's releases) over many pages, and \
stop researching once each number you need has a source. Never build a deck or a workbook yourself with python-pptx or openpyxl when make_deck, make_spreadsheet or \
office_task can make it: the engines carry the design system and the checks. To add or change slides in a deck the \
user already has, use add_slides (it builds them in that deck's look and keeps the original): never write slides \
with python-pptx code. If an engine call returns a job id, \
wait for it with office_job_status. If its result has a problem, call it again with the specific fix in the request \
(e.g. "exactly 10 slides; merge the two risk slides"). Run check_deck on every .pptx, check_workbook on every .xlsx \
and check_document on every .docx you deliver; fix each failure it lists (content \
failures by changing the words or numbers, then rebuild) and tell the user about any that remain. Rebuild at most \
twice to clear check results; failures marked /renderer are the engine's own and a rebuild does not fix them.
6. Finish with a short reply: what you made, where the files are, and anything the user should check. Do not call \
a tool in that reply.

Asking the user (ask_user)
The user wants the result they had in mind, not a good guess. ask_user shows them clickable options; they can also \
type their own answer.
- Before you build a deliverable (deck, workbook, document, report, analysis), make ONE ask_user call with the 2 to \
4 questions whose answers change the result most and that the request and files do not already answer. Typical ones: \
audience and purpose; density, with the options "present" (visual, few words, to present live), "balanced" and \
"read" (a dense pre-read); length (e.g. number of slides or pages); look or theme (navy, ocean, forest, burgundy, \
graphite, mono); sources to use or leave out. Give every question 2 to 5 short options, mark the one you recommend \
with recommended: true, and say in its description what it means for the result.
- Skip any question the request or the files already answer. Look facts up (files, web, connected apps) instead of \
asking for them. A quick question or a small, clear task needs no questions.
- While you work, ask a short follow-up whenever something material is ambiguous (two readings of the data, a \
missing number, a choice the user would care about). Never make two ask_user calls in a row without doing work in \
between.
- Before you finish, confirm anything important you assumed, with one short ask_user call if it could change the result.
- Pass the answers on to the engines in plain words: put them in the make_deck, make_spreadsheet, make_document or \
office_task request (e.g. "density: present", "ocean theme", "exactly 8 slides", "audience: the board").
- If the result says no one answered, go ahead with your recommendations and list them as assumptions in your reply.
- If make_deck answers "BRIEF NEEDED", ask its questions (keep its options and recommendations) in one ask_user call, \
then call it again with the answers as fields. If you already asked them, pass brief_ok=true.

Rules
- Use facts from the user's files and fetched sources. Never invent numbers, quotes or sources; say what is missing.
- You can only write inside the working folder. Ask before deleting or overwriting the user's files.
- Never print or store secrets such as API keys or tokens.
- If the user denies a tool call, do not retry it. Take another approach or ask.
- To look things up, use web_search, web_research and fetch_url. They are fast and need no browser. Match the depth to the request: a quick question gets one web_research call and a short answer with links; for a research job or report, run web_research with several varied queries, read key sources in full with fetch_url, and cite every fact with its link.
- Use the browser tools (if enabled) only for pages that need a sign-in, clicks or forms. Use computer use only for desktop apps, and only when the user asks for it. Files, commands and APIs beat clicking. Before anything irreversible on a website or in an app (sending, paying, deleting, submitting), stop and confirm with the user.
- Write plainly: short sentences, no filler."""


def _set_read_timeout(resp, seconds: float) -> None:
    """Change the read timeout of an open urllib response (CPython keeps the socket at fp.raw._sock)."""
    try:
        resp.fp.raw._sock.settimeout(seconds)
    except Exception:  # noqa: BLE001  another runtime or a test double: keep the timeout it was opened with
        pass


class _Track:
    """urllib handler mixin: hand each new connection to a callback, so Stop can shut its socket even while the
    request is still waiting for the response headers (a gateway that never answers)."""
    def __init__(self, sink, *a, **kw):
        super().__init__(*a, **kw)
        self.sink = sink

    def do_open(self, http_class, req, **kw):
        sink = self.sink

        def make(host, **k):
            c = http_class(host, **k)
            sink(c)
            return c
        return super().do_open(make, req, **kw)


class _TrackHTTP(_Track, urllib.request.HTTPHandler):
    pass


class _TrackHTTPS(_Track, urllib.request.HTTPSHandler):
    pass


class _EndpointDown(RuntimeError):
    """The endpoint stayed down for the whole recovery wait: report it, do not wait again."""


def _urlopen(req, timeout: float, sink):
    """urllib.request.urlopen with the connection handed to sink (proxies from the environment as usual)."""
    return urllib.request.build_opener(_TrackHTTP(sink), _TrackHTTPS(sink)).open(req, timeout=timeout)


def _probe_once(base: str, key: str, model: str, sink=None) -> str:
    """One tiny request: "ok" when the endpoint answers (any reply below 502), "auth" on 401/403, else the error.
    A 500 is an answer (the gateway is serving, this model or request failed); 502/503/504 mean it is not serving."""
    body = {"model": model, "max_tokens": 1, "messages": [{"role": "user", "content": "ping"}]}
    req = urllib.request.Request(f"{base}/chat/completions", data=json.dumps(body).encode("utf-8"), method="POST",
                                 headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    try:
        t = float(os.environ.get("MH_PROBE_S", "30"))
        with (_urlopen(req, t, sink) if sink else urllib.request.urlopen(req, timeout=t)) as r:
            r.read()
        return "ok"
    except urllib.error.HTTPError as e:
        return "auth" if e.code in (401, 403) else "ok" if e.code < 502 else f"HTTP {e.code}"
    except Exception as e:  # noqa: BLE001
        return f"HTTP 0: {e}"


def _image_in_newest_ask(messages: list) -> bool:
    """Whether the newest message the user typed (not a tool's screenshot, not a Meshpool note) carries an image."""
    for m in reversed(messages):
        if m.get("role") != "user":
            continue
        c = m.get("content")
        text = c if isinstance(c, str) else " ".join(str(p.get("text") or "") for p in c or []
                                                       if isinstance(p, dict) and p.get("type") == "text")
        if text.startswith(("Image returned by", "[Meshpool]")):
            continue                                    # attached by a tool (_attach_images) or a harness note
        return isinstance(c, list) and any(isinstance(p, dict) and p.get("type") == "image_url" for p in c)
    return False


def _cut_long_user_text(messages: list, keep: int = 40000) -> bool:
    """Shorten user messages over `keep` characters to their start and end, with a note in the middle. A message too
    big for the endpoint otherwise goes with every later request too and the chat cannot recover. True if cut."""
    def shorten(c: str) -> str:
        return (c[:keep * 3 // 4] + f"\n\n[... {len(c) - keep} characters left out: the message was too large for "
                "the endpoint ...]\n\n" + c[-keep // 4:])
    cut = False
    for m in messages:
        c = m.get("content")
        if m.get("role") != "user":
            continue
        if isinstance(c, str) and len(c) > keep:
            m["content"], cut = shorten(c), True
        elif isinstance(c, list):                       # text sent along with an image
            for p in c:
                if isinstance(p, dict) and p.get("type") == "text" and len(str(p.get("text") or "")) > keep:
                    p["text"], cut = shorten(str(p["text"])), True
    return cut


def _subagents_on() -> bool:
    try:
        from meshpool_harness import assistant as AS
        return AS.subagent_settings()["on"]
    except Exception:  # noqa: BLE001
        return False


def _lighten(messages: list) -> None:
    """After an outage: elide older tool outputs only when the conversation is very large (over 150k characters),
    keeping the last eight verbatim. Smaller conversations are left whole, so a long task keeps its context."""
    if _context_size(messages) > 150000:
        _compact(messages, keep_recent=8)


def _retry_text(msg: str, attempt: int) -> str:
    """One calm line per retry instead of the raw network error."""
    m = msg.lower()
    if "http 0" in m or "connection" in m or "timed out" in m:
        why = "Can't reach the model right now"
    elif "429" in m or "rate" in m:
        why = "The model is busy"
    elif "5" in m[:8] or "http 5" in m:
        why = "The model service had a problem"
    else:
        why = "The model call didn't go through"
    return f"{why}. Trying again ({attempt + 1} of 5)…"


class Interrupted(Exception):
    pass


def _summary(name: str, args: dict) -> str:
    """One line for the tool card. Arguments come from the model unchecked, so any value may be missing or of the
    wrong type: never raise."""
    try:
        return str(_summary_of(name, {k: ("" if v is None else v) for k, v in args.items()}))
    except Exception:  # noqa: BLE001
        s = json.dumps(args, ensure_ascii=False, default=str)
        return s if len(s) < 300 else s[:300] + "..."


def _summary_of(name: str, args: dict) -> str:
    if name == "bash":
        return args.get("command", "")
    if name == "bash_many":
        return " ; ".join(str(c) for c in args.get("commands") or [])[:400]
    if name in ("read_file", "write_file", "edit"):
        s = args.get("path", "")
        if name == "read_file" and (args.get("start") or args.get("end")):
            s += f" (lines {args.get('start', 1)}-{args.get('end', 'end')})"
        return s
    if name == "grep":
        return f"{args.get('pattern', '')}" + (f" in {args['path']}" if args.get("path") else "") + \
            (f" ({args['glob']})" if args.get("glob") else "")
    if name == "glob":
        return args.get("pattern", "")
    if name == "fetch_url":
        return args.get("url", "")
    if name == "office_task":
        return f"{args.get('engine', '')}: {str(args.get('request', ''))[:160]}"
    if name == "web_search":
        return args.get("query", "")
    if name == "web_research":
        return str(args.get("question", ""))[:160]
    if name == "use_skill":
        return args.get("name", "")
    if name == "ask_user":
        qs = args.get("questions") if isinstance(args.get("questions"), list) else []
        return " / ".join(str(q.get("question", "")) for q in qs if isinstance(q, dict))[:300]
    if name == "todo_write":
        items = args.get("items") or []
        return f"{sum(1 for i in items if isinstance(i, dict) and i.get('status') == 'completed')}/{len(items)} done"
    s = json.dumps(args, ensure_ascii=False)
    return s if len(s) < 300 else s[:300] + "..."


def _env_snapshot(root: Path) -> str:
    """Harness v3: a short look at the project and the tools on this computer, so the first steps are not spent
    finding them. Cross-platform, a few seconds at most."""
    import shutil
    import subprocess
    lines = []
    try:
        names = sorted(p.name + ("/" if p.is_dir() else "") for p in root.iterdir() if not p.name.startswith(".git"))
        lines.append("Project folder: " + ", ".join(names[:40]) + (f" (+{len(names) - 40} more)" if len(names) > 40 else ""))
    except OSError:
        pass
    vers = []
    for tool, arg in (("python3", "--version"), ("python", "--version"), ("node", "--version"), ("git", "--version"),
                      ("go", "version"), ("cargo", "--version"), ("java", "-version"), ("docker", "--version")):
        exe = shutil.which(tool)
        if not exe or (tool == "python" and any(v.startswith("python3") for v in vers)):
            continue
        try:
            r = subprocess.run([exe, arg], capture_output=True, text=True, timeout=3,
                               creationflags=0x08000000 if os.name == "nt" else 0)
            v = (r.stdout or r.stderr).strip().splitlines()
            if v and r.returncode == 0:
                vers.append(f"{tool}: {v[0][:80]}")
        except Exception:  # noqa: BLE001
            pass
    if vers:
        lines.append("Tools: " + "; ".join(vers))
    return "\n".join(lines)


def _context_size(messages: list) -> int:
    """Characters a request carries: text contents, text parts, tool-call arguments, and a share per image."""
    n = 0
    for m in messages:
        c = m.get("content")
        if isinstance(c, str):
            n += len(c)
        elif isinstance(c, list):
            n += sum(len(str(p.get("text") or "")) if p.get("type") == "text" else 4000
                     for p in c if isinstance(p, dict))
        for tc in m.get("tool_calls") or []:
            n += len(str((tc.get("function") or {}).get("arguments") or ""))
    return n


def _shrink_args(messages: list, keep: int = 200) -> None:
    """Shorten long string values in the arguments of past tool calls (file contents written, long edits); the
    arguments stay valid JSON."""
    for m in messages:
        for tc in m.get("tool_calls") or []:
            f = tc.get("function") or {}
            raw = f.get("arguments") or ""
            if len(raw) <= 3 * keep:
                continue
            try:
                a = json.loads(raw)
            except (ValueError, TypeError):
                continue
            if not isinstance(a, dict):
                continue
            for k, v in a.items():
                if isinstance(v, str) and len(v) > 3 * keep:
                    a[k] = v[:keep] + f" ...[{len(v) - keep} chars elided at compaction]"
            f["arguments"] = json.dumps(a, ensure_ascii=False)


def _shrink_context(messages: list, limit: int, hard: bool = False, keep_images: int = 0) -> None:
    """Make the conversation fit: elide older tool outputs, keeping fewer recent ones verbatim until it is under
    `limit`, then shorten long tool-call arguments. hard (the endpoint said the request is over the model's context):
    all of that at once, whatever the size, keeping only the outputs of the latest step."""
    if hard:
        last = max((i for i, m in enumerate(messages) if m.get("role") == "assistant"), default=-1)
        _compact(messages, keep_recent=sum(1 for m in messages[last + 1:] if m.get("role") == "tool"))
        for m in messages[last + 1:]:
            c = m.get("content")
            if m.get("role") == "tool" and isinstance(c, str) and len(c) > T.MAX_TOOL_CHARS:
                m["content"] = (c[:T.HEAD] + f"\n... [{len(c) - T.HEAD - T.TAIL} chars cut: the conversation was "
                                "over the model's context] ...\n" + c[-T.TAIL:])
        _shrink_args(messages)
        _drop_images(messages, keep=keep_images)
        return
    for keep in (12, 4, 1):
        _compact(messages, keep_recent=keep)
        if _context_size(messages) <= limit:
            return
    _shrink_args(messages)


def _args_ok(call: dict) -> bool:
    try:
        return isinstance(json.loads((call.get("function") or {}).get("arguments") or "{}"), dict)
    except (ValueError, TypeError):
        return False


def _has_images(messages: list) -> bool:
    return any(isinstance(m.get("content"), list) and any(p.get("type") == "image_url" for p in m["content"])
               for m in messages)


def _drop_images(messages: list, keep: int = 0) -> None:
    """Replace all but the latest `keep` images in the conversation with a short note."""
    seen = 0
    for m in reversed(messages):
        c = m.get("content")
        if not isinstance(c, list):
            continue
        new = []
        for part in reversed(c):
            if part.get("type") == "image_url":
                seen += 1
                if seen > keep:
                    continue
            new.append(part)
        new.reverse()
        if len(new) != len(c):
            if not any(p.get("type") == "image_url" for p in new):
                m["content"] = " ".join(p.get("text", "") for p in new if p.get("type") == "text") + \
                    " [screenshot removed to save space]"
            else:
                m["content"] = new


class Agent:
    def __init__(self, workdir: str, *, mcp=None, emit=None, approve=None, mode: str | None = None,
                 session_id: str | None = None, max_steps: int = 0, profile: str = "code",
                 sessions_dir: str | None = None):
        self.profile = "work" if profile == "work" else "code"
        self.root = Path(workdir).expanduser().resolve()
        if not self.root.is_dir():
            raise ValueError(f"Project folder not found: {workdir}")
        self.id = session_id or time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:4]
        self.dir = (Path(sessions_dir) if sessions_dir else SESSIONS) / self.id   # headless runs: inside workdir
        self.ws = Workspace(str(self.root), str(self.dir / "tool-output"))
        self.ws.should_stop = lambda: self._stop
        self.mcp = mcp
        self.emit = emit or (lambda ev: None)
        self.approve = approve
        self.mode = mode or os.environ.get("MH_AGENT_PERMISSIONS", "ask")
        self.always: set[str] = set()
        # 0: no limit. A turn runs until the task is done or the user stops it; a caller may still pass a cap.
        self.max_steps = max_steps or int(os.environ.get("MH_MAX_STEPS", "0") or 0)
        self.pace_deadline = 0.0                 # set for a quick request: answer by then (see pace.py)
        self._stop = False
        self._inbox, self._inbox_lock = [], threading.Lock()
        self.created = time.time()
        self.usage = {"in": 0, "cached": 0, "out": 0, "calls": 0}
        self.model: str | None = None          # None: the default model from settings
        self.plan = False                      # plan mode: read-only tools, the agent writes a plan
        self.messages = [{"role": "system", "content": self.system_prompt()}]
        self.title = ""

    # ---------- prompt -----------------------------------------------------------------------------------------
    def system_prompt(self) -> str:
        from meshpool_harness.config import find_bash
        bash = find_bash()
        if os.name == "nt":
            shell = (("bash (Git Bash). For Windows tools, run them through powershell -NoProfile -Command \"...\" "
                      "or cmd /c \"...\"") if bash else
                     "PowerShell (no bash on this computer: write PowerShell commands; cmd /c \"...\" also works)")
        else:
            shell = "bash" if bash else "sh"
        text = (WORK_SYSTEM if self.profile == "work" else SYSTEM).format(
            root=self.root, os_name=f"{platform.system()} {platform.release()}", shell=shell)
        if V3:
            text += V3_RULES
        if os.environ.get("MH_OBSERVE") == "1" and self.profile == "code":
            text += ("\n- Before your first edit to fix a bug, reproduce it with probe (a failing test or a short "
                     "script) and read its diagnosis. If the cause is not clear, run trace on the same command to see "
                     "which project functions run. After an edit, use probe to see what still fails.")
        for name in PROJECT_FILES:
            p = self.root / name
            if p.is_file():
                body = p.read_text(encoding="utf-8", errors="replace")[:20000]
                text += f"\n\nProject instructions from {name} (follow them):\n{body}"
                break
        try:
            from meshpool_harness import skills as SK
            text += SK.catalog_text()
        except Exception:
            pass
        from meshpool_harness.assistant import HONESTY, PLAIN
        return text + "\n\n" + PLAIN + "\n\n" + HONESTY

    def tool_schemas(self) -> list:
        if self.plan:
            return T.schemas(PLAN_TOOLS)
        names = self._tool_names()
        if "delegate_task" not in names and getattr(self, "_delegate_depth", 0) == 0 and _subagents_on():
            names = names + ["delegate_task"]
        return T.schemas(names) + ([x for x in self.mcp.schemas()
                                    if not x["function"]["name"].replace("-", "_").startswith("mcp__meshpool_web__")
                                    and not x["function"]["name"].endswith("run_subtasks")]   # delegate_task instead
                                   if self.mcp else [])

    def _tool_names(self) -> list:
        return PLAN_TOOLS if self.plan else (WORK_TOOLS if self.profile == "work" else BUILTIN)

    def current_model(self) -> str:
        return self.model or os.environ.get("MH_MODEL") or DEFAULT_MODEL

    # ---------- public -----------------------------------------------------------------------------------------
    def stop(self):
        self._gen = getattr(self, "_gen", 0) + 1         # anything still running from this turn sees it is stale
        self._stop = True
        if getattr(self, "_delegations", None):          # the sub-agents it is running
            from meshpool_harness.agent import delegate as DL
            DL.stop_all(self)

    def _run_subtasks(self, name: str, args: dict):
        """Sub-agents for this chat run here, in the app's own process, with what only this chat knows: its model,
        its permission mode and its project. Nothing crosses to another process, so nothing there can be forged:
        any secret handed to another process of the same user can be read by a harness model's shell command
        (review rounds 6 to 8). The assistant server's run_subtasks, used by Hermes, Codex and Pi, never trusts a
        parent. Stop here stops them within a fraction of a second."""
        from meshpool_harness import assistant as AS
        gen = getattr(self, "_gen", 0)
        stopped = lambda: self._stop or getattr(self, "_gen", 0) != gen   # noqa: E731

        def run():
            try:
                return AS.run_subtasks(args.get("tasks") or [], str(self.root), int(args.get("max_parallel") or 0),
                                       parent={"model": self.current_model() or "", "mode": self.mode,
                                               "root": str(self.root)}, trusted=True, cancelled=stopped), False
            except (ValueError, KeyError, TypeError) as e:
                return str(e), True
        return self._wait(run)

    def _exists(self, path: str) -> bool:
        try:
            return self.ws._path(path, write=True).exists()
        except Exception:  # noqa: BLE001
            return False

    def _wait(self, fn, *a, **kw):
        """Run a blocking call (a model request, an MCP tool, a web fetch) so Stop works within a fraction of a
        second instead of when the call returns. A call that is abandoned finishes in the background, unseen."""
        box: dict = {}

        def run():
            try:
                box["v"] = fn(*a, **kw)
            except BaseException as e:  # noqa: BLE001  re-raised in the caller's thread
                box["e"] = e
        t = threading.Thread(target=run, daemon=True)
        t.start()
        while t.is_alive():
            t.join(0.15)
            if self._stop:
                self._abort_live()                 # close the model stream now, not when it next sends a byte
                raise Interrupted()
        if "e" in box:
            raise box["e"]
        return box["v"]

    def steer(self, text: str, images: list | None = None):
        """Add to the request while the agent works. It reads this before its next step."""
        with self._inbox_lock:
            self._inbox.append((text, images or []))
        self.emit({"type": "user", "text": text.split("\n\n\u200b")[0],
                   "images": [im.get("name", "image") for im in images or []], "plan": self.plan, "steer": True})

    def _take_inbox(self) -> bool:
        with self._inbox_lock:
            items, self._inbox = self._inbox, []
        for text, images in items:
            body = "[Added by the user while you were working] " + text
            if images:
                parts = [{"type": "text", "text": body}] + [
                    {"type": "image_url", "image_url": {"url": f"data:{im.get('mime') or 'image/png'};base64,{im['data']}"}}
                    for im in images[:6]]
                self.messages.append({"role": "user", "content": parts})
            else:
                self.messages.append({"role": "user", "content": body})
        return bool(items)

    def reset(self):
        self.messages = [{"role": "system", "content": self.system_prompt()}]
        self.ws = Workspace(str(self.root), str(self.dir / "tool-output"))
        self.ws.should_stop = lambda: self._stop
        self.title = ""

    def send(self, text: str, images: list | None = None) -> str:
        """Run one user request to the end of the agent's turn. Returns the final reply text.
        images: [{"mime": "image/png", "data": base64, "name": ...}] attached by the user."""
        self._stop = False
        if not hasattr(self, "_inbox"):
            self._inbox, self._inbox_lock = [], threading.Lock()
        if not self.title:
            self.title = text.strip().splitlines()[0][:80] if text.strip() else "Session"
        body = text
        if self.plan:
            body = ("[Plan mode] Investigate as needed with read-only tools, then reply with a clear, numbered plan "
                    "for the request below. Do not change any files or run commands that change anything; the user "
                    "will review the plan and switch to building.\n\n" + text)
        if images:
            parts = [{"type": "text", "text": body}] + [
                {"type": "image_url", "image_url": {"url": f"data:{im.get('mime') or 'image/png'};base64,{im['data']}"}}
                for im in images[:6]]
            self.messages.append({"role": "user", "content": parts})
        else:
            self.messages.append({"role": "user", "content": body})
        self.save()                                  # the thread and its message survive a restart mid-turn
        self.emit({"type": "user", "text": text.split("\n\n\u200b")[0], "images": [im.get("name", "image") for im in images or []],
                   "plan": self.plan})
        return self._turn()

    def has_steers(self) -> bool:
        """True when the user added something (steer) that no turn has read yet."""
        with self._inbox_lock:
            return bool(self._inbox)

    def resume_steers(self) -> str | None:
        """Answer what the user added after the last turn stopped reading (a steer that arrived just as send()
        returned). Front ends call this when send() returns; None when nothing is waiting."""
        if not self.has_steers():
            return None
        self._stop = False
        return self._turn()

    def _turn(self) -> str:
        turn = self._turn_usage = {"in": 0, "cached": 0, "out": 0, "calls": 0}   # sub-agents add theirs
        final, said = "", ""                         # said: reply text before an output-limit cut-off
        self._todo_touched = False
        g = {"hist": [], "doom_at": -10, "seen": set(), "edits": self.ws.edits, "edits0": self.ws.edits, "last": 0,
             "todo_nudged": False}
        if V3 and len(self.messages) <= 2 and not self.plan:
            snap = _env_snapshot(self.root)
            if snap and isinstance(self.messages[-1].get("content"), str):
                self.messages[-1]["content"] += "\n\n<environment_snapshot>\n" + snap + "\n</environment_snapshot>"
        try:
            wrapped = False
            for step in (range(self.max_steps) if self.max_steps else itertools.count()):
                self._check_stop()
                self._take_inbox()
                self._maybe_compact()
                if self.pace_deadline and not wrapped and step and (
                        time.time() > self.pace_deadline or step >= PACE.QUICK_STEPS):
                    wrapped = True                      # a quick request ran long: answer now, no more tools
                    self.messages.append({"role": "user", "content": PACE.WRAP})
                    self._no_tools = True
                self._finish = None
                try:
                    msg, streamed = self._chat(turn)
                finally:
                    self._no_tools = False
                cut_off = self._finish == "length"     # the reply hit the output limit (max_tokens)
                cut = {i for i, c in enumerate(msg.get("tool_calls") or []) if cut_off and not _args_ok(c)}
                calls = _sanitize_calls(msg.get("tool_calls") or [])
                cut = {calls[i]["id"] for i in cut}
                content = msg.get("content") or ""
                keep = {"role": "assistant", "content": content or ("" if calls else "(no reply)")}
                if calls:
                    keep["tool_calls"] = calls
                self.messages.append(keep)
                self.emit({"type": "assistant", "text": content, "streamed": streamed, "has_tools": bool(calls)})
                if not calls:
                    final = said + content
                    if cut_off and content and g.get("cont", 0) < 3:
                        g["cont"] = g.get("cont", 0) + 1   # a long reply cut off mid-way: let it continue
                        said = final
                        self.messages.append({"role": "user", "content": (
                            "[Meshpool] Your reply was cut off at the output limit. Continue exactly where it "
                            "stopped, without repeating anything.")})
                        continue
                    if self.has_steers():           # the user added something while this reply was written
                        continue
                    if (os.environ.get("MH_SCOPE_CHECK") == "1" and self.profile == "code" and not self.plan
                            and self.ws.edits > g["edits0"] and not g.get("scope_shown")):
                        g["scope_shown"] = True     # once per request: the same check as the benchmark loop's finish
                        from meshpool_harness.core.finishing import scope_report
                        rep = scope_report(self.ws, done="reply")
                        if rep:
                            self.messages.append({"role": "user", "content": "[Meshpool] " + rep})
                            continue
                    left = [t["content"] for t in getattr(self, "_todos", []) if t.get("status") != "completed"]
                    if V3 and left and self._todo_touched and not g["todo_nudged"] and not self.plan:
                        g["todo_nudged"] = True     # once per request: finish the list, or say why not
                        self.messages.append({"role": "user", "content": (
                            "[Meshpool] These items on your todo list are not completed:\n- " + "\n- ".join(left[:12])
                            + "\nComplete them now, or mark them completed if they are done. If one is not needed or "
                            "you need the user's answer, say so briefly and end your turn.")})
                        continue
                    if self.has_steers():           # added while the checks above ran
                        continue
                    break
                said = ""
                shots = []
                for c in calls:
                    self._check_stop()
                    out = self._run_tool(c, cut=c["id"] in cut)
                    self.messages.append({"role": "tool", "tool_call_id": c["id"], "content": clean(out)})
                    if self.mcp is not None and getattr(self.mcp, "last_images", None):
                        shots += [(c.get("function", {}).get("name", ""), im) for im in self.mcp.last_images]
                        self.mcp.last_images = []
                if shots:
                    self._attach_images(shots)
                if V3:
                    note = self._v3_notes(step, calls, g)
                    if note:
                        self.messages.append({"role": "user", "content": "[Meshpool] " + note})
            else:
                self.emit({"type": "error", "text": f"Stopped after {self.max_steps} steps in one turn. "
                                                    "Send a message to continue."})
        except (Interrupted, KeyboardInterrupt):
            self._repair()
            added = [t for t, _ in getattr(self, "_inbox", [])]
            self._take_inbox()                       # keep what the user added; the next turn sees it
            self.emit({"type": "interrupted", "text": "Stopped. " + (
                "What you added wasn't answered yet; it goes along with your next message." if added
                else "Send a message to continue.")})
        except Exception as e:
            self._repair()
            msg = raw = str(e)
            if "endpoint could not take this request" in msg:
                head = msg.split(" (HTTP", 1)[0]         # already says what to do; keep only the status
                code = re.search(r"HTTP \d+", msg)
                msg = raw = head + (f" ({code.group(0)})" if code else "")
            elif re.search(r"rejected the API key|stopped accepting the API key", msg):
                msg = "The endpoint rejected the API key. Check it in Settings, then send your message again."
            elif re.search(r"HTTP 0|connection problem|timed out|urlopen error", msg, re.I):
                msg = ("Couldn't reach the model, even after waiting for the endpoint to come back. Send a message "
                       "to try again; the next try starts on a fresh route."
                       if "answers small requests" not in msg else
                       "The endpoint answers small requests, but this conversation's requests keep failing. Send a "
                       "message to try again, or start a new chat.")
            elif re.search(r"HTTP 429", msg):
                msg = "The model is busy right now. Wait a minute, then send your message again."
            elif re.search(r"HTTP 5\d\d", msg):
                msg = "The model service had a problem. Send your message again in a moment."
            if re.search(r"HTTP 0|connection problem|timed out|urlopen error|HTTP 5\d\d", raw, re.I):
                _lighten(self.messages)                # the next message starts lighter, on a fresh route
                self.rotate_route()
            if msg != raw:
                msg += f" ({raw[:120]})"               # the detail, for support and the command line
            self.emit({"type": "error", "text": msg})
        finally:
            self.emit({"type": "turn_end", "turn": turn, "session": dict(self.usage)})
            self.save()
        return final

    def _v3_notes(self, step: int, calls: list, g: dict) -> str:
        """Harness v3 guards for one step: repeated identical calls, no progress for 12 or 24 steps, and the
        step budget. They add a short note for the model; they never block anything."""
        notes = []
        for c in calls:
            f = c.get("function", {})
            g["hist"].append(f.get("name", "") + ":" + str(f.get("arguments", ""))[:500])
        h = g["hist"] = g["hist"][-12:]
        hit = len(h) >= 3 and h[-1] == h[-2] == h[-3]
        for k in (2, 3):
            if not hit and len(h) >= 3 * k and h[-k:] == h[-2 * k:-k] == h[-3 * k:-2 * k]:
                hit = True
        if hit and g["doom_at"] < len(h) - 6:
            g["doom_at"] = len(h)
            notes.append("You are repeating the same tool calls and getting the same results. Stop and reconsider: "
                         "use a different tool, different arguments or a different approach.")
        new = [c for c in self.ws.commands if c.get("code") == 0 and c["cmd"] not in g["seen"]]
        g["seen"].update(c["cmd"] for c in new)
        if new or self.ws.edits != g["edits"]:
            g["last"], g["edits"] = step, self.ws.edits
        elif step - g["last"] in (12, 24) and self.profile == "code":
            notes.append(f"{step - g['last']} steps without progress (no file changed, no new command succeeded). "
                         "Your current approach is not converging. In one sentence, state a different hypothesis or "
                         "a simpler route, then try it.")
        if self.max_steps >= 30 and step == self.max_steps - 15:
            notes.append("15 steps left in this turn. Make sure what the user asked for exists and works now; refine "
                         "it afterwards if steps remain.")
        return " ".join(notes)

    # ---------- images (computer use, browser screenshots) -----------------------------------------------------
    def _attach_images(self, shots: list):
        """Tool results cannot carry images on the chat API, so screenshots follow as a user message. Only the latest
        two images stay in the conversation; older ones become a short note to keep requests small."""
        if os.environ.get("MH_VISION", "1") == "0" or getattr(self, "_no_vision", False):
            return
        # A screenshot bigger than the endpoint takes (a full-size Retina or 4K PNG) would fail every request and
        # be dropped each step, leaving the model acting blind without knowing why: leave it out and say so.
        cap = int(os.environ.get("MH_BIG_REQUEST", "3000000")) * 2 // 3
        big = [n for n, im in shots if len(str(im.get("data") or "")) > cap]
        shots = [(n, im) for n, im in shots if len(str(im.get("data") or "")) <= cap]
        if big:
            self.messages.append({"role": "user", "content": "[Meshpool] The image from " + ", ".join(sorted(set(big)))
                                  + " was too large for the model endpoint and was left out, so you did not see it. "
                                  "Ask the tool for a smaller image (a region, a lower resolution or a JPEG) or go on "
                                  "without it."})
        if not shots:
            return
        _drop_images(self.messages, keep=max(0, 2 - len(shots)))
        parts = [{"type": "text", "text": "Image returned by " + ", ".join(sorted({n for n, _ in shots})) + ":"}]
        for _, im in shots[-2:]:
            parts.append({"type": "image_url", "image_url": {"url": f"data:{im['mime']};base64,{im['data']}"}})
        self.messages.append({"role": "user", "content": parts})

    # ---------- model ------------------------------------------------------------------------------------------
    def _check_stop(self):
        if self._stop:
            raise Interrupted()

    def _maybe_compact(self):
        limit = int(os.environ.get("MH_COMPACT_CHARS", "280000"))   # well under a 128K-token window
        if _context_size(self.messages) > limit:
            _shrink_context(self.messages, limit)
            self.emit({"type": "status", "text": "Older tool outputs were shortened to keep the context small."})

    def _body(self) -> dict:
        from meshpool_harness.profiles import apply, for_model
        model = self.current_model()
        prof = for_model(model, os.environ.get("MH_PROFILE"))
        lim = int((prof.get("limits") or {}).get("max_output_tokens", 8192))
        body = {"model": model, "max_tokens": lim, "tools": self.tool_schemas(),
                "tool_choice": "none" if getattr(self, "_no_tools", False) else "auto"}
        msgs, body = apply(prof, list(self.messages), body, "worker")
        keep = int(os.environ.get("MH_MAX_IMAGES", "4"))
        if sum(1 for m in msgs if isinstance(m.get("content"), list)
               for p in m["content"] if isinstance(p, dict) and p.get("type") == "image_url") > keep:
            # Only the latest images go with a request; the chat keeps all of them. A month-old chat full of
            # screenshots otherwise resends megabytes every turn and the gateway refuses it (413).
            msgs = [dict(m) if isinstance(m.get("content"), list) else m for m in msgs]
            _drop_images(msgs, keep=keep)
        if os.environ.get("MH_TEMPERATURE"):
            body["temperature"] = float(os.environ["MH_TEMPERATURE"])
        body["messages"] = msgs
        if os.environ.get("MH_CACHE_KEY", "1") == "1":
            body["prompt_cache_key"] = self.route_id()
            body["user"] = self.route_id()
        return body

    def _chat(self, turn: dict) -> tuple[dict, bool]:
        body = self._body()
        # The model's provider decides where the call goes (providers.py): the key endpoint in settings or the
        # ChatGPT sign-in.
        from meshpool_harness import providers as PV
        prov, body["model"] = PV.split(body["model"])
        base, key = PV.endpoint(prov)         # the key endpoint: providers.key()/key_base() (an OpenAI key only
                                               # with OPENAI_BASE_URL, never with the Meshpool base)
        wire = "chatgpt" if prov == "chatgpt" else os.environ.get("MH_WIRE", "chat")
        if not key and wire != "chatgpt":
            raise RuntimeError(f"No API key. Add MESHPOOL_API_KEY to {C.config_path()} or open Settings in the app.")
        stream = wire == "chat" and os.environ.get("MH_STREAM", "1") != "0"
        self.emit({"type": "model_start"})
        resp, streamed, cleaned, shrunk, last = None, False, False, False, None
        attempt, recoveries, swapped = -1, 0, False
        self._first_scale = 1.0
        while True:
            attempt += 1
            if attempt >= 8:                               # every attempt waited or withheld: report the last error
                raise last or RuntimeError("HTTP 0: the model call did not go through")
            self._check_stop()
            try:
                if wire == "chatgpt":                  # the user's ChatGPT sign-in (core/chatgpt.py)
                    from meshpool_harness.core import chatgpt as CG
                    from meshpool_harness.core.wire import from_responses

                    def on_text(t):
                        nonlocal streamed
                        streamed = True
                        self.emit({"type": "text_delta", "text": t})
                    resp = from_responses(self._wait(CG.chat, body, task_id=self.id, on_text=on_text,
                                                     idle=int(os.environ.get("MH_AGENT_IDLE_S", "120"))))
                elif wire == "responses":
                    from meshpool_harness.core.wire import SimpleLedger
                    extra = {k: v for k, v in body.items() if k not in ("model", "messages", "max_tokens", "temperature")}
                    resp = SimpleLedger("agent").chat(model=body["model"], messages=body["messages"],
                                                      max_tokens=body.get("max_tokens", 8192),
                                                      temperature=body.get("temperature"), extra=extra, retries=0)
                else:
                    h = GW.health(base, key, body["model"])
                    if h.is_down() and not self._await_endpoint(h, base, key, body["model"]):
                        raise _EndpointDown("HTTP 0: the model endpoint did not come back")
                    slot = h.acquire(self._check_stop, self.emit)
                    ok = False
                    try:
                        resp, streamed = self._wait(self._post, base, key, body, stream)
                        ok = True
                    finally:
                        h.release(ok, slot)
                self._first_scale = 1.0
                break
            except (Interrupted, KeyboardInterrupt):
                raise
            except RuntimeError as e:
                last, msg = e, str(e)
                if isinstance(e, _EndpointDown):           # it already waited the whole recovery time
                    raise
                if type(e).__name__ in ("NotSignedIn", "UsageLimit"):   # no sign-in or plan limit: retrying cannot help
                    raise
                kind = classify_error(msg)                 # the same reading as LiveClient.chat (core/client.py)
                if kind == "auth":
                    raise RuntimeError("The endpoint rejected the API key. Check it in settings. " + msg[:200])
                if kind == "model_missing":
                    # A chat from weeks ago on a model the endpoint has dropped: continue on today's default for
                    # its provider, once per call. Otherwise say so at once; retrying the same model cannot help.
                    C.load_into_env(override=True)
                    if prov == "chatgpt":
                        from meshpool_harness.core import chatgpt as CG
                        now = "chatgpt:" + CG.default_model()
                    else:
                        now = os.environ.get("MH_MODEL") or DEFAULT_MODEL
                    if not swapped and PV.split(now)[0] == prov and PV.split(now)[1] != body["model"]:
                        swapped, old_model = True, body["model"]
                        self.model = now
                        body = self._body()
                        body["model"] = PV.split(body["model"])[1]
                        self.emit({"type": "status", "text": f"This chat's model ({old_model}) is no longer available "
                                                             f"on your endpoint, so it continues on {body['model']}. "
                                                             "You can pick another model for it any time."})
                        continue
                    raise RuntimeError(f"The endpoint does not serve the model {body['model']}. Choose another model "
                                       f"for this chat. ({msg[:160]})")
                if kind == "context" and not shrunk:       # over the model's context: shrink hard, retry once
                    shrunk = True
                    # Too long, or too big a request (413): elide older outputs and all but the latest two images,
                    # so a screenshot the user just attached still goes with the retry.
                    _shrink_context(self.messages, 0, hard=True, keep_images=2)
                    body = self._body()
                    body["model"] = PV.split(body["model"])[1]
                    self.emit({"type": "status", "text": "The conversation was longer than the model can take; "
                                                         "older tool outputs were shortened before trying again."})
                    continue
                if kind == "vision" and _has_images(body["messages"]) and not getattr(self, "_no_vision", False):
                    self._no_vision = True                 # this model does not take images: continue without them
                    _drop_images(self.messages, keep=0)
                    _drop_images(body["messages"], keep=0)
                    self.emit({"type": "status", "text": "This model cannot see images, so screenshots are left out. "
                                                         "For computer use, choose a vision model in Settings."})
                    continue
                if kind in ("bad_request", "vision", "context"):
                    if cleaned:
                        raise
                    cleaned = True                         # control characters in a tool output: clean and retry
                    for m in body["messages"]:
                        if isinstance(m.get("content"), str):
                            m["content"] = clean(m["content"])
                    continue
                if kind == "rate_limit":
                    self._n429 = getattr(self, "_n429", 0) + 1   # no reset time: back off 3, 6, 12 ... 60 s (v4)
                    wait = rate_limit_wait(msg, self._n429)
                    self.emit({"type": "status", "text": f"Rate limited by the endpoint. Waiting {int(wait)} s."})
                    self._sleep(wait)
                    continue
                if kind == "gateway_reject" and attempt >= 2:
                    if _withhold_latest_output(self.messages):   # content-tied gateway 400: drop the worst output
                        body = self._body()
                        body["model"] = PV.split(body["model"])[1]
                        continue
                net = bool(re.search(r"HTTP 0|timed out|connection|HTTP 5\d\d|upstream", msg, re.I))
                if net and wire == "chat":
                    # One probe says which it is: the endpoint is down or stuck for this key (the probe fails too),
                    # or it is up and this request is slow or stuck on its route (the probe answers).
                    h = GW.health(base, key, body["model"])
                    state = h.check(lambda: self._wait(self._probe, base, key, body["model"]),
                                    check_stop=self._check_stop)
                    if state == "auth":                    # not the network: say so, not "can't reach"
                        raise RuntimeError("The endpoint rejected the API key. Check it in settings. (HTTP 401 on a "
                                           "check after the call failed)")
                    if state == "down":
                        if recoveries >= 3:
                            raise
                        recoveries += 1                    # no blind retries: they only pile up behind a stuck key
                        if not self._await_endpoint(h, base, key, body["model"]):
                            raise _EndpointDown(msg)
                        _lighten(self.messages)            # an outage is not about size: trim only a very large one
                        self.rotate_route()
                        body = self._body()
                        body["model"] = PV.split(body["model"])[1]
                        attempt, self._first_scale = -1, 1.0
                        continue
                    big = len(json.dumps(body)) > int(os.environ.get("MH_BIG_REQUEST", "3000000"))
                    if big and re.search(r"HTTP 5\d\d", msg) and not shrunk:
                        # Up for a small probe, failing for this big request: the real gateway answers an oversized
                        # body with a plain 502, not a 413. Treat it as too big: keep the latest two images, trim.
                        shrunk = True
                        _shrink_context(self.messages, 0, hard=True, keep_images=2)
                        lim = int(os.environ.get("MH_BIG_REQUEST", "3000000"))
                        if len(json.dumps(self._body())) > lim:
                            _drop_images(self.messages, keep=1)   # still too big: only the newest image stays
                        if self._size_with(self.messages) > lim:
                            # Try each remedy on a copy and apply only one that makes the request fit: never cut
                            # the user's text when an image is the problem, or the other way round.
                            cut = copy.deepcopy(self.messages)
                            did_cut = _cut_long_user_text(cut)
                            noimg = copy.deepcopy(self.messages)
                            _drop_images(noimg, keep=0)
                            both = copy.deepcopy(cut)
                            _drop_images(both, keep=0)
                            has_img = _has_images(self.messages)
                            newest = _image_in_newest_ask(self.messages)
                            if has_img and not newest and self._size_with(noimg) <= lim:
                                self.messages[:] = noimg          # an older or tool image: the cheapest remedy
                                note = ("An image from earlier in the chat, or a tool's screenshot, made the "
                                        "request too large for the endpoint, so it was left out.")
                            elif did_cut and self._size_with(cut) <= lim:
                                self.messages[:] = cut
                                note = ("Your message was too large for the endpoint, so its middle was left out. "
                                        "Attach long text as a file to keep all of it.")
                            elif has_img and self._size_with(noimg) <= lim:
                                self.messages[:] = noimg          # gone from the chat, so the next message works
                                raise RuntimeError("The endpoint could not take this request: the attached image "
                                                   "was too large, so it was removed from the chat. Attach a "
                                                   "smaller one (a screenshot of part of the screen, or a JPEG) "
                                                   "and send it again. (" + msg[:120] + ")")
                            elif did_cut and has_img and self._size_with(both) <= lim:
                                self.messages[:] = both
                                note = ("Your message and its image were too large for the endpoint: the middle of "
                                        "the text and the image were left out.")
                            else:
                                raise RuntimeError("The endpoint could not take this request: the conversation is "
                                                   "too large even after it was shortened. Start a new chat to "
                                                   "carry on. (" + msg[:120] + ")")
                        else:
                            note = ("The request was too large for the endpoint; older images and tool outputs were "
                                    "left out before trying again.")
                        body = self._body()
                        body["model"] = PV.split(body["model"])[1]
                        self.emit({"type": "status", "text": note})   # one line, saying what was done
                        continue
                    if attempt >= 5:                       # up, yet this conversation's requests keep failing
                        raise RuntimeError(msg + " [the endpoint answers small requests, but this conversation's "
                                           "requests keep failing]")
                    if re.search(r"timed out", msg, re.I):   # up but slow to start (prefill, a queue): wait longer
                        self._first_scale = min(5.0, self._first_scale * 2.5)
                    if attempt == 1:                       # a stuck sticky route (cache affinity) upstream: new route
                        self.rotate_route()
                        if "prompt_cache_key" in body:     # only when this endpoint gets cache keys (MH_CACHE_KEY)
                            body["prompt_cache_key"] = body["user"] = self.route_id()
                    if attempt == 2 and stream:            # a gateway that mishandles the stream: ask for one reply
                        stream = False
                        self.emit({"type": "status", "text": "Trying again without streaming."})
                elif attempt >= 5:
                    raise
                stalled = re.search(r"HTTP 5\d\d|timed out", msg, re.I)   # 5xx, or no byte back within the timeout
                if stalled and attempt >= 1:               # a big request that keeps failing upstream:
                    before = _context_size(self.messages)
                    if before > 150000:                    # shrink it instead of resending the same thing
                        _compact(self.messages, keep_recent=8)
                        if _context_size(self.messages) < before:
                            body = self._body()
                            body["model"] = PV.split(body["model"])[1]
                            self.emit({"type": "status", "text": "The request was very large; older tool outputs "
                                                                 "were shortened before trying again."})
                self.emit({"type": "status", "text": _retry_text(msg, attempt)})
                if streamed:
                    self.emit({"type": "stream_reset"})
                self._sleep(min(20, 2 ** attempt))
        self.emit({"type": "model_end"})
        self._n429 = 0
        u = resp.get("usage") or {}
        cached = (u.get("prompt_tokens_details") or {}).get("cached_tokens") or u.get("prompt_cache_hit_tokens") or 0
        d = {"in": u.get("prompt_tokens", 0) or 0, "cached": cached or 0, "out": u.get("completion_tokens", 0) or 0,
             "calls": 1}
        for k, v in d.items():
            turn[k] += v
            self.usage[k] += v
        if not hasattr(self, "_meter"):
            from meshpool_harness.core.metrics import CacheMeter
            self._meter = CacheMeter()
        self._meter.observe(u, self.usage)                 # full_miss_calls / full_miss_tokens, as in the benchmark loop
        self.emit({"type": "usage", **d})
        choice = (resp.get("choices") or [{}])[0]
        self._finish = choice.get("finish_reason")
        return choice.get("message") or {}, streamed

    def _size_with(self, messages: list) -> int:
        """How big the request would be with these messages (the body _post sends)."""
        saved = self.messages
        self.messages = messages
        try:
            return len(json.dumps(self._body()))
        finally:
            self.messages = saved

    def route_id(self) -> str:
        """The id the endpoint sees for this chat (cache affinity, gateway session). Changes after a stuck route."""
        return self.id + getattr(self, "_route", "")

    def rotate_route(self):
        n = getattr(self, "_route_n", 0) + 1
        self._route_n, self._route = n, f"-r{n}"

    def _abort_live(self):
        c, self._live = getattr(self, "_live", None), None
        if c is not None:
            c._mh_abort = True                           # still connecting: checked_connect closes it (_post)
        sock = getattr(c, "sock", None)
        if sock is not None:
            try:
                import socket
                sock.shutdown(socket.SHUT_RDWR)          # wakes a read blocked on headers or on the stream
            except Exception:  # noqa: BLE001  already closed
                pass

    def _probe(self, base: str, key: str, model: str) -> str:
        def sink(c):                                     # Stop shuts the probe's socket too (_abort_live)
            self._live = c
            connect = c.connect

            def checked_connect():
                connect()
                if getattr(c, "_mh_abort", False):
                    c.close()
                    raise ConnectionAbortedError("stopped while connecting")
            c.connect = checked_connect
        return _probe_once(base, key, model, sink)

    def _await_endpoint(self, h, base: str, key: str, model: str) -> bool:
        """Wait, shared with every other chat on this endpoint, until it answers a probe again: up to
        MH_RECOVER_MIN minutes (30). False when it did not come back in time, or recovery is off."""
        budget = max(0.0, float(os.environ.get("MH_RECOVER_MIN", "30")) * 60)   # 0: one probe, no waiting
        return h.wait_until_up(lambda: self._wait(self._probe, base, key, model), self._sleep, self.emit, budget,
                               self._check_stop)

    def _sleep(self, s: float):
        end = time.time() + s
        while time.time() < end:
            self._check_stop()
            time.sleep(0.25)

    def _post(self, base: str, key: str, body: dict, stream: bool) -> tuple[dict, bool]:
        b = dict(body)
        if stream:
            b["stream"] = True
            b["stream_options"] = {"include_usage": True}
        req = urllib.request.Request(f"{base}/chat/completions", data=json.dumps(b).encode("utf-8"), method="POST",
                                     headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json",
                                              "X-Harness-Task-Id": self.route_id(), "session_id": self.route_id()})
        # Two clocks: a large context can take minutes before the first byte (prefill, a queue upstream), while a
        # stream that goes quiet mid-reply is stuck. Waiting only 120 s for the first byte failed every retry of a
        # long task the same way ("The read operation timed out").
        idle = float(os.environ.get("MH_AGENT_IDLE_S", "120"))
        first = max(idle, float(os.environ.get("MH_AGENT_FIRST_BYTE_S", "120"))) * getattr(self, "_first_scale", 1.0)
        got_text = False
        gen = getattr(self, "_gen", 0)
        live = []

        def sink(c):
            live.append(c)
            self._live = c                               # Stop shuts this socket, before or after the headers
            connect = c.connect

            def checked_connect():                       # Stop while connecting: close before sending anything
                connect()
                if getattr(c, "_mh_abort", False):
                    c.close()
                    raise ConnectionAbortedError("stopped while connecting")
            c.connect = checked_connect
        try:
            with _urlopen(req, first, sink) as r:
                if not stream:
                    raw_body = r.read()
                    try:
                        return json.loads(raw_body.decode("utf-8", "replace")), False
                    except ValueError:                   # a gateway that streams anyway, or sends an error page
                        if raw_body.lstrip().startswith(b"data:"):
                            return sse_to_chat(raw_body.splitlines(keepends=True)), False
                        raise RuntimeError(f"HTTP 0: the endpoint sent an unreadable reply ({raw_body[:80]!r})")

                def tee(lines):
                    nonlocal got_text
                    started, t_data = False, time.time()
                    for raw in lines:
                        if not started:                  # bytes are flowing: from here on, a gap is the idle clock
                            started = True
                            _set_read_timeout(r, idle)
                        if raw.startswith(b"data:"):
                            t_data = time.time()
                        elif time.time() - t_data > first:   # only keep-alives: they must not hold a slot forever
                            raise RuntimeError(f"HTTP 0: connection problem (timed out: only keep-alives for "
                                               f"{int(first)} s)")
                        if self._stop or getattr(self, "_gen", 0) != gen:
                            raise Interrupted()
                        line = raw.decode("utf-8", "replace").strip()
                        if line.startswith("data:") and line[5:].strip() not in ("", "[DONE]"):
                            try:
                                ev = json.loads(line[5:].strip())
                                for ch in ev.get("choices") or []:
                                    t = (ch.get("delta") or {}).get("content")
                                    if t:
                                        got_text = True
                                        self.emit({"type": "text_delta", "text": t})
                            except ValueError:
                                pass
                        yield raw
                resp = sse_to_chat(tee(r))
                if not resp.get("choices") or resp["choices"][0].get("finish_reason") is None and \
                        not resp["choices"][0]["message"].get("content") and \
                        not resp["choices"][0]["message"].get("tool_calls"):
                    raise RuntimeError("HTTP 0: the stream ended before the reply")
                return resp, got_text
        except RuntimeError:
            if got_text:                             # a reply cut short after some text: the retry starts clean
                self.emit({"type": "stream_reset"})
            raise
        except urllib.error.HTTPError as e:
            ra = (e.headers or {}).get("Retry-After") if hasattr(e, "headers") else None
            raise RuntimeError(f"HTTP {e.code}: {e.read()[:400].decode('utf-8', 'replace')}"
                               + (f" [retry-after: {ra}]" if ra else ""))
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError, http.client.HTTPException) as e:
            if got_text:                             # HTTPException: e.g. IncompleteRead, the stream dropped mid-reply
                self.emit({"type": "stream_reset"})
            raise RuntimeError(f"HTTP 0: connection problem ({e})")
        finally:
            if live and getattr(self, "_live", None) is live[-1]:   # never clear a newer call's handle
                self._live = None

    # ---------- tools ------------------------------------------------------------------------------------------
    def _outside(self, name: str, args: dict) -> str:
        """Why a read-only call still needs a yes in Supervised and Auto-accept edits: it reads outside the project
        (an SSH key, another app's secrets), or a web fetch carries a lot of data out in its address. "" if fine."""
        if name in ("read_file", "grep"):
            p = os.path.expanduser(str(args.get("path") or "."))
        elif name == "glob":
            pat = str(args.get("pattern") or "").replace("\\", "/")
            return ("" if not (pat.startswith(("/", "~")) or re.match(r"^[A-Za-z]:", pat) or ".." in pat.split("/"))
                    else f"List files outside the project ({pat[:80]})?")
        elif name in ("fetch_url", "mcp__meshpool_web__web_fetch"):
            from urllib.parse import urlparse
            u = urlparse(str(args.get("url") or ""))
            host = u.hostname or ""
            sub = ".".join(host.split(".")[:-2])           # labels before the site's own name can carry data too
            carried = len(u.query) + len(u.path) + len(u.fragment) + len(sub)
            return (f"Send this to {host or 'a website'}? The address carries {carried} characters of data."
                    if carried > 300 or len(sub) > 60 or len(u.query) > 200 else "")
        else:
            return ""
        try:
            q = Path(p).resolve() if os.path.isabs(p) else (self.ws.root / p).resolve()
        except (OSError, ValueError):
            return f"Read {p[:80]} (outside the project)?"
        # the project, this chat's own tool output and the files the user attached; never other chats' histories
        ok = [self.ws.root, self.dir.resolve(), (C.runs_dir() / "_uploads").resolve()]
        return "" if any(q == r or r in q.parents for r in ok) else f"Read {str(q)[:120]} (outside the project)?"

    # A new deck or workbook written in code instead of through the engines (python-pptx, openpyxl, xlsxwriter,
    # pptxgenjs). Reading or editing cell values in an existing workbook (load_workbook) is fine.
    _HANDMADE = re.compile(r"\bPresentation\(\s*\)|\.slides\.add_slide\(|\bWorkbook\(\s*\)|\bxlsxwriter\b|"
                           r"pptxgenjs|PptxGenJS")

    def _engine_only(self, name: str, args: dict) -> str:
        """In Work, every slide and every workbook goes through the engines (make_deck, add_slides,
        make_spreadsheet, office_task): they carry the house style and the checks. A command or file that builds one
        by hand is not run; the reply says which tool to use instead. "" when the call is fine."""
        if self.profile != "work" or name not in ("bash", "bash_many", "write_file", "edit"):
            return ""
        text = " ".join(str(args.get(k) or "") for k in ("command", "content", "new")) + \
            " ".join(str(c) for c in args.get("commands") or [])
        m = self._HANDMADE.search(text)
        if not m:
            return ""
        deck = any(k in m.group(0) for k in ("Presentation", "slide", "pptx", "Pptx"))
        return ("Not run: slides are never written by hand here. Use make_deck for a new deck, or add_slides to add "
                "slides to a deck the user has (it keeps the deck's look). Put what each slide must show, with the "
                "numbers or the files that hold them, in the request." if deck else
                "Not run: workbooks are never built by hand here. Use make_spreadsheet (or office_task) for a new "
                "workbook or model; describe the sheets, inputs and outputs in the request. To read or change a few "
                "values in an existing workbook, openpyxl.load_workbook is fine.")

    def _permit(self, name: str, args: dict) -> tuple[bool, str]:
        if name == "delegate_task":
            return True, ""                    # its sub-agents' own actions are checked against this chat's mode
        risky = self._outside(name, args) if self.mode != "auto" else ""
        if not risky and (self.mode == "auto" or name in READ_ONLY or name.replace("-", "_") in SAFE_MCP):
            return True, ""
        if self.mode == "edits" and name in ("edit", "write_file"):
            return True, ""                    # auto-accept edits: file changes go through, commands still ask
        if name in ("edit", "write_file") and self._exists(args.get("path", "")) and \
                self._diff(name, args) == "(no change)" and self._same_bytes(name, args):
            return True, ""                    # nothing would change, byte for byte: do not ask
        group = "edit" if name in ("edit", "write_file") else "bash" if name in ("bash_many", "probe", "trace") else name
        if risky:
            group = "outside-" + name
        if group in self.always:
            return True, ""
        if not self.approve:
            return False, "No one is available to approve this. Run with --auto to allow changes."
        if risky:
            kind, title, detail = "read", risky, json.dumps(args, indent=1, ensure_ascii=False)[:3000]
        elif name == "bash":
            kind, title, detail = "bash", "Run this command?", args.get("command", "")
        elif name == "bash_many":
            cmds = [str(c) for c in args.get("commands") or []]
            kind, title, detail = "bash", f"Run these {len(cmds)} commands?", "\n".join(cmds)
        elif name in ("edit", "write_file"):
            kind, title, detail = "edit", f"{'Edit' if name == 'edit' else 'Write'} {args.get('path', '')}?", \
                self._diff(name, args)
        else:
            tool = name.split('__', 2)[-1]
            target = str(args.get("filename") or args.get("path") or args.get("url") or "")[:80]
            nice = {"make_document": "Make a Word document", "make_pdf": "Make a PDF", "make_spreadsheet":
                    "Build a spreadsheet", "make_deck": "Make a PowerPoint deck", "convert_document": "Convert a file",
                    "analyze_data": "Analyse data"}.get(tool) or ("Use " + tool.replace("_", " ") + " ("
                                                              + name.split('__')[1].replace("_", " ") + ")")
            kind, title = ("doc" if args.get("markdown") else "mcp"), f"{nice}{' ' + target if target else ''}?"
            body = args.get("markdown") or args.get("request") or args.get("content")
            detail = (str(body)[:3000] if isinstance(body, str) and body.strip()
                      else json.dumps(args, indent=1, ensure_ascii=False)[:3000])
        decision, note = self.approve(kind, title, detail)
        if decision == "always":
            self.always.add(group)
        return decision in ("yes", "always"), note or ""

    def _same_bytes(self, name: str, args: dict) -> bool:
        """True when the write would leave the file's bytes exactly as they are. A diff of decoded lines hides line
        endings, the final newline and bytes that are not UTF-8, so it alone cannot skip the prompt."""
        try:
            p = self.ws._path(str(args.get("path", "")), write=True)
            raw = p.read_bytes()
            if name == "write_file" and not args.get("append"):
                return raw == str(args.get("content", "")).encode("utf-8")
            return False
        except Exception:  # noqa: BLE001
            return False

    def _diff(self, name: str, args: dict) -> str:
        """The change an edit or write_file would make, for the tool card and the approval prompt. Never raises:
        the arguments may not have been validated yet."""
        try:
            return self._diff_text(name, args)
        except Exception:  # noqa: BLE001
            return ""

    def _diff_text(self, name: str, args: dict) -> str:
        path = str(args.get("path", ""))
        try:
            p = self.ws._path(path, write=True)
            before = p.read_text(encoding="utf-8", errors="replace") if p.exists() else ""
        except Exception:
            before = ""
        if name == "edit":
            if before.count(args.get("old", "")) == 1:
                after = before.replace(args["old"], args.get("new", ""), 1)
            else:
                before, after = args.get("old", ""), args.get("new", "")
        else:
            after = (before + args.get("content", "")) if args.get("append") else args.get("content", "")
        d = list(difflib.unified_diff(before.splitlines(), after.splitlines(), f"a/{path}", f"b/{path}",
                                      n=2, lineterm=""))
        if not before and name == "write_file":
            lines = after.splitlines()
            return f"New file, {len(lines)} line{'' if len(lines) == 1 else 's'}\n" + "\n".join("+" + x for x in lines[:60]) + \
                ("\n..." if len(lines) > 60 else "")
        return "\n".join(d[:200]) + ("\n..." if len(d) > 200 else "") if d else "(no change)"

    def _run_tool(self, c: dict, cut: bool = False) -> str:
        f = c.get("function") or {}
        name, raw = f.get("name", ""), f.get("arguments") or "{}"
        cid = c.get("id", "")
        try:
            args = json.loads(raw) if isinstance(raw, str) else dict(raw)
            if not isinstance(args, dict):
                args = {}
        except ValueError:
            args = {}
        is_mcp = bool(self.mcp and self.mcp.has(name))
        bad = None                                   # validate first: the card shows the arguments that will run
        if cut:
            bad = ToolError(f"this {name} call was cut off at the model's output limit, so its arguments are "
                            "incomplete and it was not run. Split the content into smaller pieces: write_file the "
                            "first part, then write_file with append=true for each further part, or make several "
                            "smaller edits.")
        elif not is_mcp:
            try:
                args = validate(name, raw, self._tool_names() + (
                    ["delegate_task"] if name == "delegate_task" and getattr(self, "_delegate_depth", 0) == 0
                    and not self.plan and _subagents_on() else []))
            except ToolError as e:
                bad = e
        elif self.plan and name.replace("-", "_") not in PLAN_MCP:
            bad = ToolError(f"{name} is not available in plan mode: it can change things. Investigate with "
                            "read-only tools and write the plan; the user switches to building to run it.")
        self.emit({"type": "tool_start", "id": cid, "name": name, "summary": _summary(name, args), "mcp": is_mcp,
                   "diff": self._diff(name, args) if name in ("edit", "write_file") and not bad else ""})
        ok, denied = True, False
        try:
            if bad:
                raise bad
            steer = self._engine_only(name, args)
            allowed, note = (False, "") if steer else self._permit(name, args)
            if steer:
                out, ok = steer, False
            elif not allowed:
                denied, ok = True, False
                out = "The user denied this action." + (f" Their note: {note}" if note else "") + \
                    " Do not retry it. Take another approach or ask the user."
            elif name == "delegate_task":
                from meshpool_harness.agent import delegate as DL
                out, ok = DL.run_tool(self, args)      # not _wait: on Stop it returns the finished reports
            elif is_mcp and name.endswith("run_subtasks"):
                out, err = self._run_subtasks(name, args)
                ok = not err
                out = self.ws._cap(str(out), "mcp")
            elif is_mcp:
                out, err = self._wait(self.mcp.call, name, args)
                ok = not err
                out = self.ws._cap(str(out), "mcp")    # head and tail; the full output goes to a file
            elif name == "fetch_url":
                out = self._wait(self._fetch, args["url"], args.get("start", 0), args.get("max_chars", 15000),
                                 bool(args.get("links")))
            elif name == "web_research":
                from meshpool_harness import web as WEB
                try:
                    out = self._wait(WEB.research, args.get("question", ""), args.get("queries"), args.get("max_sources", 6),
                                       args.get("recency", ""), args.get("site", ""))
                except WEB.WebError as e:
                    raise ToolError(str(e))
            elif name == "office_task":
                out, ok = self._office(args)
            elif name == "web_search":
                out = self._wait(self._search, args["query"], args.get("max_results", 8), args.get("recency", ""),
                                 args.get("site", ""))
            elif name == "ask_user":
                out = self._ask_user(args)
            elif name == "use_skill":
                from meshpool_harness import skills as SK
                out = SK.instructions(str(args.get("name", "")).lstrip("$"))
            elif name == "todo_write":
                items = [{"content": str(i.get("content", "")), "status": i.get("status", "pending")}
                         for i in args.get("items") or [] if isinstance(i, dict)]
                self.emit({"type": "plan", "entries": items})
                self._todos, self._todo_touched = items, True
                done = sum(i["status"] == "completed" for i in items)
                out = f"Plan updated: {done} of {len(items)} done."
            else:
                out = getattr(self.ws, name)(**args)
                if name == "bash":
                    m = re.match(r"\[exit (\d+)\]", out)
                    ok = bool(m) and m.group(1) == "0"
                elif name == "bash_many":
                    ok = "[exit " in out and not re.search(r"\[exit [1-9]", out)
                elif name in ("probe", "trace"):
                    ok = out.startswith("[exit 0]")
        except ToolError as e:
            out, ok = f"ERROR: {e}", False
        except (Interrupted, KeyboardInterrupt):
            raise
        except Exception as e:
            out, ok = f"ERROR: {type(e).__name__}: {e}", False
        out = clean(str(out))
        self.emit({"type": "tool_end", "id": cid, "name": name, "ok": ok, "denied": denied,
                   "output": out[:6000] + ("\n..." if len(out) > 6000 else "")})
        return out

    # ---------- questions (ask_user) -----------------------------------------------------------------------------
    def pending_question(self) -> dict | None:
        p = getattr(self, "_question", None)
        return {"id": p["id"], "questions": p["questions"]} if p else None

    def reply(self, question_id: str, answers) -> None:
        """A front end answers the waiting ask_user question: answers is a list with one {"selected": [labels],
        "other": text} per question, or plain text the user typed instead of picking."""
        p = getattr(self, "_question", None)
        if not p or (question_id and p["id"] != question_id):
            raise ValueError("That question is no longer waiting.")
        p["answers"] = answers if answers is not None else []
        p["ev"].set()

    def _ask_user(self, args: dict) -> str:
        return self._ask(ASK.normalize(args))[2]

    def _ask(self, qs: list) -> tuple[list, str, str, str]:
        """Ask the user and wait; returns (answers, source, tool result, the text the user typed instead of picking
        options or ""). Who answers: a front end's own
        `ask(qid, questions)` callback (the terminal), a page that calls reply() while this waits (`interactive`
        true: the web app), or no one (headless runs, ACP, tests, auto mode with MH_ASK_AUTO=1): then the
        recommended options, and the result says so."""
        qid = "q-" + uuid.uuid4().hex[:10]
        wait = ASK.timeout_s()
        own = getattr(self, "ask", None)
        live = getattr(self, "interactive", False)
        live = bool(live() if callable(live) else live)
        source = ""
        if self.mode == "auto" and os.environ.get("MH_ASK_AUTO") == "1":
            source = "auto"
        elif not own and not live:
            source = "headless"
        self.emit({"type": "question", "id": qid, "questions": qs, "timeout_s": 0 if source else int(wait)})
        raw = None
        if not source and own:
            raw = own(qid, qs)
            source = "user" if raw is not None else "headless"
        elif not source:
            p = {"id": qid, "questions": qs, "ev": threading.Event(), "answers": None}
            self._question = p
            end = time.time() + wait
            try:
                while not p["ev"].wait(0.2):
                    if self._stop:
                        break
                    if time.time() > end:
                        break
            finally:
                self._question = None
            if p["ev"].is_set():
                raw, source = p["answers"], "user"
            elif self._stop:
                self.emit({"type": "question_done", "id": qid, "source": "stopped", "answers": [],
                           "text": "Stopped before you answered."})
                raise Interrupted()
            else:
                source = "timeout"
        answers, shown, result = ASK.finish(qs, raw, source, wait)
        self.emit({"type": "question_done", "id": qid, "source": source, "answers": answers, "text": shown})
        typed = raw.strip()[:4000] if source == "user" and isinstance(raw, str) else ""
        return answers, source, result, typed

    def _deck_brief(self, args: dict) -> str:
        """office_task with engine slides: settle the deck brief (engines/forge/brief.py) before building. The
        open questions go to the user in one question card; with no one to ask, the recommended answers."""
        req = args["request"]
        try:
            from engines.forge import brief as BR
        except Exception:  # noqa: BLE001  engines not shipped: build from the request as it is
            return req
        fields = {k: args[k] for k in BR.FIELDS if args.get(k) not in (None, "")}
        qs = BR.questions(req, args.get("files"), fields)
        if not qs or args.get("brief_ok") or BR.wants_defaults(req):
            return BR.complete(req, args.get("files"), fields)
        answers, source, _, typed = self._ask(ASK.normalize({"questions": qs}))
        if typed:                                    # the user wrote their own answer: it goes into the request,
            req = f"{req}\n\nThe user's answer to the brief questions: {typed}"   # and only what it leaves open
            return BR.complete(req, args.get("files"), fields)                     # gets a recommendation
        fields.update(BR.answers_from(qs, answers))
        return BR.complete(req, args.get("files"), fields)

    def _office(self, args: dict) -> tuple[str, bool]:
        """Run a one-shot engine (slides, sheets, data) in its own process; files land in the working folder. The
        same runner and report as the Office MCP server (agent/office_jobs.py)."""
        from meshpool_harness.agent import office_jobs as OJ
        engine = args["engine"]
        if engine == "slides":
            args = dict(args, request=self._deck_brief(args))
        paths, missing = OJ.resolve_files(self.root, args.get("files"))
        if missing:
            return f"ERROR: input file not found: {missing}", False
        jid = OJ.new_job(engine, args["request"], paths, self.root)
        self.emit({"type": "status", "text": f"The {engine} engine is working. Decks and models take a few minutes."})
        OJ.run(jid, timeout=float(os.environ.get("MH_OFFICE_TIMEOUT_S", "1500")), should_stop=lambda: self._stop)
        if OJ.JOBS[jid].get("stopped"):
            raise Interrupted()
        text, failed = OJ.report(jid, rel_to=self.root)
        return text, not failed

    def _search(self, query: str, n: int = 8, recency: str = "", site: str = "") -> str:
        from meshpool_harness import web as WEB
        try:
            return WEB.format_results(WEB.search(query, n, recency, "", site))
        except WEB.WebError as e:
            raise ToolError(str(e))

    def _fetch(self, url: str, start: int = 0, max_chars: int = 15000, links: bool = False) -> str:
        from meshpool_harness import web as WEB
        try:
            return WEB.format_page(WEB.fetch(url), start, max(2000, min(int(max_chars or 15000), 60000)), links)
        except WEB.WebError as e:
            raise ToolError(str(e))

    def _repair(self):
        """After an interruption every tool call must still have a result, or the next request is invalid."""
        for i in range(len(self.messages) - 1, -1, -1):
            m = self.messages[i]
            if m.get("role") == "assistant" and m.get("tool_calls"):
                have = {x.get("tool_call_id") for x in self.messages[i + 1:] if x.get("role") == "tool"}
                for c in m["tool_calls"]:
                    if c["id"] not in have:
                        self.messages.append({"role": "tool", "tool_call_id": c["id"],
                                              "content": "Not run: the user stopped the agent."})
                break
            if m.get("role") == "user":
                break

    # ---------- sessions ---------------------------------------------------------------------------------------
    def save(self):
        if getattr(self, "deleted", False):           # the user deleted this thread; never bring it back
            return
        try:
            self.dir.mkdir(parents=True, exist_ok=True)
            (self.dir / "session.json").write_text(json.dumps({
                "id": self.id, "workdir": str(self.root), "title": self.title, "created": self.created,
                "updated": time.time(), "model": self.current_model(), "model_choice": self.model, "plan": self.plan,
                "harness": "meshpool-work" if self.profile == "work" else "meshpool-code",
                "kind": "cowork" if self.profile == "work" else "code",
                "usage": self.usage, "messages": self.messages[1:]}, ensure_ascii=False), encoding="utf-8")
        except OSError:
            pass

    @classmethod
    def resume(cls, workdir: str, session_id: str | None = None, **kw) -> "Agent | None":
        root = str(Path(workdir).expanduser().resolve())
        best = None
        if SESSIONS.exists():
            for p in SESSIONS.glob("*/session.json"):
                if p.parent.name.startswith("sub-"):     # a sub-agent's, never the user's chat
                    continue
                try:
                    d = json.loads(p.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    continue
                if session_id and d.get("id") != session_id:
                    continue
                if not session_id and d.get("workdir") != root:
                    continue
                if d.get("harness") not in (None, "meshpool-code", "meshpool-work"):
                    continue
                if best is None or d.get("updated", 0) > best.get("updated", 0):
                    best = d
        if not best:
            return None
        kw.setdefault("profile", "work" if best.get("harness") == "meshpool-work" else "code")
        a = cls(best["workdir"], session_id=best["id"], **kw)
        a.messages = [a.messages[0]] + best.get("messages", [])
        a.usage = best.get("usage") or a.usage
        a.title = best.get("title", "")
        a.created = best.get("created", a.created)
        a.model = best.get("model_choice")
        a.plan = bool(best.get("plan"))
        return a
