"""Core tools: strict schemas, validation with precise errors, sandboxed execution in the run's workdir."""
from __future__ import annotations

import fnmatch
import hashlib
import json
import os
import re
import subprocess
import threading
import time
from pathlib import Path


def _bash():
    from meshpool_harness.config import find_bash
    return find_bash()

MAX_TOOL_CHARS = 8000
HEAD, TAIL = 3000, 3000
_CAP_LOCK = threading.Lock()

_RO_PROGS = {"cat", "head", "tail", "ls", "find", "grep", "egrep", "fgrep", "rg", "wc", "file", "stat", "which",
             "type", "echo", "printf", "pwd", "env", "printenv", "uname", "du", "df", "tree", "nl", "sort", "uniq",
             "cut", "tr", "diff", "cmp", "md5sum", "sha256sum", "sha1sum", "xxd", "od", "hexdump", "strings", "jq",
             "readlink", "realpath", "basename", "dirname", "id", "whoami", "date", "ps", "true", "column", "less",
             "awk", "sed", "git", "pip", "pip3", "python", "python3", "node", "npm", "cargo", "go", "java", "gcc",
             "rustc", "ldd", "nproc", "free", "hostname", "lsb_release", "getconf", "test", "["}
_RO_GIT = {"log", "show", "diff", "status", "grep", "ls-files", "blame", "rev-parse", "branch", "remote", "describe",
           "--no-pager", "-C", "shortlog", "cat-file", "ls-tree", "tag"}


def read_only(cmd: str) -> bool:
    """Conservative check that a shell command only reads (safe to run beside others). Unknown means False."""
    import re
    import shlex
    s = re.sub(r"\d?>\s*/dev/null|2>&1|>&2", " ", cmd)
    if re.search(r"[>`]|\$\(|<<|\bxargs\b|-exec\b|-delete\b|\bsudo\b", s):
        return False
    for seg in re.split(r"\|\|?|&&|;|\n", s):
        try:
            w = shlex.split(seg)
        except ValueError:
            return False
        while w and re.match(r"^[A-Za-z_]\w*=", w[0]):             # FOO=bar cmd
            w = w[1:]
        if not w:
            continue
        prog, rest = w[0].rsplit("/", 1)[-1], w[1:]
        if prog not in _RO_PROGS:
            return False
        if prog == "sed" and any(a.startswith("-i") or a == "--in-place" for a in rest):
            return False
        if prog == "awk" and "system" in seg:
            return False
        if prog == "git" and not (rest and rest[0] in _RO_GIT):
            return False
        if prog in ("pip", "pip3") and not (rest and rest[0] in ("show", "list", "freeze", "--version", "-V")):
            return False
        if prog in ("python", "python3", "node", "npm", "cargo", "go", "java", "gcc", "rustc") and not (
                rest and (rest[0] in ("--version", "-V", "version", "-version")
                          or rest[:3] in (["-m", "pip", "show"], ["-m", "pip", "list"]))):
            return False
    return True


def _fn(name, desc, props, req):
    return {"type": "function", "function": {"name": name, "description": desc, "parameters": {
        "type": "object", "properties": props, "required": req, "additionalProperties": False}}}


