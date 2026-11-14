// Meshpool approval gate for Pi. Every tool call that could change something asks the host first, through the RPC
// extension UI (a confirm dialog). Meshpool answers it from the thread's access mode or by asking the user.
// The tools that never ask come from Meshpool (pi_rpc.py READ_ONLY), so this file keeps no copy of the list. Without
// it every tool asks, and Meshpool still lets the read-only ones through.
import path from "node:path";

// Reading tools ask too when they reach outside the project (an SSH key, another app's secrets).
const ROOT = process.env.MESHPOOL_PI_ROOT || "";
const FILE_TOOLS = new Set(["read", "grep", "find", "ls"]);
function outside(input) {
  if (!ROOT) return false;
  const raw = String(input?.path ?? input?.directory ?? ".").replace(/^~(?=$|[\\/])/, process.env.HOME || process.env.USERPROFILE || "~");
  const rel = path.relative(ROOT, path.resolve(ROOT, raw));
  return rel.startsWith("..") || path.isAbsolute(rel);
}

let SAFE = new Set();
try {
  SAFE = new Set(JSON.parse(process.env.MESHPOOL_PI_SAFE || "[]"));
} catch {
  SAFE = new Set();
}

const DOCS = /\.(pdf|docx|docm|pptx|pptm|xlsx|xlsm|xls|pbix|pbit|odt|ods|odp|rtf|eml|epub|doc|ppt)$/i;

export default function (pi) {
  pi.on("tool_call", async (event, ctx) => {
    if (event.toolName === "read" && DOCS.test(String(event.input?.path ?? ""))) {
      return {block: true, reason: "This is a binary document. Call mcp__meshpool_office__read_document with this " +
        "path to read it as text (PDF, Word, PowerPoint, Excel, Power BI and more)."};
    }
    if (SAFE.has(event.toolName) && !(FILE_TOOLS.has(event.toolName) && outside(event.input))) return;
    const detail = JSON.stringify({id: event.toolCallId, name: event.toolName, input: event.input ?? {}});
    const ok = await ctx.ui.confirm("meshpool:" + event.toolName, detail);
    if (!ok) return {block: true, reason: "The user did not allow this. Ask them or try another way."};
  });
}
