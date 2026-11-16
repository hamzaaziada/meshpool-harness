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
