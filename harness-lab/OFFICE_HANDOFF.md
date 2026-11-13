# Handoff: Meshpool Work and office output (13 November 2026)

Notes for the agent taking over office work. The coding and app-UX work continues separately on the same branch,
so keep commits small, rebase before you push, and avoid editing `ui/app.js` / `ui/app.css` at the same time.

## Where we left off (start here)

The practical office run was stopped on purpose at 11:03 UTC, 13 November 2026, partway through the contract task.
Done: proposal, sales_xlsx and deck for all four harnesses, and contract for Meshpool Work. Its output folders were
in a session scratch directory and are gone, so rerun what you need. Key spend at the stop: $2.59. The owner's
working budget was $4 total on this key, so about $1.40 is left; check the spend before each run with
`python harness-lab/bench/spend.py`, and ask the owner before going over.

Next steps, in order:
1. Finish the run. This grades the files each task leaves and spends about $0.6:
   `python testkit/practical/practical.py http://127.0.0.1:PORT OUT --only work --case contract,invoices,pdf,letters --budget 3.9`
2. Run `git status`. If anything new appears under `app/`, the engines are still writing into the install folder
   (see "Fixed during this check").
3. Fix open issues 1 and 2 below. Rerun the affected cases.
4. Test the capability gaps listed under "Still to check". Add a practical case for each one you build.
5. Write the result for the owner: can Meshpool Work do what normal office tools do, and how it compares with
   Claude Cowork. Put it in `docs/BENCHMARKS.md` and log it in `harness-lab/LOG.md`.

## What was tested, and how

`testkit/practical/practical.py` runs real tasks through the app's own API on the real model, auto-approving like a
user who clicks Allow, and grades the files left behind. One run at a time so the key spend can be split per run.

```bash
python testkit/practical/practical.py http://127.0.0.1:PORT OUT_DIR --only work [--case a,b] [--harness a,b] --budget 3.9
python testkit/practical/practical.py x OUT_DIR --regrade --case NAME   # grade again, no model calls
```

Start the app on the real model with a fresh `MESHPOOL_HOME` and a fake `HOME`, and strip every credential
variable first (see `testkit/run_all.sh`), or the subscription harnesses sign in with your own account.
Fixtures are in `testkit/practical/fixtures/office` (sales.csv, contract.docx, three invoice PDFs).

## Results so far (real model, DeepSeek V4.1 Flash; the run was stopped here)

| Task | Meshpool Work | Hermes | Pi | Auto |
|---|---|---|---|---|
| Word proposal with headings, bullets, 2+ tables | pass, 42 s | pass, 64 s | pass, 15 s | pass, 115 s |
| Excel from CSV: live formulas, currency, chart, frozen header | pass, 111 s | pass, 109 s | pass, 120 s | pass, 110 s |
| 6 to 8 slide deck with charts | pass, 170 s | pass, 176 s | pass, 115 s | pass, 174 s |
| Edit contract.docx in place, keep formatting | pass, 15 s | not run | not run | not run |
| Invoice PDFs to Excel with total; PDF copy; one letter per customer | not run | not run | not run | not run |

Each run cost $0.004 to $0.047. The decks were rendered and looked at: both Meshpool Work's and Hermes' are
leadership-ready (action titles, real charts, correct numbers). Meshpool Work's was slightly cleaner.

## Fixed during this check (committed)

- Office jobs ran with the app's install folder as their working folder, so a job copied the user's `sales.csv`
  and wrote `deck.json` / `deck.pptx` into it. That folder is read-only inside the Mac app. Fixed in three places:
  `agent/office_mcp_server.py`, `agent/core.py` (`_office`), `ui/server.py` (workbench jobs).
- The slides and sheets engines' model sometimes changed into Meshpool's own source folder to read the build code.
  Both engine prompts now say to stay in the output folder. **Check that no file appears under `app/` after an
  office run** (`git status` in the repo); if one does, a hard guard in the engine workspace is the next step.

