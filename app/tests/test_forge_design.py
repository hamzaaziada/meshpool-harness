"""Design-first pages (engines/forge/design.py), offline: stub image and vision clients stand in for the models.
The prompt quotes the real content and avoids recent layouts; the image call has the right shape and falls back
sensibly; the vision spec is normalised (bboxes clamped, colours on theme slots, invented text dropped); the
rebuild is native (shapes on the grid, text inside its shape, a native chart, library icons), fits its text, and
falls back to the archetype when it cannot; the build wires it all and records which path drew each slide."""
import io
import json
import re
import sys
import warnings
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
warnings.filterwarnings("ignore")

import pytest  # noqa: E402
from pptx import Presentation  # noqa: E402

from engines.forge import design as D, render as R  # noqa: E402

COLORS = D.direction()["colors"]
CHART = {"kind": "column", "categories": ["Q2 2026", "Q3 2026", "Q4 2026"],
         "series": [{"name": "Revenue", "values": [2.58, 3.66, 4.1]}], "unit": "$B", "decimals": 2,
         "highlight_categories": ["Q4 2026"]}
SLIDE = {"node": "3.1", "archetype": "grid", "layout": "main_side", "title": "Revenue rose to $4.1B by the fourth quarter",
         "parts": [{"type": "chart", "heading": "Quarterly revenue, $B", "chart": CHART},
                   {"type": "stats", "heading": "Backlog build",
                    "items": [{"value": "$132.5B", "label": "Backlog at 30 Sep 2026", "prov": ["F12"]},
                              {"value": "$28.3B", "label": "Backlog added in one quarter", "prov": ["F12"]}]}],
         "source": {"text": "Company results"}}


def png(w=1536, h=864) -> bytes:
    from PIL import Image
    b = io.BytesIO()
    Image.new("RGB", (w, h), "white").save(b, "PNG")
    return b.getvalue()


def split_spec(fill="#0A2540"):
    """What a vision model would return for SLIDE: chart left, two stat cards right, an icon (text copied)."""
    return {"layout": "split_chart_insights", "elements": [
        {"type": "text", "bbox": [0.6, 1.6, 7.27, 0.35], "z": 2,
         "runs": [{"text": "Quarterly revenue, $B", "role": "heading", "size": 15, "color": "#0A1A2F"}]},
        {"type": "chart", "bbox": [0.6, 2.05, 7.27, 4.4], "z": 2, "ref": "parts.0"},
        {"type": "text", "bbox": [8.26, 1.6, 3.8, 0.35], "z": 2,
         "runs": [{"text": "Backlog build", "role": "heading", "size": 15, "color": "#0A1A2F"}]},
        {"type": "icon", "bbox": [12.1, 1.6, 0.6, 0.6], "z": 3, "meaning": "growth", "color": "#1E5BD8",
         "style": "disc"},
        {"type": "rect", "bbox": [8.26, 2.3, 4.47, 1.9], "z": 1, "fill": "#ECEDEE"},
        {"type": "text", "bbox": [8.5, 2.45, 4.0, 0.7], "z": 2,
         "runs": [{"text": "$132.5B", "role": "figure", "size": 40, "color": "#1E5BD8"}]},
        {"type": "text", "bbox": [8.5, 3.2, 4.0, 0.4], "z": 2,
         "runs": [{"text": "Backlog at 30 Sep 2026", "role": "label", "size": 12}]},
        {"type": "rect", "bbox": [8.26, 4.4, 4.47, 1.9], "z": 1, "fill": fill},
        {"type": "text", "bbox": [8.5, 4.55, 4.0, 0.7], "z": 2,
         "runs": [{"ref": "parts.1.items.1.value", "text": "$28.3B", "role": "figure", "size": 40,
                   "color": "#FFFFFF"}]},
        {"type": "text", "bbox": [8.5, 5.3, 4.0, 0.4], "z": 2,
         "runs": [{"text": "Backlog added in one quarter", "role": "label", "size": 12, "color": "#FFFFFF"}]},
    ]}


