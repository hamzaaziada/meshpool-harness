"""Same-time Terminal-Bench 2.0 comparison: harness arms, the app agent, Codex CLI and pi on one model.

  python3 tb_bench.py --tb <terminal-bench-2 dir> --tasks a,b --arms v5,app,codex,pi --out out.jsonl --budget 5

Per task each arm gets its own container from the task's prebuilt image (host network, through this machine's
proxy), the same instruction plus the docker exec form, an empty local folder, and the task's wall-clock limit plus
600 s (the earlier runs' rule). Grading copies the task's tests into the container and reads
/logs/verifier/reward.txt."""
from __future__ import annotations

import argparse, json, os, shutil, sys, tempfile, time, tomllib
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import mw_bench as M        # noqa: E402
import ver_bench as V       # noqa: E402
from spend import spend     # noqa: E402

CA = os.environ.get("BENCH_CA", "/root/.ccr/ca-bundle.crt")
ARMS = {"v4": {"MH_PRESET": "v4"}, "v5": {"MH_PRESET": "v5"}, "app": {"MH_PRESET": "v5"}, "codex": {}, "pi": {}}
sh = V.sh


def meta(tb, name):
    return tomllib.loads((Path(tb) / name / "task.toml").read_text())


def proxy_env():
    hp = os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy", "")
    ev = []
    for k in ("HTTPS_PROXY", "HTTP_PROXY", "https_proxy", "http_proxy"):
        ev += ["-e", f"{k}={hp}"]
    return ev + ["-e", "NO_PROXY=localhost,127.0.0.1", "-e", "SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt",
                 "-e", "REQUESTS_CA_BUNDLE=/etc/ssl/certs/ca-certificates.crt",
                 "-e", "PIP_CERT=/etc/ssl/certs/ca-certificates.crt", "-e", "CURL_CA_BUNDLE=/etc/ssl/certs/ca-certificates.crt"]


def pull(img):
    for attempt in range(4):
        if not sh(["docker", "image", "inspect", img]).returncode:
            return
        if not sh(["docker", "pull", img], timeout=2400).returncode:
            return
        time.sleep(20 * (attempt + 1))
    raise RuntimeError(f"pull failed: {img}")


def setup(tb, name, arm):
    m = meta(tb, name)
    img = m["environment"]["docker_image"]
    pull(img)
    c = f"tbb_{arm}_{name}"[:60]
    sh(["docker", "rm", "-f", c])
    r = sh(["docker", "run", "-d", "--name", c, "--network", "host", *proxy_env(),
            "-v", f"{CA}:/usr/local/share/ca-certificates/proxy.crt:ro", "--entrypoint", "sleep", img, "infinity"])
    if r.returncode:
        raise RuntimeError(f"docker run failed: {r.stderr[-300:]}")
    sh(["docker", "exec", c, "bash", "-lc",
        "mkdir -p /etc/ssl/certs; cat /usr/local/share/ca-certificates/proxy.crt >> /etc/ssl/certs/ca-certificates.crt "
        "2>/dev/null; sed -E -i 's#http://(deb.debian.org|security.debian.org|archive.ubuntu.com|security.ubuntu.com|"
        "ports.ubuntu.com)#https://\\1#g' /etc/apt/sources.list /etc/apt/sources.list.d/* 2>/dev/null; "
        "mkdir -p /logs/verifier /logs/agent; true"])
    wd = sh(["docker", "inspect", "-f", "{{.Config.WorkingDir}}", img]).stdout.strip() or "/app"
    host = Path(tempfile.mkdtemp(prefix=f"tbb_{arm}_"))
    return {"container": c, "workdir": wd, "host": host, "timeout": int(m.get("agent", {}).get("timeout_sec", 900)),
            "vtimeout": int(m.get("verifier", {}).get("timeout_sec", 900))}


def instructions(tb, name, env):
    text = (Path(tb) / name / "instruction.md").read_text().strip()
    c, wd = env["container"], env["workdir"]
    return (f"{text}\n\n---\nEnvironment: the task runs inside a Docker container (working directory {wd}). Do all work "
            f"inside it. Run every command with:\n  docker exec -w {wd} {c} bash -lc '<command>'\n"
            f"To write a file in the container you can use: docker exec -i -w {wd} {c} bash -lc 'cat > path' <<'EOF' ... "
            f"EOF or write it locally and copy it with: docker cp <local file> {c}:<path>\n"
            f"The container has internet access. When the task is complete, stop.")


