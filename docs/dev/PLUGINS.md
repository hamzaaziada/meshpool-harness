# Extending the Meshpool Harness

The harness is small on purpose. There are four plug-in points. Each needs no change to the core loop.

## 1. Skills (drop-in instructions)
A skill is a short Markdown file of instructions for one kind of job. The agent sees a one-line index of skills and
calls `load_skill(name)` only when it needs one, so unused skills cost no tokens. Pass skills to `Workspace(...,
skills={"name": "markdown text"})` or add them to an engine.

## 2. Model profiles (any OpenAI-compatible model)
`profiles/<family>.yaml` describes how to talk to a model family: which model id to use per role, sampling
settings, tool-calling style, output limits and caching notes. Add a file, then set `MH_MODEL=<model id>` (and
`MESHPOOL_BASE_URL` / `MESHPOOL_API_KEY` for the endpoint). Existing profiles: deepseek, openai, claude, glm, kimi.

## 3. Engines (whole task types)
`engines/<name>/` holds one task type: `code` (repositories and terminals), `data` (analysis with number tracing),
`sheets` (banker-style workbooks), `slides` (consulting decks), `browser` (computer use in a real Chromium: the page is
shown as a numbered list of elements, so text-only models like DeepSeek can drive it; `pip install playwright`). The router (`meshpool_harness/router/`) picks an
engine from the request. A new engine needs an entry point that returns `{"ok", "answer", ...}` and a router rule.

## 4. Connectors (where tasks come from and where results go)
`meshpool_harness/connectors/` turns a ticket into a task and posts the outcome back:

```
python -m meshpool_harness work github:owner/repo#12 --workdir . --report
python -m meshpool_harness work jira:PROJ-7 --workdir . --report
python -m meshpool_harness work http:https://queue.internal/tasks/42 --report
python -m meshpool_harness work file:task.md
```

| Connector | Settings |
|---|---|
| `github` | `GITHUB_TOKEN` (or `GH_TOKEN`); `GITHUB_API_URL` for GitHub Enterprise. Also `open_pr()` for draft PRs. |
| `jira` | `JIRA_BASE_URL` plus `JIRA_EMAIL` + `JIRA_API_TOKEN` (Cloud) or `JIRA_PAT` (Server/Data Center) |
| `http` | Any system: `GET <url>` returns `{"title","body"}`, the result is POSTed to `<url>/result`. `MH_HTTP_TOKEN` optional. |
| `file` | A Markdown file (first line = title) or `-` for stdin |

Custom connector: subclass `Connector`, decorate with `@register("name")`, and list the module in `MH_CONNECTORS`
(or publish it under the `meshpool_harness.connectors` entry-point group). Example:

```python
from meshpool_harness.connectors import register
from meshpool_harness.connectors.base import Connector, Task

@register("linear")
class Linear(Connector):
    def fetch(self, ref):            # ref is what follows "linear:"
        return Task("linear", ref, title="...", body="...")
    def report(self, task, result):  # post self.summary(result) back
        ...
```

## Settings (environment variables)
| Variable | Effect |
|---|---|
| `MESHPOOL_BASE_URL`, `MESHPOOL_API_KEY` | OpenAI-compatible endpoint and key |
| `MH_MODEL` | Model id (default `deepseek-v4.1-flash`) |
| `MH_BUDGET_USD` | Optional per-task spend cap (stops the run when reached) |
| `MH_MODE=terminal` | Terminal light mode: no exploration nudges, longer command timeouts |
| `MH_MAX_SECONDS` | Wall-clock cap per task |

## In GitHub
`action.yml` runs the harness on a task; `.github/workflows/meshpool-fix.yml` runs it when an issue gets the
`meshpool` label and opens a draft pull request. Add your key as the `MESHPOOL_API_KEY` repository secret.

## Next (not built yet)
An MCP server wrapper, so other agents (Claude Code, Codex, Junie) can call the Meshpool Harness as a cheap tool.
