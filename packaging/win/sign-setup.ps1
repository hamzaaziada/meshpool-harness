# Put the Artifact Signing client tools (the dlib) and signtool on this machine, and record where they are in
# MP_DLIB and MP_SIGNTOOL for packaging/win/sign-file.ps1.
#
#   packaging/win/sign-setup.ps1
#
# Only needed for the NSIS uninstaller, which is generated while makensis runs; the artifact-signing-action brings
# its own copy of both for everything else. Does nothing when the Azure secrets are not set.
$ErrorActionPreference = "Stop"

if (-not $env:AZURE_SIGNING_ENDPOINT -or -not $env:AZURE_SIGNING_ACCOUNT -or -not $env:AZURE_CERT_PROFILE) {
  Write-Host "sign-setup.ps1: no Azure signing secrets set, skipping"
  exit 0
}

$Root = Join-Path $env:RUNNER_TEMP "meshpool-signing-tools"
New-Item -ItemType Directory -Force -Path $Root | Out-Null

# The dlib, from NuGet. Microsoft.ArtifactSigning.Client is the current name of the Trusted Signing client package.
$Pkg = Join-Path $Root "pkg"
if (-not (Test-Path $Pkg)) {
  $Nuget = Join-Path $Root "nuget.exe"
  if (-not (Test-Path $Nuget)) {
    Invoke-WebRequest -UseBasicParsing "https://dist.nuget.org/win-x86-commandline/latest/nuget.exe" -OutFile $Nuget
  }
  & $Nuget install Microsoft.ArtifactSigning.Client -OutputDirectory $Pkg -NonInteractive -Verbosity quiet
  if ($LASTEXITCODE -ne 0) { throw "sign-setup.ps1: could not download the Artifact Signing client tools" }
}

$Dlib = Get-ChildItem -Path $Pkg -Recurse -Filter "Azure.CodeSigning.Dlib.dll" |
  Where-Object { $_.FullName -match "\\x64\\" } | Select-Object -First 1
if (-not $Dlib) {
  $Dlib = Get-ChildItem -Path $Pkg -Recurse -Filter "Azure.CodeSigning.Dlib.dll" | Select-Object -First 1
}
if (-not $Dlib) { throw "sign-setup.ps1: Azure.CodeSigning.Dlib.dll is not in the client tools package" }

# signtool, from the Windows SDK. The runner images ship one; otherwise take it from the SDK build tools package.
$SignTool = Get-ChildItem -Path "${env:ProgramFiles(x86)}\Windows Kits\10\bin" -Recurse -Filter "signtool.exe" -ErrorAction SilentlyContinue |
  Where-Object { $_.FullName -match "\\x64\\" } | Sort-Object FullName -Descending | Select-Object -First 1
if (-not $SignTool) {
  $Sdk = Join-Path $Root "sdk"
  if (-not (Test-Path $Sdk)) {
    $Nuget = Join-Path $Root "nuget.exe"
    & $Nuget install Microsoft.Windows.SDK.BuildTools -OutputDirectory $Sdk -NonInteractive -Verbosity quiet
    if ($LASTEXITCODE -ne 0) { throw "sign-setup.ps1: could not download the Windows SDK build tools" }
  }
  $SignTool = Get-ChildItem -Path $Sdk -Recurse -Filter "signtool.exe" |
    Where-Object { $_.FullName -match "\\x64\\" } | Select-Object -First 1
}
if (-not $SignTool) { throw "sign-setup.ps1: signtool.exe was not found" }

"MP_DLIB=$($Dlib.FullName)" | Out-File -FilePath $env:GITHUB_ENV -Append -Encoding utf8
"MP_SIGNTOOL=$($SignTool.FullName)" | Out-File -FilePath $env:GITHUB_ENV -Append -Encoding utf8
Write-Host "sign-setup.ps1: dlib $($Dlib.FullName)"
Write-Host "sign-setup.ps1: signtool $($SignTool.FullName)"