def grade(tb, name, env):
    c = env["container"]
    sh(["docker", "exec", c, "bash", "-lc", "rm -rf /tests && mkdir -p /tests /logs/verifier"])
    sh(["docker", "cp", str(Path(tb) / name / "tests") + "/.", f"{c}:/tests/"])
    try:
        r = sh(["docker", "exec", "-w", env["workdir"], c, "bash", "-lc", "bash /tests/test.sh"],
               timeout=env["vtimeout"] + 300)
        tail = (r.stdout + r.stderr)[-800:]
    except Exception:  # noqa: BLE001
        tail = "verifier timeout"
    reward = sh(["docker", "exec", c, "bash", "-lc", "cat /logs/verifier/reward.txt 2>/dev/null"]).stdout.strip()
    return reward.startswith("1"), tail


def one(arm, tb, name):
    row = {"arm": arm, "bench": "tb2", "task": name, "model": M.MODEL}
    env = None
    try:
        env = setup(tb, name, arm)
        text = instructions(tb, name, env)
        wall = env["timeout"] + 600
        t0 = time.time()
        old = V.WALL
        V.WALL = wall
        if arm.startswith("v"):
            r = V.run_engine(text, str(env["host"]), dict(ARMS[arm], MH_MODE="terminal"), "code", f"{arm}-tb-{name}")
        elif arm == "app":
            r = V.run_engine(text, str(env["host"]), ARMS[arm], "agent", f"{arm}-tb-{name}")
        elif arm == "codex":
            r = M.run_codex(text, str(env["host"]), wall)
        else:
            r = M.run_pi(text, str(env["host"]), wall)
        V.WALL = old
        row["wall_s"] = round(time.time() - t0, 1)
        row.update(r)
        row["passed"], row["test_tail"] = grade(tb, name, env)
    except Exception as e:  # noqa: BLE001
        row["infra_error"] = f"{type(e).__name__}: {e}"[:400]
        row.setdefault("passed", False)
    finally:
        if env:
            sh(["docker", "rm", "-f", env["container"]])
            run = env["host"] / ".run"
            if run.is_dir():
                td = Path(__file__).resolve().parent / "results" / "transcripts" / f"tb-{arm}"
                td.mkdir(parents=True, exist_ok=True)
                for f in run.glob("transcript-*.jsonl"):
                    shutil.copy(f, td / f"{name}.jsonl")
            shutil.rmtree(env["host"], ignore_errors=True)
    unc = max(row.get("prompt", 0) - row.get("cached", 0), 0)
    row["cost"] = round((unc * M.PRICE["in"] + row.get("cached", 0) * M.PRICE["cached"]
                         + row.get("output", 0) * M.PRICE["out"]) / 1e6, 6)
    return row


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tb", required=True)
    ap.add_argument("--tasks", required=True)
    ap.add_argument("--arms", default="v5,app,codex,pi")
    ap.add_argument("--out", required=True)
    ap.add_argument("--budget", type=float, default=5.0)
    a = ap.parse_args()
    s0 = spend()
    print(f"key spend at start: ${s0}", flush=True)
    for name in a.tasks.split(","):
        s = spend()
        if s0 is not None and s is not None and s - s0 > a.budget:
            print(f"STOP: spent ${s - s0:.2f} > budget ${a.budget}", flush=True)
            break
        try:
            pull(meta(a.tb, name)["environment"]["docker_image"])
        except Exception as e:  # noqa: BLE001
            print(f"skip {name}: {e}", flush=True)
            continue
        arms = a.arms.split(",")
        with ThreadPoolExecutor(len(arms)) as ex:
            rows = list(ex.map(lambda arm: one(arm, a.tb, name), arms))
        with open(a.out, "a") as f:
            for r in rows:
                f.write(json.dumps(r) + "\n")
        print(" | ".join(f"{r['arm']}:{'OK' if r.get('passed') else 'x'} {r.get('wall_s', '-')}s ${r.get('cost', 0):.4f}"
                         + (f" INFRA {r['infra_error'][:50]}" if r.get('infra_error') else "") for r in rows),
              f"<- {name}", flush=True)
        sh(["docker", "rmi", "-f", meta(a.tb, name)["environment"]["docker_image"]])
        sh("docker container prune -f; docker image prune -f")
    s1 = spend()
    print(f"key spend at end: ${s1} (this run: ${(s1 or 0) - (s0 or 0):.2f})", flush=True)


if __name__ == "__main__":
    main()
