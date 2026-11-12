"""Same-time MiniWoB++ comparison: our harness arms, Codex CLI and pi, one model, one task text, one grader.

  python3 mw_bench.py --html <miniwob html dir> --tasks book-flight,terminal --arms v4,v5,codex,pi --seeds 1 --out out.jsonl

Every arm on a task starts at the same moment, each with its own browser. The task text is the same for every
agent (the browser shell commands, batch included). Reward is read by the grader with a lock token agents never see.
Rows: arm, task, seed, reward, passed, wall_s, prompt/cached/output tokens, calls, cost (list prices, same formula
for all), error. No benchmark-specific hints anywhere."""
from __future__ import annotations

import argparse, functools, http.server, json, os, secrets, shutil, socket, subprocess, sys, tempfile, threading, time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

APP = Path(__file__).resolve().parents[2] / "app"
BASE = os.environ.get("BENCH_BASE_URL", "https://llm.meshpool.ai/v1")
MODEL = os.environ.get("BENCH_MODEL", "deepseek-v4.1-flash")
PRICE = {"in": 0.15, "cached": 0.003, "out": 0.60}          # $/M list prices used in the earlier runs
SETUP = """() => { Math.seedrandom('%s'); core.EPISODE_MAX_TIME = 3600000; var o = core.endEpisode;
  core.endEpisode = function (r, t, w) { if (window.__mh === undefined) { window.__mh = {raw: r}; } return o(r, t, w); };
  core.startEpisodeReal(); return core.getUtterance(); }"""
V5 = {"MH_BROWSER_READONLY": "1", "MH_BROWSER_SETTLE": "1", "MH_BROWSER_STATES": "1", "MH_BROWSER_KEYTYPE": "1"}
ARMS = {"v4": {"MH_PRESET": "v4"}, "v5": {"MH_PRESET": "v5"}, "codex": {}, "pi": {},
        "codex5": dict(V5), "pi5": dict(V5)}     # codex5/pi5: the other agents with the fixed browser tool


def port():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p


def serve(html):
    h = functools.partial(type("Q", (http.server.SimpleHTTPRequestHandler,), {"log_message": lambda *a: None}),
                          directory=html)
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", port()), h)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv.server_address[1]


def ctl(bport, *a, env_extra=None, timeout=120):
    env = dict(os.environ, MH_BROWSER_PORT=str(bport), PYTHONPATH=str(APP), **(env_extra or {}))
    r = subprocess.run([sys.executable, "-m", "engines.browser.ctl", *a], cwd=APP, env=env, capture_output=True,
                       text=True, timeout=timeout)
    return r.stdout.strip()


def task_text(utter, bport):
    c = f"cd {APP} && MH_BROWSER_PORT={bport} {sys.executable} -m engines.browser.ctl"
    return (f"A web page is already open in the browser. Do this task on it: {utter}\n\n"
            f"Do not reload the page or open another page. Control the browser with these shell commands:\n"
            f"  {c} snapshot\n  {c} click <n>\n  {c} type <n> <text>\n  {c} select <n> <option text>\n"
            f"  {c} press <key>\n  {c} hover <n>\n  {c} click_text <text>   (click the visible element showing exactly "
            f"this text)\nEach command prints a numbered list of page elements and the page text. Always use the "
            f"numbers from the latest output. Stop when the task is done.\n\n"
            f"Several actions in one command: {c} batch '<JSON list>', for example\n"
            f"  {c} batch '[{{\"do\":\"type\",\"target\":{{\"field\":\"Username\"}},\"text\":\"ann\"}},"
            f"{{\"do\":\"click\",\"target\":{{\"role\":\"button\",\"name\":\"Login\"}}}}]'\n"
            "Steps: type, click, dblclick, hover, check, uncheck, select (option), press (key), wait_text, "
            "expect_text. Targets: {\"n\":3}, {\"field\":\"label next to the box\"}, {\"label\":...}, "
            "{\"role\":...,\"name\":...}, {\"text\":...}. It stops at the first failed step.")


