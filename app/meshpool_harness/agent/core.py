"""Meshpool coding agent: an interactive conversation in a project folder.

Unlike the one-shot harness (`meshpool run`), the agent keeps the whole conversation, answers in plain text when it
is done with a request, and waits for the next one. It reuses the harness's file and shell tools (Workspace), its
transport (any OpenAI-compatible endpoint, streamed), its model profiles, and adds MCP tools and permission prompts.

Front ends (terminal, web app) talk to it through two callbacks:
  emit(event: dict)                      every step, as it happens (text deltas, tool calls, usage, errors)
  approve(kind, title, detail) -> (decision, note)   decision in {"yes", "always", "no"}
"""
from __future__ import annotations

import difflib
import html
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

from meshpool_harness import config as C
from meshpool_harness.core import tools as T
from meshpool_harness.core.loop import _compact, _sanitize_calls
from meshpool_harness.core.tools import ToolError, Workspace, clean, validate
from meshpool_harness.core.wire import sse_to_chat

SESSIONS = C.runs_dir() / "sessions"
DEFAULT_MODEL = "deepseek-v4.1-flash"
READ_ONLY = {"read_file", "grep", "glob", "fetch_url", "office_task", "web_search", "web_research", "todo_write", "use_skill"}
SAFE_MCP = {"mcp__meshpool_web__web_search", "mcp__meshpool_web__web_fetch", "mcp__meshpool_web__web_research",
            "mcp__meshpool_assistant__memory_view", "mcp__meshpool_assistant__memory_save",
            "mcp__meshpool_assistant__memory_forget", "mcp__meshpool_assistant__schedule_task",
            "mcp__meshpool_assistant__list_scheduled_tasks", "mcp__meshpool_assistant__search_chats",
            "mcp__meshpool_assistant__run_subtasks", "mcp__meshpool_office__read_document",
            "mcp__meshpool_office__office_job_status", "mcp__meshpool_tools__current_time",
            "mcp__meshpool_tools__calculate"}
PLAN_TOOLS = ["read_file", "grep", "glob", "fetch_url", "web_search", "web_research", "todo_write", "use_skill"]
BUILTIN = ["read_file", "grep", "glob", "edit", "write_file", "bash", "fetch_url", "web_search", "web_research",
           "todo_write", "use_skill"]
WORK_TOOLS = BUILTIN + ["office_task"]
PROJECT_FILES = ("MESHPOOL.md", "AGENTS.md", "CLAUDE.md")

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
5. Do what was asked, nothing more. If the request is unclear, ask one short question instead of guessing.
6. When you finish a request, reply with a short summary of what you changed and how you checked it, without \
calling a tool. That reply ends your turn; the user then answers or gives the next request.

Rules
- Paths are relative to the project folder. You can only write inside it.
- Never print or store secrets such as API keys or tokens.
- Ask before destructive steps (deleting files, force-pushing, dropping data) unless the user asked for them.
- If the user denies a tool call, do not retry it. Take another approach or ask.
- Long outputs are cut. The full text is saved to a file you can read.
- Tools whose names start with mcp__ come from MCP servers the user connected. Use them when they fit.
- To look things up, use web_search, web_research and fetch_url. They are fast and need no browser. For research, run web_research with several varied queries, read key sources in full with fetch_url, and cite every fact with its link.
- Use the browser tools (if enabled) only for pages that need a sign-in, clicks or forms. Use computer use only for desktop apps, and only when the user asks for it. Files, commands and APIs beat clicking. Before anything irreversible on a website or in an app (sending, paying, deleting, submitting), stop and confirm with the user.
- Write plainly: short sentences, no filler. Use Markdown code blocks for code."""


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
     "files": {"type": "array", "items": T.S}}, ["engine", "request"]))

WORK_SYSTEM = """You are Meshpool Cowork, an assistant that does real work for the user on their own computer: \
research, writing, analysis, spreadsheets, presentations and file housekeeping. You work through tools, and the user \
sees every tool call.

Environment
- Working folder: {root}
- Operating system: {os_name}
- Shell for the bash tool: {shell}

How to work
1. Understand the job. If something important is unclear (audience, format, scope), ask one short question first.
2. For a multi-step job, keep a short plan with todo_write and update it as you go.
3. Gather material: read the user's files (read_file, glob, grep), research the web with web_research and \
web_search, read pages and PDFs with fetch_url, and use connected apps (mcp__ tools) when they fit. For a big \
research job, split it into parts, research each, then write up.
4. Produce real deliverables as files in the working folder. For decks, spreadsheets, models and data analysis use \
office_task: it returns a finished, checked file. For notes, memos and reports write Markdown with write_file. Use \
Python through bash for quick calculations.
5. Check the result: open what you made, verify numbers against their sources, and fix problems before you finish.
6. Finish with a short reply: what you made, where the files are, and anything the user should check. Do not call \
a tool in that reply.

