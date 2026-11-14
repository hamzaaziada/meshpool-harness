# Trusted Install prompt

Give this whole file to an agent working in `hamzaaziada/meshpool-harness`. Part 1 needs the owner; the rest the
agent does.

---

**Goal:** Meshpool opens like any normal app on first launch: no "can't verify" message, no trip to System Settings →
Privacy & Security on Mac, and no "Windows protected your PC" (SmartScreen) screen on Windows. The way to get there is
to sign the apps with certificates Apple and Microsoft trust, and to have Apple notarize the Mac builds. Nothing
inside the app can skip these checks, and no workaround should try to.

**Rules:** Read `HANDOFF.md` first and follow it. Work on `claude/release-build`. Commit as
`Meshpool <hello@meshpool.ai>`, with no Claude co-author lines. Never delete branches. Never print, log or commit a
secret. Builds run in `.github/workflows/release.yml`, and every push to `claude/release-build` republishes v0.0.1.

## Part 1: the owner sets up two accounts (once)

1. **Apple Developer Program** (USD 99 a year): https://developer.apple.com/programs/enroll/
   - Enrolling as an individual is usually approved within 1–2 days. An organization needs a D-U-N-S number, which can
     take one to two weeks.
   - In Xcode (Settings → Accounts → Manage Certificates), or at developer.apple.com → Certificates, create a
     **Developer ID Application** certificate. Export it as a `.p12` file with a password.
   - At appleid.apple.com, create an **app-specific password** for notarization.
   - Note the **Team ID**, from developer.apple.com → Membership.
2. **Windows: Azure Artifact Signing** (formerly Trusted Signing; about USD 10 a month):
   https://learn.microsoft.com/azure/trusted-signing/
   - Create an Azure account and a Trusted Signing account. Complete identity validation (individuals are accepted
     in some countries; organizations are accepted more widely). Create a **Public Trust** certificate profile.
   - Create an app registration (service principal) with the role "Trusted Signing Certificate Profile Signer".
     Note its tenant ID, client ID and client secret.
   - This is the cheapest option that SmartScreen trusts from the first download. An ordinary OV certificate still
     shows the warning until enough downloads build up reputation. An EV certificate also works, but needs a
     hardware token or a cloud HSM.
3. Add these as GitHub secrets, at Settings → Secrets and variables → Actions → New repository secret:

| Secret | Value |
|---|---|
| `MAC_CERT_P12_BASE64` | `base64 -i DeveloperID.p12` output |
| `MAC_CERT_PASSWORD` | the `.p12` password |
| `MAC_SIGN_IDENTITY` | e.g. `Developer ID Application: Your Name (TEAMID)` |
| `APPLE_ID` | the Apple ID email |
| `APPLE_APP_PASSWORD` | the app-specific password |
| `APPLE_TEAM_ID` | the Team ID |
| `AZURE_TENANT_ID`, `AZURE_CLIENT_ID`, `AZURE_CLIENT_SECRET` | from the app registration |
| `AZURE_SIGNING_ENDPOINT` | e.g. `https://eus.codesigning.azure.net/` (the region of the signing account) |
| `AZURE_SIGNING_ACCOUNT`, `AZURE_CERT_PROFILE` | the names chosen in Azure |

## Part 2: the agent adds signing to the release build

Every signing step runs only when its secrets exist. Without them, the build keeps producing today's ad hoc-signed
apps, so nothing breaks before the accounts are ready.

### Mac (job `mac`, in `packaging/mac/repack.sh` or a new `packaging/mac/sign.sh`)

1. Import the certificate into a temporary keychain:
   `security create-keychain`, then `security import ... -T /usr/bin/codesign`, then
   `security set-key-partition-list`.
2. Sign from the inside out, not with `--deep`:
   - First every Mach-O file inside the app: `.so`, `.dylib`, executables under `Resources/python`,
     `Resources/node`, `Resources/app/vendor`, Playwright's driver, and so on. Find them by the Mach-O magic number,
     as `packaging/mac/check_minos.py` does.
   - Then `Contents/MacOS/Meshpool`, then the `.app` itself.
   - Use `codesign --force --timestamp --options runtime --entitlements packaging/mac/entitlements.plist --sign "$MAC_SIGN_IDENTITY"`.
