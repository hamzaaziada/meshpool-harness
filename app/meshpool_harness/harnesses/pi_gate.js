// Meshpool approval gate for Pi. Every tool call that could change something asks the host first, through the RPC
// extension UI (a confirm dialog). Meshpool answers it from the thread's access mode or by asking the user.
const SAFE = new Set(["read", "grep", "find", "ls", "tool_search", "codemode", "list_mcp_resources",
  "list_mcp_resource_templates", "read_mcp_resource", "mcp__meshpool_office__read_document",
  "mcp__meshpool_office__office_job_status", "mcp__meshpool_tools__current_time", "mcp__meshpool_tools__calculate",
  "mcp__meshpool_web__web_search", "mcp__meshpool_web__web_fetch", "mcp__meshpool_web__web_research",
  "mcp__meshpool_assistant__memory_view", "mcp__meshpool_assistant__memory_save", "mcp__meshpool_assistant__memory_forget",
  "mcp__meshpool_assistant__schedule_task", "mcp__meshpool_assistant__list_scheduled_tasks",
  "mcp__meshpool_assistant__search_chats", "mcp__meshpool_assistant__run_subtasks"]);

const DOCS = /\.(pdf|docx|docm|pptx|pptm|xlsx|xlsm|xls|pbix|pbit|odt|ods|odp|rtf|eml|epub|doc|ppt)$/i;

export default function (pi) {
  pi.on("tool_call", async (event, ctx) => {
    if (event.toolName === "read" && DOCS.test(String(event.input?.path ?? ""))) {
      return {block: true, reason: "This is a binary document. Call mcp__meshpool_office__read_document with this " +
        "path to read it as text (PDF, Word, PowerPoint, Excel, Power BI and more)."};
    }
    if (SAFE.has(event.toolName)) return;
    const detail = JSON.stringify({id: event.toolCallId, name: event.toolName, input: event.input ?? {}});
    const ok = await ctx.ui.confirm("meshpool:" + event.toolName, detail);
    if (!ok) return {block: true, reason: "The user did not allow this. Ask them or try another way."};
  });
}