def run_harness(text, wd, flags, bport, timeout):
    env = dict(os.environ, **flags, MH_BROWSER_PORT=str(bport), PYTHONPATH=str(APP), MESHPOOL_BASE_URL=BASE,
               MESHPOOL_API_KEY="dummy", MH_MODEL=MODEL, MH_MAX_SECONDS=str(timeout - 20),
               MH_FAILURES_LOG="off")
    code = ("import json,sys; from meshpool_harness import run_task; "
            "r = run_task(sys.stdin.read(), workdir=sys.argv[1], engine='browser', max_turns=25); "
            "print('@@' + json.dumps({k: r.get(k) for k in ('ok','reason','turns','usage','error')}, default=str))")
    p = subprocess.run([sys.executable, "-c", code, wd], input=text, cwd=APP, env=env, capture_output=True, text=True,
                       timeout=timeout)
    line = next((l for l in p.stdout.splitlines() if l.startswith("@@")), None)
    if not line:
        return {"error": (p.stderr or p.stdout)[-400:]}
    r = json.loads(line[2:])
    u = r.get("usage") or {}
    return {"prompt": u.get("prompt_tokens", 0), "cached": u.get("cached_tokens", 0),
            "output": u.get("completion_tokens", 0), "calls": u.get("model_calls", 0), "turns": r.get("turns"),
            "error": r.get("error"), "full_miss_calls": u.get("full_miss_calls", 0)}


def run_codex(text, wd, timeout):
    home = tempfile.mkdtemp(prefix="cx_")
    (Path(home) / "config.toml").write_text(
        f'model = "{MODEL}"\nmodel_provider = "meshpool"\nmodel_context_window = 128000\n\n'
        f'[model_providers.meshpool]\nname = "Meshpool"\nbase_url = "{BASE}"\nenv_key = "MESHPOOL_KEY"\n'
        'wire_api = "responses"\nrequest_max_retries = 25\nstream_max_retries = 25\nstream_idle_timeout_ms = 600000\n')
    env = dict(os.environ, CODEX_HOME=home, MESHPOOL_KEY="dummy")
    try:
        p = subprocess.run(["codex", "exec", "--json", "-s", "danger-full-access", "--skip-git-repo-check", "-C", wd,
                            text], env=env, capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL)
        out = p.stdout
    except subprocess.TimeoutExpired as e:
        out = e.stdout.decode() if isinstance(e.stdout, bytes) else (e.stdout or "")
    finally:
        shutil.rmtree(home, ignore_errors=True)
    t = {"prompt": 0, "cached": 0, "output": 0, "calls": 0}
    for l in out.splitlines():
        try:
            ev = json.loads(l)
        except ValueError:
            continue
        u = ev.get("usage") if isinstance(ev, dict) else None
        if ev.get("type") == "turn.completed" and u:
            t["prompt"] += u.get("input_tokens", 0); t["cached"] += u.get("cached_input_tokens", 0)
            t["output"] += u.get("output_tokens", 0)
        if ev.get("type") == "item.completed" and (ev.get("item") or {}).get("type") == "command_execution":
            t["calls"] += 1
    if not any(t.values()):
        t["error"] = out[-300:]
    return t


def run_pi(text, wd, timeout):
    d = tempfile.mkdtemp(prefix="pi_")
    (Path(d) / "models.json").write_text(json.dumps({"providers": {"meshpool": {
        "baseUrl": BASE, "api": "openai-completions", "apiKey": "dummy", "models": [{"id": MODEL}]}}}))
    env = dict(os.environ, PI_CODING_AGENT_DIR=d)
    try:
        p = subprocess.run(["pi", "--mode", "json", "-p", "--no-session", "--provider", "meshpool", "--model", MODEL,
                            text], cwd=wd, env=env, capture_output=True, text=True, timeout=timeout,
                           stdin=subprocess.DEVNULL)
        out = p.stdout
    except subprocess.TimeoutExpired as e:
        out = e.stdout.decode() if isinstance(e.stdout, bytes) else (e.stdout or "")
    finally:
        shutil.rmtree(d, ignore_errors=True)
    t = {"prompt": 0, "cached": 0, "output": 0, "calls": 0}
    for l in out.splitlines():
        try:
            ev = json.loads(l)
        except ValueError:
            continue
        if ev.get("type") == "message_end" and (ev.get("message") or {}).get("role") == "assistant":
            u = ev["message"].get("usage") or {}
            t["prompt"] += (u.get("input", 0) or 0) + (u.get("cacheRead", 0) or 0)
            t["cached"] += u.get("cacheRead", 0) or 0
            t["output"] += u.get("output", 0) or 0
            t["calls"] += 1
    if not any(t.values()):
        t["error"] = out[-300:]
    return t


