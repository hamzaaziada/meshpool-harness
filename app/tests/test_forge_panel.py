"""The senior partner and the MD inside the deck build (engines/forge/critics.py, research.py, build.py): the
storyboard loop with research before any slide is written, the deck loop that routes each issue to the step that
fixes it, keeps the best version, reports honestly when the reviewers cannot run, and holds the slide count."""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from engines.forge import build as B, critics as C, research as RS  # noqa: E402
from engines.forge.common import load_rules  # noqa: E402
from engines.review import personas as P  # noqa: E402

BRIEF = ("Make a 4-slide deck on Northwind's regional growth for the board.\n"
         "Facts\n- North revenue $120m, South $90m, East $60m in 2025\n- North grew 18% in 2025\n"
         "- Group margin was 14% in 2025")
GOOD = {"partner": {"storyboard": {"answer": 9, "storyline": 9, "evidence": 9, "visual_plan": 9},
                    "deck": {"storyline": 9, "proof": 9, "visuals": 9, "craft": 9, "language": 9}},
        "md": {"storyboard": {"deal_logic": 9, "sourcing": 9, "risk": 9},
               "deck": {"accuracy": 9, "sourcing": 9, "valuation": 9, "honesty": 9, "polish": 9}}}


def _scores(who, target, v):
    return {k: v for k in GOOD[who][target]}


def _plan(extra=None):
    sl = [{"node": "0", "title": "Northwind regional growth", "archetype": "cover"},
          {"node": "1.1", "section": "Growth", "title": "North revenue reached $120m in 2025", "archetype": "bar_takeaway",
           "visual": "bar chart", "evidence": ["F1"]},
          {"node": "1.2", "section": "Growth", "title": "North grew 18% in 2025", "archetype": "column_trend",
           "visual": "column chart", "evidence": ["F2"]},
          {"node": "2.1", "section": "Margin", "title": "Group margin held at 14% in 2025", "archetype": "bar_takeaway",
           "visual": "benchmark bars", "evidence": ["F3"]}]
    return {"governing_thought": "North drives the growth", "slides": sl + (extra or [])}


def _slide_body(x):
    if x["archetype"] == "cover":
        return dict(x, subtitle="Board", date="November 2026")
    return dict(x, subtitle="Revenue by region, $m, 2025", takeaway={"text": "North leads growth", "prov": ["F1"]},
                chart={"kind": "bar", "categories": ["East", "North", "South"],
                       "series": [{"name": "Revenue", "values": [60, 120, 90]}], "unit": "$m"},
                bullets=[{"text": "North revenue was $120m, the largest of the three regions this year", "prov": ["F1"]},
                         {"text": "South revenue was $90m and East revenue was $60m in the same year", "prov": ["F1"]}],
                source={"text": "Company data", "prov": ["F1"]})


class Stub:
    """Writer, storyline and reviewers in one: `reviewer(who, target, material, n)` gives each reply."""

    def __init__(self, reviewer, story=None, replan=None, deck_replan=None, fix=None):
        self.reviewer, self.story, self.replan, self.deck_replan, self.fix = reviewer, story, replan, deck_replan, fix
        self.calls = {"partner": {"storyboard": 0, "deck": 0}, "md": {"storyboard": 0, "deck": 0}}
        self.content, self.fixes, self.models = {}, [], []

    def chat(self, *, model, messages, **kw):
        sys_, user = messages[0]["content"], messages[-1]["content"]
        text = " ".join(p.get("text", "") for p in user if isinstance(p, dict)) if isinstance(user, list) else user
        if "You are reviewing" in sys_:
            who = "partner" if "senior partner" in sys_ else "md"
            target = "storyboard" if "STORYBOARD" in sys_ else "deck"
            self.calls[who][target] += 1
            self.models.append(model)
            body = self.reviewer(who, target, text, self.calls[who][target])
            if isinstance(body, Exception):
                raise body
        elif sys_.startswith("REVISION"):
            body = self.replan()
        elif "engagement manager at a top strategy firm" in sys_:
            body = self.story or _plan()
        elif "raised storyline issues" in sys_:
            body = self.deck_replan()
        elif "Fix this one consulting slide" in sys_:
            self.fixes.append(text)
            body = json.loads(text.split("Slide:\n", 1)[1].split("\n\nFacts", 1)[0])
            if self.fix:
                body = self.fix(body)
        elif "ONE consulting slide" in sys_:
            x = json.loads(text.split("This slide: ", 1)[1].split("\n\nFacts", 1)[0])
            self.content[x["node"]] = text
            body = _slide_body(x)
        elif "You revise a consulting storyboard" in sys_:
            body = json.loads(text.split("Storyboard:\n", 1)[1])
        else:
            body = {"edits": []}
        return {"choices": [{"message": {"content": json.dumps(body)}}]}


