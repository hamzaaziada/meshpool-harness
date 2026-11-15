"""Paired A/B of the code engine on SWE-bench Verified: same tasks, same model, same hour; arms differ only in env flags.

    python testkit/swe_ab.py --arm base:MH_ERROR_CONTEXT=0 --arm ctx:MH_ERROR_CONTEXT=1 --n 30 --out /tmp/ab
    python testkit/swe_ab.py --arm main:MH_AB_APP=/path/to/main-checkout/app --arm new:MH_ERROR_CONTEXT=1 ...

Per task: pull the task's image, run every arm at the same time, each in its own container (engines/code/engine.py
solve, the same entry point as the benchmark runs; MH_AB_APP=<checkout>/app runs an arm on another version of the code),
take the patch, grade it in a fresh container with the task's own
eval script and SWE-bench's log parser, then remove the image. Each arm runs in its own process, so flags never leak
between arms. Results go to OUT/results.jsonl (one row per task and arm, resumable); --report prints the paired
summary: solves, tasks only one arm solved (exact McNemar p), time, model calls, tokens and cost per solved task.

Needs Docker, `pip install swebench datasets`, and a key for the model endpoint (MESHPOOL_API_KEY).
"""
from __future__ import annotations

import argparse
import json
import math
import os
import random
import shlex
import subprocess
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

APP = Path(__file__).resolve().parents[1] / "app"
DATASET = "SWE-bench/SWE-bench_Verified"
LOCK = threading.Lock()


def sh(args, timeout=600, **kw):
    return subprocess.run(args, capture_output=True, text=True, timeout=timeout, errors="replace", **kw)


def load(ids=None):
    from datasets import load_dataset
    rows = list(load_dataset(DATASET, split="test"))
    return [r for r in rows if not ids or r["instance_id"] in ids]


# ---------- one arm on one task (runs in a child process with the arm's env) ---------------------------------------
def run_one(inst_path: str, arm: str, out_path: str, model: str, max_turns: int):
    sys.path.insert(0, os.environ.get("MH_AB_APP") or str(APP))
    from engines.code.engine import solve
    from meshpool_harness.core.client import LiveClient
    from meshpool_harness.core.metrics import cached_tokens

    inst = json.loads(Path(inst_path).read_text())
    usage = {"calls": 0, "prompt": 0, "cached": 0, "completion": 0}

    class Counting(LiveClient):
        def chat(self, **kw):
            r = super().chat(**kw)
            u = r.get("usage") or {}
            usage["calls"] += 1
            usage["prompt"] += u.get("prompt_tokens", 0) or 0
            usage["cached"] += cached_tokens(u)
            usage["completion"] += u.get("completion_tokens", 0) or 0
            return r

    name = f"ab_{arm}_{inst['instance_id']}".replace("/", "_")[:120]
    sh(["docker", "rm", "-f", name, name + "_src"])
    # The repository is copied out of the image and bind-mounted back at /testbed, as in the benchmark runs: file tools
    # work on the host copy, commands run in the container.
    workdir = tempfile.mkdtemp(prefix="ab-")
    r = sh(["docker", "create", "--name", name + "_src", inst["image"]])
    r = r if r.returncode else sh(["docker", "cp", f"{name}_src:/testbed/.", workdir], timeout=900)
    sh(["docker", "rm", "-f", name + "_src"])
    r = r if r.returncode else sh(["docker", "run", "-d", "--name", name, "-v", f"{workdir}:/testbed", inst["image"],
                                   "sleep", "infinity"])
    if r.returncode:
        Path(out_path).write_text(json.dumps({"infra": "container: " + r.stderr[-300:]}))
        return
    sh(["git", "config", "--global", "--add", "safe.directory", workdir])
    task = ("Resolve the GitHub issue below in the repository at /testbed (mounted into the container from "
            f"{workdir}).\n"
            f"Run every command inside the task container with: docker exec -w /testbed {name} bash -lc '<command>'\n"
            f"<issue>\n{inst['problem_statement']}\n</issue>")
    row, t0 = {}, time.time()
    try:
        res = solve(task, workdir=workdir, task_id=f"{inst['instance_id']}-{arm}", max_turns=max_turns,
                    model=model, client=Counting())
        row = {k: res.get(k) for k in ("ok", "turns", "reason", "commands", "edits", "attempts")}
    except Exception as e:  # noqa: BLE001 - an agent crash is a failed attempt, not an infra error
        row = {"ok": False, "reason": f"crash: {type(e).__name__}: {str(e)[:300]}"}
    row["secs"] = round(time.time() - t0, 1)
    row["usage"] = usage
    row["patch"] = sh(["docker", "exec", "-w", "/testbed", name, "git", "-c", "core.fileMode=false", "diff",
                       "HEAD"]).stdout
    sh(["docker", "rm", "-f", name])
    keep = Path(os.environ.get("MH_AB_LOGDIR") or Path(tempfile.gettempdir()) / "swe_ab_transcripts")
    keep.mkdir(parents=True, exist_ok=True)
    for t in Path(workdir, ".run").glob("transcript-*.jsonl"):
        sh(["cp", str(t), str(keep / t.name)])
    sh(["rm", "-rf", workdir])
    Path(out_path).write_text(json.dumps(row))


