"""Terminal front end for the Meshpool coding agent.

  meshpool                       start a session in the current folder
  meshpool --dir PATH            start in another folder
  meshpool --continue            resume the last session in this folder
  meshpool -p "request"          answer one request and exit (prints only the reply)
  meshpool --auto                allow edits and commands without asking
"""
from __future__ import annotations

import os
import re
import sys
import threading
import time

from meshpool_harness import config as C
from meshpool_harness.agent import brand as B

HELP = """Commands
  /help            this list
  /clear           start a new conversation (same folder)
  /auto            switch between asking before changes and allowing them
  /model [id]      show or change the model: an id from your key, or chatgpt:<id>, anthropic:<id>, gemini:<id>
  /mcp             MCP servers and their tools
  /connect [app]   connect an app (Notion, Gmail, Slack...) or an MCP server by URL; no app lists them
  /tokens          tokens used in this session
  /init            ask the agent to write MESHPOOL.md (project notes it reads every session)
  /import          bring your old chats, Claude Code and Codex sessions, and context from ChatGPT or Claude
  /setup           change the provider: your key, another key, or a ChatGPT or Claude subscription
  /exit            quit
Tips
  End a line with \\ to keep typing on the next line.
  Ctrl+C stops the current step. Ctrl+C twice at the prompt quits."""


class Style:
    def __init__(self, depth: int):
        self.depth = depth                # 0 (no color), 256 or 24 (24-bit), from brand.depth()
        self.on = bool(depth)

    def _w(self, code, s):
        return f"\033[{code}m{s}\033[0m" if self.on else s

    def blue(self, s):                    # the Meshpool blue
        return B.paint(s, B.POOL if self.depth == 24 else B.SKY, self.depth) if self.on else s

    def amber(self, s): return self._w("38;5;172", s)
    def red(self, s): return self._w("38;5;167", s)
    def green(self, s): return self._w("38;5;71", s)
    def dim(self, s): return self._w("2", s)
    def bold(self, s): return self._w("1", s)


