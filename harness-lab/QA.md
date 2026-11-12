# Local QA set-up (no paid calls)

Used on 2026-11-12 to walk every journey in `testkit/JOURNEYS.md` against this repo's code.

## Fake home for the importer

```
fakehome/
  .claude/skills/claude-skill/SKILL.md          name: claude-skill; body "Say hello in French."
  .codex/skills/codex-skill/SKILL.md            name: codex-skill; body "Say hello in German."
  .config/Claude/claude_desktop_config.json     {"mcpServers": {"desk-server": {"command": "echo"}}}
  .cursor/mcp.json                              {"mcpServers": {"cursor-server": {"command": "echo"}}}
  .codex/config.toml                            [mcp_servers.codex-server] command = "echo"
```

## Pi and Hermes as in the installers

- Pi: `npm install @earendil-works/pi-coding-agent@1.5.2`, then link its `node_modules` as `app/vendor/pi/node_modules`.
- Hermes 0.21.2: tag `v2026.9.11` of NousResearch/hermes-agent. Apply `packaging/hermes-acp-server.patch`, then link
  it as `app/vendor/hermes`.
- Its dependencies: the `dependencies` and `acp` extra from its `pyproject.toml`. The package itself is not built;
  the app runs Hermes from source.
- `app/vendor/` is git-ignored.

## Python

A virtual environment with `packaging/requirements-cli.txt`, Hermes' dependencies, `playwright`, and optionally
`ruff` and `jedi` (for the code tools).
