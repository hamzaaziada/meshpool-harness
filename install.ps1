# Meshpool for the terminal, on Windows. One command installs it and starts setup (PowerShell):
#
#   irm https://raw.githubusercontent.com/hamzaaziada/meshpool-harness/main/install.ps1 | iex
#
# With -App it installs the Meshpool app itself instead, the same one the download button gives you:
#
#   & ([scriptblock]::Create((irm https://raw.githubusercontent.com/hamzaaziada/meshpool-harness/main/install.ps1))) -App
#
# It installs into %USERPROFILE%\.meshpool-cli and adds the `meshpool` command to your PATH, in this window too. It uses
# your Python 3.10 or later if you have one; otherwise it downloads a private copy of Python into .meshpool-cli.
# Settings: MESHPOOL_REF (branch or tag), MESHPOOL_DIR, MESHPOOL_NO_SETUP=1 (do not start setup).
#
# Everything runs inside one script block: under `irm | iex` this script runs in the user's own PowerShell session,
# so its settings ($ErrorActionPreference and the rest) stay local, and a failure prints a message and returns
# instead of closing the window.
& {
$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
$Repo = "hamzaaziada/meshpool-harness"
$Ref = if ($env:MESHPOOL_REF) { $env:MESHPOOL_REF } else { "main" }
$Dir = if ($env:MESHPOOL_DIR) { $env:MESHPOOL_DIR } else { Join-Path $env:USERPROFILE ".meshpool-cli" }
$PyUrl = "https://github.com/astral-sh/python-build-standalone/releases/download/20241016/cpython-3.12.7+20241016-x86_64-pc-windows-msvc-install_only.tar.gz"
$PySum = "f05531bff16fa77b53be0776587b97b466070e768e6d5920894de988bdcd547a"

$E = [char]27
$Blue = "$E[1;38;2;22;112;232m"; $Green = "$E[38;2;46;160;67m"; $Red = "$E[38;2;214;69;65m"
$Dim = "$E[2m"; $Off = "$E[0m"
function Say($t) { Write-Host $t }
function Ok($t) { Write-Host "  $Green✓$Off $t" }
function Step($t) { Write-Host "  $Blue›$Off $t" }
# Stops the install: the message is shown once, below, and the window stays open.
function Die($t) { $x = New-Object System.Exception $t; $x.Data["meshpool"] = $true; throw $x }

# Runs a program and returns its exit code. Windows PowerShell 5.1 turns anything a program writes to stderr (a pip
# warning, say) into an error, which "Stop" would make fatal, so programs run with "Continue" and are judged by their
# exit code alone. -Quiet hides their output.
function Run {
  param([string]$Exe, [string[]]$A = @(), [switch]$Quiet)
  $eap = $ErrorActionPreference
  $ErrorActionPreference = "Continue"
  try {
    $global:LASTEXITCODE = 1
    if ($Quiet) { & $Exe @A 2>$null | Out-Null } else { & $Exe @A | Out-Host }
    return $LASTEXITCODE
  } catch {
    return 1
  } finally {
    $ErrorActionPreference = $eap
  }
}

# ---------- the app ----------------------------------------------------------------------------------------------
# Installs the Meshpool app itself, the same one the download button gives you. It is fetched with Invoke-WebRequest,
# which does not set the Mark-of-the-Web that makes Windows show "Windows protected your PC", so the installer runs
# normally. This is the stopgap until the app is signed with a certificate SmartScreen trusts (then the download
# button works on its own and this is just a second way in).
function Install-App {
  $Base = "https://github.com/$Repo/releases/latest/download"
  $Name = "Meshpool-Setup.exe"

  Say ""
  Say "  ${Dim}Installing the Meshpool app. This takes a minute.$Off"
  Say ""

  Step "Downloading $Name..."
  $Exe = Join-Path $Tmp $Name
  try {
    Invoke-WebRequest -UseBasicParsing -Uri "$Base/$Name" -OutFile $Exe
  } catch {
    Die "Could not download $Name. Check your internet connection."
  }
  Ok $Name

  # The checksum is published next to the download. A mismatch means the file is not the one Meshpool built, so stop.
  Step "Checking the download..."
  $Sums = Join-Path $Tmp "SHA256SUMS.txt"
  try {
    Invoke-WebRequest -UseBasicParsing -Uri "$Base/SHA256SUMS.txt" -OutFile $Sums
  } catch {
    Die "Could not download SHA256SUMS.txt, so the download cannot be checked. Nothing was installed."
  }
  $Want = (Get-Content $Sums | Where-Object { $_ -match "\s\*?$([regex]::Escape($Name))$" } |
    ForEach-Object { ($_ -split '\s+')[0] } | Select-Object -First 1)
  if (-not $Want) { Die "$Name is not listed in SHA256SUMS.txt. Nothing was installed." }
  $Got = (Get-FileHash -Algorithm SHA256 -Path $Exe).Hash.ToLower()
  if ($Want.ToLower() -ne $Got) {
    Die "The download does not match its checksum, so it was not installed.`n     expected $Want`n     got      $Got`n     Try again, and if it keeps happening tell us at https://github.com/$Repo/issues"
  }
  Ok "Checksum matches"

  # /S is silent, and the installer is per user, so no admin rights are needed.
  Step "Installing..."
  $Code = Run -Exe $Exe -A @("/S")
  if ($Code -ne 0) { Die "The installer stopped with code $Code. Nothing was installed." }
  Ok "Meshpool"

  # Invoke-WebRequest does not set the Mark-of-the-Web, but be sure: without it Windows has nothing to warn about.
  $ExeDir = Join-Path $env:LOCALAPPDATA "Programs\Meshpool"
  if (Test-Path $ExeDir) {
    Get-ChildItem -Path $ExeDir -Recurse -File -ErrorAction SilentlyContinue |
      Unblock-File -ErrorAction SilentlyContinue
  }

  Say ""
  Say "  ${Blue}Meshpool is installed.$Off Opening it now."
  Say "  ${Dim}You can also open it from the Start menu.$Off"
  Say ""
  $App = Join-Path $ExeDir "Meshpool.exe"
  if (Test-Path $App) { Start-Process $App } else { Start-Process "shell:AppsFolder" }
}

$Tmp = Join-Path $env:TEMP ("meshpool-" + [guid]::NewGuid())
try {
Say ""
Say "     $E[38;2;30;132;250m▄$E[0m$E[38;2;43;149;251m$E[48;2;6;120;255m▀$E[0m$E[38;2;25;135;253m$E[48;2;0;99;254m▀$E[0m$E[38;2;17;128;252m$E[48;2;0;91;252m▀$E[0m$E[38;2;24;130;255m$E[48;2;2;91;255m▀$E[0m$E[38;2;10;94;255m▄$E[0m   $E[38;2;112;200;252m▄$E[0m$E[38;2;141;222;252m$E[48;2;119;216;255m▀$E[0m$E[38;2;170;231;253m$E[48;2;126;214;255m▀$E[0m$E[38;2;202;245;255m$E[48;2;165;236;255m▀$E[0m$E[38;2;205;244;255m▄$E[0m"
Say "    $E[38;2;14;112;251m▄$E[0m$E[38;2;6;116;255m$E[48;2;1;89;253m▀$E[0m$E[38;2;0;87;250m$E[48;2;3;76;246m▀$E[0m$E[38;2;4;84;249m$E[48;2;3;70;239m▀$E[0m$E[38;2;4;76;245m$E[48;2;3;64;227m▀$E[0m$E[38;2;3;67;235m$E[48;2;3;57;215m▀$E[0m$E[38;2;1;66;240m$E[48;2;4;53;201m▀$E[0m$E[38;2;13;74;228m$E[48;2;0;45;197m▀$E[0m$E[38;2;61;122;224m▄$E[0m$E[38;2;96;184;251m$E[48;2;77;172;255m▀$E[0m$E[38;2;95;197;255m$E[48;2;71;167;251m▀$E[0m$E[38;2;94;187;250m$E[48;2;86;180;251m▀$E[0m$E[38;2;109;198;251m$E[48;2;100;190;251m▀$E[0m$E[38;2;131;210;250m$E[48;2;119;203;252m▀$E[0m$E[38;2;179;238;255m$E[48;2;144;215;251m▀$E[0m$E[38;2;215;245;253m$E[48;2;201;247;255m▀$E[0m$E[38;2;216;240;252m▄$E[0m"
Say "   $E[38;2;16;105;250m▄$E[0m$E[38;2;6;98;255m$E[48;2;2;80;255m▀$E[0m$E[38;2;2;71;242m$E[48;2;3;63;227m▀$E[0m$E[38;2;2;65;230m$E[48;2;3;58;211m▀$E[0m$E[38;2;2;59;216m$E[48;2;3;51;194m▀$E[0m$E[38;2;4;54;204m$E[48;2;0;35;175m▀$E[0m$E[38;2;4;48;192m$E[48;2;0;34;165m▀$E[0m$E[38;2;3;43;180m$E[48;2;0;32;156m▀$E[0m$E[38;2;5;46;172m$E[48;2;19;84;203m▀$E[0m$E[38;2;31;121;248m$E[48;2;3;91;247m▀$E[0m$E[38;2;55;152;254m$E[48;2;26;114;246m▀$E[0m$E[38;2;64;158;250m$E[48;2;58;154;253m▀$E[0m$E[38;2;74;168;251m$E[48;2;61;157;251m▀$E[0m$E[38;2;87;179;251m$E[48;2;73;168;251m▀$E[0m$E[38;2;105;192;251m$E[48;2;88;179;251m▀$E[0m$E[38;2;126;205;251m$E[48;2;109;195;252m▀$E[0m$E[38;2;163;221;252m$E[48;2;139;210;251m▀$E[0m$E[38;2;214;250;255m$E[48;2;190;236;253m▀$E[0m$E[38;2;214;241;251m▄$E[0m     ${Blue}meshpool$Off"
Say "   $E[38;2;8;91;251m$E[48;2;4;71;231m▀$E[0m$E[38;2;1;61;223m$E[48;2;1;40;171m▀$E[0m$E[38;2;3;49;186m$E[48;2;3;38;158m▀$E[0m$E[38;2;15;56;172m$E[48;2;2;35;148m▀$E[0m$E[38;2;35;85;189m$E[48;2;2;33;147m▀$E[0m$E[38;2;53;114;216m$E[48;2;26;70;174m▀$E[0m$E[38;2;23;75;194m$E[48;2;70;148;248m▀$E[0m$E[38;2;12;62;178m$E[48;2;55;141;255m▀$E[0m$E[38;2;13;91;231m$E[48;2;30;104;237m▀$E[0m$E[38;2;0;63;222m$E[48;2;8;68;212m▀$E[0m$E[38;2;6;75;224m$E[48;2;0;50;198m▀$E[0m$E[38;2;48;141;251m$E[48;2;30;106;228m▀$E[0m$E[38;2;57;153;252m$E[48;2;58;157;255m▀$E[0m$E[38;2;60;157;251m$E[48;2;74;168;251m▀$E[0m$E[38;2;79;173;251m$E[48;2;94;186;252m▀$E[0m$E[38;2;88;179;251m$E[48;2;80;173;251m▀$E[0m$E[38;2;85;176;251m$E[48;2;75;170;251m▀$E[0m$E[38;2;103;187;250m$E[48;2;72;169;250m▀$E[0m$E[38;2;187;239;255m$E[48;2;86;184;252m▀$E[0m$E[38;2;223;244;250m$E[48;2;155;218;252m▀$E[0m    ${Dim}for the terminal · meshpool.ai$Off"
Say "  $E[38;2;16;86;234m$E[48;2;11;81;242m▀$E[0m$E[38;2;2;56;212m$E[48;2;2;67;234m▀$E[0m$E[38;2;2;46;185m$E[48;2;3;57;208m▀$E[0m$E[38;2;3;43;172m$E[48;2;3;48;187m▀$E[0m$E[38;2;3;38;162m$E[48;2;2;44;176m▀$E[0m$E[38;2;4;39;160m$E[48;2;0;43;175m▀$E[0m$E[38;2;0;33;164m$E[48;2;10;70;205m▀$E[0m$E[38;2;30;87;196m▀$E[0m$E[38;2;91;165;255m▀$E[0m$E[38;2;58;130;246m$E[48;2;91;165;255m▀$E[0m$E[38;2;38;105;225m$E[48;2;57;125;242m▀$E[0m$E[38;2;13;71;202m$E[48;2;40;105;223m▀$E[0m$E[38;2;3;60;196m$E[48;2;8;76;214m▀$E[0m$E[38;2;73;163;247m$E[48;2;62;127;212m▀$E[0m$E[38;2;112;200;253m$E[48;2;129;213;255m▀$E[0m$E[38;2;94;185;251m$E[48;2;94;184;251m▀$E[0m$E[38;2;82;176;251m$E[48;2;85;179;252m▀$E[0m$E[38;2;76;171;252m$E[48;2;79;173;252m▀$E[0m$E[38;2;78;173;252m$E[48;2;80;175;251m▀$E[0m$E[38;2;83;179;251m$E[48;2;87;182;250m▀$E[0m$E[38;2;112;208;255m$E[48;2;109;202;255m▀$E[0m$E[38;2;156;225;253m$E[48;2;134;217;251m▀$E[0m"
Say "  $E[38;2;7;101;251m$E[48;2;25;129;250m▀$E[0m$E[38;2;0;82;250m$E[48;2;13;114;255m▀$E[0m$E[38;2;2;70;230m$E[48;2;11;86;245m▀$E[0m$E[38;2;2;60;208m$E[48;2;8;77;230m▀$E[0m$E[38;2;0;52;192m$E[48;2;17;91;244m▀$E[0m$E[38;2;7;70;216m$E[48;2;41;106;230m▀$E[0m$E[38;2;33;96;216m▀$E[0m   $E[38;2;88;158;253m▀$E[0m$E[38;2;66;132;242m▀$E[0m$E[38;2;51;116;227m▀$E[0m$E[38;2;7;66;188m$E[48;2;19;85;211m▀$E[0m$E[38;2;100;178;239m$E[48;2;38;112;225m▀$E[0m$E[38;2;104;195;253m$E[48;2;108;200;255m▀$E[0m$E[38;2;87;179;251m$E[48;2;92;185;251m▀$E[0m$E[38;2;83;177;252m$E[48;2;87;180;251m▀$E[0m$E[38;2;84;178;252m$E[48;2;88;182;251m▀$E[0m$E[38;2;91;186;252m$E[48;2;95;187;250m▀$E[0m$E[38;2;111;200;252m$E[48;2;119;213;255m▀$E[0m$E[38;2;137;220;251m$E[48;2;136;220;252m▀$E[0m"
Say "   $E[38;2;52;143;252m▀$E[0m$E[38;2;46;127;255m▀$E[0m$E[38;2;45;119;251m▀$E[0m$E[38;2;60;131;250m▀$E[0m          $E[38;2;58;140;238m▀$E[0m$E[38;2;105;201;255m$E[48;2;77;157;247m▀$E[0m$E[38;2;96;190;254m$E[48;2;104;193;251m▀$E[0m$E[38;2;93;187;252m$E[48;2;106;200;251m▀$E[0m$E[38;2;104;203;255m$E[48;2;112;209;253m▀$E[0m$E[38;2;118;211;253m▀$E[0m"
Say ""
Say "  ${Dim}Installing Meshpool for the terminal. This takes a minute the first time.$Off"
Say ""

# -App installs the Meshpool app itself, not the terminal version.
if ($args -contains "-App" -or $args -contains "--app" -or $args -contains "-a") {
  New-Item -ItemType Directory -Path $Tmp | Out-Null
  Install-App
  return
}
if ($args -contains "-Help" -or $args -contains "--help" -or $args -contains "-h") {
  Say "Meshpool installer"
  Say "  (no arguments)  install Meshpool for the terminal"
  Say "  -App            install the Meshpool app"
  return
}

New-Item -ItemType Directory -Force -Path $Dir | Out-Null
New-Item -ItemType Directory -Path $Tmp | Out-Null

# ---------- Python ----------
$PyExe = $null; $PyArgs = @()
foreach ($c in @("py -3.13", "py -3.12", "py -3.11", "py -3.10", "python")) {
  $parts = $c.Split(" ")
  $rest = @($parts | Select-Object -Skip 1)
  $code = Run $parts[0] ($rest + @("-c", "import sys, venv; sys.exit(0 if sys.version_info >= (3, 10) else 1)")) -Quiet
  if ($code -eq 0) { $PyExe = $parts[0]; $PyArgs = $rest; break }
}
if ($PyExe) {
  Ok ("Python " + (& $PyExe @PyArgs -c "import platform; print(platform.python_version())"))
} elseif (Test-Path "$Dir\python\python.exe") {
  $PyExe = "$Dir\python\python.exe"; Ok "Python (Meshpool's own copy)"
} else {
  Step "Downloading Python (this computer does not have 3.10 or later)..."
  try { Invoke-WebRequest -UseBasicParsing $PyUrl -OutFile "$Tmp\python.tgz" } catch { Die "Could not download Python. Check your internet connection." }
  if ((Get-FileHash "$Tmp\python.tgz" -Algorithm SHA256).Hash.ToLower() -ne $PySum) { Die "The Python download did not match its checksum. Run this again." }
  if ((Run "tar" @("-xzf", "$Tmp\python.tgz", "-C", $Tmp)) -ne 0) { Die "Could not unpack Python. Run this again." }
  if (Test-Path "$Dir\python") { Remove-Item -Recurse -Force "$Dir\python" }
  Move-Item "$Tmp\python" "$Dir\python"
  $PyExe = "$Dir\python\python.exe"; Ok "Python (Meshpool's own copy)"
}

# ---------- Meshpool ----------
Step "Downloading Meshpool ($Ref)..."
try { Invoke-WebRequest -UseBasicParsing "https://codeload.github.com/$Repo/zip/$Ref" -OutFile "$Tmp\src.zip" } catch { Die "Could not download Meshpool. Check your internet connection." }
Expand-Archive "$Tmp\src.zip" -DestinationPath $Tmp
$Src = Get-ChildItem $Tmp -Directory | Where-Object Name -like "meshpool-harness-*" | Select-Object -First 1
if (-not $Src) { Die "The download was incomplete. Run this again." }
if (Test-Path "$Dir\app") { Remove-Item -Recurse -Force "$Dir\app" }
Copy-Item -Recurse "$($Src.FullName)\app" "$Dir\app"
Copy-Item "$($Src.FullName)\THIRD_PARTY_NOTICES.md" "$Dir\app\"
# The package list this copy is installed from: the in-app update compares it with a new release's list.
Copy-Item "$($Src.FullName)\packaging\requirements-cli.txt" "$Dir\app\requirements-cli.txt"
# The command runs this file rather than `python -m`: Python then puts the app folder first on sys.path, never the
# folder the command is typed in, so a project's own json.py or argparse.py is not imported in place of Python's.
# (`python -P` does the same, but only from Python 3.11, and this installer accepts 3.10.)
Set-Content -Encoding ASCII "$Dir\app\meshpool_launch.py" "import runpy`r`n`r`nrunpy.run_module(`"meshpool_harness`", run_name=`"__main__`", alter_sys=True)"
Ok "Meshpool"

Step "Installing its Python packages..."
# Reuse the environment only while it still works: it breaks when the Python it was made from is removed or upgraded.
$VPy = "$Dir\venv\Scripts\python.exe"
if (-not (Test-Path $VPy) -or (Run $VPy @("-c", "import sys") -Quiet) -ne 0) {
  if (Test-Path "$Dir\venv") { Remove-Item -Recurse -Force "$Dir\venv" }
  if ((Run $PyExe ($PyArgs + @("-m", "venv", "$Dir\venv"))) -ne 0 -or -not (Test-Path $VPy)) {
    Die "Could not create a Python environment with $PyExe $PyArgs."
  }
}
Run $VPy @("-m", "pip", "install", "-q", "--disable-pip-version-check", "--upgrade", "pip") -Quiet | Out-Null   # best effort
if ((Run $VPy @("-m", "pip", "install", "-q", "--disable-pip-version-check", "-r", "$($Src.FullName)\packaging\requirements-cli.txt")) -ne 0) {
  Die "Installing the Python packages failed (see above). Run this again."
}
Ok "Packages"

# ---------- the command, on PATH now and in new windows ----------
$Bin = Join-Path $Dir "bin"
New-Item -ItemType Directory -Force -Path $Bin | Out-Null
# Paths relative to the command's own folder (%~dp0), so the file is plain ASCII whatever the profile folder is called.
Set-Content -Encoding ASCII "$Bin\meshpool.cmd" ("@echo off`r`nsetlocal`r`nset `"PYTHONPATH=%~dp0..\app`"`r`nset `"PYTHONUTF8=1`"`r`nset `"PYTHONIOENCODING=utf-8`"`r`n" +
  "`"%~dp0..\venv\Scripts\python.exe`" `"%~dp0..\app\meshpool_launch.py`" %*`r`nexit /b %ERRORLEVEL%")
# The user's Path as stored, %VARIABLES% unexpanded (reading it through [Environment] would expand them for good).
$Key = [Microsoft.Win32.Registry]::CurrentUser.CreateSubKey("Environment")
try {
  $UserPath = [string]$Key.GetValue("Path", "", [Microsoft.Win32.RegistryValueOptions]::DoNotExpandEnvironmentNames)
  $Parts = @($UserPath -split ";" | Where-Object { $_ })
  if ($Parts -notcontains $Bin) {
    $Key.SetValue("Path", (($Parts + $Bin) -join ";"), [Microsoft.Win32.RegistryValueKind]::ExpandString)
    try {   # tell Explorer, so terminals opened from now on see the new Path
      Add-Type -Namespace MeshpoolInstall -Name Win32 -MemberDefinition '[DllImport("user32.dll", CharSet = CharSet.Unicode)] public static extern IntPtr SendMessageTimeout(IntPtr hWnd, uint Msg, UIntPtr wParam, string lParam, uint flags, uint timeout, out UIntPtr result);'
      $r = [UIntPtr]::Zero
      [MeshpoolInstall.Win32]::SendMessageTimeout([IntPtr]0xffff, 0x1A, [UIntPtr]::Zero, "Environment", 2, 5000, [ref]$r) | Out-Null
    } catch {}
  }
} finally { $Key.Close() }
if (($env:Path -split ";") -notcontains $Bin) { $env:Path = "$env:Path;$Bin" }
Ok "The meshpool command"
Say ""

$Config = Join-Path $Dir "meshpool.config"
function Ready { (Test-Path $Config) -and (Select-String -Path $Config -Pattern '^(MESHPOOL_API_KEY=.|OPENAI_API_KEY=.|MH_PROVIDER=chatgpt)' -Quiet) }

# Straight into setup: pick how Meshpool runs, where it works, and what it may do. Then the session starts.
if (-not $env:MESHPOOL_NO_SETUP -and -not (Ready)) {
  $env:MESHPOOL_FROM_INSTALLER = "1"
  try { & "$Bin\meshpool.cmd" setup } catch {}
  Remove-Item Env:\MESHPOOL_FROM_INSTALLER -ErrorAction SilentlyContinue
  Say ""
}
if (Ready) { Say "  ${Blue}Meshpool is ready.$Off Go to a project folder and type ${Blue}meshpool$Off" }
else { Say "  ${Blue}Meshpool is installed.$Off Type ${Blue}meshpool$Off to set it up and start." }
Say "  ${Dim}meshpool ui opens the full app in your browser. meshpool setup changes your choices.$Off"
Say ""
} catch {
  $m = if ($_.Exception.Data["meshpool"]) { $_.Exception.Message } else { "The install stopped: $($_.Exception.Message)" }
  Write-Host "  $Red✗$Off $m"
  Write-Host ""
  $global:LASTEXITCODE = 1
} finally {
  if (Test-Path $Tmp) { Remove-Item -Recurse -Force $Tmp -ErrorAction SilentlyContinue }
}
}
