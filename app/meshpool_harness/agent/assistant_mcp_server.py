"""Meshpool assistant tools as an MCP server, shared by every harness: memory, scheduled tasks, past-chat search and
parallel sub-agents. The data lives in the Meshpool data folder (see meshpool_harness.assistant)."""
from __future__ import annotations

from meshpool_harness import assistant as A
from meshpool_harness.agent.mcp_stdio import Tool, ToolFailed, serve


def _wrap(fn):
    def call(**kw):
        try:
            return fn(**kw)
        except (ValueError, KeyError) as e:
            raise ToolFailed(str(e))
    return call


def memory_save(fact: str) -> str:
    if not A.settings()["memory_on"]:
        raise ToolFailed("The user turned memory off in Meshpool settings.")
    return A.remember(fact)


def memory_view() -> str:
    return A.memory() or "Memory is empty."


def memory_forget(text: str) -> str:
    return A.forget(text)


def schedule_task(prompt: str, when: str, name: str = "", time: str = "09:00", day: str = "mon", at: str = "",
                  minutes: int = 60, folder: str = "", mode: str = "cowork") -> str:
    import os
    t = A.add_task(name, prompt, when, time, day, at, minutes, folder or os.environ.get("MESHPOOL_PROJECT", ""),
                   "", mode)
    return (f"Scheduled \"{t['name']}\" {A.describe(t)}. It runs while Meshpool is open on this computer; the user "
            "can change or delete it in Settings, Scheduled tasks.")


def list_scheduled_tasks() -> str:
    rows = A.tasks()
    return "\n".join(f"- {t['id']}: {t['name']} ({A.describe(t)})" for t in rows) or "No scheduled tasks."


def delete_scheduled_task(id: str) -> str:
    return "Deleted." if A.delete_task(id) else "No task with that id or name."


def search_chats(query: str) -> str:
    return A.search_chats(query)


def run_subtasks(tasks: list, max_parallel: int = 4) -> str:
    import os
    return A.run_subtasks(tasks, os.environ.get("MESHPOOL_PROJECT", ""), max_parallel)


TOOLS = {
    "memory_save": Tool(_wrap(memory_save), "Save one lasting fact or preference about the user or their work to "
                        "Meshpool memory, shared by all future chats and harnesses. Use it when the user asks you to "
                        "remember something or states something durable (role, company, clients, tools, style). One "
                        "short sentence per call. Never save secrets, passwords or keys.",
                        {"fact": {"type": "string"}}, ["fact"]),
    "memory_view": Tool(memory_view, "Show everything in Meshpool memory."),
    "memory_forget": Tool(_wrap(memory_forget), "Remove memory lines that contain the given text. Use it when the "
                          "user asks you to forget something or a saved fact is wrong.", {"text": {"type": "string"}},
                          ["text"]),
    "schedule_task": Tool(_wrap(schedule_task), "Schedule work for later or on repeat, such as a daily news brief, a "
                          "weekly report or a one-time reminder. Each run starts a new chat with the prompt. "
                          "when: once (give `at` as a local date and time like 2026-11-20T09:00), hourly, daily, "
                          "weekdays, weekly (give `day`), or every (give `minutes`, at least 15). `time` is local "
                          "HH:MM. Write the prompt so it makes sense on its own later.",
                          {"prompt": {"type": "string"}, "when": {"type": "string", "enum": list(A.WHEN)},
                           "name": {"type": "string"}, "time": {"type": "string"}, "day": {"type": "string"},
                           "at": {"type": "string"}, "minutes": {"type": "integer"},
                           "mode": {"type": "string", "enum": ["cowork", "code"]}}, ["prompt", "when"]),
    "list_scheduled_tasks": Tool(list_scheduled_tasks, "List the scheduled tasks."),
    "delete_scheduled_task": Tool(_wrap(delete_scheduled_task), "Delete a scheduled task by id or name.",
                                  {"id": {"type": "string"}}, ["id"]),
    "search_chats": Tool(_wrap(search_chats), "Search the user's earlier Meshpool chats for a topic, and get matching "
                         "chats with a snippet. Use it when the user refers to something from a past chat.",
                         {"query": {"type": "string"}}, ["query"]),
    "run_subtasks": Tool(_wrap(run_subtasks), "Run independent parts of a big job in parallel with sub-agents. Each "
                         "part gets its own agent that can search and read the web and read files (it cannot change "
                         "anything) and returns a sourced report. Use it to research many companies or people, "
                         "compare many options, or review many documents at once. Give each part a complete, "
                         "standalone prompt. Up to 12 parts.",
                         {"tasks": {"type": "array", "items": {"type": "object", "properties": {
                             "title": {"type": "string"}, "prompt": {"type": "string"}}, "required": ["prompt"]}},
                          "max_parallel": {"type": "integer", "description": "1 to 6, default 4"}}, ["tasks"]),
}

if __name__ == "__main__":
    serve("meshpool-assistant", TOOLS)
