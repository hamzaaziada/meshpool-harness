"""Partner review (engines/forge/review.py): JSON parsing, batching, model fallback, failure mapping, not_run paths.
No network: a stub client stands in for the gateway. One opt-in live test (MH_LIVE_REVIEW=1)."""
import json
import os
import threading
import time

import pytest

from engines.forge import review as R


def _png(tmp_path, n):
    from PIL import Image
    p = tmp_path / f"s{n}.png"
    Image.new("RGB", (32, 18), "white").save(p)
    return (n, p)


def _entry(n, *complaints, score=6):
    return {"slide": n, "verdict": "fix" if complaints else "ship", "score": score, "complaints": list(complaints)}


def _c(sev="medium", issue="Title 'Market overview' is a topic", fix="Rewrite as a sentence with a verb",
       scope="content", rule="S1", cat="story"):
    return {"severity": sev, "category": cat, "rule": rule, "issue": issue, "fix": fix, "scope": scope}


class _Stub:
    """Per-model behaviour: "raise", "sleep", or a function(slide numbers) -> reply text."""
    def __init__(self, plan):
        self.plan, self.calls, self.lock = plan, [], threading.Lock()

    def chat(self, *, model, messages, **kw):
        user = messages[1]["content"]
        nums = [int(p["text"].split("Slide ")[1].split(" ")[0]) for p in user
                if p["type"] == "text" and p["text"].startswith("\nSlide ")]
        imgs = sum(1 for p in user if p["type"] == "image_url")
        with self.lock:
            self.calls.append({"model": model, "slides": nums, "images": imgs, "system": messages[0]["content"]})
        how = self.plan.get(model, "raise")
        if how == "raise":
            raise RuntimeError(f"{model} unavailable")
        if how == "sleep":
            time.sleep(5)
            return {"choices": [{"message": {"content": "{}"}}]}
        return {"choices": [{"message": {"content": how(nums)}}], "usage": {"prompt_tokens": 1000, "completion_tokens": 50}}


def _ok(nums):
    return json.dumps({"slides": [_entry(n, _c()) if n % 2 else _entry(n, score=8) for n in nums]})


# ------------------------------------------------------------------ parsing
def test_parse_reply_shapes():
    body = {"slides": [_entry(2, _c())]}
    assert R.parse_reply(json.dumps(body))[0]["slide"] == 2
    assert R.parse_reply("```json\n" + json.dumps(body) + "\n```")[0]["slide"] == 2            # fenced
    assert R.parse_reply("Here is my review:\n" + json.dumps(body) + "\nThanks.")[0]["slide"] == 2  # prose around
    assert R.parse_reply(json.dumps([_entry(3), _entry(4)]))[1]["slide"] == 4                    # bare list
    assert R.parse_reply(json.dumps(_entry(5)))[0]["slide"] == 5                                 # one slide object
    trailing = '{"slides": [{"slide": 1, "verdict": "ship", "score": 8, "complaints": [],},],}'
    assert R.parse_reply(trailing)[0]["score"] == 8                                              # trailing commas
    braces = json.dumps({"slides": [_entry(1, _c(issue='Label reads "{x}" and "[GAP]"'))]})
    assert R.parse_reply(braces)[0]["complaints"][0]["issue"].startswith("Label")              # brackets in strings
    cut = json.dumps({"slides": [_entry(1, _c()), _entry(2, _c())]})[:-30]                       # truncated reply
    got = R.parse_reply(cut)
    assert [e["slide"] for e in got] == [1]                                                      # complete one kept
    assert R.parse_reply("I cannot review these slides.") is None
    assert R.parse_reply("") is None and R.parse_reply(None) is None


def test_normalize_clamps_and_guards_numbers():
    slide = {"title": "Revenue grew 168% to $3.66B", "chart": {"values": [2.58, 3.66]}}
    e = {"slide": 4, "verdict": "ship", "score": "14", "complaints": [
        _c("severe", "Bars unsorted", "Sort bars", "weird", rule="V6", cat="nonsense"),
        _c("low", "Units differ: '3.66' vs '$3.66B'", "Show $3.66B on the label", "layout", rule="T5", cat="typography"),
        _c("high", "Number wrong", "Change revenue to $4.12B", rule="D1", cat="data"),          # invented figure
        _c("medium", "Text too long", "Cut the bullet to 12 words and 2 lines", rule="L6", cat="layout"),
        {"severity": "low", "issue": "", "fix": "x"}]}
    out = R.normalize(e, 4, slide)
    assert out["score"] == 10 and out["verdict"] == "fix"                 # clamped; verdict follows the complaints
    assert [c["severity"] for c in out["complaints"]] == ["medium", "medium", "low"]
    first = out["complaints"][0]
    assert first["category"] == "visual" and first["scope"] == "content"  # category from the rule, scope defaulted
    assert out["dropped"][0]["fix"] == "Change revenue to $4.12B"          # a fix may not invent a number
    assert any(c["rule"] == "L6" for c in out["complaints"])               # word limits are not "figures"
    assert R.normalize({"complaints": [_c("low")]}, 1)["verdict"] == "ship"  # low only: ships