# ---------- grading ------------------------------------------------------------------------------------------------
def grade(inst: dict, patch: str, tag: str, logdir: Path) -> dict:
    from swebench.harness.constants import APPLY_PATCH_FAIL, APPLY_PATCH_PASS
    from swebench.harness.grading import get_eval_report
    from swebench.harness.utils import make_test_spec
    if not patch.strip():
        return {"resolved": False, "graded": "empty patch"}
    spec = make_test_spec(inst)
    name = f"grade_{tag}_{inst['instance_id']}"[:120]
    sh(["docker", "rm", "-f", name])
    if sh(["docker", "run", "-d", "--name", name, inst["image"], "sleep", "infinity"]).returncode:
        return {"resolved": False, "graded": "infra: grade container"}
    try:
        with tempfile.TemporaryDirectory() as td:
            Path(td, "patch.diff").write_text(patch)
            Path(td, "eval.sh").write_text(spec.eval_script)
            sh(["docker", "cp", f"{td}/patch.diff", f"{name}:/tmp/patch.diff"])
            sh(["docker", "cp", f"{td}/eval.sh", f"{name}:/eval.sh"])
        log = ""
        for cmd in ("git apply --verbose /tmp/patch.diff", "git apply --verbose --reject /tmp/patch.diff",
                    "patch --batch --fuzz=5 -p1 -i /tmp/patch.diff"):
            a = sh(["docker", "exec", "-w", "/testbed", name, "bash", "-c", cmd])
            if a.returncode == 0:
                log = f"{APPLY_PATCH_PASS}\n{a.stdout}\n"
                break
            sh(["docker", "exec", "-w", "/testbed", name, "git", "checkout", "--", "."])
        else:
            log = f"{APPLY_PATCH_FAIL}\n"
        if APPLY_PATCH_PASS in log:
            e = sh(["docker", "exec", name, "bash", "/eval.sh"], timeout=1800)
            log += e.stdout + "\n" + e.stderr
        p = logdir / f"{inst['instance_id']}.{tag}.log"
        p.write_text(log)
        rep = get_eval_report(spec, {"instance_id": inst["instance_id"], "model_name_or_path": tag,
                                     "model_patch": patch}, str(p), include_tests_status=False)
        return {"resolved": bool(rep[inst["instance_id"]]["resolved"]), "graded": "ok"}
    except subprocess.TimeoutExpired:
        return {"resolved": False, "graded": "infra: eval timeout"}
    finally:
        sh(["docker", "rm", "-f", name])


# ---------- driver -------------------------------------------------------------------------------------------------
def task(inst: dict, arms: list[tuple[str, dict]], args, out: Path, done: set):
    iid = inst["instance_id"]
    todo = [(a, env) for a, env in arms if (iid, a) not in done]
    if not todo:
        return
    for attempt in range(3):
        if sh(["docker", "pull", "-q", inst["image"]], timeout=1800).returncode == 0:
            break
        time.sleep(10 * (attempt + 1))
    else:
        print(f"{iid}: image pull failed, skipped", flush=True)
        return
    tmp = Path(tempfile.mkdtemp(prefix="abtask-"))
    (tmp / "inst.json").write_text(json.dumps(inst))
    procs = []
    for arm, env in todo:
        e = dict(os.environ, **env, PYTHONPATH=env.get("MH_AB_APP") or str(APP), MH_FAILURES_LOG="off", MH_CACHE_KEY="1",
                 MH_AB_LOGDIR=str(out / "logs"))
        e.setdefault("MESHPOOL_API_KEY", "sk-placeholder")
        procs.append((arm, tmp / f"{arm}.json", subprocess.Popen(
            [sys.executable, __file__, "--one", str(tmp / "inst.json"), arm, str(tmp / f"{arm}.json"),
             "--model", args.model, "--max-turns", str(args.max_turns)],
            env=e, stdout=subprocess.DEVNULL, stderr=open(tmp / f"{arm}.err", "w"))))
    for arm, path, p in procs:
        try:
            p.wait(timeout=args.task_timeout)
        except subprocess.TimeoutExpired:
            p.kill()
        row = json.loads(path.read_text()) if path.exists() else {
            "ok": False, "reason": "timeout or crash: " + (tmp / f"{arm}.err").read_text()[-300:]}
        sh(["docker", "rm", "-f", f"ab_{arm}_{iid}"[:120]])
        if "infra" not in row:
            row.update(grade(inst, row.get("patch", ""), arm, out / "logs"))
        row.update(instance_id=iid, arm=arm, difficulty=inst.get("difficulty"))
        with LOCK, (out / "results.jsonl").open("a") as f:
            f.write(json.dumps(row) + "\n")
        print(f"{iid} [{arm}] resolved={row.get('resolved')} secs={row.get('secs')} "
              f"calls={(row.get('usage') or {}).get('calls')} {row.get('graded') or row.get('infra')}", flush=True)
    if not args.keep_images:
        sh(["docker", "rmi", "-f", inst["image"]])


