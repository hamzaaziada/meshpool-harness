"""Decks drawn with one icon set: icon_set on render() (or theme["icon_set"]) embeds that set's icons as SVG with a
PNG fallback, the file still opens without repair (P0) and passes the deck rules; the default deck stays Lucide PNG."""
import re
import sys
import warnings
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
warnings.filterwarnings("ignore")

from engines.forge import ooxml_check as OX  # noqa: E402
from engines.forge import validate_pptx as VP  # noqa: E402

HAS_XSD = OX._schema_dir() is not None


def _slides():
    from test_density import sample
    return [s for s in sample() if s["archetype"] == "challenge_matrix"]


def _icons(path):
    z = zipfile.ZipFile(path)
    xml = "".join(z.read(n).decode() for n in z.namelist() if re.match(r"ppt/slides/slide\d+\.xml$", n))
    media = [n for n in z.namelist() if n.startswith("ppt/media/")]
    return xml, media, z.read("[Content_Types].xml").decode()


@pytest.mark.parametrize("icon_set", ["phosphor", "material-rounded", "carbon"])
def test_deck_with_icon_set_embeds_svg_and_passes_p0(tmp_path, icon_set):
    from engines.forge.render import render
    out = tmp_path / "d.pptx"
    render(_slides(), str(out), fix_labels=False, icon_set=icon_set)
    xml, media, ct = _icons(out)
    tags = re.findall(r'descr="icon:([^:"]+):', xml)
    assert tags and set(tags) == {icon_set}                                  # one set for the whole deck
    assert xml.count("asvg:svgBlip") == len(tags) and "{96DAC541-7B7A-43D3-8B79-37D633B846F1}" in xml
    assert any(m.endswith(".svg") for m in media) and any(m.endswith(".png") for m in media)
    assert "image/svg+xml" in ct
    r = OX.check(str(out))
    assert r["ok"], r["problems"]
    if HAS_XSD:
        assert r["schema_validation"] == "run"
    v = VP.validate(str(out), render=False)
    assert v["rules"]["P0"] == "pass" and v["rules"]["P17"] == "pass" and v["rules"]["P9"] == "pass", \
        [f for f in v["failures"] if f["rule"] in ("P0", "P9", "P17")]


def test_icon_set_from_theme_and_reused_svg_parts(tmp_path):
    from engines.forge.render import render
    out = tmp_path / "t.pptx"
    slides = _slides()
    render(slides + slides, str(out), fix_labels=False, theme={"palette": "ocean", "icon_set": "heroicons"})
    xml, media, _ = _icons(out)
    assert set(re.findall(r'descr="icon:([^:"]+):', xml)) == {"heroicons"}
    n_icons = len(re.findall(r'descr="icon:', xml))
    assert len([m for m in media if m.endswith(".svg")]) <= n_icons // 2      # the same SVG is stored once


def test_default_deck_is_unchanged_lucide_png(tmp_path):
    from engines.forge.render import ICON_SET, render
    render(_slides(), str(tmp_path / "x.pptx"), fix_labels=False, icon_set="phosphor")
    out = tmp_path / "d.pptx"
    render(_slides(), str(out), fix_labels=False)                            # the set does not leak to the next deck
    assert ICON_SET["name"] is None
    xml, media, ct = _icons(out)
    assert set(re.findall(r'descr="icon:([^:"]+):', xml)) == {"lucide"}
    assert "svgBlip" not in xml and not any(m.endswith(".svg") for m in media) and "svg+xml" not in ct
    assert re.findall(r'descr="icon:lucide:([^:"]+):', xml)[:2] == ["money", "people"]    # alt text as before


def test_unknown_icon_set_is_an_error(tmp_path):
    from engines.forge.render import render
    with pytest.raises(ValueError):
        render(_slides(), str(tmp_path / "d.pptx"), fix_labels=False, icon_set="nope")


def test_meanings_resolve_in_every_set():
    from engines.icons import SETS
    from engines.forge.render import icon_ref
    for s in SETS:
        for meaning in ("money", "people", "risk", "growth", "congestion"):
            got, name = icon_ref(meaning, s)
            assert got == s and name, (s, meaning)
    assert icon_ref("money", "phosphor") == ("phosphor", "money")
    assert icon_ref("congestion", "carbon")[1] == "traffic-cone"
    assert icon_ref("risk", "material")[1] == "warning"
    assert icon_ref("money", None) == ("lucide", "circle-dollar-sign")
