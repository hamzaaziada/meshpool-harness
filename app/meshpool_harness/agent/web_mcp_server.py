"""Meshpool web tools as an MCP server, so every harness can search and read the web without a browser.

Tools: web_search, web_fetch, web_research. Built on meshpool_harness.web (keyless DuckDuckGo and Brave, or the
search API key the user set in settings).
"""
from __future__ import annotations

from meshpool_harness import web as W
from meshpool_harness.agent.mcp_stdio import Tool, ToolFailed, serve

RECENCY = {"type": "string", "enum": ["", "day", "week", "month", "year"],
           "description": "Only results from the last day, week, month or year. Empty for any time."}


def web_search(query: str, max_results: int = 8, recency: str = "", site: str = "") -> str:
    try:
        return W.format_results(W.search(query, max_results, recency, "", site))
    except W.WebError as e:
        raise ToolFailed(str(e))


def web_fetch(url: str, start: int = 0, max_chars: int = 15000, links: bool = False) -> str:
    try:
        return W.format_page(W.fetch(url), start, max(2000, min(int(max_chars or 15000), 60000)), links)
    except W.WebError as e:
        raise ToolFailed(str(e))


def web_research(question: str, queries: list | None = None, max_sources: int = 6, recency: str = "",
                 site: str = "") -> str:
    try:
        return W.research(question, queries, max_sources, recency, site)
    except W.WebError as e:
        raise ToolFailed(str(e))


TOOLS = {
    "web_search": Tool(web_search, "Search the web. Returns titles, links and snippets. Use this, not the browser, "
                       "to look things up. Then read pages with web_fetch, or use web_research to do both at once.",
                       {"query": {"type": "string"}, "max_results": {"type": "integer", "description": "1 to 20, default 8"},
                        "recency": RECENCY, "site": {"type": "string", "description": "Limit to one site, e.g. sec.gov"}},
                       ["query"]),
    "web_fetch": Tool(web_fetch, "Read a web page, PDF or Office file from a URL as clean text (title, date, main "
                      "content). Long pages come in parts: call again with start to read on. Use this, not the "
                      "browser, for any public page.",
                      {"url": {"type": "string"}, "start": {"type": "integer", "description": "Character offset to read from"},
                       "max_chars": {"type": "integer", "description": "Default 15000"},
                       "links": {"type": "boolean", "description": "Also list the links on the page"}}, ["url"]),
    "web_research": Tool(web_research, "Research a question in one step: runs several searches in parallel, opens the "
                         "best sources in parallel and returns the passages that answer it, numbered with their links "
                         "so you can cite them. Give 2 to 5 varied queries for broad questions. Call it again with new "
                         "queries to fill gaps, and use web_fetch to read a source in full.",
                         {"question": {"type": "string"},
                          "queries": {"type": "array", "items": {"type": "string"}, "description": "Search queries to run"},
                          "max_sources": {"type": "integer", "description": "Pages to read, 2 to 12, default 6"},
                          "recency": RECENCY, "site": {"type": "string"}}, ["question"]),
}

if __name__ == "__main__":
    try:
        from meshpool_harness import config as C
        C.load_into_env()                        # search API keys from settings
    except Exception:  # noqa: BLE001
        pass
    serve("meshpool-web", TOOLS, instructions="Search and read the web without a browser. Prefer these tools over "
          "browser or computer use for research; use the browser only for pages that need a sign-in or clicks.")
