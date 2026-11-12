"""Offline cache audit from a per-call usage log (ts,tag,model,prompt,cached,output). No model calls.

For each call after the first in a tag, the context grew by delta = prompt - prev_prompt tokens. A perfect prefix cache
misses only about `delta`. `excess` = (prompt - cached) - max(delta, 0) is the miss the cache should have avoided
(routing to a cold backend, a changed prefix, compaction). Prints per agent prefix: calls, avg prompt, miss/call,
delta/call, excess/call, output/call, and the cost split at list prices (uncached 0.15, cached 0.003, out 0.6 $/M).
Usage: python3 cache_audit.py usage.csv [tag-prefix ...]"""
import csv, sys
from collections import defaultdict

def main(path, prefixes):
    rows = defaultdict(list)
    for r in csv.reader(open(path)):
        if len(r) < 6 or not r[3].isdigit():
            continue
        rows[r[1]].append((r[0], int(r[3]), int(r[4]), int(r[5])))
    agg = defaultdict(lambda: defaultdict(float))
    for tag, calls in rows.items():
        p = next((x for x in prefixes if tag.startswith(x)), None)
        if not p:
            continue
        calls.sort()
        a = agg[p]; a["tasks"] += 1
        prev = None
        for ts, pr, ca, out in calls:
            a["calls"] += 1; a["prompt"] += pr; a["miss"] += pr - ca; a["out"] += out; a["cached"] += ca
            if prev is not None:
                d = max(pr - prev, 0); a["delta"] += d; a["excess"] += max(pr - ca - d, 0); a["n2"] += 1
                if pr < prev * 0.9: a["shrinks"] += 1
            else:
                a["first_miss"] += pr - ca
            prev = pr
    print(f"{'agent':24}{'tasks':>6}{'calls/t':>8}{'prompt/c':>9}{'miss/c':>8}{'delta/c':>8}{'excess/c':>9}{'out/c':>7}"
          f"{'$unc/t':>8}{'$cach/t':>8}{'$out/t':>8}")
    for p, a in agg.items():
        c, t = a["calls"] or 1, a["tasks"] or 1
        print(f"{p:24}{a['tasks']:6.0f}{c/t:8.1f}{a['prompt']/c:9.0f}{a['miss']/c:8.0f}{a['delta']/max(a['n2'],1):8.0f}"
              f"{a['excess']/max(a['n2'],1):9.0f}{a['out']/c:7.0f}{a['miss']*.15e-6/t:8.4f}{a['cached']*.003e-6/t:8.4f}"
              f"{a['out']*.6e-6/t:8.4f}")

if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2:])
