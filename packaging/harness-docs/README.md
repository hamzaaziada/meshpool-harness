# Meshpool Harness

The Meshpool coding agent as a single terminal command, `meshpool`, with its own Python built in. Use it in any
codebase, in CI, or plug it into an editor. All it needs is your Meshpool API key.

## First run

```
meshpool login          # paste your Meshpool API key once (shared with the Meshpool app)
cd path/to/your/project
meshpool                # start a session in this folder
```

## Commands

| Command | What it does |
|---|---|
| `meshpool` | Interactive session in the current folder. It asks before it edits files or runs commands. |
| `meshpool "fix the failing test"` | Session with a first request. |
| `meshpool -p "explain src/app.py"` | One request, prints the reply, exits. Good for scripts. |
| `meshpool -p "..." --auto` | Same, without asking before edits and commands (for CI). |
| `meshpool --continue` | Resume the last session in this folder. |
| `meshpool acp` | Serve the agent over the Agent Client Protocol for editors and other apps. |
| `meshpool acp --profile work` | The same, as the work agent (research, documents, decks, spreadsheets). |
| `meshpool run "..." --engine slides` | One-shot office engines: `slides`, `sheets`, `data`, `code`, `general`. |
| `meshpool mcp` | List your MCP servers and their tools. |
| `meshpool check --live` | Check settings, installed tools and the API key. |
| `meshpool ui` | Open the Meshpool web app in your browser. |

Inside a session: `/help`, `/clear`, `/auto`, `/model`, `/mcp`, `/tokens`, `/init`, `/exit`.

## What the agent can do

- Read, search and edit code. Edits are diffs you approve.
- Run commands, tests and builds (Git Bash on Windows).
- Search the web and read pages.
- Keep a visible plan for multi-step work.
- Make PowerPoint, Excel and data analysis output with Meshpool's office engines.
- Use any MCP server: GitHub (issues, pull requests, Actions), browser use, computer use, databases and more.

## Settings

Settings are shared with the Meshpool app:

- macOS: `~/Library/Application Support/Meshpool/meshpool.config` and `mcp.json`
- Windows: `%APPDATA%\Meshpool\meshpool.config` and `mcp.json`

`meshpool.config` holds `MESHPOOL_API_KEY`, `MESHPOOL_BASE_URL`, `MH_MODEL` and tokens such as `GITHUB_TOKEN`.
`mcp.json` uses the same format as Claude Desktop; set `"disabled": false` on a server to turn it on.

## Plug it into an editor (ACP)

- Zed: add `editors/zed-settings.json` to your Zed settings.
- JetBrains IDEs and VS Code: use their ACP agent support with the command `meshpool acp`.

## CI/CD: let the agent open pull requests

`ci/meshpool-agent.yml` is a GitHub Actions workflow. Run it by hand with a task, or label an issue `meshpool`.
It runs the agent on the repository and opens a pull request with the changes. Setup:

1. Add a repository secret named `MESHPOOL_API_KEY`.
2. Put the Meshpool source on a URL the runner can download, and set the repository variable
   `MESHPOOL_SOURCE_URL` to it. You can also commit the source to your repository and change the install step.
3. Copy the workflow to `.github/workflows/` in your repository.

## Safety

Without `--auto`, the agent asks before every file change and command. File tools only write inside the project
folder. Commands are not sandboxed, so use `--auto` only where changes can be reviewed, such as CI branches.
