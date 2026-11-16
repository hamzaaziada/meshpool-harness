"""Calibrate the partner and MD reviewers against published work from the best firms.

Downloads a Goldman Sachs board discussion book that is a public SEC filing (Antero Midstream, Schedule 13E-3/A,
Exhibit (c)(23), 2018; one GIF per page), has the same reviewers grade it as a deck, and prints each reviewer's raw
score. The offset that would put that score at 8 is the calibration in app/engines/forge/rules.yaml
(review.personas.bar.calibration). The pages are not kept in the repo.

    MESHPOOL_API_KEY=... MH_REVIEW_MODEL=deepseek-v4.1-flash python testkit/calibration/reviewers.py [first last]
"""
import io
import json
import os
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "app"))

BASE = "https://www.sec.gov/Archives/edgar/data/1598968/000110465918065860/g383014oci0{:02d}.gif"
UA = os.environ.get("SEC_USER_AGENT", "meshpool-calibration research@example.com")   # the SEC asks for a contact


def pages(first: int, last: int, folder: Path) -> list[tuple[int, Path]]:
    from PIL import Image
    out = []
    for i in range(first, last + 1):
        req = urllib.request.Request(BASE.format(i), headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=30) as r:
            im = Image.open(io.BytesIO(r.read())).convert("RGB")
        im = im.resize((1100, int(1100 * im.height / im.width)))
        p = folder / f"p{i:02d}.png"
        im.save(p)
        out.append((len(out) + 1, p))
        time.sleep(0.2)
    return out


def main():
    from engines.forge import critics
    from engines.forge.common import load_rules
    from meshpool_harness.core.client import LiveClient
    first, last = (int(sys.argv[1]), int(sys.argv[2])) if len(sys.argv) > 2 else (7, 22)
    os.environ["MH_REVIEW_CALIBRATION"] = "0"                 # raw scores: the calibration is what we measure
    with tempfile.TemporaryDirectory() as d:
        pngs = pages(first, last, Path(d))
        slides = [{"archetype": "page", "title": "(title on the page)"} for _ in pngs]
        pn = critics.deck_panel(LiveClient("calibration"), "", slides, d, critics.settings(load_rules()),
                                task_id="calibration", pngs=pngs)
    raw = {r["persona"]: r.get("overall") for r in pn["reviews"]}
    print(json.dumps({"pages": [first, last], "raw": raw,
                      "offset_to_8": {k: round(8 - v, 2) for k, v in raw.items() if isinstance(v, (int, float))}}))


if __name__ == "__main__":
    main()
