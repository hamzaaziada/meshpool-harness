"""Observability tools (MH_OBSERVE=1): see the bug before editing it.

Measured on 47 of our Verified transcripts (harness-lab/findings/007): the model made 5 calls before its first edit,
with 0.5 runs of a test or repro, then 27 calls after it (7.2 edits, 3.4 failing test runs, 9.7 re-reads). It edits
nearly blind and then iterates. These tools put the failure in front of it in one call:

  probe <command>  run a test or script; return a digest: failing tests, the exception, and the failing frames in the
                   project's own code with local variable values (pytest --tb=long --showlocals), library frames
                   dropped. Counts as a check for finish.
  trace <command>  run a Python test or script under a profiler limited to the project's files; list the project
                   functions that ran, in first-call order with call counts, then the command's own output tail.
                   Fault localization (the idea behind AutoCodeRover and Agentless): where to look before editing.

Both work through Workspace._run, so they run inside the task's container when there is one. The tracer runs with the
project's own Python (3.6 or newer); it needs no packages.
"""
from __future__ import annotations

import re
import shlex

_LIB = re.compile(r"(site-packages|dist-packages|/lib/python\d|/usr/lib/|<frozen|_pytest/|pluggy/)")
_PYTEST = re.compile(r"(?<![\w/.-])((?:python[\d.]*\s+-m\s+)?pytest)(?=\s|$)")

TRACER = r'''
import collections, os, runpy, sys, threading
root = os.getcwd()
skip = ("site-packages", "dist-packages", "/lib/python", "<", "/usr/lib/")
order, calls = collections.OrderedDict(), collections.Counter()
def prof(frame, event, arg):
    if event != "call":
        return
    co = frame.f_code
    f = co.co_filename
    if not f.startswith(root) or any(s in f for s in skip) or co.co_name == "<module>":
        return
    k = (os.path.relpath(f, root), co.co_firstlineno, getattr(co, "co_qualname", co.co_name))
    calls[k] += 1
    order.setdefault(k, None)
mode, target, argv = sys.argv[1], sys.argv[2], sys.argv[3:]
sys.argv = [target] + argv
code = 0
sys.setprofile(prof); threading.setprofile(prof)
try:
    if mode == "m":
        runpy.run_module(target, run_name="__main__", alter_sys=True)
    else:
        sys.path.insert(0, os.path.dirname(os.path.abspath(target)))
        runpy.run_path(target, run_name="__main__")
except SystemExit as e:
    code = e.code if isinstance(e.code, int) else (0 if e.code is None else 1)
except BaseException:
    import traceback; traceback.print_exc(); code = 1
finally:
    sys.setprofile(None); threading.setprofile(None)
    keys = list(order)
    tests = [k for k in keys if "test" in k[0].split("/")[-1] or "/tests/" in "/" + k[0]]
    src = [k for k in keys if k not in tests]
    out = sys.__stdout__
    out.write("\n=== trace: %d project functions ran (%d in tests); source functions in first-call order ===\n"
              % (len(keys), len(tests)))
    for k in src[:80]:
        out.write("%s:%d %s  x%d\n" % (k[0], k[1], k[2], calls[k]))
    if len(src) > 80:
        out.write("... %d more\n" % (len(src) - 80))
    out.flush()
sys.exit(code)
'''


def probe_command(command: str) -> str:
    """Add pytest's long tracebacks with local values when the command runs pytest and sets no --tb itself."""
    if "--tb" in command or not _PYTEST.search(command):
        return command
    return _PYTEST.sub(lambda m: m.group(1) + " --tb=long --showlocals -rfE -p no:cacheprovider", command, count=1)


