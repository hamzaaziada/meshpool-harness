"""Same-time SWE-bench Verified comparison: harness arms, the app agent, Codex CLI and pi on one model.

  python3 ver_bench.py --data verified.json --ids a,b --arms v4,v5,app,codex,pi --out out.jsonl --budget 5

Per task every arm gets its own copy of the repo (from the official image) mounted into its own container, the same
issue text, the same caps (60 turns where the agent has a turn cap, 30 min wall clock), and the official swebench
grader. A spend check before each task stops the run once the key has spent `--budget` dollars since the start."""
from __future__ import annotations

import argparse, json, os, shutil, subprocess, sys, tempfile, time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import mw_bench as M      # noqa: E402  (agent launchers, prices)
from spend import spend   # noqa: E402

APP = M.APP
RD = "/testbed"
WORK = Path(os.environ.get("BENCH_WORK", "/root/ver_work"))
V5 = {"MH_SCOPE_CHECK": "1", "MH_CODE_FINISH_SAME_TURN": "1", "MH_PARALLEL_MANY": "1"}
ARMS = {"v4": {"MH_PRESET": "v4"}, "v5": {"MH_PRESET": "v5"}, "v5s": {"MH_PRESET": "v5", **V5},
        "app": {"MH_PRESET": "v5"},
        "v5nt": {"MH_PRESET": "v5", "MH_TODO": "0"}, "v5ft": {"MH_PRESET": "v5", "MH_CODE_FINISH_SAME_TURN": "1"},
        "v5sp": {"MH_PRESET": "v5", "MH_SCOPE_PROMPT": "1"},
        "v5o": {"MH_PRESET": "v5", "MH_OBSERVE": "1"}, "appo": {"MH_PRESET": "v5", "MH_OBSERVE": "1"},
        "v5x": {"MH_PRESET": "v5", "MH_OBSERVE": "1", "MH_LINT_EDIT": "1", "MH_NAV": "1"},
        "appx": {"MH_PRESET": "v5", "MH_OBSERVE": "1", "MH_LINT_EDIT": "1", "MH_NAV": "1"},
        "codex": {}, "pi": {}}
TURNS, WALL = 60, 1800


def sh(cmd, **kw):
    return subprocess.run(cmd, shell=isinstance(cmd, str), capture_output=True, text=True, **kw)


def image(iid):
    return f"swebench/sweb.eval.x86_64.{iid.replace('__', '_1776_')}:latest".lower()


def setup(iid, arm):
    img = image(iid)
    for attempt in range(4):                              # registry hiccups: retry before calling it infra
        if not sh(["docker", "image", "inspect", img]).returncode:
            break
        r = sh(["docker", "pull", img], timeout=1800)
        if not r.returncode:
            break
        time.sleep(20 * (attempt + 1))
    else:
        raise RuntimeError("pull failed: " + r.stderr[-300:])
    host = WORK / f"{iid}_{arm}"
    shutil.rmtree(host, ignore_errors=True)
    host.parent.mkdir(parents=True, exist_ok=True)
    tmp = sh(["docker", "create", img]).stdout.strip()
    r = sh(["docker", "cp", f"{tmp}:{RD}", str(host)])
    sh(["docker", "rm", tmp])
    if r.returncode:
        raise RuntimeError(r.stderr[-300:])
    sh(f"cd {host} && git config --global --add safe.directory '*' && git add -A >/dev/null 2>&1; "
       f"git -c user.email=x@y -c user.name=x commit -qm baseline --allow-empty >/dev/null 2>&1")
    name = f"vb_{arm}_{iid}"[:60]
    sh(["docker", "rm", "-f", name])
    r = sh(["docker", "run", "-d", "--name", name, "-v", f"{host}:{RD}", "-w", RD, "--entrypoint", "sleep", img,
            "infinity"])
    if r.returncode:
        raise RuntimeError(r.stderr[-300:])
    sh(["docker", "exec", name, "bash", "-c",
        "echo 'source /opt/miniconda3/bin/activate testbed 2>/dev/null' > /etc/profile.d/zz_testbed.sh"])
    return host, name


