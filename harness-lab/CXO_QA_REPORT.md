# Meshpool desktop app: first-time consumer QA

Date: 12 Nov 2026. Version shown in Settings, About: Meshpool 2.6.0.
Setup: app in browser mode on port 8790, own data folder, mock model on port 8913, test project `/tmp/claude-0/cxo/proj` (git repo with `calc.py` and `README.md`).
I drove everything through clicks and typing with Playwright (Chromium) and looked at the screenshots. Window sizes: 1400x900, 1024x700, 800x700.

## Two notes before the findings

1. **A real paid model call happened.** Meshpool Auto sent my coding request ("fix the bug in calc.py and add a unit test") to **Claude (your subscription)**. The test home folder holds a real Claude sign-in file, so the real Claude model answered. It ran three commands and wrote `test_calc.py`, all inside the test project. I did not choose that harness; Auto did. This is finding B2 below. After that I sent no more coding requests to Auto. The Claude helper process stayed idle and ended when I stopped the app.
2. **Setup mistake (mine, already undone).** A wrongly grouped shell command in my setup ran `git init`/`commit` and overwrote `README.md` in the repo itself. I undid it right away (`git reset --soft HEAD~1` plus restoring README.md). The coordinator also checked it. The repo is back at commit 5730f18 with a clean working tree. This was not an app bug.

## How to read severity

- **Blocker**: can lose user data or spend money without consent; must fix before release.
- **Major**: a common journey is broken or very confusing.
- **Minor**: works, but is confusing or rough.
- **Cosmetic**: looks wrong but does no harm.

---

## Blockers

### B1. "Undo turn" deletes files the agent never touched
- **Journey:** Code mode, Diff panel, Undo.
- **Steps:** In a Meshpool Code thread, send "use the mcp calculator to add 2 and 3". The agent changes no files. Afterwards, outside Meshpool, create `notes.md` and `util.py` in the project folder (as if you saved them in your own editor). Open the Diff panel. Pick "Turn 1: use the mcp calculator..." in the dropdown. It says **"No changes."** but still shows a red **Undo turn** button. Click it and confirm.
- **What I saw:** `notes.md` and `util.py` were deleted from disk. The "All changes in this thread" view had already listed them as this thread's changes. The thread then said "Undid turn 1 and everything after it: **2 files put back**", even though the files were *deleted*. The whole conversation was also wiped.
- **Why:** `app/meshpool_harness/checkpoints.py`, `revert_to_before()`, resets the *whole folder* to the snapshot taken before that turn. It deletes every file created since and reverts every edit, whoever made it: the user, their editor, or another thread in the same folder.
- **Expected:** Undo only reverses changes this agent made in this turn. If the folder changed in other ways, it warns and lists the files it will touch. "No changes" should never come with an active Undo button.
- **Severity:** Blocker (silent data loss).

