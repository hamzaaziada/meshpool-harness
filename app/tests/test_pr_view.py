"""The pull request view: list, one pull request in full (diff, checks, runs, reviews, deployments) and the merge
rule (only an open, non-draft pull request whose checks pass and that GitHub says can merge)."""
import pytest

from meshpool_harness.ui import workbench as W

PR = {"number": 7, "title": "Add totals", "html_url": "https://github.com/o/r/pull/7", "state": "open", "draft": False,
      "merged_at": None, "head": {"ref": "feat", "sha": "abc1234def", "repo": {"full_name": "o/r"}},
      "base": {"ref": "main"}, "user": {"login": "meshpool"}, "updated_at": "2026-11-15T09:00:00Z",
      "created_at": "2026-11-15T08:00:00Z", "node_id": "PR_x", "body": "Adds totals", "additions": 12, "deletions": 3,
      "changed_files": 2, "commits": 1, "mergeable": True, "mergeable_state": "clean"}


def fake(checks="success", status="completed", mergeable=True, calls=None):
    def api(path, token, method="GET", body=None):
        if calls is not None:
            calls.append((method, path, body))
        if path.startswith("/repos/o/r/pulls?state=open"):
            return [PR]
        if path.startswith("/repos/o/r/pulls?state=closed"):
            return []
        if path == "/repos/o/r/pulls/7":
            return dict(PR, mergeable=mergeable)
        if path.startswith("/repos/o/r/pulls/7/files"):
            return [{"filename": "a.py", "status": "modified", "additions": 10, "deletions": 3,
                     "patch": "@@ -1,3 +1,4 @@\n x = 1\n-y = 2\n+y = 3\n+z = 4"},
                    {"filename": "logo.png", "status": "added", "additions": 0, "deletions": 0}]
        if "/check-runs" in path:
            return {"check_runs": [{"name": "tests", "status": status, "conclusion": checks if status == "completed" else None,
                                    "html_url": "u", "started_at": "2026-11-15T09:00:00Z", "completed_at": "2026-11-15T09:02:00Z",
                                    "app": {"name": "GitHub Actions"}, "output": {"title": "41 passed"}}]}
        if path.endswith("/status"):
            return {"statuses": []}
        if "/actions/runs?" in path:
            return {"workflow_runs": [{"id": 9, "name": "CI", "status": status, "conclusion": checks if status == "completed" else None,
                                       "html_url": "r", "event": "pull_request", "created_at": "x", "run_attempt": 1}]}
        if path.endswith("/reviews?per_page=50"):
            return [{"user": {"login": "ana"}, "state": "APPROVED"}]
        if "/deployments" in path or "/issues/7/comments" in path:
            return []
        if path.endswith("/merge"):
            return {"merged": True}
        raise AssertionError(path)
    return api


@pytest.fixture
def repo(monkeypatch):
    monkeypatch.setattr(W, "git_status", lambda root: {"repo": True, "github": "o/r", "branch": "feat"})
    from meshpool_harness import mcp_apps as CN
    monkeypatch.setattr(CN, "github_token", lambda: "tok")
    return "/proj"


def test_list_and_detail(repo, monkeypatch):
    monkeypatch.setattr(W, "_gh_api", fake())
    lst = W.pr_list(repo)
    assert lst["current"] == 7 and lst["prs"][0]["checks"] == "passing"
    d = W.pr_detail(repo, 7)
    assert [f["path"] for f in d["files"]] == ["a.py", "logo.png"] and d["files"][1]["binary"]
    assert "+y = 3" in d["files"][0]["patch"]
    assert d["verdict"] == "passing" and d["checks"][0]["seconds"] == 120 and d["checks"][0]["summary"] == "41 passed"
    assert d["reviews"] == [{"user": "ana", "state": "approved"}] and d["runs"][0]["id"] == 9
    assert d["can_merge"]


@pytest.mark.parametrize("kw,why", [({"checks": "failure"}, "failing"), ({"status": "in_progress"}, "running"),
                                     ({"mergeable": False}, "cannot merge")])
def test_merge_is_refused_unless_green_and_mergeable(repo, monkeypatch, kw, why):
    calls = []
    monkeypatch.setattr(W, "_gh_api", fake(calls=calls, **kw))
    assert not W.pr_detail(repo, 7)["can_merge"]
    with pytest.raises(ValueError, match=why):
        W.pr_action(repo, {"action": "merge", "pr": 7})
    assert not any(m == "PUT" for m, _, _ in calls)                     # nothing was merged
    with pytest.raises(ValueError, match=why):                          # the older Git panel path follows the same rule
        W.git_ci_action(repo, {"action": "merge", "pr": 7})


def test_merge_when_green_pins_the_checked_commit(repo, monkeypatch):
    calls = []
    monkeypatch.setattr(W, "_gh_api", fake(calls=calls))
    W.pr_action(repo, {"action": "merge", "pr": 7})
    put = [c for c in calls if c[0] == "PUT"]
    assert put and put[0][2]["sha"] == "abc1234def"                    # the commit whose checks passed, nothing newer


def test_comments_show_and_post_to_github(repo, monkeypatch):
    calls = []
    base = fake(calls=calls)

    def api(path, token, method="GET", body=None):
        if "/issues/7/comments" in path:
            calls.append((method, path, body))
            return [{"user": {"login": "ana"}, "body": "Looks good", "created_at": "x", "html_url": "c"}] if method == "GET" else {}
        return base(path, token, method, body)
    monkeypatch.setattr(W, "_gh_api", api)
    assert W.pr_detail(repo, 7)["comments"][0]["body"] == "Looks good"
    W.pr_action(repo, {"action": "comment", "pr": 7, "body": "Ship it"})
    assert ("POST", "/repos/o/r/issues/7/comments", {"body": "Ship it"}) in calls
    with pytest.raises(ValueError, match="Write a comment"):
        W.pr_action(repo, {"action": "comment", "pr": 7, "body": "  "})