def digest(out: str, limit: int = 6000) -> str:
    """Keep what explains a failure: summary lines, failure sections with project frames and locals, a Python
    traceback's project frames. Library frames are dropped. Passing runs keep their summary and tail."""
    lines = out.splitlines()
    head = lines[0] if lines and lines[0].startswith("[exit ") else ""
    body = lines[1:] if head else lines
    text = "\n".join(body)
    if head == "[exit 0]":
        summ = [l for l in body if re.search(r"\d+ (passed|failed|error)", l)]
        return "\n".join([head, "passed."] + summ[-2:] + ["--- tail ---"] + body[-15:])[:limit]
    parts = [head] if head else []
    summary = [l for l in body if re.match(r"(FAILED|ERROR) ", l) or re.search(r"=+ .*\d+ (failed|error)", l)]
    if summary:
        parts.append("--- summary ---")
        parts += summary[:20]
    sections = re.split(r"\n_{5,} (.+?) _{5,}\n", "\n" + text)
    shown = 0
    if len(sections) > 1:                                   # pytest failure sections
        for i in range(1, len(sections) - 1, 2):
            name, sec = sections[i], sections[i + 1]
            sec = re.split(r"\n-+ Captured |\n=+ ", sec)[0]
            keep, frames = [], re.split(r"\n(?=\S[^\n]*\.py:\d+: )", sec)
            for fr in frames:
                loc = re.search(r"^(\S[^\n]*\.py):\d+: ", fr, re.M)
                if loc and _LIB.search(loc.group(1)):
                    continue
                keep.append(fr.strip("\n"))
            body_s = "\n".join(keep)
            if len(body_s) > 2500:
                body_s = body_s[:1000] + "\n... [frame text cut] ...\n" + body_s[-1500:]
            parts.append(f"--- failure: {name} ---\n{body_s}")
            shown += 1
            if shown >= 3 or sum(len(p) for p in parts) > limit:
                break
    elif "Traceback (most recent call last)" in text:      # plain Python traceback
        tb = text[text.rindex("Traceback (most recent call last)"):]
        out_l, skip_next = [], False
        for l in tb.splitlines():
            m = re.match(r'\s*File "([^"]+)", line \d+', l)
            if m:
                skip_next = bool(_LIB.search(m.group(1)))
                if skip_next:
                    continue
            elif skip_next and l.startswith("    "):
                continue
            else:
                skip_next = False
            out_l.append(l)
        parts.append("--- traceback (project frames) ---\n" + "\n".join(out_l))
    if len(parts) <= (1 if head else 0):
        parts.append("--- tail ---\n" + "\n".join(body[-40:]))
    res = "\n".join(parts)
    return res if len(res) <= limit else res[:limit - 1500] + "\n... [cut] ...\n" + res[-1500:]


def trace_command(command: str) -> str | None:
    """Rewrite `[cd D &&] [ENV=..] python[3] [-m mod | script.py] args` or `pytest args` to run under the tracer.
    None when the command is not a plain Python invocation."""
    m = re.match(r"^\s*(cd\s+\S+\s*&&\s*)?((?:[A-Za-z_]\w*=\S*\s+)*)(.*)$", command, re.S)
    prefix, env, rest = (m.group(1) or ""), (m.group(2) or ""), m.group(3).strip()
    if re.search(r"[|;&<>`]|\$\(", rest):
        return None
    try:
        w = shlex.split(rest)
    except ValueError:
        return None
    if not w:
        return None
    if w[0] == "pytest":
        py, mode, target, args = "python", "m", "pytest", w[1:]
    elif re.fullmatch(r"python[\d.]*", w[0].rsplit("/", 1)[-1]):
        py = w[0]
        if len(w) >= 3 and w[1] == "-m":
            mode, target, args = "m", w[2], w[3:]
        elif len(w) >= 2 and w[1].endswith(".py"):
            mode, target, args = "p", w[1], w[2:]
        else:
            return None
    else:
        return None
    script = TRACER.replace("'", "'\"'\"'")
    argv = " ".join(shlex.quote(a) for a in [mode, target] + list(args))
    return f"{prefix}{env}{py} -c '{script}' {argv}"