@pytest.fixture
def live(monkeypatch, tmp_path):
    """Reviewers on for stub clients, fake page images (no LibreOffice render for the reviewer), one rule round."""
    from PIL import Image
    monkeypatch.setattr(B, "_review_on", lambda client, rules: True)

    def pngs(pptx, out_dir, width):
        from pptx import Presentation
        out = []
        for i in range(1, len(Presentation(pptx).slides) + 1):
            p = tmp_path / f"page{i}.png"
            Image.new("RGB", (8, 5), "white").save(p)
            out.append((i, p))
        return out
    monkeypatch.setattr(C, "deck_pngs", pngs)
    rules = load_rules()
    rules["repair"]["max_rounds"] = 0
    return rules


def _signoff(who, target, text, n):
    return {"scores": _scores(who, target, 9), "headline": "Ready"}


def test_storyboard_loop_researches_replans_and_new_facts_reach_the_writer(live, tmp_path, monkeypatch):
    asked = []

    def investigate(qs, facts, **kw):
        asked.extend(q["question"] for q in qs)
        f = {"id": f"F{RS.next_fact_no(facts)}", "text": "Northwind backlog was $300m at end-2025 "
             "(Source: example.com, Annual report; https://example.com/ar)", "url": "https://example.com/ar"}
        return {"facts": [f], "questions": [{"question": qs[0]["question"], "status": "found", "facts": [f["id"]],
                                             "sources": [f["url"]]},
                                            {"question": qs[1]["question"], "status": "nothing found", "facts": [],
                                             "sources": []}], "seconds": 0.1}
    monkeypatch.setattr(RS, "investigate", investigate)
    monkeypatch.setattr(RS, "network_ok", lambda timeout=3.0: True)

    def reviewer(who, target, text, n):
        if target == "deck":
            return _signoff(who, target, text, n)
        if "F4:" not in text:                                  # round 1: the backlog is missing
            return {"scores": _scores(who, target, 6), "headline": "No backlog",
                    "issues": [{"severity": "major", "scope": "research", "where": "slide 3",
                                "issue": "growth claim has no forward evidence", "fix": "Find Northwind's 2025 backlog"}],
                    "research_requests": [{"question": "What was Northwind's backlog at end-2025?", "why": "proves growth"}]}
        return _signoff(who, target, text, n)

    replanned = _plan([{"node": "2.2", "title": "One slide too many", "archetype": "bar_takeaway", "evidence": ["F3"]}])
    replanned["slides"][2] = dict(replanned["slides"][2], title="Backlog of $300m underpins North's growth",
                                  evidence=["F2", "F4"])
    stub = Stub(reviewer, replan=lambda: replanned)
    res = B.build(BRIEF, str(tmp_path), slides_wanted=4, client=stub, rules=live, task_id="t", research=True)

    sb = json.loads((tmp_path / "storyboard.json").read_text())
    assert [r["signed_off"] for r in sb["rounds"]] == [False, True]
    assert sb["rounds"][0]["research"]["facts"][0]["id"] == "F4" and sb["rounds"][0]["replanned"]
    assert "What was Northwind's backlog at end-2025?" in asked and "Find Northwind's 2025 backlog" in asked
    assert "F4: Northwind backlog was $300m" in stub.content["1.2"]      # the new fact reaches the content call
    assert "https://example.com/ar" in stub.content["1.2"]
    rep = res["panel"]
    assert rep["storyboard"]["signed_off"] and rep["storyboard"]["rounds"][0]["new_facts"] == ["F4"]
    assert rep["storyboard"]["rounds"][0]["research"][1]["status"] == "nothing found"   # recorded, not invented
    assert rep["status"] == "signed_off" and rep["deck"]["rounds"][0]["signed_off"]
    from pptx import Presentation
    assert len(Presentation(res["pptx"]).slides) == 4                    # the extra planned slide was cut
    qa = json.loads((tmp_path / "qa.json").read_text())
    assert qa["panel"]["research"]["steps"][0]["new_facts"][0]["id"] == "F4"
    from engines.forge.engine import panel_sentence
    s = panel_sentence(rep)
    assert "Storyboard: 2 review rounds" in s and "Senior partner 9.0/10 signs off; MD 9.0/10 signs off" in s


