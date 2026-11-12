"""Terminal front end for the Meshpool coding agent.

  meshpool                       start a session in the current folder
  meshpool --dir PATH            start in another folder
  meshpool --continue            resume the last session in this folder
  meshpool -p "request"          answer one request and exit (prints only the reply)
  meshpool --auto                allow edits and commands without asking
"""
from __future__ import annotations

import os
import sys
import threading
import time

from meshpool_harness import config as C

HELP = """Commands
  /help            this list
  /clear           start a new conversation (same folder)
  /auto            switch between asking before changes and allowing them
  /model [id]      show or change the model for this session
  /mcp             MCP servers and their tools
  /tokens          tokens used in this session
  /init            ask the agent to write MESHPOOL.md (project notes it reads every session)
  /exit            quit
Tips
  End a line with \\ to keep typing on the next line.
  Ctrl+C stops the current step. Ctrl+C twice at the prompt quits."""


class Style:
    def __init__(self, on: bool):
        self.on = on

    def _w(self, code, s):
        return f"\033[{code}m{s}\033[0m" if self.on else s

    def teal(self, s): return self._w("38;5;37", s)
    def amber(self, s): return self._w("38;5;172", s)
    def red(self, s): return self._w("38;5;167", s)
    def green(self, s): return self._w("38;5;71", s)
    def dim(self, s): return self._w("2", s)
    def bold(self, s): return self._w("1", s)


def _enable_ansi() -> bool:
    if os.environ.get("NO_COLOR") or not sys.stdout.isatty():
        return False
    if os.name == "nt":
        os.system("")                     # turns on ANSI escape handling in the Windows console
    return True


class Spinner:
    def __init__(self, st: Style, out):
        self.st, self.out = st, out
        self._t = None
        self._on = False
        self.lock = threading.Lock()

    def start(self, label="Thinking"):
        if not self.out.isatty() or self._on:
            return
        self._on, t0 = True, time.time()

        def spin():
            frames = "|/-\\"
            i = 0
            while self._on:
                with self.lock:
                    if self._on:
                        self.out.write(f"\r  {self.st.dim(frames[i % 4] + ' ' + label + f' ({int(time.time() - t0)} s)')}")
                        self.out.flush()
                i += 1
                time.sleep(0.2)
        self._t = threading.Thread(target=spin, daemon=True)
        self._t.start()

    def stop(self):
        if not self._on:
            return
        with self.lock:
            self._on = False
            self.out.write("\r\033[K" if self.st.on else "\r" + " " * 60 + "\r")
            self.out.flush()


class Terminal:
    def __init__(self, print_mode: bool = False):
        self.st = Style(_enable_ansi())
        self.print_mode = print_mode
        self.out = sys.stdout
        self.info = sys.stderr if print_mode else sys.stdout
        self.spin = Spinner(self.st, self.info)
        self.in_text = False
        self.agent = None

    def p(self, s="", end="\n", to=None):
        self.spin.stop()
        (to or self.info).write(s + end)
        (to or self.info).flush()

    # agent callbacks
    def emit(self, ev: dict):
        t, st = ev.get("type"), self.st
        if t == "model_start":
            self.spin.start("Thinking")
        elif t == "text_delta":
            self.spin.stop()
            if not self.in_text:
                self.in_text = True
                if not self.print_mode:
                    self.out.write("\n")
            self.out.write(ev["text"])
            self.out.flush()
        elif t == "stream_reset":
            if self.in_text:
                self.p(st.dim("\n[connection dropped; the reply restarts below]"))
            self.in_text = False
        elif t == "assistant":
            self.spin.stop()
            if not ev.get("streamed") and ev.get("text"):
                self.p("\n" + ev["text"], to=self.out)
            elif self.in_text:
                self.out.write("\n")
                self.out.flush()
            self.in_text = False
        elif t == "tool_start":
            name = ev["name"]
            label = name.split("__", 2)[-1] + st.dim(f" ({name.split('__')[1]})") if ev.get("mcp") else name
            self.p(f"\n{st.teal('●')} {st.bold(label)}  {ev.get('summary', '')}")
        elif t == "tool_end":
            out = (ev.get("output") or "").rstrip()
            if ev.get("denied"):
                self.p("  " + st.amber("└ denied"))
                return
            lines = out.splitlines()
            if ev["name"] in ("read_file",):
                head = lines[:1]
            elif ev["name"] in ("edit", "write_file"):
                head = lines[:1]
            else:
                head = lines[:5]
            color = st.dim if ev.get("ok") else st.red
            for i, line in enumerate(head):
                self.p(("  └ " if i == 0 else "    ") + color(line[:220]))
            if len(lines) > len(head):
                self.p("    " + st.dim(f"... {len(lines) - len(head)} more lines"))
        elif t == "status":
            self.p(st.dim("  " + ev["text"]))
        elif t == "plan":
            mark = {"completed": st.green("[x]"), "in_progress": st.teal("[>]")}
            self.p("")
            for e in ev.get("entries") or []:
                txt = e.get("content", "")
                self.p(f"  {mark.get(e.get('status'), st.dim('[ ]'))} " + (st.dim(txt) if e.get("status") == "completed" else txt))
        elif t == "error":
            self.p(st.red("\n" + ev["text"]))
        elif t == "interrupted":
            self.p(st.amber("\n" + ev["text"]))
        elif t == "turn_end":
            self.spin.stop()
            u, s = ev["turn"], ev["session"]
            if u["calls"] and not self.print_mode:
                pct = round(100 * u["cached"] / u["in"]) if u["in"] else 0
                self.p(st.dim(f"\n  {u['in']:,} in ({pct}% cached), {u['out']:,} out this turn. "
                              f"Session: {s['in']:,} in, {s['out']:,} out."))

    def approve(self, kind: str, title: str, detail: str):
        st = self.st
        self.spin.stop()
        if not sys.stdin.isatty():
            return "no", "No terminal to ask in. Run with --auto."
        self.p("")
        self.p(st.amber(title))
        for line in detail.splitlines()[:80]:
            if kind == "edit" and line.startswith("+") and not line.startswith("+++"):
                self.p("  " + st.green(line))
            elif kind == "edit" and line.startswith("-") and not line.startswith("---"):
                self.p("  " + st.red(line))
            else:
                self.p("  " + (line if kind == "bash" else st.dim(line)))
        what = {"edit": "file edits", "bash": "commands", "mcp": "this MCP tool"}[kind]
        while True:
            try:
                a = input(f"  {st.bold('y')} yes   {st.bold('a')} yes, and allow {what} for this session   "
                          f"{st.bold('n')} no > ").strip().lower()
            except EOFError:
                return "no", ""
            if a in ("y", "yes", ""):
                return "yes", ""
            if a in ("a", "always"):
                return "always", ""
            if a in ("n", "no"):
                try:
                    note = input("  What should Meshpool do instead? (Enter to skip) > ").strip()
                except EOFError:
                    note = ""
                return "no", note


