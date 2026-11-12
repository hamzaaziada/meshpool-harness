# Harnesses

Each `.json` file in this folder adds a harness (an agent) to Meshpool. Drop a file here, or drag it onto
Settings > Harnesses in the app, then start a new session.

Any agent that speaks the Agent Client Protocol (ACP) works:

```json
{
  "id": "my-agent",
  "name": "My agent",
  "mode": "cowork",
  "type": "acp",
  "command": "my-agent",
  "args": ["acp"],
  "env": {"SOME_KEY": "value"},
  "description": "One line shown in the picker.",
  "install": "How to install it, shown when the command is missing.",
  "share_mcp": true
}
```

- `mode` is `code` or `cowork`: the tab the harness appears in.
- `share_mcp` (default true) passes the MCP servers from Meshpool's `mcp.json` to the harness.
- The `examples` folder has ready-made files for Gemini CLI, Goose, Claude Code and Codex.