def _enable_ansi() -> int:
    return B.depth(sys.stdout)


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
        self.failed = False               # an error or a stop in the turn (meshpool -p then exits non-zero)
        self.stopping = False             # Ctrl+C stopped the turn: questions still arriving are answered "no"
        self.idle = threading.Event()     # cleared while a question is waiting for an answer at the terminal
        self.idle.set()

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
            self.p(f"\n{st.blue('●')} {st.bold(label)}  {ev.get('summary', '')}")
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
            mark = {"completed": st.green("[x]"), "in_progress": st.blue("[>]")}
            self.p("")
            for e in ev.get("entries") or []:
                txt = e.get("content", "")
                self.p(f"  {mark.get(e.get('status'), st.dim('[ ]'))} " + (st.dim(txt) if e.get("status") == "completed" else txt))
        elif t == "error":
            self.failed = True
            self.p(st.red("\n" + ev["text"]))
        elif t == "interrupted":
            self.failed = True
            self.p(st.amber("\n" + ev["text"]))
        elif t == "turn_end":
            self.spin.stop()
            u, s = ev["turn"], ev["session"]
            if u["calls"] and not self.print_mode:
                pct = round(100 * u["cached"] / u["in"]) if u["in"] else 0
                self.p(st.dim(f"\n  {u['in']:,} in ({pct}% cached), {u['out']:,} out this turn. "
                              f"Session: {s['in']:,} in, {s['out']:,} out."))

    def approve(self, kind: str, title: str, detail: str, options=None):
        st = self.st
        self.spin.stop()
        if not sys.stdin.isatty():
            return "no", "No terminal to ask in. Run with --auto."
        if self.stopping:
            return "no", ""
        self.idle.clear()
        try:
            choice, note = self._approve(kind, title, detail)
        finally:
            self.idle.set()
        if self.stopping:                 # Ctrl+C while the question was open: the turn is over
            return "no", ""
        if choice == "always" and options:
            # an ACP harness (options from its permission request): "always" is its own allow-always option
            return next((o["id"] for o in options if o.get("kind") == "allow_always"), "yes"), note
        return choice, note

    def _approve(self, kind: str, title: str, detail: str):
        st = self.st
        self.p("")
        self.p(st.amber(title))
        for line in detail.splitlines()[:80]:
            if kind == "edit" and line.startswith("+") and not line.startswith("+++"):
                self.p("  " + st.green(line))
            elif kind == "edit" and line.startswith("-") and not line.startswith("---"):
                self.p("  " + st.red(line))
            else:
                self.p("  " + (line if kind == "bash" else st.dim(line)))
        what = {"edit": "file edits", "bash": "commands", "mcp": "this MCP tool"}.get(kind, "this")
        while True:
            try:
                a = input(B.prompt(f"  {st.bold('y')} yes   {st.bold('a')} yes, and allow {what} for this session   "
                                   f"{st.bold('n')} no (Enter: no) > ")).strip().lower()
            except EOFError:
                return "no", ""
            if self.stopping:
                return "no", ""
            if a == "":                   # Enter alone never approves a change
                return "no", ""
            if a in ("y", "yes"):
                return "yes", ""
            if a in ("a", "always"):
                return "always", ""
            if a in ("n", "no"):
                try:
                    note = input("  What should Meshpool do instead? (Enter to skip) > ").strip()
                except EOFError:
                    note = ""
                return "no", note


    def ask_questions(self, qid: str, questions: list):
        """ask_user in the terminal: numbered options per question, Enter takes the recommended one, any other
        text is the user's own answer. No terminal: None (the agent goes on with its recommendations)."""
        st = self.st
        self.spin.stop()
        if not sys.stdin.isatty():
            return None
        answers = []
        for q in questions:
            self.p("")
            self.p(st.amber(f"{q['header']}: {q['question']}") + (st.dim("  (several: 1,3)") if q.get("multi") else ""))
            rec = [str(i) for i, o in enumerate(q["options"], 1) if o.get("recommended")]
            for i, o in enumerate(q["options"], 1):
                self.p(f"  {st.bold(str(i))} {o['label']}" + (st.green("  (recommended)") if o.get("recommended") else "")
                       + (st.dim("  " + o["description"]) if o.get("description") else ""))
            try:
                a = input(f"  Pick{' (Enter: ' + ','.join(rec) + ')' if rec else ''}, or type your own answer > ").strip()
            except EOFError:
                return None
            nums = [int(x) for x in re.split(r"[ ,]+", a or ",".join(rec)) if x.isdigit()]
            if a and not nums or any(not 1 <= n <= len(q["options"]) for n in nums):
                answers.append({"selected": [], "other": a})
            else:
                answers.append({"selected": [q["options"][n - 1]["label"] for n in nums], "other": ""})
        return answers


def ask_key(force: bool = False) -> bool:
    """Ask for the API key in the terminal, check it, and save it to meshpool.config (meshpool login)."""
    C.ensure_home() if os.environ.get("MESHPOOL_HOME") else None
    C.load_into_env()
    if not force and (os.environ.get("MESHPOOL_API_KEY") or os.environ.get("OPENAI_API_KEY")):
        return True
    if not sys.stdin.isatty():
        print(f"No API key. Run: meshpool login   (or add MESHPOOL_API_KEY=... to {C.config_path()})", file=sys.stderr)
        return False
    from meshpool_harness.agent import onboard
    return onboard.key_step(Style(_enable_ansi()), header=True)


def logo_lines(color: bool) -> list[str]:
    """The Meshpool mark and wordmark, for terminals."""
    return B.banner(B.depth() if color else 0)


def _banner(term: Terminal, agent, mcp, logo: bool = True):
    st = term.st
    acp = hasattr(agent, "manifest")
    name = agent.manifest.get("name") if acp else "Meshpool Code"
    runs_on = (agent.manifest.get("runs_on") or "") if acp else ""
    term.p("")
    if logo:
        for row in B.banner(st.depth):
            term.p(row)
        term.p("")
    term.p(f"  {st.dim('Agent')}   {name}" + (st.dim(f" on {runs_on}") if runs_on else ""))
    if not acp:
        from meshpool_harness import providers as PV
        prov, bare = PV.split(agent.current_model() if hasattr(agent, "current_model") else "")
        term.p(f"  {st.dim('Model')}   {bare}" + st.dim(f" on {PV.name(prov)}"))
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


