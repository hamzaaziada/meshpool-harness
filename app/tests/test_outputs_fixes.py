"""Regressions from the outputs and tools bug hunt: git safety, file names, overwrites, comps, checks, honesty."""
import subprocess
from pathlib import Path

import pytest

from engines.sheets import builder as B
from meshpool_harness.ui import workbench as W


def git(cwd, *a):
    return subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", *a], cwd=cwd, capture_output=True,
                          text=True)


@pytest.fixture
def repo(tmp_path, monkeypatch):
    monkeypatch.setattr(W, "check_root", lambda r: Path(r))
    git(tmp_path, "init", "-q", "-b", "main")
    (tmp_path / "a.txt").write_text("base\n")
    git(tmp_path, "add", "-A")
    git(tmp_path, "commit", "-qm", "base")
    return tmp_path


def test_commit_is_refused_during_a_merge_conflict(repo):
    git(repo, "checkout", "-qb", "other")
    (repo / "a.txt").write_text("other\n")
    git(repo, "commit", "-qam", "other")
    git(repo, "checkout", "-q", "main")
    (repo / "a.txt").write_text("main\n")
    git(repo, "commit", "-qam", "main")
    git(repo, "merge", "other")                                     # conflicts
    with pytest.raises(ValueError, match="conflicts"):
        W.git_action(str(repo), {"action": "commit", "message": "merge"})
    assert "<<<<<<<" not in git(repo, "show", "HEAD:a.txt").stdout


def test_switching_away_from_commits_on_no_branch_is_refused(repo):
    git(repo, "checkout", "-q", "--detach")
    (repo / "notes.txt").write_text("work\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "detached work")
    with pytest.raises(ValueError, match="no branch"):
        W.git_action(str(repo), {"action": "checkout", "branch": "main"})
    assert (repo / "notes.txt").exists()


def test_file_names_beyond_latin1_are_sent_safely():
    from meshpool_harness.ui.server import _disposition
    h = _disposition("inline", "图表 – chart ✓.png")
    h.encode("latin-1")
    assert "filename*=UTF-8''" in h


def test_a_second_document_of_the_same_name_never_replaces_the_first(tmp_path):
    from meshpool_harness.agent.office_mcp_server import _free
    (tmp_path / "report.docx").write_text("first")
    assert _free(tmp_path / "report.docx").name == "report (2).docx"
    assert _free(tmp_path / "new.docx").name == "new.docx"


def test_comps_mark_bad_multiples_not_meaningful():
    calc = {k: f for k, _, f, _ in B.COMPS_CALCS}
    assert "<=0" in calc["equity"] and "NM" in calc["equity"]              # no price or shares: no equity value
    assert "ISNUMBER" in calc["ev"]
    assert "{ev}<=0" in calc["ev_ebitda"] and "{ev}<=0" in calc["ev_rev"]   # negative EV: no multiple


def test_master_check_counts_error_values(tmp_path):
    wb = B.new_workbook(["Checks"])
    B.check_row(wb, "Balance sheet balances", "=1/0") if hasattr(B, "check_row") else None
    src = Path(B.__file__).read_text()
    assert "ISERROR(C4:C" in src                                             # a #DIV/0! check no longer reads OK


def test_every_agent_is_told_not_to_make_things_up(tmp_path):
    from meshpool_harness import assistant as A
    from meshpool_harness.agent.core import Agent
    assert "I don't know" in A.HONESTY and "N/A" in A.HONESTY
    assert A.HONESTY in A.context_block()                                   # first message of every chat, any agent
    assert Agent(str(tmp_path)).system_prompt().endswith(A.HONESTY)


def test_a_broken_edit_shows_the_exact_place(tmp_path):
    from meshpool_harness.core.tools import ToolError, Workspace
    ws = Workspace(str(tmp_path), str(tmp_path / "out"))
    assert "'(' was never closed" in ws.write_file("a.py", "def f(x:\n    return x\n")
    ws.write_file("b.py", "x = [1, 2,\n     3]\n")
    ws.read_file("b.py")
    with pytest.raises(ToolError) as e:
        ws.edit("b.py", "3]", "3")
    assert "line 1, column 5" in str(e.value) and "^" in str(e.value) and (tmp_path / "b.py").read_text().endswith("3]\n")
    assert "column 7" in ws.write_file("c.json", '{"a": 1,\n "b": }\n')


def test_slides_added_to_a_users_deck_keep_its_slides_and_carry_charts(tmp_path):
    from pptx import Presentation
    from pptx.chart.data import CategoryChartData
    from pptx.enum.chart import XL_CHART_TYPE
    from pptx.util import Inches
    from engines.forge.merge import append
    from engines.forge.ooxml_check import check
    mine = Presentation()                                         # the user's 4:3 deck
    s = mine.slides.add_slide(mine.slide_layouts[0])
    s.shapes.title.text = "Q4 review"
    mine.save(tmp_path / "mine.pptx")
    new = Presentation()
    new.slide_width, new.slide_height = Inches(13.333), Inches(7.5)
    s = new.slides.add_slide(new.slide_layouts[6])
    cd = CategoryChartData()
    cd.categories = ["North", "South"]
    cd.add_series("Growth", (0.15, 0.06))
    s.shapes.add_chart(XL_CHART_TYPE.COLUMN_CLUSTERED, Inches(1), Inches(1), Inches(8), Inches(5), cd)
    tb = s.shapes.add_textbox(Inches(10), Inches(7), Inches(3), Inches(0.3))
    tb.name = "Firm"
    tb.text_frame.text = "Meshpool      7"
    new.save(tmp_path / "new.pptx")
    assert append(str(tmp_path / "mine.pptx"), str(tmp_path / "new.pptx"), str(tmp_path / "out.pptx")) == 1
    out = Presentation(tmp_path / "out.pptx")
    assert len(out.slides) == 2 and out.slides[0].shapes.title.text == "Q4 review"
    added = out.slides[1]
    assert any(sh.has_chart for sh in added.shapes)
    assert next(sh for sh in added.shapes if sh.name == "Firm").text_frame.text.endswith("2")   # renumbered
    assert all(sh.left + sh.width <= out.slide_width for sh in added.shapes)                      # scaled to fit 4:3
    assert check(str(tmp_path / "out.pptx"))["ok"]                                                 # opens without repair
