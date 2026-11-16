"""Post-render passes: the type scale (P4) and the reading order (P18)."""
from pptx import Presentation
from pptx.util import Inches, Pt


def _deck(path, boxes):
    prs = Presentation()
    prs.slide_width, prs.slide_height = Inches(13.33), Inches(7.5)
    sl = prs.slides.add_slide(prs.slide_layouts[6])
    for top, size, text in boxes:
        tb = sl.shapes.add_textbox(Inches(1), Inches(top), Inches(10), Inches(0.5))
        r = tb.text_frame.paragraphs[0].add_run()
        r.text, r.font.size = text, Pt(size)
    prs.save(path)
    return prs


def test_type_scale_snaps_down_to_the_cap(tmp_path):
    from engines.forge.typescale import harmonize
    p = str(tmp_path / "d.pptx")
    sizes = [12, 13, 14, 15, 16, 16, 16]
    _deck(p, [(1 + 0.6 * i, z, f"Body text line {i} with words") for i, z in enumerate(sizes)])
    assert harmonize(p) > 0
    got = [r.font.size.pt for sh in Presentation(p).slides[0].shapes for r in sh.text_frame.paragraphs[0].runs]
    assert len(set(got)) <= 3 and all(g <= z for g, z in zip(got, sizes)) and min(got) == 12


def test_reading_order_runs_top_to_bottom():
    from engines.forge.render import _reading_order
    import tempfile, os
    p = os.path.join(tempfile.mkdtemp(), "o.pptx")
    prs = _deck(p, [(5.8, 14, "Decision box at the bottom"), (1.7, 14, "Row one"), (2.8, 14, "Row two")])
    _reading_order(prs)
    assert [sh.text_frame.text for sh in prs.slides[0].shapes] == ["Row one", "Row two", "Decision box at the bottom"]


def test_reading_order_puts_the_title_first():
    """A kicker drawn above the title moves after it: the title is always read first."""
    from pptx import Presentation as P
    from pptx.util import Inches
    from engines.forge.render import _reading_order
    prs = P()
    sl = prs.slides.add_slide(prs.slide_layouts[5])        # title only
    sl.shapes.title.text, sl.shapes.title.top = "The title", Inches(0.5)
    tb = sl.shapes.add_textbox(Inches(1), Inches(0.1), Inches(4), Inches(0.3))
    tb.text_frame.text = "Kicker above the title"
    low = sl.shapes.add_textbox(Inches(1), Inches(3), Inches(4), Inches(0.3))
    low.text_frame.text = "Body"
    sl.shapes._spTree.remove(low._element)
    sl.shapes._spTree.insert(2, low._element)                # drawn first, sits lowest
    _reading_order(prs)
    assert [sh.text_frame.text for sh in sl.shapes] == ["The title", "Kicker above the title", "Body"]


def test_reading_order_keeps_a_cover_fill_behind_the_title(tmp_path):
    """The dark cover's full-bleed rectangle is drawn first; the title must stay above it (not hidden under it)."""
    from pptx import Presentation as P
    from engines.forge.render import render
    out = tmp_path / "c.pptx"
    render([{"archetype": "cover", "title": "Fund the warehouses and return cash", "subtitle": "Board briefing",
             "date": "November 2026"}], str(out), carry=False)
    shapes = list(P(str(out)).slides[0].shapes)
    ti = next(i for i, sh in enumerate(shapes) if sh.is_placeholder and sh.placeholder_format.type in (1, 3))
    big = [i for i, sh in enumerate(shapes) if sh.width and sh.height and sh.width * sh.height > 0.5 * 12192000 * 6858000]
    assert all(i < ti for i in big), (ti, big, [sh.name for sh in shapes])