def _context() -> str:
    """The user's instructions and memory (shared with the app), sent out of sight with a session's first message.
    The same block the app sends (assistant.context_block), so the two never drift apart."""
    try:
        from meshpool_harness import assistant as AS
        return AS.context_block() or ""
    except Exception:  # noqa: BLE001
        return ""


def _stop_turn(term: Terminal, agent, wait: float = 4.0):
    """Ctrl+C during a turn: stop the agent itself (an ACP harness keeps running otherwise, and its permission
    questions would race the prompt for input), let an open question finish, and wait briefly for the turn to end."""
    term.stopping = True
    term.spin.stop()
    try:
        agent.stop()
    except Exception:  # noqa: BLE001
        pass
    try:
        if not term.idle.is_set():        # a question is waiting for a line in another thread: let it take one
            term.p(term.st.dim("  Press Enter to dismiss the open question."))
            term.idle.wait()
        # an ACP harness answers its open session/prompt request once it has cancelled
        cv, pending, rid = (getattr(agent, a, None) for a in ("_cv", "_pending", "_id"))
        if cv is not None and isinstance(pending, dict) and rid:
            end = time.time() + wait
            with cv:
                while rid not in pending and getattr(agent, "_alive", False) and time.time() < end:
                    cv.wait(0.2)
                answered = pending.pop(rid, None) is not None
            if not answered and getattr(agent, "_alive", False) and hasattr(agent, "_hard_restart"):
                agent._hard_restart()     # it ignored the cancel: end it; the next message restarts it
    except KeyboardInterrupt:
        pass
    except Exception:  # noqa: BLE001
        pass


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
            if not lines:
                print(flush=True)             # a separate newline: readline garbles colors in a multi-line prompt
            line = input(B.prompt(st.blue(">") + " " if not lines else st.blue(".") + " "))
        except EOFError:
            return None
        if line.endswith("\\"):
            lines.append(line[:-1])
            continue
        lines.append(line)
        return "\n".join(lines).strip()


