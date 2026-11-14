<img src="../app/meshpool_harness/ui/brand/mark.png" alt="Meshpool" width="44">

# Building Meshpool

The repository holds Meshpool's own code. The installers also bundle third-party runtimes, which are kept out of git. Put them in a `deps` folder at the repo root, or point `MESHPOOL_DEPS` at another folder.

## What goes in deps

| Folder | What it is | Where it comes from |
|---|---|---|
| `rt-mac/python` | Python 3.12 for Apple silicon, with the packages in `packaging/requirements-cli.txt` (the same list `install.sh` uses: office formats and PDF readers) | [python-build-standalone](https://github.com/astral-sh/python-build-standalone) `aarch64-apple-darwin` install_only, then `pip install` |
| `rt-macx64/python` | The same for Intel Macs | `x86_64-apple-darwin` build |
| `rt-win/python` | The same for Windows | `x86_64-pc-windows-msvc` build |
| `node-mac-arm64`, `node-mac-x64`, `node-win` | Node.js 24 | [nodejs.org/dist](https://nodejs.org/dist/) |
| `pi/node_modules` | The Pi coding agent | `npm install @earendil-works/pi-coding-agent` |
| `vendor-hermes` | Hermes Agent 0.21.2 with `packaging/hermes-acp-server.patch` applied. Hermes runs from source and refuses to build as a package, so install only its dependencies: `python packaging/hermes_requirements.py <hermes folder> > req.txt`, then `pip install -r req.txt` (its `acp` and `mcp` extras are included; without `mcp`, Hermes connects to no MCP servers) | [NousResearch/hermes-agent](https://github.com/NousResearch/hermes-agent), then `patch -p1 < packaging/hermes-acp-server.patch` |
| `ocr-models` | PP-OCRv5 recognition models for RapidOCR | [PaddleOCR](https://github.com/PaddlePaddle/PaddleOCR) model releases |
| `git-win` | MinGit for Windows (Git and Bash) | [git-for-windows](https://github.com/git-for-windows/git/releases) |
| `win/Meshpool` | `Meshpool.exe` | Build `packaging/win/launcher` with Go: `GOOS=windows GOARCH=amd64 go build -ldflags=-H=windowsgui -o Meshpool.exe` (the icon and version come from `rsrc_windows_amd64.syso`) |

## Build

1. Assemble the app trees:

   ```sh
   python packaging/build_all.py
   ```

   This writes `dist-src/app-mac-arm64`, `dist-src/app-mac-x64` and `dist-src/app-win`. It fills the version from `app/meshpool_harness/__init__.py` into `Info.plist` and `installer.nsi`, and stops if `packaging/win/launcher/winres/winres.json` (the version inside `Meshpool.exe`) does not match (see [RELEASING.md](RELEASING.md)).

   In the Windows tree, the terminal command is `bin\meshpool.cmd` (from `packaging/win/meshpool.cmd`). The installer puts only that `bin` folder on PATH: next to `Meshpool.exe`, typing `meshpool` would start the app, because Windows tries `.exe` before `.cmd`.

2. **Windows installer.** Compile the Python code with the bundled runtime, then build the installer with NSIS:

   ```sh
   cd dist-src/app-win
   makensis installer.nsi
   ```

   The result is `Meshpool Setup.exe`. Publish it as `Meshpool-Setup.exe`. `installer.nsi` needs `winsetup.ps1` beside it (`build_all.py` copies it there): the installer and uninstaller use it to close running Meshpool programs and to edit the user's PATH.

3. **Mac apps**, on a Mac:
   1. Compile `packaging/mac/launcher/main.c` into `Contents/MacOS/Meshpool` for each architecture.
   2. Copy the app trees into a work folder as `app-mac-arm64/Meshpool.app` and `app-mac-x64/Meshpool.app`.
   3. Run:

      ```sh
      packaging/mac/repack.sh /path/to/work
      ```

   The script signs both apps ad hoc and writes `Meshpool.dmg` and `Meshpool-Intel.dmg` to `work/out`.

## Test

- **Unit tests:**

  ```sh
  cd app
  python -m pytest -q tests
  ```

- **Mock model:**

  ```sh
  python testkit/mock_llm.py 8911 /tmp/mock.jsonl
  ```

  Set the endpoint to `http://127.0.0.1:8911/v1` and the key to `test-key-1234`.

- **UI tests:** the scripts in `testkit/*.js` drive the app with Playwright on port 8799.

- **A built dmg on a Mac:**

  ```sh
  packaging/mac/test.sh
  ```

  Set `DMG` to the dmg's path.