### B2. Meshpool Auto silently sends work to the user's paid Claude subscription
- **Journey:** Cowork, harness picker, Meshpool Auto, a coding request.
- **Steps:** Choose Meshpool Auto. Send "fix the bug in calc.py and add a unit test".
- **What I saw:** "Meshpool Auto picked Claude (your subscription). Best benchmark score for a code task (bug, fix, test)." The real Claude model then ran three commands and created `test_calc.py`. No approval card appeared, although the mode was "Supervised" (my script never clicked Allow).
- **Expected:** Auto should not spend a personal subscription unless the user agreed to that once, or it should at least ask before the first use. Supervised mode should still ask before commands and file writes, whichever harness runs.
- **Severity:** Blocker (spends a paid plan's usage without consent; skips approvals).

### B3. Hermes runs commands without asking in Supervised mode
- **Journey:** Cowork, Hermes, "hello" and "long task please".
- **Steps:** Default mode "Supervised" (tooltip: "Asks before edits and commands"). Send "hello".
- **What I saw:** Hermes asked "Approve edit: hello.py" but then ran `python3 hello.py || python hello.py` with **no approval**. With "long task please" it ran `sleep 25; echo slept` with no approval. Meshpool Code, Meshpool Work and Pi all asked "Run this command?" in the same mode.
- **Expected:** Supervised asks before every command in every harness. Hermes is the default Cowork harness, so most users hit this.
- **Severity:** Blocker (a safety promise is broken in the default setup).

---

## Major

### M1. No way to add a folder when the system folder picker is missing
- **Journey:** First screen, add project folder.
- **Steps:** Browser mode on Linux without `zenity`. Click "+" next to Projects, or "Choose a folder on this computer".
- **What I saw:** Nothing at all. No dialog, no message. `choose_folder()` returns an empty string and the UI says nothing. There is no text box to type or paste a path. (To keep testing, I put a stub `zenity` on the PATH.)
- **Expected:** A "type or paste a folder path" fallback, or at least a message like "No folder picker found on this computer".
- **Severity:** Major for browser and Linux users (a dead end on the very first step); not seen on Mac or Windows.

### M2. Harness descriptions in the picker are cut off mid-word
- **Journey:** Harness picker (Cowork and Code).
- **What I saw:** "Hermes ... builds its own ski", "Meshpool Work ... needs nothing else installed. Not y", "Meshpool Auto ... You see which on". There is no "..." and no tooltip with the full text. Cause: `app.js` line 571, `.slice(0, 140)`.
- **Also:** The full texts lean on benchmark jargon ("SWE-bench Verified", "MiniWoB++", "Terminal-Bench", "$0.008 and 107 s per solved task"). A consumer cannot tell from this which one to pick. Meshpool Code (the default for Code) says "7 of 9 solved" while Pi says it "Solved the most (8 of 9)", which makes the default look like the weaker choice. "Hermes" and "Hermes (your install)" look like duplicates.
- **Expected:** Short plain-English "best for" lines that are not cut off, with the details on a "more" link.
- **Severity:** Major (this is the main choice a new user makes).

### M3. The Hermes error for a wrong API key is raw and gives no next step
- **Steps:** Settings, Endpoint and key: paste a wrong key, Save, send a message with Hermes.
- **What I saw:** The reply text is just `HTTP 401: {"error":"bad key"}`, as plain chat text with no error styling and no link to Settings. Meshpool Work shows the good version: "The endpoint rejected the API key. Check it in settings."
- **Also:** Save accepts a key right after "Test connection" said it failed. It shows a green "Saved." with no warning.
- **Severity:** Major (Hermes is the default harness).

### M4. Helper processes pile up and never close
- **What I saw:** After about 30 short threads, the app had **89 live child processes**: 16 copies each of the web, demo, browser and assistant MCP servers, 10 office servers, 10 `acp_adapter`, 4 `pi`, and one Claude helper. They are all idle and belong to finished threads. The other test run's app had 118. They do close when the app quits.
- **Expected:** The helpers for a finished or idle thread shut down after a while.
- **Severity:** Major for a desktop app that stays open all day (memory and CPU creep).

### M5. Thread search shows raw internal data and duplicates
- **Steps:** Type "Bonjour" in Search threads.
- **What I saw:** Result snippets like `images": [], "plan": false}, {"type": "assistant", "text": "Bonjour! ...` and `Say hello in French. "}, {"role": "assistant", ...`. Each thread appears twice. Searching "long" showed 9 rows for 8 threads.
- **Expected:** One row per thread, with a readable snippet around the match.
- **Severity:** Major (search is a core feature and looks broken).

### M6. At 1024 px wide with the side panel open, the chat is too narrow to use
- **Steps:** Open a Cowork thread at 1024x700. The Progress panel opens by default.
- **What I saw:** The chat column is about 250 px. Code blocks are cut off ("tput": "hello from meshpool", "), the model pill shows "d...", and the message box wraps its placeholder onto two lines. At 800 px, the panel slides over the chat and covers half the message box. The sidebar never collapses on its own.
- **Expected:** On narrow windows, collapse the sidebar or show the panel as an overlay with a clear close, and keep the chat readable.
- **Severity:** Major at 1024 (a common laptop width); Minor at 800.

---

## Minor

1. **Stale "running" label (Hermes).** After the edit is allowed and the task is done, the step "Edited write: hello.py" still says "running", and Progress shows it with a blue dot, not a check. After a Stop or Interrupt, "execute terminal: sleep 25" also stays "running".
2. **Stopped steps are labeled "failed".** Meshpool Code, Pi and Work: after Stop, the step shows "Ran sleep 25; echo slept" in red "failed" and the summary says "1 step, 1 failed". The user stopped it; nothing failed. Same in Plan mode, where blocked writes show as red "failed" instead of "not allowed in Plan mode".
3. **Mixed messages after Interrupt.** The thread shows "Interrupted. Working on your new message instead." and right under it, in orange, "Stopped. Send a message to continue.", and then it does continue. Pick one message.
4. **Choosing ChatGPT (not signed in) opens Settings at the top.** The "Sign in" button for ChatGPT is several screens down, and nothing says "Sign in to ChatGPT first". The picker quietly stays on the old harness.
5. **The import list is all pre-selected and hidden below the fold.** "Import from Claude, Codex and other apps" found the right items (claude-skill, codex-skill, cursor-server, codex-server, plus a Claude Desktop server), but also 77 Claude Code plugin skills, all ticked. One click on "Import selected" would flood the `$` menu. The list appears below the fold, so after the click the screen hardly changes. After importing, no lasting "Imported 2 skills and 2 servers" message stays on screen.
6. **"Check servers" colors every line red**, including "meshpool-web: connected, 3 tools". GitHub shows "Connected" on the Connectors page, but the check says `github: HTTP 400: bad request: Authorization header is badly formatted`. Broken imported servers say "see its log in runs/mcp-logs" (no full path, no link) and stay switched on.
7. **Progress says "Loaded skill claude-skill" and "Done" when the skill was not found** (before import).
8. **A deleted project folder stays in the sidebar with no warning.** Sending a message shows a short message, "Project folder not found: ...", that covers the footer. There is no "Remove" or "Locate" action.
9. **Advanced settings are developer-speak.** "1 = streamed calls with an idle cutoff", "1 = stable prompt_cache_key per conversation", "minimal = the benchmarked v2 setup. Leave empty for v1.", "Wire format: chat/responses". The help text says "1" while the controls say On/Off. The web search keys appear both here and on the Web search page.
10. **Usage page lists "hermes" as a model** with 0 tokens in every column, so Hermes usage is not counted.
11. **Composer pills lose their labels.** Inside a thread, or at 1024/800 px, the harness and permission pills show only icons. The user cannot see which harness or mode is active without opening the picker.
12. **Approval and progress text show raw tool data.** For example: "Use the MCP tool make_document from 'meshpool-office'?" followed by a JSON blob, or "make document {"markdown": ...}" in Progress. The Claude subscription thread shows "execute Terminal" steps with no command text at all.
13. **Approval cards differ by harness.** Hermes: "Allow edit / Deny". Others: "Allow / Allow all edits in this thread / Deny" plus a note field.
14. **Threads with the same title can't be told apart.** Several "hello" and "long task please" threads have no harness badge.
15. **The file viewer is plain text.** No syntax colors for `.py` and no rendered preview for `.md`. Editing and Ctrl+S save worked.
16. **Git panel shows raw status codes** ("??") instead of words like "New".
17. **One-off (not reproduced):** right after fixing the API key, the first Hermes reply showed only the tail of the text ("​Today is Thursday 12 November 2026, 22:48 local time (UTC"), and the start of the reply was missing. Two fresh Hermes threads afterwards replied in full.

## Cosmetic

1. "New file, 1 lines" (should be "1 line") in the Meshpool Code approval card.
2. Files made shows a folder emoji for `hello.py`.
3. "Change folder" wraps onto its own line with an odd indent on the first screen at 1400 px.
4. First screen: the sidebar says "Add a folder to start" while the hero already says "Hermes works in Meshpool Projects".
5. The top bar chips are cut off when the side panel is open ("pı", "2,000 in · 8(").
6. Imported MCP servers are described as "Imported from another app (cursor-server)" without naming the app.

---

## Mock-model artifacts (not app bugs)

- The Hermes "hello" reply showed `tput": "hello from meshpool", "exit_code": 0, "error": null}`, because the mock prints the last 60 characters of the tool output.
- "The MCP calculator says: (1200*0.15)/12 = 15.0" for "add 2 and 3" is a fixed mock reply.
- In Plan mode the reply says "I created hello.py and ran it" even though both steps were blocked. That is the mock's text; the app blocked them correctly.
- "The skill did not load: ERROR: ValueError..." and the "Spend report" Word document content are mock text.
- After Interrupt, the reply shows the app's wrapper "(I stopped your previous request: ...", because the mock echoes its input.
- Auto routing "long task please" to Meshpool Work in Code mode ("general task") is a reasonable result for a nonsense prompt.

## What worked well

- Clean, calm first screen with useful starter prompts for Cowork and Code.
- "hello" worked end to end in Meshpool Code, Meshpool Work, Pi and Hermes. Diffs in approval cards are clear.
- Stop and Interrupt work in all four harnesses. The command really stops (the `sleep` process was gone in 1.5 to 2 s), and the follow-up message runs.
- Skills: the `$` menu lists skills with descriptions. After import, `$claude-skill greet me` answered "Bonjour" in all four harnesses.
- Import found the Claude and Codex skills and the Cursor, Codex and Claude Desktop MCP servers. "Select none" plus ticking items works.
- The Meshpool Work wrong-key message, the "Test connection" result, and the "Folder not found" message are clear.
- Model switching works: the request went out as `openai/gpt-5.1-mini`.
- Plan mode blocks edits and commands, and offers "Build this plan / Keep planning".
- Meshpool Auto names the harness it picked and gives a one-line reason.
- Rename, pin (a "Pinned" section appears), archive ("Show archived (1)") and delete (with confirmation) all work.
- The Terminal panel runs commands and shows exit codes. Git panel commit works and updates the branch chip. File edit and save work.
- No sideways scrolling at 1024 or 800 px. Settings fit at both sizes.
- Meshpool Work made a Word file and listed it under "Files made".
- No browser console errors except the expected 400 responses.

## Not covered

- "read the pdf" and Hermes "word doc" journeys (time).
- Real Claude and ChatGPT sign-in flows (not allowed; for ChatGPT I only checked what selecting it shows).
- Native desktop window mode (only browser mode was tested).