class Images:
    """Stub image client: records the prompts, returns a 16:9 PNG (or a failure)."""
    model = "image-stub"

    def __init__(self, ok=True):
        self.ok, self.prompts = ok, []

    def generate(self, prompt):
        self.prompts.append(prompt)
        if not self.ok:
            return {"status": "not_run", "why": "images/generations HTTP 401: refused"}
        return {"status": "ok", "png": png(), "size": (1536, 864), "via": "images", "model": self.model}


class Vision:
    """Stub chat client for the reconstruction: answers with a spec built from the atoms it is sent."""

    def __init__(self, spec_for=None):
        self.calls = []
        self.spec_for = spec_for

    def chat(self, *, model, messages, **kw):
        self.calls.append(model)
        user = messages[1]["content"]
        assert any(p.get("type") == "image_url" and p["image_url"]["url"].startswith("data:image/png;base64,")
                   for p in user)
        content = json.loads(user[0]["text"].split("\n", 1)[1])
        spec = self.spec_for(content) if self.spec_for else column_spec(content)
        return {"choices": [{"message": {"content": json.dumps(spec)}}]}


def column_spec(content):
    """Any slide: the exhibit on the left, one card per atom on the right, each with its atom inside."""
    els = []
    exh = content["exhibits"]
    if exh:
        els.append({"type": exh[0]["kind"] if exh[0]["kind"] in ("chart", "table") else "chart",
                    "bbox": [0.6, 1.7, 7.2, 4.8], "ref": exh[0]["id"], "z": 1})
    atoms = [a for a in content["atoms"] if not a["id"].endswith(".heading") or not exh]
    h = 4.8 / max(1, len(atoms))
    for k, a in enumerate(atoms):
        y = 1.7 + k * h
        els.append({"type": "rect", "bbox": [8.26, y, 4.47, h - 0.12], "fill": "#ECECEC", "z": 1})
        els.append({"type": "text", "bbox": [8.4, y + 0.08, 4.2, h - 0.3], "z": 2,
                    "runs": [{"ref": a["id"], "text": a["text"], "role": a["role"], "size": 13}]})
    return {"layout": "split_chart_insights", "elements": els}


# ------------------------------------------------------------------ 1. the design prompt
def test_prompt_quotes_the_real_content_and_avoids_recent_layouts():
    v = D.view(SLIDE, "balanced")
    p = D.design_prompt(v, D.direction(None, "balanced"), ["split_chart_insights", "stat_grid"])
    assert '"Revenue rose to $4.1B by the fourth quarter"' in p          # the title, verbatim and quoted
    assert 'FIGURE: "$132.5B"' in p and 'LABEL: "Backlog added in one quarter"' in p
    assert '"Q3 2026" = 3.66' in p and 'highlight: "Q4 2026"' in p      # the chart's real data
    assert "#1E5BD8" in p and "Segoe UI" in p and "16:9" in p             # palette, fonts, format
    assert "no stock photos" in p.lower() and "no logos" in p.lower()
    assert "Do NOT use these layouts" in p and "split chart insights" in p.split("Do NOT use")[1]
    first = p.split("Layout: ")[1].split(":")[0]
    assert first.replace(" ", "_") not in ("split_chart_insights", "stat_grid")  # a layout the last pages did not use
    assert D.pick_layout(v, []) == "split_chart_insights"
    assert D.pick_layout(v, ["split_chart_insights"]) != "split_chart_insights"


def test_mode_wording_options_env_and_rules(monkeypatch):
    assert D.mode_from("Build a 6-slide deck with a designed look") == "image"
    assert D.mode_from("image-designed pages please") == "image"
    assert D.mode_from("a deck on Q3", {"design": "auto"}) == "auto"
    assert D.mode_from("a deck on Q3") is None
    monkeypatch.delenv("MH_DESIGN", raising=False)
    assert D.resolve_mode(None, {}) == "archetype"
    assert D.resolve_mode(None, {"design": {"mode": "auto"}}) == "auto"
    monkeypatch.setenv("MH_DESIGN", "image")
    assert D.resolve_mode(None, {}) == "image" and D.resolve_mode("archetype", {}) == "archetype"
    assert D.eligible(SLIDE, "auto") and not D.eligible(dict(SLIDE, archetype="cover"), "image")
    assert D.eligible({"archetype": "executive_summary"}, "image")
    assert not D.eligible({"archetype": "executive_summary"}, "auto")
    assert not D.eligible(dict(SLIDE, fixed=True), "image") and not D.eligible(SLIDE, "archetype")


