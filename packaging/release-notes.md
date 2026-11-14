The first public beta of Meshpool: one desktop agent for code, documents, spreadsheets, decks and research.

- **Agents:** Meshpool Auto (picks per task: quality first, then cost and speed), Meshpool Code, Meshpool Work, Pi, Hermes, Codex and OpenCode, or add your own.
- **Runs on** your Meshpool API key, any OpenAI-compatible key, or your ChatGPT plan.
- **Office files** checked before they reach you: Word, Excel with live formulas, PowerPoint, PDF.
- **Decks** in a top strategy firm's style by default: the answer first in an executive summary, an agenda and section tracker, takeaway titles, a different exhibit on every page, Segoe UI and line icons. Add slides to your own deck in its look.

| Computer | Download |
|---|---|
| Mac with Apple silicon | Meshpool.dmg |
| Mac with Intel | Meshpool-Intel.dmg |
| Windows 10 or 11 | Meshpool-Setup.exe |

**Mac** (Meshpool.dmg for Apple silicon, macOS 11 Big Sur or later; Meshpool-Intel.dmg for Intel, macOS 10.15 Catalina or later, where Pi, Codex, OpenCode and browser use need macOS 11 or later): drag Meshpool to Applications and open it. Until the app is notarized, the first time macOS says it can't verify it: open System Settings → Privacy & Security, scroll down and click **Open Anyway** (on macOS 14 and earlier, right-click the app and choose Open). **Windows:** run Meshpool-Setup.exe; until it is signed, SmartScreen may appear: choose More info, then Run anyway.

**Or install from the terminal**, which skips the "downloaded from the internet" prompt browsers add:

```
curl -fsSL https://raw.githubusercontent.com/hamzaaziada/meshpool-harness/main/install.sh | bash -s -- --app
```

```
& ([scriptblock]::Create((irm https://raw.githubusercontent.com/hamzaaziada/meshpool-harness/main/install.ps1))) -App
```

This is a beta: when something breaks, click **Report this** next to the error, or the **Beta** badge.