def report(out: Path, price_in: float, price_cached: float, price_out: float):
    rows = [json.loads(l) for l in (out / "results.jsonl").read_text().splitlines() if l.strip()]
    rows = [r for r in rows if "infra" not in r and not str(r.get("graded", "")).startswith("infra")]
    by = {}
    for r in rows:
        by.setdefault(r["instance_id"], {})[r["arm"]] = r
    arms = sorted({r["arm"] for r in rows})
    paired = {k: v for k, v in by.items() if all(a in v for a in arms)}
    print(f"{len(paired)} tasks graded in every arm ({DATASET}); prices per M tokens: in ${price_in}, "
          f"cached ${price_cached}, out ${price_out}\n")
    print(f"{'arm':<10}{'solved':>10}{'$/task':>10}{'$/solved':>10}{'s/task':>9}{'s/solved':>10}{'calls':>8}"
          f"{'cache%':>8}")
    for a in arms:
        rs = [v[a] for v in paired.values()]
        n, ok = len(rs), sum(bool(r.get("resolved")) for r in rs)
        cost = [((r["usage"]["prompt"] - r["usage"]["cached"]) * price_in + r["usage"]["cached"] * price_cached
                 + r["usage"]["completion"] * price_out) / 1e6 for r in rs]
        secs = [r.get("secs") or 0 for r in rs]
        pr = sum(r["usage"]["prompt"] for r in rs) or 1
        print(f"{a:<10}{ok:>5}/{n:<4}{sum(cost) / max(n, 1):>10.4f}{sum(cost) / max(ok, 1):>10.4f}"
              f"{sum(secs) / max(n, 1):>9.0f}{sum(secs) / max(ok, 1):>10.0f}"
              f"{sum(r['usage']['calls'] for r in rs) / max(n, 1):>8.1f}"
              f"{100 * sum(r['usage']['cached'] for r in rs) / pr:>8.0f}")
    if len(arms) == 2:
        a, b = arms
        only_a = sorted(k for k, v in paired.items() if v[a].get("resolved") and not v[b].get("resolved"))
        only_b = sorted(k for k, v in paired.items() if v[b].get("resolved") and not v[a].get("resolved"))
        d, k = len(only_a) + len(only_b), min(len(only_a), len(only_b))
        p = min(1.0, 2 * sum(math.comb(d, i) for i in range(k + 1)) / 2 ** d) if d else 1.0
        print(f"\nonly {a}: {len(only_a)} {only_a}\nonly {b}: {len(only_b)} {only_b}\nexact McNemar p = {p:.3f}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--one", nargs=3, metavar=("INST", "ARM", "OUT"))
    ap.add_argument("--arm", action="append", default=[], help="name:VAR=val,VAR=val")
    ap.add_argument("--n", type=int, default=30)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--ids", default="", help="comma-separated instance ids (overrides --n)")
    ap.add_argument("--parallel", type=int, default=3)
    ap.add_argument("--model", default="deepseek-v4.1-flash")
    ap.add_argument("--max-turns", type=int, default=60)
    ap.add_argument("--task-timeout", type=int, default=1800)
    ap.add_argument("--keep-images", action="store_true")
    ap.add_argument("--out", default="ab-out")
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--price", nargs=3, type=float, default=(0.14, 0.0028, 0.28),
                    metavar=("IN", "CACHED", "OUT"), help="$ per million tokens (assumed list prices)")
    args = ap.parse_args()
    if args.one:
        return run_one(*args.one, model=args.model, max_turns=args.max_turns)
    out = Path(args.out)
    if args.report:
        return report(out, *args.price)
    (out / "logs").mkdir(parents=True, exist_ok=True)
    arms = []
    for spec in args.arm:
        name, _, envs = spec.partition(":")
        arms.append((name, dict(kv.split("=", 1) for kv in envs.split(",") if kv)))
    insts = load(set(args.ids.split(",")) if args.ids else None)
    if not args.ids:
        random.Random(args.seed).shuffle(insts)
        insts = insts[:args.n]
    done = set()
    if (out / "results.jsonl").exists():
        done = {(r["instance_id"], r["arm"]) for r in map(json.loads, (out / "results.jsonl").read_text().splitlines())
                if r.get("graded") and not str(r.get("graded")).startswith("infra")}
    with ThreadPoolExecutor(max_workers=args.parallel) as ex:
        list(ex.map(lambda i: task(i, arms, args, out, done), insts))
    report(out, *args.price)


if __name__ == "__main__":
    main()