def test_model_names_are_configurable(monkeypatch):
    monkeypatch.setenv("MH_DESIGN_IMAGE_MODEL", "cheap-image")
    monkeypatch.setenv("MH_DESIGN_VISION_MODEL", "cheap-vision")
    cfg = D.settings({"design": {"vision_models": ["v2"]}})
    assert cfg["image_model"] == "cheap-image"
    assert D.vision_models(cfg)[:2] == ["cheap-vision", "v2"]


# ------------------------------------------------------------------ 2. the image call
def test_image_call_shape_and_size_fallback():
    import base64
    sent = []

    def post(url, body, timeout):
        sent.append((url, dict(body)))
        if body["size"] == "1536x864":
            return 400, {"error": {"message": "Invalid size '1536x864'. Supported sizes are 1024x1024, 1536x1024"}}
        return 200, {"data": [{"b64_json": base64.b64encode(png(1536, 1024)).decode()}]}
    ic = D.ImageClient("gpt-image-2", base="https://gw.example/v1", post=post)
    r = ic.generate("a slide")
    url, body = sent[0]
    assert url == "https://gw.example/v1/images/generations"
    assert body == {"model": "gpt-image-2", "prompt": "a slide", "size": "1536x864", "n": 1,
                    "response_format": "b64_json"}
    assert sent[1][1]["size"] == "1536x1024"                             # the nearest size the API allows
    assert r["status"] == "ok" and r["size"] == (1536, 864)             # cropped to 16:9


def test_image_call_drops_response_format_and_falls_back_to_chat():
    import base64
    b64 = base64.b64encode(png()).decode()

    def post(url, body, timeout):
        if "response_format" in body:
            return 400, {"error": {"message": "Unknown parameter: 'response_format'."}}
        return 200, {"data": [{"b64_json": b64}]}
    r = D.ImageClient(base="https://gw/v1", post=post).generate("x")
    assert r["status"] == "ok" and r["via"] == "images"

    def chat_only(url, body, timeout):
        if url.endswith("/images/generations"):
            return 404, {"error": "not found"}
        assert body["modalities"] == ["image", "text"] and body["max_tokens"] >= 16
        return 200, {"choices": [{"message": {"content": "", "images": [
            {"type": "image_url", "image_url": {"url": "data:image/png;base64," + b64}}]}}]}
    r = D.ImageClient(base="https://gw/v1", post=chat_only).generate("x")
    assert r["status"] == "ok" and r["via"] == "chat"


def test_image_call_is_not_run_when_refused_or_unreachable():
    calls = []

    def refused(url, body, timeout):
        calls.append(url)
        return 401, {"error": "invalid key"}
    r = D.ImageClient(base="https://gw/v1", post=refused).generate("x")
    assert r["status"] == "not_run" and "401" in r["why"] and len(calls) == 1   # no second endpoint on a refusal
    r = D.ImageClient(base="https://gw/v1", post=lambda u, b, t: (0, {"error": "timed out"})).generate("x")
    assert r["status"] == "not_run"


