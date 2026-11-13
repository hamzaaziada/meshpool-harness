"""CLI: python -m engines.research.check report.md  -> PASS or the problems (sources.json next to the report)."""
import sys
from pathlib import Path

from engines.research.library import Corpus
from engines.research.verify import check, report_text

if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit("usage: python -m engines.research.check <report.md>")
    p = Path(sys.argv[1])
    c = Corpus(str(p.resolve().parent))
    probs = check(p.read_text(), c)
    if probs:
        print(report_text(probs, 40))
        print(f"FAIL: {len(probs)} problems; {len(c.sources)} sources, {len(c.passages)} passages")
        sys.exit(1)
    print(f"PASS: {len(c.sources)} sources, {len(c.passages)} passages")