S = {"type": "string"}
I = {"type": "integer"}
SCHEMAS = {
    "read_file": _fn("read_file", "Read a file. Text and code come back with line numbers (use start/end, 1-based, "
                     "for large files). PDF, Word, PowerPoint, Excel, CSV, Power BI (.pbix/.pbit), OpenDocument, "
                     "notebooks and email files come back as readable text; images and scanned PDFs are read with "
                     "OCR in many languages. For a PDF, start/end pick pages.",
                     {"path": S, "start": I, "end": I}, ["path"]),
    "grep": _fn("grep", "Search file contents with a regular expression. Returns path:line:text.",
                {"pattern": S, "path": S, "glob": S}, ["pattern"]),
    "glob": _fn("glob", "List files matching a glob pattern relative to the workdir, e.g. '**/*.py'.",
                {"pattern": S}, ["pattern"]),
    "edit": _fn("edit", "Replace exactly one occurrence of `old` with `new` in a file. Fails if `old` is missing, "
                        "not unique, or the file changed since you last read it.",
                {"path": S, "old": S, "new": S}, ["path", "old", "new"]),
    "write_file": _fn("write_file", "Create or overwrite a file. Set append=true to add to the end (for large files written in parts).",
                      {"path": S, "content": S, "append": {"type": "boolean"}}, ["path", "content"]),
    "bash": _fn("bash", "Run a shell command in the workdir. Output is capped; full output is saved to a file.",
                {"command": S, "timeout_s": I}, ["command"]),
    "bash_many": _fn("bash_many", "Run several independent shell commands in ONE call (for example: read 3-5 file "
                                  "ranges, grep several patterns, check versions). Each runs on its own, even if an "
                                  "earlier one fails; you get every output, labelled.",
                     {"commands": {"type": "array", "items": S}, "timeout_s": I}, ["commands"]),
    "run_tests": _fn("run_tests", "Run a test or check command. Its exit code counts as evidence for finish.",
                     {"command": S, "timeout_s": I}, ["command"]),
    "finish": _fn("finish", "Finish the task. `evidence` must name checks you actually ran in this session "
                            "(the exact command) that passed after your last edit.",
                  {"answer": S, "evidence": {"type": "array", "items": S}, "artifacts": {"type": "array", "items": S}},
                  ["answer", "evidence"]),
    "todo": _fn("todo", "Write or update your todo list for this task: the concrete requirements and steps, each with "
                        "done true/false. Call it again to update. finish is refused while items are not done.",
                {"items": {"type": "array", "items": {"type": "object", "properties": {"text": S, "done": {"type": "boolean"}},
                                                      "required": ["text", "done"], "additionalProperties": False}}},
                ["items"]),
    "load_skill": _fn("load_skill", "Load detailed instructions for a skill listed in the skill index.",
                      {"name": S}, ["name"]),
    "need_context": _fn("need_context", "Ask the orchestrator for files or information outside your job packet "
                                        "instead of guessing.",
                        {"paths": {"type": "array", "items": S}, "reason": S}, ["reason"]),
}
CORE = ["read_file", "grep", "glob", "edit", "write_file", "bash", "run_tests", "finish"]


def schemas(names):
    return [SCHEMAS[n] for n in names]


_CTRL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f\ud800-\udfff]")


def clean(text: str) -> str:
    """Tool output can contain binary bytes (e.g. reading a .db file); upstream APIs reject NUL/control chars and
    lone surrogates with HTTP 400, which used to crash the run."""
    return _CTRL.sub("\ufffd", text) if isinstance(text, str) else text


class ToolError(Exception):
    pass


def validate(name: str, raw_args, allowed: list[str]) -> dict:
    if name not in allowed:
        raise ToolError(f"unknown tool '{name}'. Available: {', '.join(allowed)}")
    try:
        args = json.loads(raw_args) if isinstance(raw_args, str) else dict(raw_args or {})
    except json.JSONDecodeError as e:
        raise ToolError(f"arguments for {name} are not valid JSON ({e.msg} at char {e.pos}). "
                        f"Send a single JSON object.")
    if not isinstance(args, dict):
        raise ToolError(f"arguments for {name} must be a JSON object")
    sch = SCHEMAS[name]["function"]["parameters"]
    for k in sch["required"]:
        if k not in args:
            raise ToolError(f"{name}: missing required argument '{k}'")
    for k, v in args.items():
        if k not in sch["properties"]:
            raise ToolError(f"{name}: unknown argument '{k}'; allowed: {', '.join(sch['properties'])}")
        t = sch["properties"][k]["type"]
        ok = {"string": isinstance(v, str), "integer": isinstance(v, int) and not isinstance(v, bool),
              "boolean": isinstance(v, bool), "array": isinstance(v, list)}[t]
        if not ok:
            raise ToolError(f"{name}: argument '{k}' must be {t}, got {type(v).__name__}")
    return args


