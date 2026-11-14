"""Suggestions while typing: connectors named in the message, matching skills, the best agent, commands."""
from meshpool_harness import mcp_apps as CN
from meshpool_harness import skills as SK
from meshpool_harness import suggest as SG

CONN = [{"id": "atlassian", "name": "Jira and Confluence", "connected": False, "enabled": False},
        {"id": "notion", "name": "Notion", "connected": True, "enabled": False},
        {"id": "slack", "name": "Slack", "connected": True, "enabled": True},
        {"id": "linear", "name": "Linear", "connected": False, "enabled": False}]
SKILLS = [{"id": "dcf", "name": "finance-dcf-valuation", "description": "Build a discounted cash flow valuation."},
          {"id": "pr", "name": "pull-request", "description": "Open a pull request on GitHub."},
          {"id": "rec", "name": "finance-reconciliation", "description": "Match a bank statement to the ledger."}]


def setup(monkeypatch):
    monkeypatch.setattr(CN, "listing", lambda: CONN)
    monkeypatch.setattr(SK, "load_all", lambda include_disabled=True: SKILLS)


def kinds(text, **kw):
    return [(x["type"], x["id"]) for x in SG.suggest(text, **kw)]


def test_connectors(monkeypatch):
    setup(monkeypatch)
    assert ("connect", "atlassian") in kinds("Pull the open tickets from Jira and summarise them")
    assert ("enable", "notion") in kinds("Add these notes to Notion please")
    assert not [k for k in kinds("Post the summary to my Slack channel") if k[1] == "slack"]   # connected and on
    assert not [k for k in kinds("Draw a linear regression of these points") if k[1] == "linear"]   # a plain word
    assert ("connect", "linear") in kinds("File these bugs in Linear for the team")


def test_skills_match_specific_words_not_common_verbs(monkeypatch):
    setup(monkeypatch)
    assert ("skill", "dcf") in kinds("Build a DCF for Apple with a sensitivity table")
    assert ("skill", "rec") in kinds("Reconcile the bank statement with the ledger")
    assert ("skill", "pr") not in kinds("Pull the open tickets from Jira")
    assert kinds("$finance-dcf-valuation for Apple, five year forecast") == []      # already in the message


def test_commands_and_quiet_cases(monkeypatch):
    setup(monkeypatch)
    assert ("command", "/review") in kinds("review the changes on this branch before the PR")
    assert ("command", "/qa") in kinds("do a ux audit of my landing page")
    assert ("command", "/goal") in kinds("fix every failing test until all of them pass")
    assert kinds("what is the capital of france", harness="meshpool-code", kind="code") == []
    assert kinds("/goal ship it") == [] and kinds("hi") == []