# ------------------------------------------------------------------ 3. reconstruction and normalisation
def test_normalize_clamps_maps_colours_and_drops_invented_text():
    v = D.view(SLIDE, "balanced")
    raw = split_spec()
    raw["elements"] += [
        {"type": "text", "bbox": [0.6, 6.3, 4, 0.3], "runs": [{"text": "Illustrative estimate", "size": 10}]},
        {"type": "text", "bbox": [0.6, 0.4, 9, 0.8], "runs": [{"text": "Revenue rose to $4.1B by the fourth quarter"}]},
        {"type": "rect", "bbox": [-1, 1.0, 20, 9], "fill": "#0B2541"},
        {"type": "sparkle", "bbox": [1, 2, 1, 1]}]
    spec, probs = D.normalize_spec(raw, v, COLORS)
    assert probs["invented"] == ["Illustrative estimate"]                 # not in the content: dropped, reported
    assert "unknown type 'sparkle'" in " ".join(probs["dropped"])
    texts = [r for e in spec["elements"] for r in e.get("runs") or []]
    assert all(r["ref"] for r in texts) and not any("fourth quarter" in str(r) for r in texts)   # title: the frame's
    big = next(e for e in spec["elements"] if e["type"] == "rect" and e["bbox"][2] > 12)
    assert big["bbox"] == [R.LEFT, R.BODY_Y, round(R.RIGHT - R.LEFT, 3), round(R.BODY_B - R.BODY_Y, 3)]
    assert big["fill"] == "dk2" and probs["clamped"] >= 1                 # #0B2541 -> the dark brand slot
    card = [e for e in spec["elements"] if e["type"] == "rect"][0]
    assert card["fill"] == "lt2"                                          # #ECEDEE -> the panel grey slot
    assert next(e for e in spec["elements"] if e["type"] == "chart")["ref"] == "parts.0"
    assert D.coverage(spec, v) == []


def test_normalize_scales_pixels_and_caps_icons():
    v = D.view(SLIDE, "balanced")
    raw = {"elements": [{"type": "icon", "bbox": [100 + 90 * i, 300, 60, 60], "meaning": "growth"} for i in range(6)]
           + [{"type": "chart", "bbox": [100, 200, 800, 500]}]}
    spec, probs = D.normalize_spec(raw, v, COLORS, (1536, 864))
    icons = [e for e in spec["elements"] if e["type"] == "icon"]
    assert len(icons) == D.MAX_ICONS and len([d for d in probs["dropped"] if "icons" in d]) == 2
    ch = next(e for e in spec["elements"] if e["type"] == "chart")
    assert ch["ref"] == "parts.0" and abs(ch["bbox"][2] - 800 * 13.333 / 1536) < 0.01
    assert {"parts.1.items.0.value", "parts.1.items.1.label", "parts.1.heading"} <= set(D.coverage(spec, v))


def test_nearest_slot_and_binding():
    assert D.nearest_slot("#FFFFFF", COLORS) == "lt1" and D.nearest_slot("#1D5AD7", COLORS) == "accent1"
    assert D.nearest_slot("none", COLORS) is None and D.nearest_slot("accent2", COLORS) == "accent2"
    atoms, _ = D.content_model(D.view(SLIDE, "balanced"))
    assert D.bind("$132.5B", atoms, SLIDE)[0] == "parts.1.items.0.value"
    assert D.bind("Backlog added in one", atoms, SLIDE)[0] == "parts.1.items.1.label"   # a long enough piece
    assert D.bind("$4.1B", atoms, SLIDE)[0] is None                       # short and not an atom: rejected
    assert D.bind("Source: Company results", atoms, SLIDE)[0] == "frame"


def test_reconstruct_uses_the_first_vision_model_that_answers():
    class Flaky(Vision):
        def chat(self, *, model, messages, **kw):
            if model == "down":
                self.calls.append(model)
                raise RuntimeError("503")
            return super().chat(model=model, messages=messages, **kw)
    cl = Flaky(lambda c: split_spec())
    v = D.view(SLIDE, "balanced")
    rc = D.reconstruct(cl, png(), v, D.direction(), ["down", "vision"], timeout=30)
    assert rc["status"] == "ok" and rc["model"] == "vision" and cl.calls == ["down", "vision"]
    assert D.reconstruct(Vision(lambda c: {}), png(), v, D.direction(), ["v"], timeout=30)["status"] == "not_run"


# ------------------------------------------------------------------ 4. the native rebuild
def _render(tmp_path, slides, name="d.pptx"):
    out = tmp_path / name
    r = R.render(slides, str(out), carry=False, density="balanced", fix_labels=False)
    return r, Presentation(str(out))