def ask_key(force: bool = False) -> bool:
    """First run: ask for the API key in the terminal and save it to meshpool.config."""
    import getpass
    C.ensure_home() if os.environ.get("MESHPOOL_HOME") else None
    C.load_into_env()
    if not force and (os.environ.get("MESHPOOL_API_KEY") or os.environ.get("OPENAI_API_KEY")):
        return True
    if not sys.stdin.isatty():
        print(f"No API key. Run: meshpool login   (or add MESHPOOL_API_KEY=... to {C.config_path()})", file=sys.stderr)
        return False
    print("\n  Meshpool needs your API key once. It is saved to " + str(C.config_path()))
    try:
        key = getpass.getpass("  Paste your Meshpool API key (input is hidden): ").strip()
    except (EOFError, KeyboardInterrupt):
        return False
    if not key:
        return False
    C.write({"MESHPOOL_API_KEY": key})
    os.environ["MESHPOOL_API_KEY"] = key
    from meshpool_harness.cli import ping
    r = ping()
    print(("  Connected. " if r["ok"] else "  Saved, but the test call failed: ") + (r["detail"] if not r["ok"] else
          f"{r['model']} answered in {r['seconds']} s."))
    return True


def _banner(term: Terminal, agent, mcp):
    st = term.st
    model = os.environ.get("MH_MODEL") or "deepseek-v4.1-flash"
    term.p("")
    term.p(f"  {st.teal('◆')} {st.bold('Meshpool')}  {st.dim('coding agent')}")
    term.p(f"  {st.dim('Model')}   {model}")
    term.p(f"  {st.dim('Folder')}  {agent.root}")
    if mcp:
        parts = []
        for s in mcp.status():
            if s["status"] == "connected":
                parts.append(st.green(s["name"]) + st.dim(f" ({len(s['tools'])} tools)"))
            elif s["status"] == "error":
                parts.append(st.red(s["name"]) + st.dim(" (failed, see /mcp)"))
        if mcp.config_error:
            parts.append(st.red(mcp.config_error))
        term.p(f"  {st.dim('MCP')}     " + (", ".join(parts) if parts else st.dim("none enabled (edit mcp.json)")))
    mode = "allowed without asking" if agent.mode == "auto" else "asks before edits and commands"
    term.p(f"  {st.dim('Changes')} {mode}")
    if len(agent.messages) > 1:
        term.p(f"  {st.dim('Resumed')} {agent.title} ({len(agent.messages) - 1} messages)")
    term.p(st.dim("  Type a request. /help for commands."))


