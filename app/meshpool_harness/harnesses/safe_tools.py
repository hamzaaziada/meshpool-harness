"""MCP tools that run without asking in every access mode: they read, or only touch Meshpool's own memory and
schedule. One list for the built-in agent (agent/core.py) and Pi (pi_rpc.py, which hands it to pi_gate.js)."""
from __future__ import annotations

SAFE_MCP = frozenset({
    "mcp__meshpool_web__web_search", "mcp__meshpool_web__web_fetch", "mcp__meshpool_web__web_research",
    "mcp__meshpool_assistant__memory_view", "mcp__meshpool_assistant__memory_save",
    "mcp__meshpool_assistant__memory_forget", "mcp__meshpool_assistant__schedule_task",
    "mcp__meshpool_assistant__list_scheduled_tasks", "mcp__meshpool_assistant__search_chats",
    "mcp__meshpool_assistant__run_subtasks", "mcp__meshpool_office__read_document",
    "mcp__meshpool_office__office_job_status", "mcp__meshpool_tools__current_time",
    "mcp__meshpool_tools__calculate", "mcp__meshpool_office__check_deck",
    "mcp__meshpool_office__check_workbook", "mcp__meshpool_office__measure_office_file",
    "mcp__meshpool_office__check_document", "mcp__meshpool_office__find_icon",
})