def test_rebuild_is_native_on_the_grid_with_text_inside_shapes(tmp_path):
    spec, _ = D.normalize_spec(split_spec(), D.view(SLIDE, "balanced"), COLORS)
    r, prs = _render(tmp_path, [dict(SLIDE, _design=spec)])
    assert r["design"][1]["path"] == "image", r["design"]
    sl = prs.slides[0]
    assert "design:image" in sl._element.cSld.get("name")
    shapes = list(sl.shapes)
    charts = [s for s in shapes if s.has_chart]
    assert len(charts) == 1 and list(charts[0].chart.plots[0].categories) == CHART["categories"]
    assert [round(v, 2) for v in charts[0].chart.plots[0].series[0].values] == [2.58, 3.66, 4.1]   # the real data
    cards = [s for s in shapes if s.shape_type == 1 and s.has_text_frame and "$" in s.text_frame.text]
    assert {c.text_frame.text.split("\n")[0] for c in cards} == {"$132.5B", "$28.3B"}    # text inside the card
    dark = next(c for c in cards if "$28.3B" in c.text_frame.text)
    assert dark.name.startswith("role_numeral_")
    assert dark.fill.fore_color.theme_color == R.TC.TEXT_2                # a theme slot, not a hex
    run = dark.text_frame.paragraphs[0].runs[0]
    assert run.font.color.theme_color == R.TC.BACKGROUND_1                # light text on the dark card
    x, w = dark.left / 914400, dark.width / 914400
    assert min(abs(x - e) for e in D.EDGES_L) < 0.01 and min(abs(x + w - e) for e in D.EDGES_R) < 0.01
    assert abs(dark.top / 914400 - 4.4) < 0.03
    icon = [s for s in shapes if s.name.startswith("ov_Icon") and s.shape_type == 13]
    assert icon and icon[0]._element.find(".//{*}cNvPr").get("descr").startswith("icon:lucide:")
    free = [s for s in shapes if s.shape_type == 17 and s.text_frame.text in ("$132.5B", "$28.3B")]
    assert not free                                                        # no text boxes laid over the cards (P17)
    assert sl.shapes.title.text_frame.text == SLIDE["title"]
    assert D.lost_content(sl, D.view(SLIDE, "balanced")) == []


def test_fit_pass_shrinks_within_the_floor_then_gives_up(tmp_path):
    long = dict(SLIDE, parts=[SLIDE["parts"][0], {"type": "text", "heading": "What it means", "bullets": [
        {"text": "Revenue and backlog both stepped up in the quarter, but the cost of that growth shows below the "
                 "top line in interest and losses"}]}])
    v = D.view(long, "balanced")

    def spec(h):
        return {"layout": "x", "elements": [
            {"type": "chart", "bbox": [0.6, 1.7, 7.2, 4.8], "ref": "parts.0"},
            {"type": "text", "bbox": [8.26, 1.7, 4.47, 0.4],
             "runs": [{"text": "What it means", "role": "heading", "size": 15}]},
            {"type": "line", "bbox": [8.26, 2.15, 4.47, 0], "color": "#0A1A2F"},
            {"type": "rect", "bbox": [8.26, 2.3, 4.47, h], "fill": "#ECECEC"},
            {"type": "text", "bbox": [8.4, 2.4, 4.2, h - 0.2], "runs": [{"ref": "parts.1.bullets.0", "size": 15}]},
            {"type": "rect", "bbox": [8.26, 2.3 + h + 0.1, 4.47, 0.3], "fill": "#0A2540"}]}
    s1, _ = D.normalize_spec(spec(1.0), v, COLORS)
    r, prs = _render(tmp_path, [dict(long, _design=s1)], "a.pptx")
    assert r["design"][1]["path"] == "image"
    card = next(s for s in prs.slides[0].shapes if s.has_text_frame and "Revenue and backlog" in s.text_frame.text)
    sizes = {run.font.size.pt for p in card.text_frame.paragraphs for run in p.runs}
    assert max(sizes) < 15 and min(sizes) >= 12                            # shrunk, never below the body floor
    s2, _ = D.normalize_spec(spec(0.3), v, COLORS)                         # boxed in: cannot fit, cannot grow
    r, prs = _render(tmp_path, [dict(long, _design=s2)], "b.pptx")
    assert r["design"][1]["path"] == "archetype" and "does not fit" in r["design"][1]["why"]
    assert "design:image" not in prs.slides[0]._element.cSld.get("name")
    assert any(s.has_chart for s in prs.slides[0].shapes)                  # the archetype drew it whole


