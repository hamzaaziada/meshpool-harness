# Meshpool for the terminal, on Windows. One command installs it and starts setup (PowerShell):
#
#   irm https://raw.githubusercontent.com/hamzaaziada/meshpool-harness/main/install.ps1 | iex
#
# It installs into %USERPROFILE%\.meshpool-cli and adds the `meshpool` command to your PATH, in this window too. It uses
# your Python 3.10 or later if you have one; otherwise it downloads a private copy of Python into .meshpool-cli.
# Settings: MESHPOOL_REF (branch or tag), MESHPOOL_DIR, MESHPOOL_NO_SETUP=1 (do not start setup).
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
function Die($t) { Write-Host "  $Red✗$Off $t"; exit 1 }

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

New-Item -ItemType Directory -Force -Path $Dir | Out-Null
$Tmp = Join-Path $env:TEMP ("meshpool-" + [guid]::NewGuid())
New-Item -ItemType Directory -Path $Tmp | Out-Null

# ---------- Python ----------
$Py = $null
foreach ($c in @("py -3.13", "py -3.12", "py -3.11", "py -3.10", "python")) {
  $parts = $c.Split(" ")
  try {
    & $parts[0] $parts[1..9] -c "import sys, venv; sys.exit(0 if sys.version_info >= (3, 10) else 1)" 2>$null
    if ($LASTEXITCODE -eq 0) { $Py = $parts; break }
  } catch {}
}
if ($Py) {
  Ok ("Python " + (& $Py[0] $Py[1..9] -c "import platform; print(platform.python_version())"))
} elseif (Test-Path "$Dir\python\python.exe") {
  $Py = @("$Dir\python\python.exe"); Ok "Python (Meshpool's own copy)"
} else {
  Step "Downloading Python (this computer does not have 3.10 or later)..."
  try { Invoke-WebRequest $PyUrl -OutFile "$Tmp\python.tgz" } catch { Die "Could not download Python. Check your internet connection." }
  if ((Get-FileHash "$Tmp\python.tgz" -Algorithm SHA256).Hash.ToLower() -ne $PySum) { Die "The Python download did not match its checksum. Run this again." }
  tar -xzf "$Tmp\python.tgz" -C $Tmp
  if (Test-Path "$Dir\python") { Remove-Item -Recurse -Force "$Dir\python" }
  Move-Item "$Tmp\python" "$Dir\python"
  $Py = @("$Dir\python\python.exe"); Ok "Python (Meshpool's own copy)"
}

# ---------- Meshpool ----------
Step "Downloading Meshpool ($Ref)..."
try { Invoke-WebRequest "https://codeload.github.com/$Repo/zip/$Ref" -OutFile "$Tmp\src.zip" } catch { Die "Could not download Meshpool. Check your internet connection." }
Expand-Archive "$Tmp\src.zip" -DestinationPath $Tmp
$Src = Get-ChildItem $Tmp -Directory | Where-Object Name -like "meshpool-harness-*" | Select-Object -First 1
if (Test-Path "$Dir\app") { Remove-Item -Recurse -Force "$Dir\app" }
Copy-Item -Recurse "$($Src.FullName)\app" "$Dir\app"
Copy-Item "$($Src.FullName)\LICENSE", "$($Src.FullName)\NOTICE", "$($Src.FullName)\THIRD_PARTY_NOTICES.md" "$Dir\app\"
Ok "Meshpool"

Step "Installing its Python packages..."
if (-not (Test-Path "$Dir\venv\Scripts\python.exe")) { & $Py[0] $Py[1..9] -m venv "$Dir\venv" }
& "$Dir\venv\Scripts\python.exe" -m pip install -q --disable-pip-version-check --upgrade pip 2>$null | Out-Null
& "$Dir\venv\Scripts\python.exe" -m pip install -q --disable-pip-version-check -r "$($Src.FullName)\packaging\requirements-cli.txt"
if ($LASTEXITCODE -ne 0) { Die "Installing the Python packages failed (see above). Run this again." }
Ok "Packages"

# ---------- the command, on PATH now and in new windows ----------
$Bin = Join-Path $Dir "bin"
New-Item -ItemType Directory -Force -Path $Bin | Out-Null
Set-Content -Encoding ASCII "$Bin\meshpool.cmd" "@echo off`r`nset PYTHONPATH=$Dir\app`r`n`"$Dir\venv\Scripts\python.exe`" -m meshpool_harness %*"
$UserPath = [Environment]::GetEnvironmentVariable("Path", "User")
if (($UserPath -split ";") -notcontains $Bin) { [Environment]::SetEnvironmentVariable("Path", (($UserPath.TrimEnd(";"), $Bin) -join ";").TrimStart(";"), "User") }
if (($env:Path -split ";") -notcontains $Bin) { $env:Path = "$env:Path;$Bin" }
Ok "The meshpool command"
Remove-Item -Recurse -Force $Tmp
Say ""

$Config = Join-Path $Dir "meshpool.config"
function Ready { (Test-Path $Config) -and (Select-String -Path $Config -Pattern '^(MESHPOOL_API_KEY=.|OPENAI_API_KEY=.|MH_PROVIDER=(claude|chatgpt))' -Quiet) }

# Straight into setup: pick how Meshpool runs, where it works, and what it may do. Then the session starts.
if (-not $env:MESHPOOL_NO_SETUP -and -not (Ready)) {
  $env:MESHPOOL_FROM_INSTALLER = "1"
  & "$Bin\meshpool.cmd" setup
  Remove-Item Env:\MESHPOOL_FROM_INSTALLER -ErrorAction SilentlyContinue
  Say ""
}
if (Ready) { Say "  ${Blue}Meshpool is ready.$Off Go to a project folder and type ${Blue}meshpool$Off" }
else { Say "  ${Blue}Meshpool is installed.$Off Type ${Blue}meshpool$Off to set it up and start." }
Say "  ${Dim}meshpool ui opens the full app in your browser. meshpool setup changes your choices.$Off"
Say ""
