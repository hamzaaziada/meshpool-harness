"""Score a same-time run: solves, $ per solved, time per solved, per arm, on tasks every arm finished without an
infrastructure error. Usage: python3 report.py results/*.jsonl"""
import json
import sys
from collections import defaultdict


def main(paths):
    rows = [json.loads(l) for p in paths for l in open(p) if l.strip()]
    arms = sorted({r["arm"] for r in rows})
    by = defaultdict(dict)
    for r in rows:
        by[(r["task"], str(r.get("seed", "")))][r["arm"]] = r
    keep = {k: v for k, v in by.items() if all(a in v and not v[a].get("infra_error") for a in arms)}
    dropped = len(by) - len(keep)
    print(f"{len(keep)} tasks scored ({dropped} dropped: an arm missing or an infrastructure error)\n")
    print(f"{'arm':8}{'solved':>10}{'$/solved':>10}{'s/solved':>10}{'$ total':>9}{'s total':>9}{'calls/t':>8}"
          f"{'out tok/t':>10}{'cache%':>8}{'miss/t':>7}")
    for a in arms:
        rs = [v[a] for v in keep.values()]
        n, ok = len(rs), sum(bool(r.get("passed")) for r in rs)
        cost, wall = sum(r.get("cost", 0) for r in rs), sum(r.get("wall_s", 0) or 0 for r in rs)
        pr, ca = sum(r.get("prompt", 0) for r in rs), sum(r.get("cached", 0) for r in rs)
        print(f"{a:8}{f'{ok}/{n}':>10}{(cost / ok if ok else float('nan')):10.4f}{(wall / ok if ok else float('nan')):10.1f}"
              f"{cost:9.4f}{wall:9.0f}{sum(r.get('calls', 0) or 0 for r in rs) / max(n, 1):8.1f}"
              f"{sum(r.get('output', 0) for r in rs) / max(n, 1):10.0f}{100 * ca / max(pr, 1):8.1f}"
              f"{sum(r.get('full_miss_calls', 0) or 0 for r in rs) / max(n, 1):7.1f}")
    print("\nper task:")
    for (t, s), v in sorted(keep.items()):
        print(f"  {t[:34]:34}" + " ".join(f"{a}:{'OK' if v[a].get('passed') else '--'}/{v[a].get('wall_s', 0):.0f}s"
                                         for a in arms))


if __name__ == "__main__":
    main(sys.argv[1:])
