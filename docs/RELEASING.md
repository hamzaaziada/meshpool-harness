# Releasing an update

1. **Change the code** in `app/`. Then run the unit tests:

   ```sh
   cd app
   python -m pytest -q tests
   ```

2. **Bump the version** in three places:
   - `app/meshpool_harness/__init__.py` (`__version__`)
   - `packaging/mac/Info.plist` (`CFBundleShortVersionString` and `CFBundleVersion`)
   - `packaging/nsis/installer.nsi` (the version strings)

   Everything else reads `__version__`.

3. **Build** the Windows installer and the Mac dmgs (see [BUILDING.md](BUILDING.md)). For a code-only update on a Mac that already has the app trees, run `packaging/mac/repack.sh`.

4. **Test** each build:
   - On a Mac, run `packaging/mac/test.sh`.
   - Install the Windows build once on a real PC.

5. **Publish:**

   ```sh
   shasum -a 256 Meshpool.dmg Meshpool-Intel.dmg Meshpool-Setup.exe > SHA256SUMS.txt
   gh release create vX.Y.Z Meshpool.dmg Meshpool-Intel.dmg Meshpool-Setup.exe SHA256SUMS.txt \
     --title "Meshpool X.Y.Z" --notes-file notes.md
   ```

   Keep the asset names exactly as they are. The download links in the README point to `releases/latest/download/<name>`, so they always serve the newest release.