def test_grow_into_free_space_before_shrinking():
    v = D.view(SLIDE, "balanced")
    spec, _ = D.normalize_spec(split_spec(), v, COLORS)
    shapes, texts = D._plan(spec, v)
    it = {"el": {}, "name": "t", "runs": [{"ref": "parts.1.items.0.label", "size": 12}],
          "amap": {a["id"]: a for a in D.content_model(v)[0]}, "roles": {"parts.1.items.0.label": "label"},
          "dark": False, "box": [8.26, 6.0, 4.47, 0.05], "margins": [0, 0, 0, 0], "inner": (4.47, 0.05)}
    assert D.fit([it], v, []) == [] and it["box"][3] > 0.2                # grew down into the free band


def test_missing_content_falls_back_to_the_archetype(tmp_path):
    raw = split_spec()
    raw["elements"] = [e for e in raw["elements"] if "$28.3B" not in json.dumps(e)]
    spec, _ = D.normalize_spec(raw, D.view(SLIDE, "balanced"), COLORS)
    assert "parts.1.items.1.value" in D.coverage(spec, D.view(SLIDE, "balanced"))
    r, prs = _render(tmp_path, [dict(SLIDE, _design=spec)])
    assert r["design"][1]["path"] == "archetype" and "leaves out" in r["design"][1]["why"]
    assert "$28.3B" in D.slide_text(prs.slides[0])[0]


def test_lost_content_finds_a_missing_bullet_and_chart_value(tmp_path):
    s = {"archetype": "bar_takeaway", "title": "North carries the growth with 44% of revenue",
         "chart": {"kind": "bar", "categories": ["North", "South", "East"],
                   "series": [{"name": "Revenue", "values": [420, 310, 220]}], "unit": "$m"},
         "takeaway": {"text": "North carries the growth"}, "bullets": ["North grew 18% on new stores"]}
    _, prs = _render(tmp_path, [s])
    assert D.lost_content(prs.slides[0], s) == []
    more = dict(s, bullets=s["bullets"] + ["West is flat at $90m"],
                chart=dict(s["chart"], series=[{"name": "Revenue", "values": [420, 310, 225]}]))
    lost = D.lost_content(prs.slides[0], more)
    assert any("bullets.1" in x for x in lost) and any("225" in x for x in lost)


# ------------------------------------------------------------------ 5. the pipeline and the build
def test_designer_saves_images_caps_and_rejects(tmp_path, monkeypatch):
    monkeypatch.setenv("MH_DESIGN_VISION_MODEL", "vision")
    imgs = Images()
    d = D.Designer(Vision(lambda c: split_spec()), {"design": {"max_images": 1}}, str(tmp_path), mode="image",
                   images=imgs, density="balanced")
    other = dict(SLIDE, node="3.2", title="Backlog grew by $28.3B in one quarter")
    specs = d.specs([{"archetype": "cover", "title": "x"}, SLIDE, other])
    assert list(specs) == [1] and (tmp_path / "design" / "slide02.png").exists()
    assert (tmp_path / "design" / "slide02.spec.json").exists()
    assert len(imgs.prompts) == 1 and d.log[2]["path"] == "image"
    assert d.log[3]["path"] == "archetype" and "image cap" in d.log[3]["why"]   # the cost cap
    assert 1 not in d.log                                                  # the cover never takes the design path
    d.check("missing.pptx", {"design": {}}, {"failures": [
        {"rule": "P5", "check": "text_overflow", "status": "fail", "severity": "error", "slide": 2,
         "message": "overflows"}]}, [{"archetype": "cover"}, SLIDE, other], specs)
    assert d.log[2]["path"] == "archetype" and d.log[2]["why"].startswith("P5")
    assert d.specs([{"archetype": "cover", "title": "x"}, SLIDE, other]) == {}   # stays on the archetype
    rep = d.report()
    assert rep["images_used"] == 1 and rep["slides"]["2"]["path"] == "archetype"


