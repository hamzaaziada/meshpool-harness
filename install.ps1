# Meshpool command line installer for Windows.
#   irm https://raw.githubusercontent.com/hamzaaziada/meshpool-harness/main/install.ps1 | iex
# Installs into %USERPROFILE%\.meshpool-cli and adds a `meshpool` command to your user PATH. Needs Python 3.10+.
$ErrorActionPreference = "Stop"
$Repo = "hamzaaziada/meshpool-harness"
$Ref = if ($env:MESHPOOL_REF) { $env:MESHPOOL_REF } else { "main" }
$Dir = if ($env:MESHPOOL_DIR) { $env:MESHPOOL_DIR } else { Join-Path $env:USERPROFILE ".meshpool-cli" }
Write-Host "  meshpool" -ForegroundColor Blue
Write-Host "  command line installer" -ForegroundColor DarkGray

$Py = $null
foreach ($c in @("py -3.12", "py -3.11", "py -3.10", "python")) {
  $parts = $c.Split(" ")
  try { & $parts[0] $parts[1..9] -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" 2>$null; if ($LASTEXITCODE -eq 0) { $Py = $parts; break } } catch {}
}
if (-not $Py) { Write-Host "Meshpool needs Python 3.10 or later. Install it from python.org, then run this again."; exit 1 }

Write-Host "Downloading Meshpool ($Ref)..."
$Tmp = Join-Path $env:TEMP ("meshpool-" + [guid]::NewGuid())
New-Item -ItemType Directory -Path $Tmp | Out-Null
Invoke-WebRequest "https://codeload.github.com/$Repo/zip/$Ref" -OutFile "$Tmp\src.zip"
Expand-Archive "$Tmp\src.zip" -DestinationPath $Tmp
$Src = Get-ChildItem $Tmp -Directory | Where-Object Name -like "meshpool-harness-*" | Select-Object -First 1
New-Item -ItemType Directory -Force -Path $Dir | Out-Null
if (Test-Path "$Dir\app") { Remove-Item -Recurse -Force "$Dir\app" }
Copy-Item -Recurse "$($Src.FullName)\app" "$Dir\app"
Copy-Item "$($Src.FullName)\LICENSE", "$($Src.FullName)\NOTICE", "$($Src.FullName)\THIRD_PARTY_NOTICES.md" "$Dir\app\"

Write-Host "Installing Python packages (first time takes a minute)..."
if (-not (Test-Path "$Dir\venv\Scripts\python.exe")) { & $Py[0] $Py[1..9] -m venv "$Dir\venv" }
& "$Dir\venv\Scripts\python.exe" -m pip install -q --upgrade pip | Out-Null
& "$Dir\venv\Scripts\python.exe" -m pip install -q -r "$($Src.FullName)\packaging\requirements-cli.txt"

$Bin = Join-Path $Dir "bin"
New-Item -ItemType Directory -Force -Path $Bin | Out-Null
Set-Content -Encoding ASCII "$Bin\meshpool.cmd" "@echo off`r`nset PYTHONPATH=$Dir\app`r`n`"$Dir\venv\Scripts\python.exe`" -m meshpool_harness %*"
$UserPath = [Environment]::GetEnvironmentVariable("Path", "User")
if ($UserPath -notlike "*$Bin*") { [Environment]::SetEnvironmentVariable("Path", "$UserPath;$Bin", "User") }
Remove-Item -Recurse -Force $Tmp

Write-Host ""
Write-Host "Meshpool is installed. Open a new terminal, then:" -ForegroundColor Blue
Write-Host "  meshpool login     save your Meshpool API key"
Write-Host "  meshpool           start the agent in the current folder"
Write-Host "  meshpool ui        open the full app in your browser on localhost"