# ------------------------------------------------------------------ the review loop
def test_batches_parallel_and_rubric_prompt(tmp_path):
    pngs = [_png(tmp_path, n) for n in range(1, 8)]
    slides = [{"archetype": "grid", "title": f"Slide {n} title", "prov": ["F1"]} for n in range(1, 8)]
    stub = _Stub({"claude-sonnet-5.5": _ok})
    rv = R.review_deck("", slides=slides, client=stub, pngs=pngs, batch=3)
    assert rv["status"] == "ok" and rv["model"] == "claude-sonnet-5.5" and rv["calls"] == 3
    assert sorted(len(c["slides"]) for c in stub.calls) == [1, 3, 3]
    assert all(c["images"] == len(c["slides"]) for c in stub.calls)       # one image per slide in the batch
    assert [s["slide"] for s in rv["slides"]] == list(range(1, 8))
    assert "What a polished deck is" in stub.calls[0]["system"] and "Return ONLY JSON" in stub.calls[0]["system"]
    assert rv["usage"]["in"] == 3000
    assert [s["verdict"] for s in rv["slides"]][:2] == ["fix", "ship"]


def test_outline_and_slide_json_reach_the_model(tmp_path):
    seen = {}

    class Spy(_Stub):
        def chat(self, *, model, messages, **kw):
            seen["text"] = "\n".join(p.get("text", "") for p in messages[1]["content"])
            return super().chat(model=model, messages=messages, **kw)
    slides = [{"archetype": "cover", "title": "Deck"}, {"archetype": "bar_takeaway", "title": "Sales rose 9%",
                                                       "prov": ["F9"], "notes": "secret"}]
    R.review_deck("", slides=slides, client=Spy({"claude-sonnet-5.5": _ok}), pngs=[_png(tmp_path, 2)])
    assert "1. [cover] Deck" in seen["text"] and "2. [bar_takeaway] Sales rose 9%" in seen["text"]
    assert "Slide 2 (bar_takeaway)" in seen["text"] and "secret" not in seen["text"] and "F9" not in seen["text"]


def test_fallback_chain_and_env_model(tmp_path, monkeypatch):
    pngs = [_png(tmp_path, n) for n in (1, 2)]
    stub = _Stub({"gpt-6.1-terra": _ok})                                 # sonnet missing: terra answers
    rv = R.review_deck("", client=stub, pngs=pngs, batch=2)
    assert [c["model"] for c in stub.calls] == ["claude-sonnet-5.5", "gpt-6.1-terra"]
    assert rv["status"] == "ok" and rv["model"] == "gpt-6.1-terra" and rv["errors"]
    monkeypatch.setenv("MH_REVIEW_MODEL", "my-vision")
    assert R.chain() == ["my-vision", "claude-sonnet-5.5", "gpt-6.1-terra", "kimi-k3"]
    assert R.chain("explicit")[:2] == ["explicit", "my-vision"]


def test_bad_json_falls_through_and_renumbered_replies_map_by_order(tmp_path):
    pngs = [_png(tmp_path, n) for n in (5, 6)]
    stub = _Stub({"claude-sonnet-5.5": lambda nums: "Sorry, here you go: {not json",
                  "gpt-6.1-terra": lambda nums: json.dumps({"slides": [_entry(1, _c()), _entry(2)]})})
    rv = R.review_deck("", client=stub, pngs=pngs, batch=2)
    assert rv["status"] == "ok" and [s["slide"] for s in rv["slides"]] == [5, 6]
    assert "unparseable" in rv["errors"][0]