def instructions(inst, c):
    return (f"Resolve this GitHub issue in the repository checked out in the current directory.\n\n"
            f"<issue>\n{inst['problem_statement'].strip()}\n</issue>\n\n"
            f"Environment: the project's dependencies are installed only inside a Docker container. Edit files in the "
            f"current directory as usual (it is mounted into the container at {RD}). To run any code or tests, use:\n"
            f"  docker exec -w {RD} {c} bash -lc '<command>'\n"
            f"for example: docker exec -w {RD} {c} bash -lc 'python -m pytest -x -q <test path>'\n\n"
            f"Make a minimal source change that fixes the issue. You may add a new test to reproduce it, but do not "
            f"modify or delete existing tests. Verify by running the relevant tests in the container before finishing.")


def run_engine(text, wd, flags, engine, tid):
    env = dict(os.environ, **flags, PYTHONPATH=str(APP), MESHPOOL_BASE_URL=M.BASE, MESHPOOL_API_KEY="dummy",
               MH_MODEL=M.MODEL, MH_MAX_SECONDS=str(WALL - 60), MH_FAILURES_LOG="off",
               OPENAI_BASE_URL=M.BASE, OPENAI_API_KEY="dummy", MH_AGENT_PERMISSIONS="auto")
    kw = "max_turns=60" if engine == "code" else "max_steps=60"
    code = ("import json,sys; from meshpool_harness import run_task; "
            f"r = run_task(sys.stdin.read(), workdir=sys.argv[1], engine='{engine}', task_id='{tid}', {kw}); "
            "print('@@' + json.dumps({k: r.get(k) for k in ('ok','reason','turns','usage','error')}, default=str))")
    try:
        p = subprocess.run([sys.executable, "-c", code, wd], input=text, cwd=APP, env=env, capture_output=True,
                           text=True, timeout=WALL)
    except subprocess.TimeoutExpired:
        return {"error": "wall-clock cap"}
    line = next((l for l in p.stdout.splitlines() if l.startswith("@@")), None)
    if not line:
        return {"error": (p.stderr or p.stdout)[-400:]}
    r = json.loads(line[2:])
    u = r.get("usage") or {}
    return {"prompt": u.get("prompt_tokens", 0), "cached": u.get("cached_tokens", 0),
            "output": u.get("completion_tokens", 0), "calls": u.get("model_calls", 0), "turns": r.get("turns"),
            "reason": r.get("reason"), "error": r.get("error"), "full_miss_calls": u.get("full_miss_calls", 0)}


def patch_of(host):
    sh(f"cd {host} && git add -A -- . ':!.run' ':!.junie' >/dev/null 2>&1")
    return sh(f"cd {host} && git diff --cached -- . ':!.run' ':!.junie'").stdout


def grade(iid, patch, arm, data):
    d = WORK / "_preds"
    d.mkdir(parents=True, exist_ok=True)
    rid = f"vb_{arm}_{iid}_{int(time.time())}"
    pf = d / f"{rid}.jsonl"
    pf.write_text(json.dumps({"instance_id": iid, "model_patch": patch, "model_name_or_path": arm}) + "\n")
    r = sh([sys.executable, "-m", "swebench.harness.run_evaluation", "--dataset_name", data, "--predictions_path",
            str(pf), "--instance_ids", iid, "--run_id", rid, "--max_workers", "1", "--timeout", "1800"],
           cwd=str(d), timeout=3600)
    rep = list(d.glob(f"{arm}.{rid}.json"))
    if rep:
        return iid in json.loads(rep[0].read_text()).get("resolved_ids", [])
    raise RuntimeError("grader: " + (r.stdout + r.stderr)[-400:])