Rules
- Use facts from the user's files and fetched sources. Never invent numbers, quotes or sources; say what is missing.
- You can only write inside the working folder. Ask before deleting or overwriting the user's files.
- Never print or store secrets such as API keys or tokens.
- If the user denies a tool call, do not retry it. Take another approach or ask.
- To look things up, use web_search, web_research and fetch_url. They are fast and need no browser. For research, run web_research with several varied queries, read key sources in full with fetch_url, and cite every fact with its link.
- Use the browser tools (if enabled) only for pages that need a sign-in, clicks or forms. Use computer use only for desktop apps, and only when the user asks for it. Files, commands and APIs beat clicking. Before anything irreversible on a website or in an app (sending, paying, deleting, submitting), stop and confirm with the user.
- Write plainly: short sentences, no filler."""


class Interrupted(Exception):
    pass


def _summary(name: str, args: dict) -> str:
    if name == "bash":
        return args.get("command", "")
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
        return f"{args.get('engine', '')}: {args.get('request', '')[:160]}"
    if name == "web_search":
        return args.get("query", "")
    if name == "web_research":
        return args.get("question", "")[:160]
    if name == "use_skill":
        return args.get("name", "")
    if name == "todo_write":
        items = args.get("items") or []
        return f"{sum(1 for i in items if isinstance(i, dict) and i.get('status') == 'completed')}/{len(items)} done"
    s = json.dumps(args, ensure_ascii=False)
    return s if len(s) < 300 else s[:300] + "..."


def _html_to_text(s: str) -> str:
    s = re.sub(r"(?is)<(script|style|noscript|svg|head)\b.*?</\1>", " ", s)
    s = re.sub(r"(?i)<br\s*/?>|</(p|div|li|h[1-6]|tr|section|article)>", "\n", s)
    s = re.sub(r"(?s)<[^>]+>", " ", s)
    s = html.unescape(s)
    s = re.sub(r"[ \t\r\f\v]+", " ", s)
    return re.sub(r"\n\s*\n+", "\n\n", s).strip()


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
                 session_id: str | None = None, max_steps: int = 80, profile: str = "code"):
        self.profile = "work" if profile == "work" else "code"
        self.root = Path(workdir).expanduser().resolve()
        if not self.root.is_dir():
            raise ValueError(f"Project folder not found: {workdir}")
        self.id = session_id or time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:4]
        self.dir = SESSIONS / self.id
        self.ws = Workspace(str(self.root), str(self.dir / "tool-output"))
        self.ws.should_stop = lambda: self._stop
        self.mcp = mcp
        self.emit = emit or (lambda ev: None)
        self.approve = approve
        self.mode = mode or os.environ.get("MH_AGENT_PERMISSIONS", "ask")
        self.always: set[str] = set()
        self.max_steps = max_steps
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
        shell = "bash" + (f" ({bash})" if os.name == "nt" and bash else "") if bash else \
            ("cmd.exe (no bash found: write Windows commands)" if os.name == "nt" else "sh")
        text = (WORK_SYSTEM if self.profile == "work" else SYSTEM).format(
            root=self.root, os_name=f"{platform.system()} {platform.release()}", shell=shell)
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
        return text

    def tool_schemas(self) -> list:
        if self.plan:
            return T.schemas(PLAN_TOOLS)
        return T.schemas(self._tool_names()) + ([x for x in self.mcp.schemas()
                                                 if not x["function"]["name"].replace("-", "_").startswith("mcp__meshpool_web__")]
                                                if self.mcp else [])

    def _tool_names(self) -> list:
        return PLAN_TOOLS if self.plan else (WORK_TOOLS if self.profile == "work" else BUILTIN)

    def current_model(self) -> str:
        return self.model or os.environ.get("MH_MODEL") or DEFAULT_MODEL

    # ---------- public -----------------------------------------------------------------------------------------
    def stop(self):
        self._stop = True

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
        self.emit({"type": "user", "text": text.split("\n\n\u200b")[0], "images": [im.get("name", "image") for im in images or []],
                   "plan": self.plan})
        turn = {"in": 0, "cached": 0, "out": 0, "calls": 0}
        final = ""
        try:
            for _ in range(self.max_steps):
                self._check_stop()
                self._take_inbox()
                self._maybe_compact()
                msg, streamed = self._chat(turn)
                calls = _sanitize_calls(msg.get("tool_calls") or [])
                content = msg.get("content") or ""
                keep = {"role": "assistant", "content": content or ("" if calls else "(no reply)")}
                if calls:
                    keep["tool_calls"] = calls
                self.messages.append(keep)
                self.emit({"type": "assistant", "text": content, "streamed": streamed, "has_tools": bool(calls)})
                if not calls:
                    final = content
                    if self._inbox:                 # the user added something while this reply was written
                        continue
                    break
                shots = []
                for c in calls:
                    self._check_stop()
                    out = self._run_tool(c)
                    self.messages.append({"role": "tool", "tool_call_id": c["id"], "content": clean(out)})
                    if self.mcp is not None and getattr(self.mcp, "last_images", None):
                        shots += [(c.get("function", {}).get("name", ""), im) for im in self.mcp.last_images]
                        self.mcp.last_images = []
                if shots:
                    self._attach_images(shots)
            else:
                self.emit({"type": "error", "text": f"Stopped after {self.max_steps} steps in one turn. "
                                                    "Send a message to continue."})
        except (Interrupted, KeyboardInterrupt):
            self._repair()
            self._take_inbox()                       # keep what the user added; the next turn sees it
            self.emit({"type": "interrupted", "text": "Stopped. Send a message to continue."})
        except Exception as e:
            self._repair()
            self.emit({"type": "error", "text": f"{e}"})
        finally:
            self.emit({"type": "turn_end", "turn": turn, "session": dict(self.usage)})
            self.save()
        return final

    # ---------- images (computer use, browser screenshots) -----------------------------------------------------
    def _attach_images(self, shots: list):
        """Tool results cannot carry images on the chat API, so screenshots follow as a user message. Only the latest
        two images stay in the conversation; older ones become a short note to keep requests small."""
        if os.environ.get("MH_VISION", "1") == "0" or getattr(self, "_no_vision", False):
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
        limit = int(os.environ.get("MH_COMPACT_CHARS", "400000"))
        size = sum(len(m.get("content") or "") if isinstance(m.get("content"), str) else 0 for m in self.messages)
        if size > limit:
            _compact(self.messages, keep_recent=12)
            self.emit({"type": "status", "text": "Older tool outputs were shortened to keep the context small."})

    def _body(self) -> dict:
        from meshpool_harness.profiles import apply, for_model
        model = self.current_model()
        prof = for_model(model, os.environ.get("MH_PROFILE"))
        lim = int((prof.get("limits") or {}).get("max_output_tokens", 8192))
        body = {"model": model, "max_tokens": lim, "tools": self.tool_schemas(), "tool_choice": "auto"}
        msgs, body = apply(prof, list(self.messages), body, "worker")
        if os.environ.get("MH_TEMPERATURE"):
            body["temperature"] = float(os.environ["MH_TEMPERATURE"])
        body["messages"] = msgs
        if os.environ.get("MH_CACHE_KEY", "1") == "1":
            body["prompt_cache_key"] = self.id
            body["user"] = self.id
        return body

    def _chat(self, turn: dict) -> tuple[dict, bool]:
        body = self._body()
        base = (os.environ.get("MESHPOOL_BASE_URL") or os.environ.get("OPENAI_BASE_URL")
                or "https://llm.meshpool.ai/v1").rstrip("/")
        key = os.environ.get("MESHPOOL_API_KEY") or os.environ.get("OPENAI_API_KEY") or ""
        if not key:
            raise RuntimeError(f"No API key. Add MESHPOOL_API_KEY to {C.config_path()} or open Settings in the app.")
        wire = os.environ.get("MH_WIRE", "chat")
        stream = wire == "chat" and os.environ.get("MH_STREAM", "1") != "0"
        self.emit({"type": "model_start"})
        resp, streamed, cleaned = None, False, False
        for attempt in range(8):
            self._check_stop()
            try:
                if wire == "responses":
                    from meshpool_harness.core.wire import SimpleLedger
                    extra = {k: v for k, v in body.items() if k not in ("model", "messages", "max_tokens", "temperature")}
                    resp = SimpleLedger("agent").chat(model=body["model"], messages=body["messages"],
                                                      max_tokens=body.get("max_tokens", 8192),
                                                      temperature=body.get("temperature"), extra=extra, retries=0)
                else:
                    resp, streamed = self._post(base, key, body, stream)
                break
            except (Interrupted, KeyboardInterrupt):
                raise
            except RuntimeError as e:
                msg = str(e)
                if "HTTP 401" in msg or "HTTP 403" in msg:
                    raise RuntimeError("The endpoint rejected the API key. Check it in settings. " + msg[:200])
                if "HTTP 400" in msg and _has_images(body["messages"]) and not getattr(self, "_no_vision", False):
                    self._no_vision = True                 # this model does not take images: continue without them
                    _drop_images(self.messages, keep=0)
                    _drop_images(body["messages"], keep=0)
                    self.emit({"type": "status", "text": "This model cannot see images, so screenshots are left out. "
                                                         "For computer use, choose a vision model in Settings."})
                    continue
                if "HTTP 400" in msg and "rejected as invalid" not in msg:
                    if cleaned:
                        raise
                    cleaned = True                         # control characters in a tool output: clean and retry
                    for m in body["messages"]:
                        if isinstance(m.get("content"), str):
                            m["content"] = clean(m["content"])
                    continue
                if "HTTP 429" in msg:
                    wait = 30
                    mt = re.search(r"resets at: (\d{4}-\d\d-\d\d \d\d:\d\d:\d\d)", msg)
                    if mt:
                        import calendar
                        wait = max(5, min(120, calendar.timegm(time.strptime(mt.group(1), "%Y-%m-%d %H:%M:%S"))
                                          - time.time() + 3))
                    self.emit({"type": "status", "text": f"Rate limited by the endpoint. Waiting {int(wait)} s."})
                    self._sleep(wait)
                    continue
                if attempt >= 5:
                    raise
                self.emit({"type": "status", "text": f"Model call failed ({msg[:120]}). Retrying."})
                if streamed:
                    self.emit({"type": "stream_reset"})
                self._sleep(min(20, 2 ** attempt))
        self.emit({"type": "model_end"})
        u = resp.get("usage") or {}
        cached = (u.get("prompt_tokens_details") or {}).get("cached_tokens") or u.get("prompt_cache_hit_tokens") or 0
        d = {"in": u.get("prompt_tokens", 0) or 0, "cached": cached or 0, "out": u.get("completion_tokens", 0) or 0,
             "calls": 1}
        for k, v in d.items():
            turn[k] += v
            self.usage[k] += v
        self.emit({"type": "usage", **d})
        msg = (resp.get("choices") or [{}])[0].get("message") or {}
        return msg, streamed

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
                                              "X-Harness-Task-Id": self.id})
        timeout = float(os.environ.get("MH_AGENT_IDLE_S", "120"))
        got_text = False
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                if not stream:
                    return json.loads(r.read().decode("utf-8", "replace")), False

                def tee(lines):
                    nonlocal got_text
                    for raw in lines:
                        self._check_stop()
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
        except urllib.error.HTTPError as e:
            raise RuntimeError(f"HTTP {e.code}: {e.read()[:400].decode('utf-8', 'replace')}")
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as e:
            if got_text:
                self.emit({"type": "stream_reset"})
            raise RuntimeError(f"HTTP 0: connection problem ({e})")

    # ---------- tools ------------------------------------------------------------------------------------------
    def _permit(self, name: str, args: dict) -> tuple[bool, str]:
        if self.mode == "auto" or name in READ_ONLY or name.replace("-", "_") in SAFE_MCP:
            return True, ""
        if self.mode == "edits" and name in ("edit", "write_file"):
            return True, ""                    # auto-accept edits: file changes go through, commands still ask
        group = "edit" if name in ("edit", "write_file") else name
        if group in self.always:
            return True, ""
        if not self.approve:
            return False, "No one is available to approve this. Run with --auto to allow changes."
        if name == "bash":
            kind, title, detail = "bash", "Run this command?", args.get("command", "")
        elif name in ("edit", "write_file"):
            kind, title, detail = "edit", f"{'Edit' if name == 'edit' else 'Write'} {args.get('path', '')}?", \
                self._diff(name, args)
        else:
            kind, title = "mcp", f"Use the MCP tool {name.split('__', 2)[-1]} from '{name.split('__')[1]}'?"
            detail = json.dumps(args, indent=1, ensure_ascii=False)[:3000]
        decision, note = self.approve(kind, title, detail)
        if decision == "always":
            self.always.add(group)
        return decision in ("yes", "always"), note or ""

    def _diff(self, name: str, args: dict) -> str:
        path = args.get("path", "")
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
            return f"New file, {len(lines)} lines\n" + "\n".join("+" + x for x in lines[:60]) + \
                ("\n..." if len(lines) > 60 else "")
        return "\n".join(d[:200]) + ("\n..." if len(d) > 200 else "") if d else "(no change)"

    def _run_tool(self, c: dict) -> str:
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
        self.emit({"type": "tool_start", "id": cid, "name": name, "summary": _summary(name, args), "mcp": is_mcp,
                   "diff": self._diff(name, args) if name in ("edit", "write_file") else ""})
        ok, denied = True, False
        try:
            if not is_mcp:
                args = validate(name, raw, self._tool_names())
            allowed, note = self._permit(name, args)
            if not allowed:
                denied, ok = True, False
                out = "The user denied this action." + (f" Their note: {note}" if note else "") + \
                    " Do not retry it. Take another approach or ask the user."
            elif is_mcp:
                out, err = self.mcp.call(name, args)
                ok = not err
            elif name == "fetch_url":
                out = self._fetch(args["url"], args.get("start", 0), args.get("max_chars", 15000), bool(args.get("links")))
            elif name == "web_research":
                from meshpool_harness import web as WEB
                try:
                    out = WEB.research(args.get("question", ""), args.get("queries"), args.get("max_sources", 6),
                                       args.get("recency", ""), args.get("site", ""))
                except WEB.WebError as e:
                    raise ToolError(str(e))
            elif name == "office_task":
                out, ok = self._office(args)
            elif name == "web_search":
                out = self._search(args["query"], args.get("max_results", 8), args.get("recency", ""), args.get("site", ""))
            elif name == "use_skill":
                from meshpool_harness import skills as SK
                out = SK.instructions(str(args.get("name", "")).lstrip("$"))
            elif name == "todo_write":
                items = [{"content": str(i.get("content", "")), "status": i.get("status", "pending")}
                         for i in args.get("items") or [] if isinstance(i, dict)]
                self.emit({"type": "plan", "entries": items})
                done = sum(i["status"] == "completed" for i in items)
                out = f"Plan updated: {done} of {len(items)} done."
            else:
                out = getattr(self.ws, name)(**args)
                if name == "bash":
                    m = re.match(r"\[exit (\d+)\]", out)
                    ok = bool(m) and m.group(1) == "0"
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

    def _office(self, args: dict) -> tuple[str, bool]:
        """Run a one-shot engine (slides, sheets, data) in its own process; files land in the working folder."""
        import subprocess
        import sys as _sys
        engine = args["engine"]
        stamp = time.strftime("%Y%m%d-%H%M%S")
        out = self.root / "Meshpool outputs" / f"{stamp}-{engine}"
        out.mkdir(parents=True, exist_ok=True)
        files = []
        for f in args.get("files") or []:
            p = (self.root / f).resolve() if not Path(f).is_absolute() else Path(f)
            if not p.exists():
                return f"ERROR: input file not found: {f}", False
            files.append(str(p))
        cmd = [_sys.executable, "-m", "meshpool_harness", "run", args["request"], "--engine", engine, "--out", str(out)]
        if files:
            cmd += ["--files", *files]
        env = dict(os.environ, PYTHONPATH=str(C.APP), PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
        env.pop("MH_EVENT_LOG", None)
        self.emit({"type": "status", "text": f"The {engine} engine is working. Decks and models take a few minutes."})
        proc = subprocess.Popen(cmd, cwd=str(C.APP), env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                text=True, encoding="utf-8", errors="replace")
        t0 = time.time()
        while proc.poll() is None:
            if self._stop:
                proc.kill()
                raise Interrupted()
            if time.time() - t0 > float(os.environ.get("MH_OFFICE_TIMEOUT_S", "1500")):
                proc.kill()
                return "ERROR: the engine took too long and was stopped.", False
            time.sleep(0.5)
        stdout, stderr = proc.communicate()
        try:
            res = json.loads(stdout[stdout.find("{"):]) if "{" in stdout else {}
        except ValueError:
            res = {}
        made = [str(p.relative_to(self.root)) for p in sorted(out.rglob("*"))
                if p.is_file() and not any(x.startswith(".") for x in p.relative_to(out).parts)]
        if not res:
            return f"ERROR: the {engine} engine failed.\n{stderr[-1500:]}", False
        steps = res.get("steps") or []
        answer = "\n".join(s.get("answer") or "" for s in steps).strip()
        checks = "passed" if res.get("ok") else "did not all pass"
        return (f"{engine} engine finished; its checks {checks}.\nFiles:\n" + "\n".join(f"- {m}" for m in made)
                + (f"\nEngine notes: {answer[:2000]}" if answer else "")), bool(made)

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
