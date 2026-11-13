# Open UX issues (independent audit 4, 13 November 2026)

Merged at 7.2/10 at the owner's request; the target is 9/10. Earlier audits: 6.3, 6.6, 7.2. Everything those
audits found was fixed. These are the findings still open, most important first. The audit used the mock model;
screenshots were in the session scratchpad and are not kept.

## P0: history and data
1. **Restart loses messages.** A thread running when the app restarts comes back blank ("New thread", message
   gone), or loses the message that was being worked on with no "interrupted" note. Expected: the message is kept,
   marked Interrupted, and can be resent.
2. **History rewritten after a restart.** Stopped steps reload as "Ran ..." without the "Stopped" notes or the
   approval lines, so the record says the commands finished.
3. **Diff shows another thread's changes.** "All changes in this thread" lists files made later by another thread
   in the same folder (e.g. `Meshpool outputs/spend.docx` under unrelated Code threads). Per-reply Undo is correct.

## P1
4. "Remove from Meshpool" (folder menu) gives no confirmation or toast, and a folder with threads stays in the
   sidebar.
5. Diff panel: picking "Reply 1" shows Undo above "This thread hasn't changed any files yet."; that Undo erases
   the conversation.
6. A decided Word-document approval puts the preview card inside the "Allowed." sentence in the steps box.
7. Folder browser: the path is cut at the end (hides the current folder); no typed path, keyboard or breadcrumb;
   "Use this folder" at "~" silently picks the home folder; height jumps; always starts at ~.
8. Plan mode: a blocked write shows raw JSON and "failed" instead of "Not allowed in Plan mode".
9. Onboarding step 3 at 1280x800: the list is clipped behind the sticky Start button; Select all/none off screen.

## P2
10. Unsent draft text is lost on reload.
11. The message box moves about 17 px when the running hint appears; Stop, Send now and Send at once look
    redundant.
12. The running command is shown twice (step row and status line), with no live output.
13. A write and a run approved in a row still split into two "1 step" boxes.
14. Step notes read awkwardly ("Write hello.py? Allowed."); "Show step details" sits in the thread menu.
15. Icon-only buttons in the top bar and, with a panel open, in the message box; chips hidden at smaller widths.
16. With a panel open on a new thread, the folder can only be changed from the heading link.
17. Diff Refresh button wraps at 1100 px.
18. Two different empty-diff texts.
19. Work mode lists folders with no threads and no hint.
20. Folders have no right-click menu.
21. Top-right toasts cover top-bar buttons.
22. Onboarding step 2: two cards look selected after picking a folder; no Back button; link indented 4 px.
23. "Bring your setup": "Runs on this computer (echo)" and raw server names.
24. Settings: Usage shows raw model ids and misaligned headers; "(your subscription) (made for Code)"; raw tool
    ids on Tools; "Responses API" / "Older, stricter checks" in Advanced; Terminal command page has no action on
    Linux.
25. Agent picker lists Hermes twice (bundled and "your install · not installed").
26. A new thread's top-bar title stays "New thread" during its first task.
27. Delete has no Undo (Archive does).

Mock-model echoes (for example the hidden quick-request note shown back in a reply) are not product issues.
