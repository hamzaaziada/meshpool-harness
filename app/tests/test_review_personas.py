"""The two reviewers: briefs per target, tough verdicts recomputed from the bar, merged issues, panel sign-off."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from engines.review import personas as P  # noqa: E402


def test_briefs_name_the_persona_dimensions_scopes_and_json():
    for who in ("partner", "md"):
        for target in P.TARGETS:
            s = P.system_prompt(who, target)
            assert P.PERSONAS[who]["who"][:40] in s
            for k, _, _ in P.dimensions(who, target):
                assert f'"{k}"' in s
            assert all(sc in s for sc in P.SCOPES[target])
    assert "cut_or_merge" in P.system_prompt("partner", "storyboard")
    assert "cut_or_merge" not in P.system_prompt("md", "deck")
    for who in P.PERSONAS:
        for target in P.TARGETS:
            assert abs(sum(w for _, _, w in P.dimensions(who, target)) - 1) < 1e-9


def test_verdict_follows_the_bar_not_the_reviewer():
    text = json.dumps({"scores": {"accuracy": 9, "sourcing": 9, "valuation": 9, "honesty": 9, "polish": 9},
                       "verdict": "sign_off", "issues": [{"severity": "blocking", "scope": "data", "where": "slide 4",
                                                          "issue": "premium uses the wrong price", "fix": "use $48.42"}]})
    r = P.parse_review(text, "md", "deck")
    assert r["overall"] == 9.0 and r["verdict"] == "revise"                     # a blocker stops sign-off
    low = P.parse_review(json.dumps({"scores": {"storyline": 9, "proof": 9, "visuals": 9, "craft": 6, "language": 9},
                                     "verdict": "sign_off"}), "partner", "deck")
    assert low["verdict"] == "revise"                                           # one dimension under the floor
    ok = P.parse_review("```json\n" + json.dumps({"scores": {"storyline": 8, "proof": 8, "visuals": 8, "craft": 8,
                                                              "language": 9}, "verdict": "revise",
                                                   "issues": [{"severity": "minor", "scope": "layout", "where": "slide 2",
                                                               "issue": "orphan word", "fix": "shorten"}],}) + "\n```",
                        "partner", "deck")
    assert ok["verdict"] == "sign_off" and ok["overall"] == 8.1
    bad = P.parse_review("not json", "partner", "deck")
    assert bad["status"] == "unparsed" and bad["verdict"] == "revise"


def test_panel_needs_both_and_merges_duplicate_issues():
    class Stub:
        def __init__(self, replies):
            self.replies = replies

        def chat(self, *, model, messages, **kw):
            who = "partner" if "senior partner" in messages[0]["content"] else "md"
            return {"choices": [{"message": {"content": json.dumps(self.replies[who])}}]}
    good_p = {"scores": {"storyline": 9, "proof": 9, "visuals": 9, "craft": 9, "language": 9}}
    good_m = {"scores": {"accuracy": 9, "sourcing": 9, "valuation": 9, "honesty": 9, "polish": 9}}
    dup = {"severity": "major", "scope": "content", "where": "Slide 3", "issue": "Title not proved by the chart",
           "fix": "show growth"}
    res = P.panel(Stub({"partner": dict(good_p, issues=[dup]), "md": dict(good_m, issues=[dict(dup, severity="blocking")])}),
                  "deck", "material", "m")
    assert not res["signed_off"] and len(res["issues"]) == 1
    assert res["issues"][0]["severity"] == "blocking" and res["issues"][0]["persona"] == "md, partner"
    assert P.panel(Stub({"partner": good_p, "md": good_m}), "deck", "m", "m")["signed_off"]

    class Down:
        def chat(self, **kw):
            raise RuntimeError("HTTP 401")
    nr = P.panel(Down(), "workbook", "m", "m")
    assert not nr["signed_off"] and nr["not_run"] == ["md", "partner"]
    assert P.slide_of("Slide 12, chart") == 12 and P.slide_of("DCF!C14") is None
    assert "senior partner 9.0/10 (signs off)" in P.summary(P.panel(Stub({"partner": good_p, "md": good_m}), "deck", "m", "m"))


def test_a_review_cut_off_mid_json_keeps_its_scores_and_complete_issues():
    """Seen live: the MD's long workbook review hit the token limit and was thrown away as unparsed."""
    text = ('```json\n{"persona": "md", "target": "workbook", "scores": {"integrity": 1, "assumptions": 3, '
            '"valuation": 1, "structure": 3, "outputs": 1}, "headline": "This file cannot leave the desk", '
            '"verdict": "revise", "issues": [{"severity": "blocking", "scope": "logic", "where": "Model!C5", '
            '"issue": "C5 references itself", "fix": "link C5 to Inputs"}, {"severity": "blocking", "scope": "valua')
    r = P.parse_review(text, "md", "workbook")
    assert r["status"] == "ok" and r["verdict"] == "revise" and r["overall"] < 2
    assert len(r["issues"]) == 1 and r["issues"][0]["where"] == "Model!C5"
    assert r["headline"] == "This file cannot leave the desk"
