# Meshpool

Meshpool is a desktop agent for Mac and Windows. It does research, writing, analysis, spreadsheets, decks and code on your own computer, with any model on the Meshpool endpoint. All you need is a Meshpool API key.

## Download

| Computer | Download |
|---|---|
| Mac with Apple silicon (M1 or later) | [Meshpool.dmg](https://github.com/hamzaaziada/meshpool-harness/releases/latest/download/Meshpool.dmg) |
| Mac with an Intel processor | [Meshpool-Intel.dmg](https://github.com/hamzaaziada/meshpool-harness/releases/latest/download/Meshpool-Intel.dmg) |
| Windows 10 or 11 (64-bit) | [Meshpool-Setup.exe](https://github.com/hamzaaziada/meshpool-harness/releases/latest/download/Meshpool-Setup.exe) |

All versions and checksums are on the [Releases](https://github.com/hamzaaziada/meshpool-harness/releases) page.

### Install

- **Mac:** open the dmg and drag Meshpool to Applications. The app is not notarized yet, so the first time, right-click Meshpool in Applications and choose **Open**.
- **Windows:** run Meshpool-Setup.exe. If SmartScreen appears, choose **More info**, then **Run anyway**.

On first launch, paste your Meshpool API key. Then choose where Meshpool can work, and allow the permissions it asks for.

## Quick start

There are three ways to run Meshpool.

1. **Desktop app:** download it above.
2. **Command line** on macOS or Linux:

   ```sh
   curl -fsSL https://raw.githubusercontent.com/hamzaaziada/meshpool-harness/main/install.sh | sh
   meshpool login
   meshpool
   ```

   On Windows, in PowerShell:

   ```powershell
   irm https://raw.githubusercontent.com/hamzaaziada/meshpool-harness/main/install.ps1 | iex
   ```

   The command line installer needs Python 3.10 or later.

3. **In your browser on localhost:** after installing the command line, run `meshpool ui`. It opens the full app at `http://127.0.0.1:8765`. It runs only on your computer and is not reachable from other machines.

### Use your Claude or ChatGPT subscription

Meshpool can run tasks on your own Claude Pro or Max plan, or on your ChatGPT Plus, Pro or Business plan. It does this through the official Claude Code and Codex agents.

1. Open **Settings**, then **Harnesses**.
2. Choose **Sign in** next to **Claude (your subscription)** or **ChatGPT (your subscription)**.
3. Pick that harness for a thread.

Your Meshpool connectors, skills and MCP servers work with it too. This needs Node.js, which the desktop app includes.

### Bring your skills and MCP servers

On first launch, and later under **Settings**, Meshpool finds the skills and MCP servers you already use, and imports the ones you pick. It looks in:

- Claude Desktop and Claude Code;
- Codex;
- Cursor, Windsurf, VS Code, Cline and Gemini CLI.

## What it does

- **Two modes.**
  - **Cowork** is a chat with a live progress panel and the files the task made.
  - **Code** adds diff, terminal, Git, pull request, CI and deployment panels.
- **Harnesses (the agent behind a thread), switchable per thread:**
  - **Meshpool Auto** picks the best harness for each task, based on benchmark results. See [docs/BENCHMARKS.md](docs/BENCHMARKS.md).
  - **Meshpool Code, Meshpool Work, Pi and Hermes** run on your Meshpool key and model list.
  - **Claude and ChatGPT** run on your own subscription.
- **Web research without a browser.**
  - The agent can search the web and read pages, PDFs and Office files.
  - It can research a question across many sources at once, with links you can check.
- **Connectors:** GitHub, Jira and Confluence, Linear, Notion, Apollo, Instantly, ClickUp, Airtable, Attio, Intercom, Granola, Canva, Zapier, Netlify, Webflow, Sentry, GitLab, Vercel, Supabase and Stripe. You can also add any remote MCP server.
- **Skills**, shared by every agent.
- **Instructions and memory** that carry over between chats.
- **Scheduled tasks**, such as a daily brief or a weekly report.
- **Parallel sub-agents** for big jobs.
- **Files in:** PDF (including scans, with offline OCR in many languages), Word, Excel, PowerPoint, Power BI, CSV, images and more.
- **Files out:** Excel, PowerPoint, Word and PDF.
- **Control while it works:**
  - **Enter** adds to the task.
  - **Interrupt** stops the task and does your new message instead.
  - **Esc** stops.

## Repository layout

| Folder | Contents |
|---|---|
| `app/` | Meshpool's source: the agent, the desktop app UI, the office engines, defaults and tests. |
| `packaging/` | Build scripts, the Mac and Windows launchers, the Windows installer script, icons and the Hermes patch. |
| `testkit/` | A mock model server, end-to-end and UI tests, and the real-model checks. See [JOURNEYS.md](testkit/JOURNEYS.md). |
| `docs/` | [Benchmarks](docs/BENCHMARKS.md), [building](docs/BUILDING.md), [releasing](docs/RELEASING.md), [routing](docs/ROUTING.md); developer notes in `docs/dev/`. |
| `harness-lab/` | Harness experiments: the [log](harness-lab/LOG.md), findings, benchmark drivers and summary results, and working notes. |

To run from source during development:

```sh
cd app
pip install -r requirements.txt
python -m meshpool_harness ui
```

## License and credit

Meshpool is licensed under the [Apache License 2.0](LICENSE). You can use, change and share it.

If you redistribute Meshpool or anything based on it, you must keep the [NOTICE](NOTICE) file and its credit to Meshpool. The Meshpool name and logo are not licensed: a modified version must use its own name and logo.

Meshpool bundles third-party open-source software, each under its own license. See [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