def test_partial_and_not_run(tmp_path):
    pngs = [_png(tmp_path, n) for n in (1, 2, 3, 4)]
    half = _Stub({"claude-sonnet-5.5": lambda nums: _ok(nums) if 1 in nums else "nonsense"})
    rv = R.review_deck("", client=half, pngs=pngs, batch=2)
    assert rv["status"] == "partial" and rv["not_run"] == [3, 4] and [s["slide"] for s in rv["slides"]] == [1, 2]
    dead = R.review_deck("", client=_Stub({}), pngs=pngs, batch=2)
    assert dead["status"] == "not_run" and dead["slides"] == [] and dead["not_run"] == [1, 2, 3, 4]
    assert R.as_failures(dead) == {} and R.deck_verdict(dead)["status"] == "not_run"


def test_timeout_gives_not_run(tmp_path):
    t0 = time.time()
    rv = R.review_deck("", client=_Stub({m: "sleep" for m in R.CHAIN}), pngs=[_png(tmp_path, 1)], timeout=0.2)
    assert rv["status"] == "not_run" and "TimeoutError" in rv["errors"][0]
    assert time.time() - t0 < 3                                           # three models x 0.2 s, not 3 x 5 s


def test_render_failure_is_not_run(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("LibreOffice not installed")
    monkeypatch.setattr(R, "render_pngs", boom)
    rv = R.review_deck("/nope/deck.pptx", client=_Stub({}))
    assert rv["status"] == "not_run" and "LibreOffice" in rv["errors"][0]


def test_client_construction_failure_is_not_run(tmp_path, monkeypatch):
    import meshpool_harness.core.client as C

    class Broken:
        def __init__(self, *a, **k):
            raise RuntimeError("no transport")
    monkeypatch.setattr(C, "LiveClient", Broken)
    rv = R.review_deck("", pngs=[_png(tmp_path, 1)])
    assert rv["status"] == "not_run" and "no transport" in rv["errors"][0]


# ------------------------------------------------------------------ for the build loop
def _review():
    return {"status": "ok", "slides": [
        R.normalize(_entry(2, _c("high", "Chart mixes $ and %", "Split into two charts", "content", "V4", "visual"),
                           _c("low", "Legend not needed", "Remove the legend", "layout", "V8", "visual")), 2),
        R.normalize(_entry(3, _c("medium", "Panel half empty", "Widen the chart", "layout", "L1", "layout"),
                           _c("medium", "Title 'Overview' is a topic", "Rewrite as a message", "content")), 3),
        R.normalize(_entry(4, score=9), 4)]}


def test_as_failures_mapping():
    f = R.as_failures(_review())
    assert f == {2: ["[review] Chart mixes $ and % -> Split into two charts (content)"],
                 3: ["[review] Panel half empty -> Widen the chart (layout)",
                     "[review] Title 'Overview' is a topic -> Rewrite as a message (content)"]}
    assert R.as_failures(_review(), scopes=("content",)) == {
        2: ["[review] Chart mixes $ and % -> Split into two charts (content)"],
        3: ["[review] Title 'Overview' is a topic -> Rewrite as a message (content)"]}
    assert 2 in R.as_failures(_review(), severities=("low",)) and 3 not in R.as_failures(_review(), severities=("low",))
    assert R.as_failures({"status": "not_run"}) == {} and R.as_failures(None) == {}


def test_deck_verdict():
    rv = _review()
    rv["slides"][1]["complaints"].append(dict(rv["slides"][1]["complaints"][0], issue="Box empty"))  # L1 twice
    v = R.deck_verdict(rv)
    assert v["fix"] == [2, 3] and v["verdict"] == "fix"
    assert v["score"] == round(sum(s["score"] for s in rv["slides"]) / 3, 1)
    assert v["top"][0]["rule"] == "L1" and v["top"][0]["count"] == 2 and v["top"][0]["slides"] == [3]
    assert v["complaints"] == {"high": 1, "medium": 3, "low": 1}
    assert R.deck_verdict({"status": "not_run"})["score"] is None


def test_rubric_covers_the_standard():
    text = R.rubric()
    for head in ("## 1. Story", "## 2. Visual evidence", "## 3. Layout", "## 4. Typography", "## 5. Data", "## 6. Finish"):
        assert head in text
    for must in ("so what", "football", "zero", "[GAP]", "12 pt", "ALL CAPS", "page number", "Source"):
        assert must.lower() in text.lower(), must


# ------------------------------------------------------------------ live (opt-in)
@pytest.mark.skipif(os.environ.get("MH_LIVE_REVIEW") != "1", reason="live gateway test: set MH_LIVE_REVIEW=1")
def test_live_review_flags_a_words_only_topic_slide(tmp_path):
    from pptx import Presentation
    from pptx.util import Inches, Pt
    prs = Presentation()
    prs.slide_width, prs.slide_height = Inches(13.333), Inches(7.5)
    s = prs.slides.add_slide(prs.slide_layouts[6])
    tb = s.shapes.add_textbox(Inches(0.6), Inches(0.4), Inches(12), Inches(1)).text_frame
    tb.text = "Market overview"
    tb.paragraphs[0].runs[0].font.size = Pt(32)
    body = s.shapes.add_textbox(Inches(0.6), Inches(1.6), Inches(5), Inches(3)).text_frame
    body.text = "The market is big and growing fast"
    for line in ("Growth of 168", "Competitors are the the main risk", "Value TBD"):
        body.add_paragraph().text = line
    path = tmp_path / "deck.pptx"
    prs.save(path)
    rv = R.review_deck(str(path), slides=[{"archetype": "argument_only", "title": "Market overview"}],
                       out_dir=str(tmp_path))
    assert rv["status"] == "ok", rv["errors"]
    s1 = rv["slides"][0]
    assert s1["verdict"] == "fix" and s1["score"] <= 5
    rules = {c["rule"] for c in s1["complaints"]}
    assert rules & {"S1", "V1"}, s1["complaints"]
    assert R.as_failures(rv)[1][0].startswith("[review] ")


def test_build_sends_review_complaints_to_the_writer_and_reports_the_verdict(tmp_path, monkeypatch):
    """The partner review runs after the rule repairs: its content complaints go back to the writer once, the deck
    is reviewed again, and the build returns the verdict."""
    import json as _json
    from engines.forge import build as B, review as RV
    asked = []

    class Stub:
        def chat(self, *, model, messages, **kw):
            sys_, user = messages[0]["content"], messages[-1]["content"]
            if isinstance(user, list):
                user = " ".join(p.get("text", "") for p in user if isinstance(p, dict))
            if "engagement manager" in sys_:
                body = {"slides": [{"node": "0", "title": "Northwind plan", "archetype": "cover"},
                                   {"node": "1.1", "title": "North revenue grew 18% to $120m", "archetype": "bar_takeaway",
                                    "evidence": ["F1"]}]}
            elif "Fix this one consulting slide" in sys_:
                asked.append(user)
                body = _json.loads(user.split("Slide:\n", 1)[1].split("\n\nFacts", 1)[0])
            elif "ONE consulting slide" in sys_:
                x = _json.loads(user.split("This slide: ", 1)[1].split("\n\nFacts", 1)[0])
                body = dict(x, subtitle="Board", date="2026") if x["archetype"] == "cover" else dict(
                    x, subtitle="Revenue by region, $m, 2025", takeaway={"text": "North leads growth", "prov": ["F1"]},
                    chart={"kind": "bar", "categories": ["North", "South", "East"],
                           "series": [{"name": "Revenue", "values": [120, 90, 60], "prov": ["F1"]}], "unit": "$m"},
                    bullets=[{"text": "North revenue was $120m", "prov": ["F1"]}],
                    source={"text": "Company data", "prov": ["F1"]})
            else:
                body = {"edits": []}
            return {"choices": [{"message": {"content": _json.dumps(body)}}]}

    reviews = iter([
        {"status": "ok", "slides": [{"slide": 2, "verdict": "fix", "score": 5, "complaints": [
            {"severity": "high", "category": "story", "issue": "title not proved", "fix": "show the growth rate",
             "scope": "content"}]}]},
        {"status": "ok", "slides": [{"slide": 2, "verdict": "ship", "score": 8, "complaints": []}]}])
    monkeypatch.setattr(B, "_review_on", lambda client, rules: True)
    monkeypatch.setenv("MH_PERSONAS", "0")               # the generic single review (personas: test_forge_panel.py)
    monkeypatch.setattr(RV, "review_deck", lambda *a, **k: next(reviews))
    brief = "Facts\n- North revenue $120m, South $90m, East $60m in 2025; North grew 18%"
    res = B.build(brief, str(tmp_path), slides_wanted=2, client=Stub(), task_id="t")
    assert any("[review] title not proved" in a for a in asked)
    assert res["review"]["score"] == 8 and res["review"]["verdict"] == "ship"
    assert (tmp_path / "review.json").exists()