class Workspace:
    """Executes tools inside workdir, tracks reads/writes/commands for guards."""

    def __init__(self, workdir: str, run_dir: str, skills: dict[str, str] | None = None,
                 container: str | None = None, container_dir: str | None = None, mounted: bool = True):
        """container: run bash/run_tests natively inside this Docker container (cwd container_dir), so the model
        never writes nested `docker exec ... bash -lc '...'` quoting. mounted=True: workdir is bind-mounted at
        container_dir (file tools use the host copy); mounted=False: file tools read/write inside the container."""
        self.container, self.cdir, self.mounted = container, container_dir, mounted
        self.root = Path(workdir).resolve()
        self.run_dir = Path(run_dir)
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.skills = skills or {}
        self.seen: dict[str, str] = {}        # path -> content hash the agent last saw/wrote
        self.commands: list[dict] = []        # executed commands with exit codes and edit counter
        self.edits = 0
        self.written: set[str] = set()
        self.created: set[str] = set()        # files the agent created (new tests are allowed)
        self._n = 0

    # -- helpers
    def _path(self, p: str, write=False) -> Path:
        if self.container and self.mounted and self.cdir and os.path.isabs(p) and (p == self.cdir or p.startswith(self.cdir.rstrip("/") + "/")):
            p = str(self.root / os.path.relpath(p, self.cdir))     # container path -> host copy
        q = (self.root / p).resolve() if not os.path.isabs(p) else Path(p).resolve()
        if write and self.root not in q.parents and q != self.root:
            raise ToolError(f"writes must stay inside the workdir {self.root}; got {q}")
        return q

    @staticmethod
    def _hash(text: str) -> str:
        return hashlib.sha1(text.encode("utf-8", "replace")).hexdigest()

    def _cap(self, text: str, label: str) -> str:
        text = clean(text)
        if len(text) <= MAX_TOOL_CHARS:
            return text
        with _CAP_LOCK:                          # bash_many may run commands in threads (MH_PARALLEL_MANY)
            self._n += 1
            n = self._n
        f = self.run_dir / f"out_{n:03d}_{label}.txt"
        try:                                     # the task may delete .run (e.g. git clean): recreate it
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text(text)
        except OSError:
            return text[:HEAD] + f"\n... [{len(text) - HEAD - TAIL} chars truncated] ...\n" + text[-TAIL:]
        return (text[:HEAD] + f"\n... [{len(text) - HEAD - TAIL} chars truncated; full output in {f}] ...\n"
                + text[-TAIL:])

    # -- container (unmounted) file access
    def _cpath(self, p: str) -> str:
        return p if p.startswith("/") else f"{self.cdir.rstrip('/')}/{p}"

    def _cexec(self, args: list[str], input_text: str | None = None, timeout=120):
        cmd = ["docker", "exec"] + (["-i"] if input_text is not None else []) + ["-w", self.cdir, self.container, *args]
        return subprocess.run(cmd, input=input_text, capture_output=True, text=True, timeout=timeout, errors="replace")

    def _cread(self, path: str) -> str:
        r = self._cexec(["cat", self._cpath(path)])
        if r.returncode:
            raise ToolError(f"file not found or unreadable in container: {path}")
        return r.stdout

    def _cwrite(self, path: str, text: str, append=False):
        cp = self._cpath(path)
        r = self._cexec(["sh", "-c", f'mkdir -p "$(dirname "{cp}")" && cat {">>" if append else ">"} "{cp}"'], input_text=text)
        if r.returncode:
            raise ToolError(f"could not write {path} in container: {r.stderr[-200:]}")

    # -- tools
    def read_file(self, path, start=None, end=None):
        if self.container and not self.mounted:
            text = self._cread(path)
            self.seen[self._cpath(path)] = self._hash(text)
            lines = text.splitlines()
            s, e = max(1, start or 1), min(len(lines), end or len(lines))
            body = "\n".join(f"{i:>5}  {lines[i - 1]}" for i in range(s, e + 1))
            return self._cap(f"{path} lines {s}-{e} of {len(lines)}\n{body}", "read")
        p = self._path(path)
        if not p.exists():
            raise ToolError(f"file not found: {path}")
        if p.is_dir():
            raise ToolError(f"{path} is a directory; use glob")
        from meshpool_harness import docread
        if docread.is_image(p):
            return self._cap(docread.extract(p), "read")
        if docread.is_document(p) and p.suffix.lower() not in (".html", ".htm", ".csv", ".tsv", ".ipynb"):
            pages = f"{start or 1}-{end or ''}" if (start or end) else None
            return self._cap(docread.extract(p, pages=pages), "read")
        text = p.read_text(errors="replace")
        self.seen[str(p)] = self._hash(text)
        lines = text.splitlines()
        s = max(1, start or 1)
        e = min(len(lines), end or len(lines))
        body = "\n".join(f"{i:>5}  {lines[i - 1]}" for i in range(s, e + 1))
        return self._cap(f"{path} lines {s}-{e} of {len(lines)}\n{body}", "read")

    def grep(self, pattern, path=".", glob=None):
        if self.container and not self.mounted:
            inc = f"--include='{glob}'" if glob else ""
            out, _ = self._run(f"grep -rnE {inc} --exclude-dir=.git --exclude-dir=node_modules -e {json.dumps(pattern)} "
                               f"{json.dumps(path)} | head -400", 60, "grep", record=False)
            return out
        try:
            rx = re.compile(pattern)
        except re.error as e:
            raise ToolError(f"invalid regex: {e}")
        base = self._path(path)
        files = [base] if base.is_file() else [f for f in base.rglob("*") if f.is_file()]
        out = []
        for f in files:
            rel = f.relative_to(self.root) if self.root in f.parents else f
            if any(part in (".git", "node_modules", "__pycache__", ".venv") for part in f.parts):
                continue
            if glob and not fnmatch.fnmatch(str(rel), glob) and not fnmatch.fnmatch(f.name, glob):
                continue
            try:
                for i, line in enumerate(f.read_text(errors="ignore").splitlines(), 1):
                    if rx.search(line):
                        out.append(f"{rel}:{i}:{line[:300]}")
                        if len(out) >= 400:
                            break
            except (OSError, UnicodeDecodeError):
                continue
            if len(out) >= 400:
                out.append("... more matches truncated; narrow the pattern")
                break
        return self._cap("\n".join(out) or "no matches", "grep")

    def glob(self, pattern):
        if self.container and not self.mounted:
            out, _ = self._run(f"find . -path ./.git -prune -o -path './{pattern.lstrip('./')}' -print 2>/dev/null | head -2000"
                               if "/" in pattern else f"find . -name {json.dumps(pattern.split('/')[-1])} -not -path './.git/*' | head -2000",
                               60, "glob", record=False)
            return out
        res = sorted(str(p.relative_to(self.root)) for p in self.root.glob(pattern)
                     if ".git" not in p.parts and "node_modules" not in p.parts)
        return self._cap("\n".join(res[:2000]) or "no files", "glob")

    @staticmethod
    def _syntax_error(path: Path, text: str) -> str | None:
        """SWE-agent-style lint on edit: a Python edit that breaks parsing is rejected before it lands."""
        if path.suffix != ".py":
            return None
        try:
            compile(text, str(path), "exec", dont_inherit=True)
            return None
        except SyntaxError as e:
            return f"line {e.lineno}: {e.msg}"
        except (ValueError, TypeError):
            return None

    def edit(self, path, old, new):
        if self.container and not self.mounted:
            text, key = self._cread(path), self._cpath(path)
            if key not in self.seen:
                raise ToolError(f"read {path} before editing it")
            if self.seen[key] != self._hash(text):
                raise ToolError(f"{path} changed since you last read it (stale edit). Read it again, then edit.")
            n = text.count(old)
            if n != 1:
                raise ToolError(f"`old` text {'not found' if n == 0 else f'occurs {n} times'} in {path}; copy it exactly / add context")
            text = text.replace(old, new, 1)
            self._cwrite(path, text)
            self.seen[key] = self._hash(text)
            self.edits += 1
            self.written.add(key)
            return f"edited {path}"
        p = self._path(path, write=True)
        if not p.exists():
            raise ToolError(f"file not found: {path}")
        text = p.read_text(errors="replace")
        key = str(p)
        if key not in self.seen:
            raise ToolError(f"read {path} before editing it")
        if self.seen[key] != self._hash(text):
            raise ToolError(f"{path} changed since you last read it (stale edit). Read it again, then edit.")
        n = text.count(old)
        if n == 0:
            raise ToolError(f"`old` text not found in {path}. Copy it exactly from read_file output "
                            f"(without the line-number prefix).")
        if n > 1:
            raise ToolError(f"`old` text occurs {n} times in {path}; include more surrounding lines to make it unique")
        new_text = text.replace(old, new, 1)
        bad = self._syntax_error(p, new_text) if not self._syntax_error(p, text) else None
        if bad:
            raise ToolError(f"edit rejected: it would make {path} invalid Python ({bad}). The file is unchanged. "
                            f"Fix indentation/brackets in `new` and retry.")
        p.write_text(new_text)
        text = new_text
        self.seen[key] = self._hash(text)
        self.edits += 1
        self.written.add(key)
        return f"edited {path}"

    def write_file(self, path, content, append=False):
        if self.container and not self.mounted:
            if subprocess.run(["docker", "exec", self.container, "test", "-e", self._cpath(path)]).returncode != 0:
                self.created.add(self._cpath(path))
            self._cwrite(path, content, append)
            self.seen[self._cpath(path)] = self._hash(self._cread(path))
            self.edits += 1
            self.written.add(self._cpath(path))
            return f"wrote {path} ({len(content)} chars{', appended' if append else ''}) in the container"
        p = self._path(path, write=True)
        if not p.exists():
            self.created.add(str(p))
        p.parent.mkdir(parents=True, exist_ok=True)
        if append and p.exists():
            with p.open("a") as f:
                f.write(content)
        else:
            p.write_text(content)
        self.seen[str(p)] = self._hash(p.read_text(errors="replace"))
        self.edits += 1
        self.written.add(str(p))
        return f"wrote {path} ({len(content)} chars{', appended' if append else ''})"

    should_stop = None          # set by the agent: a callable that says the user pressed Stop

    def _proc(self, args, env, timeout_s, shell=False, encoding=None):
        """Run a command the user can stop at any moment: the whole process tree is killed on Stop or timeout."""
        import signal
        kw = {"creationflags": 0x00000200 | 0x08000000} if os.name == "nt" else {"start_new_session": True}
        p = subprocess.Popen(args, shell=shell, cwd=self.root, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             stdin=subprocess.DEVNULL, text=True, errors="replace", encoding=encoding, **kw)
        box: dict = {}

        def pump():
            box["out"], box["err"] = p.communicate()
        th = threading.Thread(target=pump, daemon=True)
        th.start()
        end = time.time() + timeout_s
        why = None
        while th.is_alive():
            th.join(0.2)
            if th.is_alive() and self.should_stop and self.should_stop():
                why = "stopped"
            elif th.is_alive() and time.time() > end:
                why = "timeout"
            if why:
                try:
                    if os.name == "nt":
                        subprocess.run(["taskkill", "/F", "/T", "/PID", str(p.pid)], capture_output=True,
                                       creationflags=0x08000000)
                    else:
                        os.killpg(p.pid, signal.SIGKILL)
                except Exception:  # noqa: BLE001
                    p.kill()
                th.join(5)
                break
        out, err = box.get("out") or "", box.get("err") or ""
        if why == "timeout":
            raise subprocess.TimeoutExpired(args, timeout_s, output=out)
        rc = 130 if why == "stopped" else p.returncode
        if why == "stopped":
            err = (err + "\n[stopped by the user]").lstrip()
        return subprocess.CompletedProcess(args, rc, out, err)

    def _run(self, command, timeout_s, kind, record=True):
        cap = 1800 if os.environ.get("MH_MODE") == "terminal" else 900   # terminal tasks: builds, training runs
        timeout_s = max(1, min(int(timeout_s or (300 if kind == 'check' else 180)), cap))
        t0 = time.time()
        try:
            from meshpool_harness import config as _C
            if _C.packaged():                                       # the user's own tools, not Meshpool's Python
                env = _C.user_env()
            else:
                env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")  # stale .pyc after same-size edits hides fixes
                proj = str(Path(__file__).resolve().parents[2])      # engine helper libraries importable anywhere
                env["PYTHONPATH"] = proj + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
            if self.container and not command.lstrip().startswith("docker "):
                r = subprocess.run(["docker", "exec", "-w", self.cdir, self.container, "bash", "-lc", command],
                                   capture_output=True, text=True, timeout=timeout_s, errors="replace")
            else:
                env.setdefault("PYTHONUTF8", "1")
                bash = _bash()
                if bash and os.name == "nt":                     # Git Bash: keep the working folder
                    env["CHERE_INVOKING"] = "1"
                    r = self._proc([bash, "-c", command], env, timeout_s, encoding="utf-8")
                elif bash:
                    r = self._proc([bash, "-c", command], env, timeout_s)
                else:
                    r = self._proc(command, env, timeout_s, shell=True)
            code, out = r.returncode, (r.stdout + ("\n" + r.stderr if r.stderr else ""))
        except subprocess.TimeoutExpired as e:
            code = 124
            out = ((e.stdout or "") if isinstance(e.stdout, str) else "") + f"\n[timed out after {timeout_s}s]"
        if not record:
            return self._cap(out or "no matches", kind), code
        self.commands.append({"cmd": command.strip(), "code": code, "kind": kind, "after_edit": self.edits,
                              "secs": round(time.time() - t0, 1), "tail": out[-600:]})
        try:                                     # full outputs: evidence for number tracing; the task may have
            self.run_dir.mkdir(parents=True, exist_ok=True)   # deleted .run (git clean), so recreate it
            with (self.run_dir / "commands.log").open("a") as f:
                f.write(f"\n$ {command.strip()}\n[exit {code}]\n{out[:200000]}\n")
        except OSError:
            pass
        return self._cap(f"[exit {code}]\n{out}", kind), code

    def bash(self, command, timeout_s=None):
        return self._run(command, timeout_s, "bash")[0]

    def bash_many(self, commands, timeout_s=None):
        """Several independent commands in one tool call: one turn instead of many (Terminus 2 / KIRA style).
        MH_PARALLEL_MANY=1: when every command is read-only (see read_only), run them at the same time; the turn then
        takes as long as the slowest command instead of the sum. Output order and format are unchanged."""
        cmds = [c for c in commands if isinstance(c, str) and c.strip()][:8]

        def one(cmd):
            if self.should_stop and self.should_stop():
                return f"$ {cmd}\n[not run: stopped by the user]"
            out, code = self._run(cmd, timeout_s, "bash")
            body = out.split("\n", 1)[1] if out.startswith("[exit ") else out
            if len(body) > 2500:
                body = body[:1200] + f"\n... [{len(body) - 2400} chars cut; run it alone for the full output] ...\n" + body[-1200:]
            return f"$ {cmd}\n[exit {code}]\n{body}"
        if os.environ.get("MH_PARALLEL_MANY") == "1" and len(cmds) > 1 and all(read_only(c) for c in cmds):
            from concurrent.futures import ThreadPoolExecutor
            with ThreadPoolExecutor(max_workers=min(4, len(cmds))) as ex:
                parts = list(ex.map(one, cmds))
        else:
            parts = [one(c) for c in cmds]
        return "\n\n".join(parts) or "no commands given"

    def run_tests(self, command, timeout_s=None):
        return self._run(command, timeout_s, "check")[0]

    def all_output(self) -> str:
        p = self.run_dir / "commands.log"
        return p.read_text(errors="replace") if p.exists() else ""

    def todo(self, items):
        self.todos = [{"text": str(i.get("text", ""))[:200], "done": bool(i.get("done"))} for i in items
                      if isinstance(i, dict)][:30]
        left = [t for t in self.todos if not t["done"]]
        return ("todo saved: " + f"{len(self.todos) - len(left)}/{len(self.todos)} done" + "".join(
            f"\n- [{'x' if t['done'] else ' '}] {t['text']}" for t in self.todos))

    def load_skill(self, name):
        if name not in self.skills:
            raise ToolError(f"unknown skill '{name}'. Available: {', '.join(self.skills) or 'none'}")
        return self.skills[name]
