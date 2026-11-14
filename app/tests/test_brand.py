"""Company look (engines/forge/brand.py, Settings > Company): learn colours, fonts, logo, name and slide master from a
company's decks; map the colours onto the forge roles with readable contrast; build decks in that look (the forge
layout recoloured, or the company's own master) that still pass the deck rules; store profiles; route them through
the forge engine and the settings API."""
import copy
import io
import json
import shutil
import sys
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlparse

import pytest
from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.util import Inches, Pt

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

HAS_LO = shutil.which("soffice") is not None or shutil.which("libreoffice") is not None
lo = pytest.mark.skipif(not HAS_LO, reason="LibreOffice needed")
GREEN, GOLD = "00594F", "C69214"
BLIP = "{http://schemas.openxmlformats.org/drawingml/2006/main}blip"
EMBED = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}embed"


def make_logo(path):
    from PIL import Image, ImageDraw
    im = Image.new("RGBA", (600, 160), (255, 255, 255, 0))
    ImageDraw.Draw(im).rectangle([0, 20, 120, 140], fill=(0, 89, 79))
    im.save(path)
    return path


def make_deck(path, logo, n=6, wide=True, master_logo=True, logo_xy=(11.6, 6.95), footer=True):
    """A company deck: green and gold fills on the stock Office theme, a logo on the master (or on every slide)
    and a repeated footer line."""
    prs = Presentation()
    if wide:
        prs.slide_width, prs.slide_height = Inches(13.333), Inches(7.5)
    if master_logo:
        tmp = prs.slides.add_slide(prs.slide_layouts[6])
        pic = tmp.shapes.add_picture(str(logo), Inches(logo_xy[0]), Inches(logo_xy[1]), height=Inches(0.35))
        m = prs.slide_masters[0]
        _, rid = m.part.get_or_add_image_part(str(logo))
        el = copy.deepcopy(pic._element)
        el.find(f".//{BLIP}").set(EMBED, rid)
        m.shapes._spTree.append(el)
        sid = prs.slides._sldIdLst[-1]
        prs.part.drop_rel(sid.rId)
        prs.slides._sldIdLst.remove(sid)
    for i in range(n):
        s = prs.slides.add_slide(prs.slide_layouts[5])
        s.shapes.title.text = f"Page {i}"
        for x, w, col in ((0.6, 5, GREEN), (6, 2, GOLD)):
            b = s.shapes.add_shape(1, Inches(x), Inches(1.6), Inches(w), Inches(2))
            b.fill.solid()
            b.fill.fore_color.rgb = RGBColor.from_string(col)
        if footer:
            t = s.shapes.add_textbox(Inches(0.6), Inches(6.95), Inches(6), Inches(0.3))
            t.text_frame.text = "© 2025 Verdant Partners | Strictly confidential"
            t.text_frame.paragraphs[0].runs[0].font.size = Pt(9)
        if not master_logo:
            s.shapes.add_picture(str(logo), Inches(11.6), Inches(0.3), height=Inches(0.35))
    prs.save(path)
    return str(path)


@pytest.fixture()
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("MESHPOOL_HOME", str(tmp_path / "home"))
    return tmp_path


@pytest.fixture()
def decks(tmp_path):
    logo = make_logo(tmp_path / "logo.png")
    return {"wide": make_deck(tmp_path / "wide.pptx", logo), "four3": make_deck(tmp_path / "four3.pptx", logo,
                                                                                 wide=False, master_logo=False)}


# ------------------------------------------------------------------ colours
def test_roles_keep_the_brand_and_stay_readable():
    from collections import Counter
    from engines.forge import brand as B
    stock = {"dk1": "000000", "lt1": "FFFFFF", "dk2": "1F497D", "lt2": "EEECE1", "accent1": "4F81BD",
             "accent2": "C0504D", "accent3": "9BBB59", "accent4": "8064A2", "accent5": "4BACC6", "accent6": "F79646"}
    c = B.map_roles(stock, Counter({GREEN: 30, GOLD: 12, "666666": 4}))
    assert c["accent1"] == GREEN                       # the colour the slides use, not the stock theme's blue
    assert B._hue_gap(c["accent2"], GOLD) < 15         # the second brand hue is the highlight, lightened
    assert c["dk2"] != c["accent1"] and B.contrast(c["dk2"], "FFFFFF") >= 7
    assert B.check_roles(c) == []
    # awkward brands: a pale yellow, a neon, a single colour, no colour at all, a custom theme only
    cases = [Counter({"FFE600": 20}), Counter({"39FF14": 9, "FF00FF": 5}), Counter({"E4002B": 50}), Counter(),
             Counter({"F2F2F2": 40, "333333": 10})]
    for use in cases:
        assert B.check_roles(B.map_roles(stock, use)) == [], use
    own = dict(stock, accent1="7A1F5C", accent2="F2A900", dk2="2B0A3D")
    c = B.map_roles(own, Counter())
    assert c["accent1"] == "7A1F5C" and B.check_roles(c) == []