def test_deck_loop_routes_content_layout_and_storyline_and_stops_on_signoff(live, tmp_path):
    def reviewer(who, target, text, n):
        if target == "storyboard" or n >= 2:
            return _signoff(who, target, text, n)
        if who == "partner":
            return {"scores": _scores(who, target, 7), "headline": "Not yet", "issues": [
                {"severity": "major", "scope": "content", "where": "slide 2", "issue": "title not proved",
                 "fix": "state the 18% growth on the page"},
                {"severity": "major", "scope": "layout", "where": "slide 3", "issue": "bars are not sorted",
                 "fix": "sort the bars largest first"},
                {"severity": "major", "scope": "layout", "where": "slide 4", "issue": "the page is half empty",
                 "fix": "add a stats part"},
                {"severity": "major", "scope": "storyline", "where": "slides 3-4",
                 "issue": "margin should come before growth rate", "fix": "swap slides 3 and 4"}]}
        return {"scores": _scores(who, target, 7), "issues": [
            {"severity": "blocking", "scope": "data", "where": "slide 2", "issue": "the $120m is not dated",
             "fix": "say 2025"}]}

    swap = {"slides": [{"from": [1], "title": "Northwind regional growth", "archetype": "cover"},
                       {"from": [2], "title": "North revenue reached $120m in 2025", "archetype": "bar_takeaway"},
                       {"from": [4], "title": "Group margin held at 14% in 2025", "archetype": "bar_takeaway"},
                       {"from": [3], "title": "North grew 18% in 2025", "archetype": "column_trend"}]}
    stub = Stub(reviewer, deck_replan=lambda: swap)
    res = B.build(BRIEF, str(tmp_path), slides_wanted=4, client=stub, rules=live, task_id="t")

    rv = json.loads((tmp_path / "review.json").read_text())
    assert [r["signed_off"] for r in rv["rounds"]] == [False, True] and stub.calls["partner"]["deck"] == 2
    act = rv["rounds"][0]["actions"]
    assert act["storyline"]["status"] == "applied"
    assert act["layout_fixed"] == {"4": "bars sorted largest first"}       # old slide 3, now 4: deterministic
    fixed_text = "\n".join(stub.fixes)
    assert "[review] (partner, major, content) title not proved" in fixed_text
    assert "[review] (md, blocking, data) the $120m is not dated" in fixed_text
    assert "the page is half empty" in fixed_text and "pick an archetype" in fixed_text   # layout -> writer
    slides = json.loads((tmp_path / "slides.json").read_text())
    assert [s["title"] for s in slides][2:] == ["Group margin held at 14% in 2025", "North grew 18% in 2025"]
    assert slides[3]["chart"]["categories"] == ["North", "South", "East"]
    assert len(slides) == 4 and res["panel"]["status"] == "signed_off"


def test_a_worse_round_is_reverted_to_the_best_slides(live, tmp_path):
    def reviewer(who, target, text, n):
        if target == "storyboard":
            return _signoff(who, target, text, n)
        if n == 1:
            return {"scores": _scores(who, target, 7.5), "issues": [
                {"severity": "major", "scope": "content", "where": "slide 2", "issue": "so-what missing",
                 "fix": "add the implication"}]}
        return {"scores": _scores(who, target, 6), "issues": [
            {"severity": "major", "scope": "content", "where": "slide 2", "issue": "worse now", "fix": "undo"}]}

    stub = Stub(reviewer, fix=lambda s: dict(s, title="A worse title nobody asked for"))
    B.build(BRIEF, str(tmp_path), slides_wanted=4, client=stub, rules=live, task_id="t")
    rv = json.loads((tmp_path / "review.json").read_text())
    assert [r["score"] for r in rv["rounds"]] == [7.5, 6.0, 6.0]
    assert rv["rounds"][1]["reverted_to"] == 1 and rv["kept_round"] == 1
    slides = json.loads((tmp_path / "slides.json").read_text())
    assert slides[1]["title"] == "North revenue reached $120m in 2025"    # the best version, not the last
    assert "[REVIEW] Senior partner and MD (major, content): so-what missing Fix: add the implication" in \
        slides[1]["notes"]
    qa = json.loads((tmp_path / "qa.json").read_text())
    assert qa["panel"]["status"] == "revise" and [r["score"] for r in qa["panel"]["deck"]["rounds"]] == [7.5, 6.0, 6.0]