def one(arm, task, seed, hport, timeout):
    bport = port()
    flags = ARMS[arm]
    ctl(bport, "start", env_extra=flags)
    wd = tempfile.mkdtemp(prefix=f"mw_{arm}_")
    row = {"arm": arm, "task": task, "seed": seed, "model": MODEL}
    try:
        ctl(bport, "goto", f"http://127.0.0.1:{hport}/miniwob/{task}.html", env_extra=flags)
        utter = _call(bport, "_eval", SETUP % f"mh-{seed}")["value"]
        tok = secrets.token_hex(16)
        _call(bport, "_lock", tok)
        text = task_text(utter, bport)
        t0 = time.time()
        if arm in ("v4", "v5"):
            r = run_harness(text, wd, flags, bport, timeout)
        elif arm.startswith("codex"):
            r = run_codex(text, wd, timeout)
        else:
            r = run_pi(text, wd, timeout)
        row["wall_s"] = round(time.time() - t0, 1)
        row.update(r)
        rew = (_call(bport, "_eval", "() => JSON.stringify(window.__mh || null)", tok).get("value"))
        raw = (json.loads(rew) or {}).get("raw") if rew else None
        row["reward"], row["passed"] = raw, bool(raw and raw > 0)
    except Exception as e:  # noqa: BLE001
        row["error"] = f"{type(e).__name__}: {e}"[:300]
        row.setdefault("passed", False)
    finally:
        ctl(bport, "stop")
        shutil.rmtree(wd, ignore_errors=True)
    unc = max(row.get("prompt", 0) - row.get("cached", 0), 0)
    row["cost"] = round((unc * PRICE["in"] + row.get("cached", 0) * PRICE["cached"]
                         + row.get("output", 0) * PRICE["out"]) / 1e6, 6)
    return row


def _call(bport, cmd, *args):
    """Thread-safe call to one browser daemon (no module globals; urllib skips the proxy for 127.0.0.1)."""
    import urllib.request
    req = urllib.request.Request(f"http://127.0.0.1:{bport}", data=json.dumps({"cmd": cmd, "args": list(args)}).encode(),
                                 method="POST", headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--html", required=True)
    ap.add_argument("--tasks", required=True)
    ap.add_argument("--arms", default="v4,v5,codex,pi")
    ap.add_argument("--seeds", default="1")
    ap.add_argument("--timeout", type=int, default=300)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    hport = serve(a.html)
    arms = a.arms.split(",")
    for task in a.tasks.split(","):
        for seed in a.seeds.split(","):
            with ThreadPoolExecutor(len(arms)) as ex:          # every arm starts on the task at the same moment
                rows = list(ex.map(lambda arm: one(arm, task, seed, hport, a.timeout), arms))
            with open(a.out, "a") as f:
                for r in rows:
                    f.write(json.dumps(r) + "\n")
            print(" | ".join(f"{r['arm']}:{'OK' if r.get('passed') else 'x'} {r.get('wall_s', '-')}s "
                             f"${r.get('cost', 0):.4f}" + (f" ERR {str(r.get('error'))[:60]}" if r.get('error') else "")
                             for r in rows), f"<- {task}#{seed}", flush=True)


if __name__ == "__main__":
    main()