def test_check_roles_names_the_problem():
    from engines.forge import brand as B
    from engines.forge.template import COLORS
    bad = dict(COLORS, accent1="FFF4B0", dk2="CCCCCC", accent2="222222")
    probs = " ".join(B.check_roles(bad))
    assert "Emphasis" in probs and "Brand dark" in probs and "Highlight" in probs


# ------------------------------------------------------------------ learning
def test_learn_from_decks(home, decks):
    from engines.forge import brand as B
    p = B.learn([decks["wide"], decks["four3"]])
    assert p["name"] == "Verdant Partners"
    assert p["footer"].startswith("© 2025 Verdant Partners")
    assert p["colors"]["accent1"] == GREEN and p["problems"] == []
    assert p["fonts"] == {"major": "Calibri", "minor": "Calibri"}
    assert p["logo"] and Path(p["logo"]).read_bytes()[:4] == b"\x89PNG"
    assert p["template"] and p["template_mode"] == "master" and p["logo_on_master"]
    assert abs(p["master_corner"] - 11.6) < 0.01       # the master's logo sits in the footer's right corner
    assert [s["file"] for s in p["sources"]] == ["wide.pptx", "four3.pptx"]


def test_learn_explains_what_it_could_not_reuse(home, decks, tmp_path):
    from engines.forge import brand as B
    p = B.learn([decks["four3"]])
    assert p["template"] is None and p["template_mode"] == "forge"
    assert "10.00 x 7.50 in" in p["master_note"]
    assert p["logo"]                                   # the same small picture on every slide is the logo
    # artwork across the body: the master is not reused
    logo = make_logo(tmp_path / "l2.png")
    d = make_deck(tmp_path / "busy.pptx", logo, logo_xy=(5, 3))
    p = B.learn([d])
    assert p["template"] is None and "body area" in p["master_note"]
    with pytest.raises(ValueError):
        B.learn([str(tmp_path / "notes.txt")])


def test_find_layout_by_placeholders(tmp_path):
    from engines.forge import brand as B
    prs = Presentation()
    for lay in prs.slide_masters[0].slide_layouts:
        lay.name = "Custom " + lay.name.replace(" ", "")[:4]
    assert {ph.placeholder_format.type for ph in B.find_layout(prs, "Title Only").placeholders} - {13, 15, 16} \
        <= {1}                                          # title (+ date / footer / number) only
    assert any(ph.placeholder_format.type == 3 for ph in B.find_layout(prs, "Title Slide").placeholders)


# ------------------------------------------------------------------ rendering
def _shapes(path):
    prs = Presentation(path)
    return [[(sh.name, sh) for sh in sl.shapes] for sl in prs.slides]


def test_render_in_the_company_look(home, decks, tmp_path):
    from engines.forge import brand as B
    from engines.forge.render import RIGHT
    p = B.save(B.learn([decks["wide"]]), activate=True)
    out = tmp_path / "forge.pptx"
    B.sample_deck(dict(p, template_mode="forge"), str(out))
    pages = _shapes(str(out))
    assert all(any(n == "ov_Logo" for n, _ in pg) for pg in pages)       # the logo on the cover and every page
    firm = [sh for pg in pages[1:] for n, sh in pg if n == "Firm"]
    assert all(sh.text_frame.text.strip().isdigit() for sh in firm)        # the logo replaces the firm name
    cover = dict(pages[0])
    assert cover["Firm"].text_frame.text == "Verdant Partners"
    prs = Presentation(str(out))
    from engines.forge.template import _theme_part
    from lxml import etree
    x = etree.fromstring(_theme_part(prs).blob)
    a1 = x.find(".//{http://schemas.openxmlformats.org/drawingml/2006/main}accent1/*").get("val")
    assert a1 == GREEN                                                     # theme slots carry the brand
    # the company master: its logo stays, the firm label ends left of it, on a column edge
    out2 = tmp_path / "master.pptx"
    B.sample_deck(p, str(out2))
    prs2 = Presentation(str(out2))
    assert any(sh.shape_type == 13 for sh in prs2.slide_masters[0].shapes)  # the master's own logo
    for pg in _shapes(str(out2))[1:]:
        d = dict(pg)
        assert "ov_Logo" not in d
        assert (d["Firm"].left + d["Firm"].width) / 914400 <= 11.6 - 0.1 + 1e-6
    assert RIGHT > 11.6