def test_reviewers_that_cannot_run_are_reported_not_scored(live, tmp_path):
    stub = Stub(lambda *a: RuntimeError("HTTP 401: Invalid or missing API key"))
    res = B.build(BRIEF, str(tmp_path), slides_wanted=4, client=stub, rules=live, task_id="t")
    rep = res["panel"]
    assert rep["status"] == "not_run" and rep["line"].startswith("Senior partner and MD could not review the deck")
    assert "refused the key" in rep["line"] and "/10" not in rep["line"]
    assert rep["deck"]["rounds"][0]["score"] is None and len(rep["deck"]["rounds"]) == 1
    assert len(rep["storyboard"]["rounds"]) == 1 and rep["storyboard"]["rounds"][0]["score"] is None
    assert stub.calls["partner"]["storyboard"] == 1                      # a refused key stops the model chain
    qa = json.loads((tmp_path / "qa.json").read_text())
    assert qa["panel"]["status"] == "not_run" and qa["panel"]["deck"]["rounds"][0]["why_not_run"] == \
        "the model gateway refused the key"
    from engines.forge.engine import panel_sentence
    s = panel_sentence(rep)
    assert "could not run (the model gateway refused the key)" in s and "no review score" in s


def test_the_slide_count_holds_when_a_replan_adds_slides():
    slides = [{"archetype": "cover", "title": "c"}, {"archetype": "grid", "title": "a"},
              {"archetype": "agenda", "title": "Agenda"}, {"archetype": "closing", "title": "z"}]
    locked = lambda i: slides[i]["archetype"] == "agenda"  # noqa: E731
    five = [{"from": [1]}, {"from": [2]}, {"from": [3]}, {"from": [], "title": "New", "archetype": "grid"}, {"from": [4]}]
    assert B.check_replan(five, slides, locked)[1].startswith("re-plan has 5 slides")
    merged = [{"from": [1]}, {"from": [2, 4]}, {"from": [3]}, {"from": [], "title": "x", "archetype": "grid"}]
    assert "closing slide must stay last" in B.check_replan(merged, slides, locked)[1]
    moved = [{"from": [1]}, {"from": [3]}, {"from": [2]}, {"from": [4]}]
    assert B.check_replan(moved, slides, locked)[1] == ""
    twice = [{"from": [1]}, {"from": [2]}, {"from": [2]}, {"from": [4]}]
    assert B.check_replan(twice, slides, locked)[1] == "re-plan uses a slide twice"
    gone = [{"from": [1]}, {"from": [2]}, {"from": [], "title": "x", "archetype": "grid"}, {"from": [4]}]
    assert "locked slide 3" in B.check_replan(gone, slides, locked)[1]


def test_research_takes_facts_only_from_passages_and_records_misses(tmp_path):
    page = ("Northwind Annual Report 2025. Northwind reported revenue of $270 million in 2025, up 12% from 2024. "
            "The order backlog stood at $300 million at the end of 2025, the highest in the company's history. "
            "Management expects the backlog to convert over the next two years.")
    search = lambda q, n, recency="", region="", site="": (  # noqa: E731
        [{"url": "https://ir.northwind.example/annual-report-2025", "title": "Annual report"}]
        if "backlog" in q.lower() else [])
    fetch = lambda url: {"title": "Northwind Annual Report 2025", "text": page * 2, "url": url, "date": "2026-02-01"}  # noqa: E731
    facts = [{"id": "F1", "text": "x 1"}, {"id": "F7", "text": "y 7"}, {"id": "W2", "text": "z"}]

    class Inventor:                                         # a model that adds a number the passage does not state
        def chat(self, *, model, messages, **kw):
            pid = messages[-1]["content"].split("[", 1)[1].split("]", 1)[0]
            return {"choices": [{"message": {"content": json.dumps({"facts": [
                {"passage": pid, "text": "The order backlog stood at $300 million at the end of 2025"},
                {"passage": pid, "text": "The order backlog stood at $450 million at the end of 2025"}]})}}]}
    res = RS.investigate(["What was Northwind's order backlog at the end of 2025?", "Zebra migration in Kenya"],
                         facts, client=Inventor(), models=["m"], out_dir=str(tmp_path), search_fn=search, fetch_fn=fetch)
    got = res["facts"]
    assert [f["id"] for f in got] == ["F8"] and "$300 million" in got[0]["text"]
    assert "https://ir.northwind.example/annual-report-2025" in got[0]["text"] and got[0]["url"]
    assert [q["status"] for q in res["questions"]] == ["found", "nothing found"]
    plain = RS.investigate(["Northwind order backlog end of 2025"], facts, out_dir=str(tmp_path / "b"),
                           search_fn=search, fetch_fn=fetch)       # no model: deterministic sentence pick
    assert plain["facts"] and all("$300 million" in f["text"] or "$270 million" in f["text"] for f in plain["facts"])


