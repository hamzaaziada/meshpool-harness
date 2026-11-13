"""Every built-in skill parses, is named after its folder, says when to use it, and names only tools that exist."""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from meshpool_harness import skills as SK  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DEFAULTS = ROOT / "defaults" / "skills"
TOOLS = {"make_document", "make_pdf", "make_spreadsheet", "make_deck", "web_search", "web_research", "fetch_url",
         "schedule_task", "run_subtasks", "memory_save", "ask_user", "use_skill"}


def _office_tools() -> set:
    src = (ROOT / "meshpool_harness" / "agent" / "office_mcp_server.py").read_text(encoding="utf-8")
    return set(re.findall(r'^\s+"(make_[a-z_]+)": \(', src, re.M))


def test_every_default_skill_is_well_formed():
    dirs = sorted(d for d in DEFAULTS.iterdir() if d.is_dir())
    assert len(dirs) >= 95
    for d in dirs:
        meta, body = SK.parse((d / "SKILL.md").read_text(encoding="utf-8"))
        assert meta.get("name") == d.name, d.name
        assert len(meta.get("description", "")) >= 40, d.name
        assert body.strip(), d.name


def test_everyday_skills_name_only_real_tools():
    assert {"make_document", "make_pdf", "make_spreadsheet", "make_deck"} <= _office_tools()
    everyday = ["inbox-triage", "email-reply", "meeting-prep", "meeting-notes", "chat-catch-up", "daily-plan",
                "weekly-status-update", "memo", "proofread", "blog-post", "translate-document", "press-release",
                "seo-content-brief", "social-posts", "competitor-analysis", "prd", "feedback-synthesis",
                "prioritise-roadmap", "job-description", "interview-plan", "analyse-data", "survey-analysis",
                "project-plan", "sop", "deck-from-document", "debug-issue", "write-tests", "project-docs"]
    for name in everyday:
        text = (DEFAULTS / name / "SKILL.md").read_text(encoding="utf-8")
        used = set(re.findall(r"\b(make_[a-z_]+|web_[a-z_]+|fetch_url|schedule_task|run_subtasks|memory_save)\b", text))
        assert used <= TOOLS, (name, used - TOOLS)
        assert "Draft, never send" in text


def test_new_defaults_reach_existing_installs(tmp_path, monkeypatch):
    """An install that already had the older defaults gets the new ones once, and a removed one stays removed."""
    monkeypatch.setenv("MESHPOOL_HOME", str(tmp_path))
    dst = SK.folder()
    dst.mkdir(parents=True)
    (dst / ".defaults-installed").write_text('["code-review", "pull-request", "research-brief", "memo"]')
    SK.ensure_defaults()
    assert (dst / "inbox-triage" / "SKILL.md").is_file()
    assert not (dst / "memo").exists()                     # offered before and removed by the user