def main(workdir: str | None = None, prompt: str | None = None, auto: bool = False, resume: bool = False,
         session: str | None = None, no_mcp: bool = False, first: str | None = None, onboarded: bool = False) -> int:
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
    from meshpool_harness import harnesses as HR
    if not HR.ready():
        if prompt is None and sys.stdin.isatty():
            from meshpool_harness.agent import onboard
            folder = onboard.run(wd, st)          # first run: key, folder, setup, then a short tour
            if folder is None:
                return 1
            wd, onboarded = str(folder), True
        elif not ask_key():
            term.p(st.red(f"No API key yet. Run meshpool login, or add MESHPOOL_API_KEY=... to {C.config_path()}"))
            return 1
    # The terminal runs Meshpool Code when it can run on the chosen provider; a Claude subscription runs Claude Code
    # (the only agent Anthropic lets it run), and the official Codex or Claude Code also when set as the default.
    hid = HR.default_for("code")
    try:
        h = HR.get(hid)
    except (KeyError, ValueError) as e:
        term.p(st.red(str(e)))
        return 1
    if not h.get("ready", True):
        term.p(st.red(f"{h['name']} cannot run yet. {h.get('setup') or ''} Run: meshpool setup"))
        return 1
    sub = h["id"] if h.get("type") == "acp" else None
    mcp = None
    if not no_mcp and not sub:                   # an ACP agent gets the MCP servers through ACP itself
        if prompt is None:
            term.spin.start("Starting MCP servers")
        mcp = Manager(roots=[wd]).start(timeout=float(os.environ.get("MH_MCP_START_S", "60")))
        term.spin.stop()
    mode = "auto" if auto else None
    kw = dict(mcp=mcp, emit=term.emit, approve=term.approve, mode=mode)
    if sub:
        from meshpool_harness.harnesses.acp import ACPAgent
        if resume or session:
            term.p(st.amber(f"Resuming is not available with {h['name']} in the terminal yet. Starting a new session."))
        try:
            agent = ACPAgent(h, wd, emit=term.emit, approve=term.approve, mode=mode)
        except (ValueError, KeyError) as e:
            term.p(st.red(str(e)))
            return 1
    else:
        agent = (Agent.resume(wd, session, **kw) if (resume or session) else None)
        if (resume or session) and agent is None:
            term.p(st.amber("No earlier session for this folder. Starting a new one."))
        try:
            agent = agent or Agent(wd, **kw)
        except ValueError as e:
            term.p(st.red(str(e)))
            return 1
    term.agent = agent
    agent.ask = term.ask_questions               # ask_user questions are answered in the terminal
    ctx = [True]                                 # your instructions and memory go with the first message

    def send(text: str):
        if ctx[0]:
            ctx[0] = False
            block = _context()
            if block:
                text += "\n\n\u200b" + block
        term.stopping = term.failed = False
        try:
            return agent.send(text)
        except KeyboardInterrupt:
            term.p(st.amber("\n  Stopped."))
            term.failed = True
            _stop_turn(term, agent)
    try:
        if prompt is not None:
            send(prompt)
            return 1 if term.failed else 0
        _banner(term, agent, mcp, logo=not onboarded)
        last_ctrl_c = 0.0
        if first:
            term.p("\n" + st.blue("> ") + first)
            send(first)
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
                    ctx[0] = True
                    term.p(st.dim("  New conversation. Same folder and settings."))
                elif cmd == "auto":
                    agent.mode = "ask" if agent.mode == "auto" else "auto"
                    if hasattr(agent, "apply_mode"):   # Codex & co. have their own modes: switch them too
                        try:
                            agent.apply_mode()
                        except Exception as e:  # noqa: BLE001
                            term.p(st.red(f"  Couldn't switch the harness's own mode: {e}"))
                    term.p(st.dim("  Edits and commands are now " +
                                  ("allowed without asking." if agent.mode == "auto" else "asked first.")))
                elif cmd == "model":
                    if sub:
                        if arg.strip():
                            agent.set_model(arg.strip())
                        term.p(st.dim(f"  Model: {agent.current_model()}"))
                        continue
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
                elif cmd == "connect":
                    from meshpool_harness.agent import connect_cli
                    try:
                        if not arg.strip():
                            connect_cli.listing(st)
                        else:
                            ok, cid = connect_cli.connect(arg.strip(), st=st)
                            if ok and mcp:                    # its tools work in this session at once
                                from meshpool_harness import mcp_apps as CN
                                spec = CN.servers().get(cid)
                                if spec:
                                    mcp.add(cid, spec)
                            elif ok:
                                term.p(st.dim("  New sessions use it."))
                    except KeyboardInterrupt:
                        term.p(st.amber("\n  Cancelled."))
                    except Exception as e:  # noqa: BLE001  (a network error must not end the session)
                        term.p(st.red(f"  Couldn't connect: {e}"))
                elif cmd == "import":
                    from meshpool_harness.agent import onboard
                    onboard.context_step(st)
                    ctx[0] = True                  # the next message carries the new context
                elif cmd == "setup":
                    from meshpool_harness.agent import onboard
                    if onboard.provider_step(st, 1):
                        term.p(st.dim("  Saved. Restart meshpool to use it (type /exit, then meshpool)."))
                elif cmd == "init":
                    send("Look at this project (layout, languages, how to install, build, run and test it, "
                               "and its conventions). Then write MESHPOOL.md in the project root with short notes a "
                               "new engineer would need: the exact commands, the main folders, and the rules to "
                               "follow. Keep it under 60 lines.")
                else:
                    term.p(st.amber(f"  Unknown command /{cmd}. Type /help."))
                continue
            send(text)
    finally:
        term.spin.stop()
        if mcp:
            mcp.close()
        if sub:
            agent.close()
    if prompt is None and not sub:
        term.p(st.dim(f"  Session saved. Resume it with: meshpool --continue"))
    return 0