3. Create `packaging/mac/entitlements.plist` with what Python and Node need under the hardened runtime:
   - `com.apple.security.cs.allow-jit`
   - `com.apple.security.cs.allow-unsigned-executable-memory`
   - `com.apple.security.cs.disable-library-validation` (needed for pip wheels and Node add-ons)
   - `com.apple.security.automation.apple-events` (computer use)
4. Notarize and staple:
   - Build the dmg.
   - `xcrun notarytool submit Meshpool.dmg --apple-id "$APPLE_ID" --password "$APPLE_APP_PASSWORD" --team-id "$APPLE_TEAM_ID" --wait`
   - `xcrun stapler staple` on the dmg, then `xcrun stapler validate`.
   - Do this for both `Meshpool.dmg` and `Meshpool-Intel.dmg`.
   - If notarization fails, fetch `notarytool log <id>`, fix every file it lists, and do not publish.
5. Check: `spctl -a -vvv -t install Meshpool.dmg` and `spctl -a -vvv Meshpool.app` both say
   "accepted, source=Notarized Developer ID". Fail the job if not.
6. Keep `check_minos.py` passing (macOS 11 minimum).

### Windows (job `windows`)

1. Use `azure/trusted-signing-action` with the secrets above.
2. Sign `Meshpool.exe` (the Go launcher) and `meshpool.cmd`'s target before `makensis`.
3. Sign the finished `Meshpool-Setup.exe` with a timestamp. If the installer writes an uninstaller, sign that too:
   use NSIS `!uninstfinalize`, or build the uninstaller first and sign it.
4. Check: `Get-AuthenticodeSignature out/Meshpool-Setup.exe` returns `Valid`, and `signtool verify /pa` passes.
   Fail the job if not.

### After it works

- Remove the "Open Anyway" and "More info → Run anyway" steps from `packaging/release-notes.md` and `README.md`.
- Push to rebuild v0.0.1.
- Download both dmgs and the exe from the release page in a browser (so they carry the quarantine flag or
  Mark-of-the-Web) and confirm each opens with no warning beyond the normal "downloaded from the internet. Open?"
  on Mac.

## Until the accounts are ready (launch-day stopgap)

Browsers mark downloads as "from the internet", and that mark triggers the warning. Files fetched by `curl` on Mac
or PowerShell on Windows don't get the mark, so a one-line install command from the terminal opens normally. This
is the same way Homebrew and rustup install. Add it next to the download buttons, as a second option:

- **Mac:** extend `install.sh` with an app mode. It picks `Meshpool.dmg` or `Meshpool-Intel.dmg` by
  `uname -m`, downloads it with `curl`, checks it against `SHA256SUMS.txt`, attaches it with `hdiutil attach -nobrowse`,
  copies `Meshpool.app` to `/Applications` (or `~/Applications` without admin rights), detaches, and opens it.
  The one-liner: `curl -fsSL https://raw.githubusercontent.com/hamzaaziada/meshpool-harness/main/install.sh | bash -s -- --app`
- **Windows:** extend `install.ps1` the same way. It downloads `Meshpool-Setup.exe` with `Invoke-WebRequest`, checks
  the SHA-256, and runs it with `/S` (silent, per user, no admin rights).
  The one-liner: `irm https://raw.githubusercontent.com/hamzaaziada/meshpool-harness/main/install.ps1 | iex` with an
  `-App` switch, e.g. `& ([scriptblock]::Create((irm .../install.ps1))) -App`.
- Both must stop with a clear message if the checksum doesn't match. Test both on a real Mac and a real Windows PC
  before telling users.

**Done means:** a fresh download from the release page, opened by double-click on a Mac with the latest macOS and on
Windows 11, shows no security block. The release notes no longer mention workarounds. `HANDOFF.md` says signing is
live.