def one(arm, inst, data):
    iid = inst["instance_id"]
    row = {"arm": arm, "bench": "verified", "task": iid, "model": M.MODEL}
    host = name = None
    try:
        host, name = setup(iid, arm)
        text = instructions(inst, name)
        t0 = time.time()
        if arm.startswith("v") and not arm.startswith("app"):
            r = run_engine(text, str(host), ARMS[arm], "code", f"{arm}-{iid}")
        elif arm.startswith("app"):
            r = run_engine(text, str(host), ARMS[arm], "agent", f"{arm}-{iid}")
        elif arm == "codex":
            r = M.run_codex(text, str(host), WALL)
        else:
            r = M.run_pi(text, str(host), WALL)
        row["wall_s"] = round(time.time() - t0, 1)
        row.update(r)
        patch = patch_of(host)
        row["patch_lines"] = len(patch.splitlines())
        pd = Path(__file__).resolve().parent / "results" / "patches" / arm
        pd.mkdir(parents=True, exist_ok=True)
        (pd / f"{iid}.patch").write_text(patch)              # kept, so a grader failure can be re-graded
        sh(["docker", "rm", "-f", name])
        name = None
        row["passed"] = grade(iid, patch, arm, data) if patch.strip() else False
    except Exception as e:  # noqa: BLE001
        row["infra_error"] = f"{type(e).__name__}: {e}"[:400]
        row.setdefault("passed", False)
    finally:
        if name:
            sh(["docker", "rm", "-f", name])
        if host and (host / ".run").is_dir():            # keep our transcripts (small) for call-by-call analysis
            td = Path(__file__).resolve().parent / "results" / "transcripts" / arm
            td.mkdir(parents=True, exist_ok=True)
            for f in (host / ".run").glob("transcript-*.jsonl"):
                shutil.copy(f, td / f"{iid}.jsonl")
        if host:
            shutil.rmtree(host, ignore_errors=True)
    unc = max(row.get("prompt", 0) - row.get("cached", 0), 0)
    row["cost"] = round((unc * M.PRICE["in"] + row.get("cached", 0) * M.PRICE["cached"]
                         + row.get("output", 0) * M.PRICE["out"]) / 1e6, 6)
    return row


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True)
    ap.add_argument("--ids", required=True)
    ap.add_argument("--arms", default="v4,v5,app,codex,pi")
    ap.add_argument("--out", required=True)
    ap.add_argument("--budget", type=float, default=5.0)
    a = ap.parse_args()
    insts = {r["instance_id"]: r for r in json.load(open(a.data))}
    s0 = spend()
    print(f"key spend at start: ${s0}", flush=True)
    for iid in a.ids.split(","):
        s = spend()
        if s0 is not None and s is not None and s - s0 > a.budget:
            print(f"STOP: spent ${s - s0:.2f} > budget ${a.budget}", flush=True)
            break
        arms = a.arms.split(",")
        setup_img = image(iid)
        if sh(["docker", "image", "inspect", setup_img]).returncode:      # pull once, before the arms start together
            sh(["docker", "pull", setup_img], timeout=1800)
        with ThreadPoolExecutor(len(arms)) as ex:
            rows = list(ex.map(lambda arm: one(arm, insts[iid], a.data), arms))
        with open(a.out, "a") as f:
            for r in rows:
                f.write(json.dumps(r) + "\n")
        print(" | ".join(f"{r['arm']}:{'OK' if r.get('passed') else 'x'} {r.get('wall_s', '-')}s ${r.get('cost', 0):.4f}"
                         + (f" INFRA {r['infra_error'][:50]}" if r.get('infra_error') else "") for r in rows),
              f"<- {iid}", flush=True)
        sh(["docker", "rmi", "-f", setup_img])                              # disk: one image at a time
        sh("docker image prune -f; docker container prune -f")
    s1 = spend()
    print(f"key spend at end: ${s1} (this run: ${(s1 or 0) - (s0 or 0):.2f})", flush=True)


if __name__ == "__main__":
    main()