def test_research_is_off_unless_allowed(monkeypatch):
    rules = load_rules()
    monkeypatch.setattr(RS, "network_ok", lambda timeout=3.0: True)
    assert RS.allowed(object(), rules, "brief")[0] is False                       # stub client
    assert RS.allowed(object(), rules, "brief", option=True)[0] is True
    assert RS.allowed(object(), rules, "Use only the facts provided.", option=None)[0] is False
    monkeypatch.setenv("MH_RESEARCH", "0")
    assert RS.allowed(object(), rules, "brief", option=True) == (False, "research off (MH_RESEARCH=0)")
    monkeypatch.delenv("MH_RESEARCH")
    monkeypatch.setattr(RS, "network_ok", lambda timeout=3.0: False)
    assert RS.allowed(object(), rules, "brief", option=True) == (False, "no network")
    q = RS.questions_from({"reviews": [{"persona": "md", "research_requests": [{"question": "Q one?"}] * 2}],
                           "issues": [{"scope": "research", "fix": "Find X", "issue": "x", "persona": "partner"},
                                      {"scope": "content", "fix": "no", "issue": "y"}]})
    assert [x["question"] for x in q] == ["Q one?", "Find X"]


def test_deck_pages_are_batched_and_each_persona_merged(tmp_path):
    from PIL import Image
    pngs = []
    for i in range(1, 9):
        p = tmp_path / f"p{i}.png"
        Image.new("RGB", (4, 3)).save(p)
        pngs.append((i, p))
    slides = [{"archetype": "grid", "title": f"T{i}"} for i in range(1, 9)]
    mats = C.deck_material(pngs, slides, 6)
    assert len(mats) == 2 and sum(1 for p in mats[0] if p["type"] == "image_url") == 6
    assert "Slide 7 [grid]: T7" in " ".join(p.get("text", "") for p in mats[1])

    class Two:
        def chat(self, *, model, messages, **kw):
            who = "partner" if "senior partner" in messages[0]["content"] else "md"
            first = "pages 1-6" in messages[1]["content"][0]["text"]
            v = 9 if first else 7
            body = {"scores": _scores(who, "deck", v),
                    "issues": [] if first else [{"severity": "major", "scope": "layout", "where": "slide 8",
                                                 "issue": "crowded", "fix": "cut"}]}
            return {"choices": [{"message": {"content": json.dumps(body)}}]}
    cfg = C.settings(load_rules())
    pn = C.deck_panel(Two(), "", slides, str(tmp_path), cfg, pngs=pngs)
    assert pn["batches"] == 2 and pn["scores"] == {"partner": 8.0, "md": 8.0}
    assert pn["signed_off"] and len(pn["issues"]) == 1     # a major issue does not block sign-off above the bar
    assert pn["issues"][0]["persona"] == "md, partner"
    assert C.verdict_line(pn) == "Senior partner 8.0/10 signs off; MD 8.0/10 signs off"


def test_verdict_line_reads_like_the_room():
    pn = {"reviews": [P.parse_review(json.dumps({"scores": {"storyline": 8.5, "proof": 8.5, "visuals": 8.5,
                                                            "craft": 8, "language": 8}}), "partner", "deck"),
                      P.parse_review(json.dumps({"scores": {"accuracy": 7, "sourcing": 8, "valuation": 8, "honesty": 8,
                                                            "polish": 8}, "issues": [
                          {"severity": "major", "scope": "data", "where": "slide 6", "issue": "Premium does not tie",
                           "fix": "recompute"}]}), "md", "deck")], "not_run": []}
    assert C.verdict_line(pn) == ("Senior partner 8.4/10 signs off; MD 7.7/10: revise, top issue: Premium does not tie "
                                  "(slide 6)")
    notes = C.review_notes(dict(pn, issues=P.merge_issues(pn["reviews"])), 8)
    assert notes == {6: ["[REVIEW] MD (major, data): Premium does not tie Fix: recompute"]}
