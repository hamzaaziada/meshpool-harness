# Sign one file with Azure Artifact Signing (formerly Trusted Signing), through signtool and its dlib.
#
#   packaging/win/sign-file.ps1 -File <path> [-Description <text>]
#
# Used by the release build for the NSIS uninstaller, which is generated while makensis runs and so cannot be signed
# by the artifact-signing-action (that action signs a list of files that already exist). Everything else is signed by
# the action itself.
#
# Needs the same settings as the action, in the environment:
#   AZURE_SIGNING_ENDPOINT, AZURE_SIGNING_ACCOUNT, AZURE_CERT_PROFILE
#   AZURE_TENANT_ID, AZURE_CLIENT_ID, AZURE_CLIENT_SECRET
# and the Artifact Signing client tools (the dlib) plus signtool, found by packaging/win/sign-setup.ps1.
param(
  [Parameter(Mandatory = $true)][string]$File,
  [string]$Description = "Meshpool"
)
$ErrorActionPreference = "Stop"

if (-not (Test-Path $File)) { throw "sign-file.ps1: $File does not exist" }

$Dlib = $env:MP_DLIB
$SignTool = $env:MP_SIGNTOOL
if (-not $Dlib -or -not (Test-Path $Dlib)) { throw "sign-file.ps1: the Artifact Signing dlib was not found (MP_DLIB)" }
if (-not $SignTool -or -not (Test-Path $SignTool)) { throw "sign-file.ps1: signtool was not found (MP_SIGNTOOL)" }

# The metadata file tells the dlib which signing account and certificate profile to use.
$Meta = Join-Path ([IO.Path]::GetTempPath()) ("meshpool-signing-" + [guid]::NewGuid() + ".json")
@{
  Endpoint              = $env:AZURE_SIGNING_ENDPOINT
  CodeSigningAccountName = $env:AZURE_SIGNING_ACCOUNT
  CertificateProfileName = $env:AZURE_CERT_PROFILE
} | ConvertTo-Json | Set-Content -Encoding UTF8 $Meta

try {
  & $SignTool sign /v /fd SHA256 /tr "http://timestamp.acs.microsoft.com" /td SHA256 `
    /dlib $Dlib /dmdf $Meta /d $Description $File
  if ($LASTEXITCODE -ne 0) { throw "sign-file.ps1: signtool failed for $File (exit $LASTEXITCODE)" }
} finally {
  Remove-Item -Force $Meta -ErrorAction SilentlyContinue
}

$sig = Get-AuthenticodeSignature $File
if ($sig.Status -ne "Valid") { throw "sign-file.ps1: $File is not validly signed ($($sig.Status))" }
Write-Host "sign-file.ps1: signed $File"