def test_brand_off_leaves_the_meshpool_look(tmp_path):
    from engines.forge import brand as B
    from engines.forge.render import render
    out = tmp_path / "plain.pptx"
    render(copy.deepcopy(B.SAMPLE), str(out), fix_labels=False)
    pages = _shapes(str(out))
    assert not any(n == "ov_Logo" for pg in pages for n, _ in pg)
    assert dict(pages[1])["Firm"].text_frame.text.startswith("Meshpool")


@lo
def test_sample_decks_pass_the_deck_rules(home, decks, tmp_path):
    from engines.forge import brand as B
    from engines.forge.validate_pptx import validate
    p = B.save(B.learn([decks["wide"]]), activate=True)
    for mode in ("forge", "master"):
        out = tmp_path / f"{mode}.pptx"
        res = B.sample_deck(dict(p, template_mode=mode), str(out))
        assert res["errors"] == []
        fails = [f["message"] for f in validate(str(out))["failures"] if f["status"] == "fail"]
        assert fails == [], (mode, fails)


# ------------------------------------------------------------------ store and engine
def test_store_and_request_routing(home, decks):
    from engines.forge import brand as B
    assert B.active() is None and B.for_request("a deck on our strategy") is None
    p = B.save(B.learn([decks["wide"]]))
    assert B.active()["id"] == p["id"]                  # the first profile becomes the active one
    other = B.save({"name": "Northwind Bank", "colors": {"accent1": "#123ABC"}}, activate=False)
    assert other["colors"]["accent1"] == "123ABC" and other["template_mode"] == "forge"
    assert B.for_request("deck for Northwind Bank board")["id"] == other["id"]
    assert B.for_request("deck for the board")["id"] == p["id"]
    assert B.for_request("deck for the board, no branding") is None
    B.delete(p["id"])
    assert B.active() is None and not (B.store_dir() / p["id"]).exists()


def test_brief_does_not_ask_for_a_theme_when_a_company_look_applies(home, decks):
    from engines.forge import brand as B
    from engines.forge.brief import complete, settled
    assert settled("A deck on the market")["theme"] is False
    assert "navy theme" in complete("A deck on the market")
    assert not (B.store_dir(create=False)).exists()                    # reading the store writes nothing
    B.save(B.learn([decks["wide"]]), activate=True)
    assert settled("A deck on the market")["theme"] is True
    assert "theme" not in complete("A deck on the market")             # the colours stay the company's


def test_engine_uses_the_active_look(home, decks):
    from engines.forge import brand as B
    from engines.forge.engine import brand_from
    look, prof = brand_from("A 10-slide deck on the market")
    assert prof is None and look == {"theme": None}
    B.save(B.learn([decks["wide"]]), activate=True)
    look, prof = brand_from("A 10-slide deck on the market")
    assert prof["name"] == "Verdant Partners"
    assert look["theme"]["accent1"] == GREEN and look["theme"]["fonts"]["major"] == "Calibri"
    assert Path(look["template"]).exists() and look["brand"]["footer_right"] == pytest.approx(11.6)
    look, _ = brand_from("A deck on the market, ocean theme")      # words in the request win over the profile
    assert look["theme"] == {"palette": "ocean"} and "template" not in look and look["brand"]["firm"]


