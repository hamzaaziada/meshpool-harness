"""Single entry point.

  meshpool                                           # interactive coding agent in the current folder
  meshpool -p "request" [--auto]                     # one request, print the reply, exit
  meshpool mcp                                       # MCP servers from mcp.json and their tools
  meshpool acp [--profile work] [--auto]             # serve the agent over ACP (Zed, JetBrains, other apps)
  meshpool setup                                     # first-run walkthrough, then a session
  meshpool login                                     # save your API key
  python -m meshpool_harness ui                      # the Meshpool web app in your browser
  python -m meshpool_harness check [--live]          # settings, installed tools, and (with --live) one tiny model call
  python -m meshpool_harness route "request" [--files a.csv ...]
  python -m meshpool_harness run "request" [--files ...] [--out DIR] [--engine code,slides] [--workdir REPO]
  python -m meshpool_harness install-browser         # optional: Playwright + Chromium for the browser engine

Settings come from meshpool.config (next to the launcher); shell variables win over the file.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from meshpool_harness import config as _config  # noqa: E402

ENGINES = ("code", "data", "sheets", "slides", "forge", "general", "browser")


def _engine(name):
    import importlib
    return importlib.import_module(f"engines.{name}.engine")


def run_request(request: str, files: list[str], out: str, engine: str | None = None, workdir: str | None = None,
                client=None, output_format: str | None = None) -> dict:
    from meshpool_harness.core.client import LiveClient
    from meshpool_harness.router.router import route
    if not request or not request.strip():
        raise SystemExit("error: empty request")
    missing = [f for f in files if not os.path.exists(f)]
    if missing:
        raise SystemExit(f"error: file(s) not found: {', '.join(missing)}")
    if engine:
        bad = [e for e in engine.split(",") if e not in ENGINES]
        if bad:
            raise SystemExit(f"error: unknown engine(s) {bad}; choose from {', '.join(ENGINES)}")
    if not (os.environ.get("MESHPOOL_API_KEY") or os.environ.get("OPENAI_API_KEY")
            or os.environ.get("MH_PROVIDER") == "chatgpt") and client is None:
        raise SystemExit(f"error: no API key. Put MESHPOOL_API_KEY=... in {_config.config_path()}")
    client = client or LiveClient("phase_2_build")
    task_id = "req-" + uuid.uuid4().hex[:8]
    r = route(request, files, output_format, workdir, client=client, engine=engine, task_id=task_id)
    outdir = Path(out).resolve()
    outdir.mkdir(parents=True, exist_ok=True)
    inputs = [str(Path(f).resolve()) for f in files]
    t0, results = time.time(), []
    for i, name in enumerate(r.engines):
        wd = workdir if (name == "code" and workdir) else str(outdir)
        for f in inputs:                         # engines see inputs inside their workdir
            dst = Path(wd) / Path(f).name
            if not dst.exists() and Path(f).parent != Path(wd):
                shutil.copy(f, dst)
        task = request
        if inputs:
            task += "\n\nInput files (in the working directory): " + ", ".join(Path(f).name for f in inputs)
        if results:
            prev = results[-1]
            task += (f"\n\nOutput of the previous step ({r.engines[i - 1]}), use it as your source:\n"
                     f"{prev['answer']}\nFiles: {', '.join(prev.get('artifacts', []))}")
        if len(r.engines) > 1:
            task += f"\n\nYou are step {i + 1} of {len(r.engines)} ({' -> '.join(r.engines)}); do only the {name} part."
        kw = {"files": inputs} if name in ("data", "sheets") else {}
        if os.environ.get("MH_MODEL"):           # the model chosen in settings, for every engine
            kw["model"] = os.environ["MH_MODEL"]
        saved = dict(os.environ)
        if name in ("code", "browser") and not os.environ.get("MH_PRESET"):
            from meshpool_harness.presets import apply as _preset
            _preset("v3")                         # the benchmarked harness for code and browser tasks (run C)
        try:
            res = _engine(name).solve(task, workdir=wd, task_id=f"{task_id}-{name}", client=client, **kw)
        except Exception as e:   # one clean message instead of a traceback
            res = {"ok": False, "answer": "", "reason": f"{type(e).__name__}: {e}", "artifacts": []}
        finally:
            os.environ.clear()
            os.environ.update(saved)              # office engines keep their own tested settings
        res["engine"] = name
        results.append(res)
        if not res.get("ok"):
            break
    spent = client.spent() if hasattr(client, "spent") else None
    return {"task_id": task_id, "route": {"engines": r.engines, "confidence": r.confidence, "reason": r.reason,
                                         "used_model_call": r.used_model_call},
            "ok": all(x.get("ok") for x in results) and len(results) == len(r.engines),
            "steps": [{k: v for k, v in x.items() if k in ("engine", "ok", "answer", "artifacts", "evidence", "turns",
                                                          "reason", "usage")} for x in results],
            "out_dir": str(outdir), "wall_s": round(time.time() - t0, 1), "key_spend_since_start": spent}


COMMANDS = {"route", "run", "work", "ui", "check", "install-browser", "agent", "mcp", "app", "acp", "login", "setup", "import", "connect", "disconnect"}


def _agent_parser():
    ap = argparse.ArgumentParser(
        prog="meshpool", formatter_class=argparse.RawDescriptionHelpFormatter,
        description="Meshpool coding agent. Run it in a project folder: `meshpool` for a session, "
                    "`meshpool -p \"...\"` for one answer.",
        epilog="other commands (meshpool <command> -h for options):\n"
               "  setup            the first-run walkthrough: key, folder, permissions, your skills\n"
               "  import           bring your old chats and sessions (ChatGPT, Claude, Claude Code, Codex) and context\n"
               "  login            save your Meshpool API key\n"
               "  check            show settings and installed tools (--live sends one tiny model call)\n"
               "  connect          connect apps: Notion, Gmail, Slack, HubSpot... or any MCP server by URL\n"
               "  mcp              list, test and switch MCP servers\n"
               "  acp              serve the agent over the Agent Client Protocol (Zed, JetBrains, other apps)\n"
               "  work             fetch a task from GitHub, Jira, a URL or a file, run it, report back\n"
               "  run / route      run or preview Meshpool's task engines (decks, spreadsheets, data, code)\n"
               "  ui / app         open the Meshpool web app or desktop app\n"
               "  install-browser  install the optional browser engine")
    ap.add_argument("request", nargs="?", help="first request for an interactive session")
    ap.add_argument("-p", "--print", dest="prompt", help="answer one request, print the reply and exit")
    ap.add_argument("--dir", help="project folder (default: the current folder)")
    ap.add_argument("--auto", action="store_true", help="allow edits and commands without asking")
    ap.add_argument("-c", "--continue", dest="resume", action="store_true", help="resume the last session here")
    ap.add_argument("--resume", dest="session", help="resume a session by id")
    ap.add_argument("--model", help="model id for this session")
    ap.add_argument("--no-mcp", action="store_true", help="do not start MCP servers")
    return ap


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if os.environ.pop("MESHPOOL_RESTART_WAIT", None):     # started by "Restart" after an update: let the old app close
        import time
        time.sleep(3)
    if os.name == "nt" and sys.executable.lower().endswith("pythonw.exe"):
        # The Windows app runs windowless under pythonw.exe, whose children get no usable stdin/stdout. Launch child
        # Pythons (Hermes, MCP servers, engines) with python.exe; their console windows are suppressed.
        from meshpool_harness.ui.desktop import _hide_child_consoles
        _hide_child_consoles()
        pe = Path(sys.executable).with_name("python.exe")
        if pe.exists():
            sys.executable = str(pe)
    if argv[:1] == ["app"]:              # the desktop app (Meshpool.app / Meshpool.exe)
        from meshpool_harness.ui.desktop import run as run_app
        return run_app()
    if os.environ.get("MESHPOOL_HOME"):
        _config.ensure_home()
    _config.load_into_env()          # before any engine import: engines read MH_* at import time
    try:
        from meshpool_harness.ui.desktop import _bundled_tools
        _bundled_tools()             # the shell, Git and browser engine that ship inside the app
    except Exception:
        pass
    if not argv or argv[0] not in COMMANDS:
        if argv and argv[0] == "agent":
            argv = argv[1:]
        a = _agent_parser().parse_args(argv)
        if a.model:
            os.environ["MH_MODEL"] = a.model
        from meshpool_harness.agent.tui import main as agent_main
        return agent_main(a.dir, a.prompt, a.auto, a.resume, a.session, a.no_mcp, a.request)
    if argv[0] == "agent":
        return main(argv[1:] or [])
    if argv[0] == "acp":
        ap = argparse.ArgumentParser(prog="meshpool acp", description="Serve the Meshpool agent over ACP on stdio.")
        ap.add_argument("--profile", choices=["code", "work"], default="code")
        ap.add_argument("--auto", action="store_true", help="allow edits and commands without asking the client")
        a = ap.parse_args(argv[1:])
        from meshpool_harness.agent.acp_server import main as acp_main
        return acp_main(a.profile, a.auto)
    if argv[0] == "setup":
        from meshpool_harness.agent import onboard
        from meshpool_harness.agent.tui import main as agent_main
        folder = onboard.run()
        if folder is None:
            return 1
        return agent_main(str(folder), onboarded=True)
    if argv[0] == "connect":
        from meshpool_harness.agent import connect_cli
        return connect_cli.main(argv[1:])
    if argv[0] == "disconnect":
        from meshpool_harness.agent import connect_cli
        if len(argv) < 2:
            print("Usage: meshpool disconnect <name>")
            return 1
        return connect_cli.disconnect(argv[1])
    if argv[0] == "import":
        from meshpool_harness.agent import onboard
        from meshpool_harness.agent.tui import Style, _enable_ansi
        if len(argv) > 1:                         # meshpool import export.zip | claude-code | codex
            from meshpool_harness import chat_import as CI
            what = argv[1]
            if what in ("claude-code", "codex"):
                r = CI.import_local([what])
                print(f"Imported {r['imported']} {what} sessions as threads ({r['skipped']} were already here).")
                return 0
            r = CI.import_export(os.path.expanduser(what))
            print(f"{'Claude' if r['kind'] == 'claude' else 'ChatGPT'} export: {r['imported']} chats as threads "
                  f"({r['skipped']} were already here), {len(r['skills'])} project skills, {r['memory']} facts into "
                  f"memory, {r['instructions']} instructions.")
            return 0
        return 0 if onboard.context_step(Style(_enable_ansi())) else 1
    if argv[0] == "login":
        from meshpool_harness.agent.tui import ask_key
        return 0 if ask_key(force=True) else 1
    if argv[0] == "mcp":
        from meshpool_harness.agent.mcp import Manager, config_file
        from meshpool_harness.agent.tui import Terminal, _mcp_status
        print("Starting MCP servers...")
        m = Manager(roots=[os.getcwd()]).start()
        try:
            _mcp_status(Terminal(), m)
        finally:
            m.close()
        return 0 if all(s["status"] != "error" for s in m.status()) and not m.config_error else 1
    ap = argparse.ArgumentParser(prog="meshpool")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for c in ("route", "run"):
        p = sub.add_parser(c)
        p.add_argument("request")
        p.add_argument("--files", nargs="*", default=[])
        p.add_argument("--engine")
        p.add_argument("--workdir")
        p.add_argument("--format", dest="output_format")
        if c == "run":
            p.add_argument("--out", default="./mh_out")
    w = sub.add_parser("work", help="fetch a task from a connector (github:, jira:, http:, file:, custom), run it, report back")
    w.add_argument("source", help="e.g. github:owner/repo#12, jira:PROJ-7, http:https://..., file:task.md")
    w.add_argument("--workdir", default=".")
    w.add_argument("--engine", default=None)
    w.add_argument("--out", default="./mh_out")
    w.add_argument("--report", action="store_true", help="post the outcome back to the source")
    u = sub.add_parser("ui", help="open the Meshpool web app")
    u.add_argument("--port", type=int, default=int(os.environ.get("MH_UI_PORT", "8765")))
    u.add_argument("--no-browser", action="store_true")
    ck = sub.add_parser("check", help="show settings and installed tools; --live sends one tiny model call")
    ck.add_argument("--live", action="store_true")
    sub.add_parser("install-browser", help="install Playwright and Chromium for the browser engine")
    a = ap.parse_args(argv)
    if a.cmd == "ui":
        from meshpool_harness.ui.server import serve
        return serve(a.port, open_browser=not a.no_browser)
    if a.cmd == "check":
        return _check(a.live)
    if a.cmd == "install-browser":
        import subprocess
        r = subprocess.run([sys.executable, "-m", "pip", "install", "playwright>=1.40"])
        if r.returncode == 0:
            r = subprocess.run([sys.executable, "-m", "playwright", "install", "chromium"])
        print("browser engine ready" if r.returncode == 0 else "browser install failed (see above)")
        return r.returncode
    if a.cmd == "work":
        from meshpool_harness.connectors import get
        conn, ref = get(a.source)
        task = conn.fetch(ref)
        res = run_request(task.text(), [], a.out, a.engine, a.workdir)
        if a.report:
            conn.report(task, res)
        print(json.dumps({"task": task.ref, "source": task.source, **{k: res.get(k) for k in ("ok", "answer")}},
                         indent=1, default=str))
        return 0 if res.get("ok") else 1
    if a.cmd == "route":
        from meshpool_harness.router.rules import decide
        eng, conf, why = decide(a.request, a.files, a.output_format, a.workdir)
        print(json.dumps({"engines": eng, "confidence": conf, "reason": why}, indent=1))
        return 0
    res = run_request(a.request, a.files, a.out, a.engine, a.workdir, output_format=a.output_format)
    print(json.dumps(res, indent=1))
    return 0 if res["ok"] else 1


def ping() -> dict:
    """One tiny model call with the current settings. Returns {ok, detail, model, seconds}."""
    from meshpool_harness.core.wire import SimpleLedger
    model = os.environ.get("MH_MODEL", "deepseek-v4.1-flash")
    t0 = time.time()
    try:
        led = SimpleLedger("check")
        r = led.chat(model=model, messages=[{"role": "user", "content": "Reply with the single word: ok"}],
                     max_tokens=5, retries=1, timeout=60)
        txt = ((r.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
        return {"ok": True, "detail": f"model replied: {txt.strip()[:40]!r}", "model": model,
                "seconds": round(time.time() - t0, 1), "endpoint": led.base_url}
    except Exception as e:
        msg = str(e)
        if "401" in msg or "403" in msg:
            msg = "the endpoint rejected the API key (" + msg[:160] + ")"
        return {"ok": False, "detail": msg[:400], "model": model, "seconds": round(time.time() - t0, 1),
                "endpoint": os.environ.get("MESHPOOL_BASE_URL", "")}


def _check(live: bool) -> int:
    key = os.environ.get("MESHPOOL_API_KEY") or os.environ.get("OPENAI_API_KEY") or ""
    print(f"Settings file : {_config.config_path()}")
    print(f"Endpoint      : {os.environ.get('MESHPOOL_BASE_URL', 'https://llm.meshpool.ai/v1')}")
    print(f"Model         : {os.environ.get('MH_MODEL', 'deepseek-v4.1-flash')}")
    print(f"API key       : {'set (ends ' + key[-4:] + ')' if key else 'MISSING'}")
    print()
    for row in _config.system_check():
        print(f"  [{'ok' if row['ok'] else '--'}] {row['name']:<15} {row['detail']}")
        if not row["ok"]:
            print(f"       {row['fix']}")
    if live:
        print()
        if not key:
            print("Live test skipped: no API key.")
            return 1
        r = ping()
        print(("Live test OK: " if r["ok"] else "Live test FAILED: ") + r["detail"] + f" ({r['seconds']} s)")
        return 0 if r["ok"] else 1
    return 0 if key else 1


if __name__ == "__main__":
    sys.exit(main())
