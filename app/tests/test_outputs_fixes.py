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