def test_designer_not_run_without_a_reachable_image_model(tmp_path):
    d = D.Designer(Vision(), {}, str(tmp_path), mode="image", images=Images(ok=False))
    assert d.specs([SLIDE]) == {} and "401" in d.log[1]["why"]


def test_build_with_design_records_the_path_and_keeps_images(tmp_path, monkeypatch):
    """A whole build, models stubbed: content slides are image-designed and rebuilt natively, the originals are kept
    in design/, and design.json / qa.json say which path drew each slide."""
    import engines.forge.build as B
    monkeypatch.setenv("MH_DESIGN_VISION_MODEL", "vision")

    class Stub(Vision):
        def chat(self, *, model, messages, **kw):
            if model == "vision":
                return super().chat(model=model, messages=messages, **kw)
            sys_, user = messages[0]["content"], messages[1]["content"]
            if "engagement manager" in sys_:
                n = int(re.search(r"Exactly (\d+) slides", sys_).group(1))
                body = [{"node": f"1.{i + 1}", "section": "Growth", "title": f"North revenue grew 18% in part {i + 1}",
                         "archetype": "bar_takeaway", "evidence": []} for i in range(n - 1)]
                out = {"slides": [{"node": "0", "title": "Northwind growth plan", "archetype": "cover"}] + body}
            elif "ONE consulting slide" in sys_:
                x = json.loads(user.split("This slide: ", 1)[1].split("\n\nFacts", 1)[0])
                out = x if x["archetype"] == "cover" else dict(
                    x, chart={"kind": "bar", "categories": ["North", "South", "East"],
                              "series": [{"name": "Revenue", "values": [420, 310, 220]}], "unit": "$m"},
                    takeaway={"text": "North carries the growth"}, bullets=[{"text": "North grew 18% on new stores"}],
                    source={"text": "Company accounts"})
            elif "Fix this one consulting slide" in sys_:
                out = json.loads(user.split("Slide:\n", 1)[1].split("\n\nFacts", 1)[0])
            else:
                out = {"edits": []}
            return {"choices": [{"message": {"content": json.dumps(out)}}]}
    imgs = Images()
    old = (B.MODEL, B.STORY_MODEL)
    B.MODEL = B.STORY_MODEL = "main"
    try:
        res = B.build("Northwind revenue was $420m in the North, $310m in the South and $220m in the East in 2025; "
                      "North grew 18%.", str(tmp_path), 3, client=Stub(), task_id="t", design="image",
                      design_images=imgs)
    finally:
        B.MODEL, B.STORY_MODEL = old
    dz = json.loads((tmp_path / "design.json").read_text())
    assert dz == json.loads(json.dumps(res["design"], default=str))
    assert dz["mode"] == "image" and set(dz["slides"]) == {"2", "3"}      # the cover is never designed
    assert all(v["path"] == "image" for v in dz["slides"].values()), dz["slides"]
    assert (tmp_path / "design" / "slide02.png").exists()
    assert json.loads((tmp_path / "qa.json").read_text())["design"]["slides"]["2"]["path"] == "image"
    prs = Presentation(res["pptx"])
    assert all("design:image" in prs.slides[i]._element.cSld.get("name") for i in (1, 2))
    assert "Do NOT use these layouts" in imgs.prompts[1]                    # the second page avoids the first's
    from engines.forge.engine import design_sentence
    assert "2 of 2 content slides were image-designed" in design_sentence(res["design"])


def test_build_without_design_is_unchanged(tmp_path, monkeypatch):
    monkeypatch.delenv("MH_DESIGN", raising=False)
    from engines.forge import build as B
    assert D.resolve_mode(None, B.load_rules()) == "archetype"


@pytest.mark.parametrize("task", ["Make it image-designed", "6 slides with a designed look"])
def test_engine_passes_the_design_wording(task):
    assert D.mode_from(task) == "image"
