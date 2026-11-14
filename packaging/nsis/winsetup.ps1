# Helper for the Meshpool installer and uninstaller. They unpack it to a temporary folder and run its text through
# -Command (see the WinSetup macro in installer.nsi), passing the inputs as MP_STOP, MP_ADDPATH and MP_REMOVEPATH
# environment variables, or as these parameters:
#   -Stop DIR               close every program running from DIR (the app, its Python, Node, Git), so files can go
#   -AddPath DIR            add DIR to the user's Path
#   -RemovePath "A;B"       take these folders off the user's Path
# The Path is read and written as stored in the registry (%VARIABLES% stay unexpanded) and kept as REG_EXPAND_SZ.
# Keep this file plain ASCII: Windows PowerShell reads a script without a byte order mark in the ANSI code page.
param([string]$Stop = $env:MP_STOP, [string]$AddPath = $env:MP_ADDPATH, [string]$RemovePath = $env:MP_REMOVEPATH)
$ErrorActionPreference = "Continue"

function Norm([string]$p) { $p.Trim().TrimEnd("\") }

if ($Stop) {
  # Only Meshpool's own programs: the install folder may be one the user shares with other software.
  $root = (Norm $Stop) + "\"
  $ours = @("runtime\", "node\", "git\", "app\", "bin\") | ForEach-Object { $root + $_ }
  $procs = @(Get-CimInstance Win32_Process | Where-Object {
      $exe = $_.ExecutablePath
      $exe -and (($exe -ieq ($root + "Meshpool.exe")) -or
                 @($ours | Where-Object { $exe.StartsWith($_, [StringComparison]::OrdinalIgnoreCase) }).Count) })
  foreach ($p in $procs) { Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue }
  if ($procs.Count) { Start-Sleep -Milliseconds 800 }      # let Windows release the files they held
}

if ($AddPath -or $RemovePath) {
  $key = [Microsoft.Win32.Registry]::CurrentUser.CreateSubKey("Environment")
  try {
    $cur = [string]$key.GetValue("Path", "", [Microsoft.Win32.RegistryValueOptions]::DoNotExpandEnvironmentNames)
    $drop = @($RemovePath -split ";" | Where-Object { $_.Trim() } | ForEach-Object { Norm $_ })
    $parts = @($cur -split ";" | Where-Object { $_.Trim() -and ($drop -notcontains (Norm $_)) })
    if ($AddPath -and (@($parts | ForEach-Object { Norm $_ }) -notcontains (Norm $AddPath))) { $parts += $AddPath }
    $new = $parts -join ";"
    if ($new -ne $cur) {
      if ($new) { $key.SetValue("Path", $new, [Microsoft.Win32.RegistryValueKind]::ExpandString) }
      else { $key.DeleteValue("Path", $false) }
      try {   # tell Explorer, so terminals opened from now on see the new Path
        Add-Type -Namespace MeshpoolSetup -Name Win32 -MemberDefinition '[DllImport("user32.dll", CharSet = CharSet.Unicode)] public static extern IntPtr SendMessageTimeout(IntPtr hWnd, uint Msg, UIntPtr wParam, string lParam, uint flags, uint timeout, out UIntPtr result);'
        $r = [UIntPtr]::Zero
        [MeshpoolSetup.Win32]::SendMessageTimeout([IntPtr]0xffff, 0x1A, [UIntPtr]::Zero, "Environment", 2, 5000, [ref]$r) | Out-Null
      } catch {}
    }
  } finally { $key.Close() }
}