def _mcp_status(term: Terminal, mcp):
    st = term.st
    if not mcp:
        term.p("MCP is off.")
        return
    from meshpool_harness.agent.mcp import config_file
    term.p(f"Config: {config_file()}")
    if mcp.config_error:
        term.p(st.red(mcp.config_error))
    for s in mcp.status():
        mark = {"connected": st.green("connected"), "error": st.red("failed"),
                "disabled": st.dim("disabled")}.get(s["status"], s["status"])
        term.p(f"  {st.bold(s['name'])}  {mark}  {st.dim(s['transport'])} {st.dim(s['server'])}")
        if s["error"]:
            term.p("    " + st.red(s["error"]))
        if s["tools"]:
            term.p("    " + st.dim(", ".join(s["tools"])))


def _read(term: Terminal) -> str | None:
    st = term.st
    lines = []
    while True:
        try:
            line = input(("\n" + st.teal("> ")) if not lines else st.teal(". "))
        except EOFError:
            return None
        if line.endswith("\\"):
            lines.append(line[:-1])
            continue
        lines.append(line)
        return "\n".join(lines).strip()


def main(workdir: str | None = None, prompt: str | None = None, auto: bool = False, resume: bool = False,
         session: str | None = None, no_mcp: bool = False, first: str | None = None) -> int:
    from meshpool_harness.agent.core import Agent
    from meshpool_harness.agent.mcp import Manager
    C.load_into_env()
    try:
        import readline  # noqa: F401  (arrow keys and history at the prompt on macOS and Linux)
    except ImportError:
        pass
    term = Terminal(print_mode=prompt is not None)
    st = term.st
    wd = workdir or os.getcwd()
    if not (os.environ.get("MESHPOOL_API_KEY") or os.environ.get("OPENAI_API_KEY")) and not ask_key():
        term.p(st.red(f"No API key yet. Run meshpool login, or add MESHPOOL_API_KEY=... to {C.config_path()}"))
        return 1
    mcp = None
    if not no_mcp:
        if prompt is None:
            term.spin.start("Starting MCP servers")
        mcp = Manager(roots=[wd]).start(timeout=float(os.environ.get("MH_MCP_START_S", "60")))
        term.spin.stop()
    mode = "auto" if auto else None
    kw = dict(mcp=mcp, emit=term.emit, approve=term.approve, mode=mode)
    agent = (Agent.resume(wd, session, **kw) if (resume or session) else None)
    if (resume or session) and agent is None:
        term.p(st.amber("No earlier session for this folder. Starting a new one."))
    try:
        agent = agent or Agent(wd, **kw)
    except ValueError as e:
        term.p(st.red(str(e)))
        return 1
    term.agent = agent
    try:
        if prompt is not None:
            agent.send(prompt)
            return 0
        _banner(term, agent, mcp)
        last_ctrl_c = 0.0
        if first:
            term.p("\n" + st.teal("> ") + first)
            try:
                agent.send(first)
            except KeyboardInterrupt:
                term.p(st.amber("\n  Stopped."))
        while True:
            try:
                text = _read(term)
            except KeyboardInterrupt:
                if time.time() - last_ctrl_c < 2:
                    break
                last_ctrl_c = time.time()
                term.p(st.dim("\n  Press Ctrl+C again to quit."))
                continue
            if text is None:
                break
            if not text:
                continue
            if text.startswith("/"):
                cmd, _, arg = text[1:].partition(" ")
                cmd = cmd.lower()
                if cmd in ("exit", "quit", "q"):
                    break
                if cmd == "help":
                    term.p(HELP)
                elif cmd == "clear":
                    agent.reset()
                    term.p(st.dim("  New conversation. Same folder and settings."))
                elif cmd == "auto":
                    agent.mode = "ask" if agent.mode == "auto" else "auto"
                    term.p(st.dim("  Edits and commands are now " +
                                  ("allowed without asking." if agent.mode == "auto" else "asked first.")))
                elif cmd == "model":
                    if arg.strip():
                        os.environ["MH_MODEL"] = arg.strip()
                    term.p(st.dim(f"  Model: {os.environ.get('MH_MODEL') or 'deepseek-v4.1-flash'}"))
                elif cmd == "mcp":
                    _mcp_status(term, mcp)
                elif cmd in ("tokens", "cost"):
                    u = agent.usage
                    pct = round(100 * u["cached"] / u["in"]) if u["in"] else 0
                    term.p(f"  {u['calls']} model calls, {u['in']:,} input tokens ({pct}% from cache), "
                           f"{u['out']:,} output tokens.")
                elif cmd == "init":
                    agent.send("Look at this project (layout, languages, how to install, build, run and test it, "
                               "and its conventions). Then write MESHPOOL.md in the project root with short notes a "
                               "new engineer would need: the exact commands, the main folders, and the rules to "
                               "follow. Keep it under 60 lines.")
                else:
                    term.p(st.amber(f"  Unknown command /{cmd}. Type /help."))
                continue
            try:
                agent.send(text)
            except KeyboardInterrupt:
                term.p(st.amber("\n  Stopped."))
    finally:
        term.spin.stop()
        if mcp:
            mcp.close()
    if prompt is None:
        term.p(st.dim(f"  Session saved. Resume it with: meshpool --continue"))
    return 0