# ------------------------------------------------------------------ settings API
def test_company_api(home, decks, monkeypatch, tmp_path):
    from meshpool_harness.ui import company_api as CO
    from meshpool_harness.ui import server as S
    monkeypatch.setattr(S, "UPLOADS", tmp_path / "uploads")
    monkeypatch.setattr(S, "RUNS", tmp_path / "runs")
    up = S.UPLOADS / "a1"
    up.mkdir(parents=True)
    shutil.copy(decks["wide"], up / "wide.pptx")

    def call(path, body=None, method="POST", raw=None):
        u = urlparse(path)
        return CO.handle(method, u, raw if raw is not None else json.dumps(body or {}).encode())

    code, st = call("/api/company", method="GET")
    assert code == 200 and st["profiles"] == [] and len(st["roles"]) == 9
    with pytest.raises(ValueError):                                    # only files the upload route wrote
        call("/api/company/learn", {"paths": [decks["wide"]]})
    code, st = call("/api/company/learn", {"paths": [str(up / "wide.pptx")]})
    pid = st["learned"]
    p = st["profiles"][0]
    assert st["active"] == pid and p["logo_data"].startswith("data:image/png;base64,")
    assert "template" not in p and "logo" not in p and p["has_master"]   # no server paths go to the browser
    code, st = call("/api/company/save", {"profile": {"id": pid, "name": "Verdant", "colors": {"accent1": "#0B6E4F"},
                                                       "template_mode": "forge"}})
    p = st["profiles"][0]
    assert p["name"] == "Verdant" and p["colors"]["accent1"] == "0B6E4F" and p["template_mode"] == "forge"
    assert p["sources"] and p["logo_data"]                                # learned fields survive an edit
    buf = io.BytesIO()
    from PIL import Image
    Image.new("RGB", (40, 20), (200, 0, 0)).save(buf, "PNG")
    code, st = call(f"/api/company/logo?id={pid}&name=new.png", raw=buf.getvalue())
    assert code == 200
    with pytest.raises(Exception):
        call(f"/api/company/logo?id={pid}&name=x.png", raw=b"not an image")
    code, r = call("/api/company/sample", {"id": pid})
    assert r["ok"] and r["errors"] == [] and r["url"].startswith("/files/company-sample/Verdant-sample-deck.pptx?t=")
    assert (S.RUNS / "company-sample" / "out" / r["file"]).exists()
    code, st = call("/api/company/activate", {"id": None})
    assert st["active"] is None
    code, st = call("/api/company/delete", {"id": pid})
    assert st["profiles"] == []


def test_company_tab_is_served_and_hooked():
    ui = ROOT / "meshpool_harness" / "ui"
    assert '<script src="/ui/company.js' in (ui / "index.html").read_text()
    js = (ui / "company.js").read_text()
    assert 'SECTIONS.splice' in js and "renderSection = async function" in js
    srv = (ui / "server.py").read_text()
    assert "company\\.js" in srv and srv.count('if u.path.startswith("/api/company")') == 2
    node = shutil.which("node")
    if node:
        import subprocess
        assert subprocess.run([node, "--check", str(ui / "company.js")]).returncode == 0


def test_attached_decks_set_the_look_for_one_deck(home, decks):
    from engines.forge import brand as B
    from engines.forge.brief import settled
    from engines.forge.engine import brand_from
    task = "Build a 10-slide deck on the market in the same format as the attached deck"
    look, prof = brand_from(task, [decks["wide"]])
    assert prof["name"] == "Verdant Partners" and look["theme"]["accent1"] == GREEN
    assert B.profiles()["profiles"] == []                               # nothing saved
    assert settled(task, [decks["wide"]])["theme"] is True
    look, prof = brand_from("Summarise the attached deck in 5 slides", [decks["wide"]])
    assert prof is None                                                 # content, not a style to copy


def _as_potx(src, dst):
    import zipfile
    with zipfile.ZipFile(src) as zin, zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED) as zout:
        for it in zin.infolist():
            data = zin.read(it.filename)
            if it.filename == "[Content_Types].xml":
                data = data.replace(b"presentationml.presentation.main+xml", b"presentationml.template.main+xml")
            zout.writestr(it, data)
    return str(dst)


def test_potx_template_is_learned_and_measured(home, decks, tmp_path):
    from engines.forge import brand as B
    from engines.forge.tools import measure_office_file
    potx = _as_potx(decks["wide"], tmp_path / "company.potx")
    p = B.learn([potx])
    assert p["template"] and p["template_mode"] == "master"            # the master is reused from a .potx
    out, err = measure_office_file(potx)
    assert not err and json.loads(out)


def test_logo_rels_use_zip_names_on_windows(decks, monkeypatch):
    import zipfile
    from pathlib import PureWindowsPath
    from engines.forge import brand as B
    monkeypatch.setattr(B, "Path", PureWindowsPath)                      # what Path does on Windows
    with zipfile.ZipFile(decks["wide"]) as zf:
        rels = B._rels(zf, "ppt/slideMasters/slideMaster1.xml")
    assert any(t.startswith("ppt/media/") for t in rels.values()), rels


def test_cli_engine_finds_the_attached_deck_in_the_workdir(home, decks, tmp_path, monkeypatch):
    from engines.forge import engine as FE
    work = tmp_path / "work"
    work.mkdir()
    shutil.copy(decks["wide"], work / "old.pptx")
    seen = {}
    monkeypatch.setattr(FE, "brand_from", lambda task, files=None: seen.setdefault("files", files) and ({}, None))
    monkeypatch.setattr("engines.forge.build.build", lambda *a, **k: (_ for _ in ()).throw(SystemExit))
    with pytest.raises(SystemExit):
        FE.solve("Build a deck in the same style as the attached deck", workdir=str(work), task_id="t")
    assert [Path(f).name for f in seen["files"]] == ["old.pptx"]
