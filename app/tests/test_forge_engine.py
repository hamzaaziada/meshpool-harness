"""Forge engine entry: the slide count is read the way the brief settles it, and an explicit field wins."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from engines.forge.brief import complete, settled, slide_count  # noqa: E402


def test_slide_count_parses_every_form_the_brief_accepts():
    for t in ("6 pages", "a 6-page deck", "slides: 6", "6slides", "a 6-slide deck", "6 slides"):
        assert settled(t)["slides"] and slide_count(t) == 6, t
    assert slide_count("Make a 10 slide deck from the 40-page report") == 10
    assert slide_count("a deck on pricing") is None


def test_explicit_slides_field_wins(monkeypatch, tmp_path):
    t = complete("Make a 10 slide deck on pricing for the board, ocean theme, density: present", None, {"slides": 6})
    assert slide_count(t) == 6
    from engines.forge import engine as FE
    seen = {}

    def fake_build(brief, out, n, **k):
        seen["n"] = n
        raise SystemExit
    monkeypatch.setattr("engines.forge.build.build", fake_build)
    monkeypatch.setattr(FE, "brand_from", lambda task, files=None: ({}, None))
    try:
        FE.solve("A 6-page deck on pricing for the board, ocean theme, density: present", workdir=str(tmp_path),
                 task_id="t")
    except SystemExit:
        pass
    assert seen["n"] == 6
