"""Long decks, slides inserted in the middle of a deck, and several decks consolidated into one."""
from pptx import Presentation

from engines.forge.merge import consolidate, insert
from engines.forge.render import render


def _deck(path, titles):
    sl = [{"archetype": "cover", "title": titles[0]}]
    for t in titles[1:]:
        sl.append({"archetype": "bar_takeaway", "title": t, "subtitle": "Revenue, $m, 2025",
                   "chart": {"kind": "bar", "categories": ["North", "South", "East"],
                             "series": [{"name": "Revenue", "values": [420, 310, 220]}], "unit": "$m"},
                   "takeaway": {"text": "North carries the growth"}, "bullets": ["North grew 18%"],
                   "source": {"text": "Company accounts"}})
    r = render(sl, str(path), carry=False, density="balanced")
    assert r["errors"] == []
    return path


def _titles(path):
    return [s.shapes.title.text_frame.text if s.shapes.title is not None else
            next((sh.text_frame.text for sh in s.shapes if sh.has_text_frame and sh.text_frame.text.strip()), "")
            for s in Presentation(str(path)).slides]


def _pages(path):
    out = []
    for s in Presentation(str(path)).slides:
        f = next((sh for sh in s.shapes if sh.name == "Firm"), None)
        last = f.text_frame.text.split()[-1] if f is not None and f.text_frame.text.split() else ""
        out.append(last if last.isdigit() else None)          # covers carry the firm name only
    return out


def test_slides_inserted_in_the_middle_keep_order_and_renumber(tmp_path):
    base = _deck(tmp_path / "base.pptx", ["Main deck", "North leads revenue growth by 18%", "South trails at 4%",
                                           "East holds flat at 220"])
    add = _deck(tmp_path / "add.pptx", ["Added", "West opens 12 stores in 2026"])
    n = insert(str(base), str(add), str(tmp_path / "out.pptx"), at=3)
    assert n == 2
    t = _titles(tmp_path / "out.pptx")
    assert t[0] == "Main deck" and t[1].startswith("North") and t[2] == "Added" and t[3].startswith("West")
    assert t[4].startswith("South") and t[5].startswith("East") and len(t) == 6
    pages = _pages(tmp_path / "out.pptx")
    assert all(p == str(i) for i, p in enumerate(pages, 1) if p)                  # every number is its place


def test_insert_past_the_end_appends(tmp_path):
    base = _deck(tmp_path / "b.pptx", ["Deck", "North leads revenue growth by 18%"])
    add = _deck(tmp_path / "a.pptx", ["Extra", "West opens 12 stores in 2026"])
    insert(str(base), str(add), str(tmp_path / "o.pptx"), at=99)
    assert _titles(tmp_path / "o.pptx")[-1].startswith("West")


def test_three_decks_consolidate_in_order_with_pages_renumbered(tmp_path):
    a = _deck(tmp_path / "a.pptx", ["Deck A", "North leads revenue growth by 18%"])
    b = _deck(tmp_path / "b.pptx", ["Deck B", "South trails at 4%"])
    c = _deck(tmp_path / "c.pptx", ["Deck C", "East holds flat at 220"])
    total = consolidate([str(a), str(b), str(c)], str(tmp_path / "all.pptx"))
    assert total == 6
    t = _titles(tmp_path / "all.pptx")
    assert [x for x in t if x.startswith("Deck")] == ["Deck A", "Deck B", "Deck C"]
    assert all(p == str(i) for i, p in enumerate(_pages(tmp_path / "all.pptx"), 1) if p)


def test_a_long_deck_renders_clean_with_an_agenda_and_numbered_pages(tmp_path):
    titles = ["Long deck"] + [f"Region {i} revenue grew {i + 3}% in 2025" for i in range(1, 24)]
    path = _deck(tmp_path / "long.pptx", titles)
    prs = Presentation(str(path))
    assert len(prs.slides) == 24
    assert [p for p in _pages(path) if p] == [str(i) for i in range(2, 25)]


def test_insert_position_from_the_request():
    from engines.forge.engine import insert_position
    assert insert_position("Add two slides on West after slide 4") == 5
    assert insert_position("put a market slide before slide 7") == 7
    assert insert_position("add it as slide 3 of the deck") == 3
    assert insert_position("insert a summary at the start") == 1
    assert insert_position("add a slide after the second slide") == 3
    assert insert_position("add three slides on pricing") is None
