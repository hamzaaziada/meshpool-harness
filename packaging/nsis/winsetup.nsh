; Shared by installer.nsi and uninstaller.nsi, so the uninstaller written at build time behaves exactly like the one
; the installer would have written.

; Run winsetup.ps1 with its inputs in environment variables. The script text goes through -Command, which a machine's
; script execution policy (AllSigned, Restricted) does not block, unlike -File; nothing needs quoting, so folder names
; with apostrophes are safe.
!macro WinSetup STOP ADD REMOVE
  System::Call 'Kernel32::SetEnvironmentVariable(t "MP_SCRIPT", t "$PLUGINSDIR\winsetup.ps1")'
  System::Call 'Kernel32::SetEnvironmentVariable(t "MP_STOP", t "${STOP}")'
  System::Call 'Kernel32::SetEnvironmentVariable(t "MP_ADDPATH", t "${ADD}")'
  System::Call 'Kernel32::SetEnvironmentVariable(t "MP_REMOVEPATH", t "${REMOVE}")'
  nsExec::ExecToLog '"$SYSDIR\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -NonInteractive -ExecutionPolicy Bypass -Command "& ([scriptblock]::Create([IO.File]::ReadAllText($$env:MP_SCRIPT)))"'
  Pop $0
!macroend