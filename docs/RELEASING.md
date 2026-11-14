<img src="../app/meshpool_harness/ui/brand/mark.png" alt="Meshpool" width="44">

# Releasing an update

1. **Change the code** in `app/`. Then run the unit tests:

   ```sh
   cd app
   python -m pytest -q tests
   ```

2. **Bump the version** in one place: `__version__` in `app/meshpool_harness/__init__.py`.

   Everything else reads it. `packaging/build_all.py` and `packaging/mac/repack.sh` fill it into `Info.plist` and `installer.nsi`, which hold `@VERSION@` placeholders in the repo; versions starting with `0.` are labelled "beta" in the installer.

   `Meshpool.exe` carries the version too, compiled in from `packaging/win/launcher/winres/winres.json`. `build_all.py` stops if that file disagrees. To bring it in line:

   ```sh
   python packaging/build_all.py --winres          # rewrites winres.json from __version__
   go install github.com/tc-hib/go-winres@latest   # once
   cd packaging/win/launcher && go-winres make --arch amd64   # regenerates rsrc_windows_amd64.syso
   GOOS=windows GOARCH=amd64 go build -ldflags=-H=windowsgui -o Meshpool.exe   # into deps/win/Meshpool
   ```

   Commit `winres.json` and `rsrc_windows_amd64.syso` together.

3. **Build** the Windows installer and the Mac dmgs (see [BUILDING.md](BUILDING.md)). For a code-only update on a Mac that already has the app trees, run `packaging/mac/repack.sh`.

4. **Test** each build:
   - On a Mac, run `packaging/mac/test.sh`.
   - Install the Windows build once on a real PC.

5. **Publish** from a computer where `gh` is signed in:

   ```sh
   packaging/release.sh X.Y.Z notes.md Meshpool.dmg Meshpool-Intel.dmg Meshpool-Setup.exe
   ```

   The script:

   - writes SHA256SUMS.txt (the in-app update checks the downloaded installer against it);
   - creates the release as a draft;
   - uploads each file with retries, so a slow or shaky connection is fine (rerun the script to resume; a rerun skips files that are already up, re-uploads any that were rebuilt, and always re-uploads SHA256SUMS.txt);
   - publishes the release once every file is on GitHub.

   Keep the asset names exactly as they are. The download links in the README point to `releases/latest/download/<name>`, so they always serve the newest release.