## Open issues found, not yet fixed

1. **Work mode opens on Hermes, not Meshpool Work.** `harnesses/__init__.py` makes Hermes the default for cowork.
   Decide on evidence which should be the default.
2. **Meshpool Work's instructions say "for notes, memos and reports write Markdown"** (`agent/core.py`,
   `WORK_SYSTEM`). Users expect Word. In the proposal test it did make a .docx because the prompt named one, but an
   unspecific "write me a report" will likely produce .md. Point it at `make_document` / python-docx instead.
3. A gateway `502 upstream request failed` surfaced as an error event in Pi's run, although the task still passed.
4. The grader for the cart task first expected floats and failed every agent; agents returned exact Decimals,
   which is correct. Check the graders before trusting a 0/4.

## Compared with Claude Cowork (researched 13 Nov 2026; sources in the research notes below)

Cowork, officially: creates and edits .docx, .xlsx (working formulas, formatting, charts), .pptx, PDF, CSV and more;
runs tasks in the cloud since 6 Oct 2026 (local files, browser and computer use need the desktop app); approval
modes Manual / Auto / Skip; connectors and plugins; Claude in Chrome; computer use (beta, Mac and Windows); scheduled
tasks; projects with memory; parallel sub-agents; Pro $20, Max $100, Ultra $200, Team $20/seat. Its public skills
also cover tracked changes and comments in Word, PDF form filling, merging and OCR. Pivot tables and conditional
formatting are not documented. User complaints: usage limits run out fast, stalls on long tasks, Windows VM crashes
(unverified).

What Meshpool Work has today: Word, Excel, PowerPoint and PDF output through `meshpool-office` (make_document,
make_pdf, make_spreadsheet, make_deck, analyze_data, convert_document, read_document); reading PDF, Word, Excel,
PowerPoint, CSV and Power BI; OCR models bundled; python-docx, openpyxl and python-pptx bundled, so the agent can
edit files in place; web search and research; browser use; computer use (Mac and Windows); connectors and MCP;
skills; scheduled tasks; memory and instructions; any model on the Meshpool endpoint. Not available: cloud runs with
the laptop closed, parallel sub-agents.

Still to check, in this order:
- Word tracked changes and comments.
- Excel pivot tables (openpyxl cannot create them) and conditional formatting.
- PDF form filling and merging.
- Editing an existing .xlsx without breaking its formulas.
- Long (30+ minute) jobs.

Sources: support.claude.com articles 13345190 (get started), 12111783 (create and edit files), 14128542 (computer
use), 13364135 (using Cowork); claude.com/blog/cowork-research-preview; github.com/anthropics/skills (docx, xlsx,
pdf skills).

## Update, 13 Nov 2026 (office agent, branch claude/elegant-edison-1oey8d)

- Open issue 1 fixed: Cowork now defaults to Meshpool Work (Hermes no longer marked default).
- Open issue 2 fixed: WORK_SYSTEM tells Work to make .docx with make_document for memos/reports, and to run
  check_deck / check_workbook / check_document (meshpool-office MCP tools) on every deliverable.
- "Needs repair" in desktop Office: `engines/forge/ooxml_check.py` validates every part against the ECMA-376 schemas
  plus relationships and ids (rules P0/X0, tool check_document). Fixed: negative chart axis ids from python-pptx.
  Forge decks, make_document output and recalculated workbooks pass. Not yet confirmed by opening the files in
  desktop PowerPoint/Word/Excel, which this container cannot run.
- Step 1 done: contract, invoices, pdf, letters pass for Meshpool Work (4/4) and Auto (4/4); Pi is not installed in
  the cloud container, Hermes is not vendored there.
- New: forge deck engine v2 is the default behind `--engine slides` (`docs/deck-engine/design-grammar.md`).
- Not done: the capability gaps under "Still to check". Key spend is about $3.1 of the $4 budget.
