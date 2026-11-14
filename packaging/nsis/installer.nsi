Unicode true
!include "MUI2.nsh"
Name "Meshpool"
OutFile "Meshpool Setup.exe"
InstallDir "$LOCALAPPDATA\Programs\Meshpool"
InstallDirRegKey HKCU "Software\Meshpool" "InstallDir"
RequestExecutionLevel user
SetCompressor /SOLID lzma
SetCompressorDictSize 64
; The version placeholders are filled in by packaging/build_all.py from app/meshpool_harness/__init__.py.
BrandingText "Meshpool @VERSION@@BETA@"
VIProductVersion "@VERSION_NUM@.0"
VIAddVersionKey "ProductName" "Meshpool"
VIAddVersionKey "FileDescription" "Meshpool Setup"
VIAddVersionKey "FileVersion" "@VERSION@"
VIAddVersionKey "ProductVersion" "@VERSION@"
VIAddVersionKey "CompanyName" "Meshpool"
VIAddVersionKey "LegalCopyright" "Copyright 2026 Meshpool."

!define MUI_ICON "Meshpool\Meshpool.ico"
!define MUI_UNICON "Meshpool\Meshpool.ico"
!define MUI_ABORTWARNING
!define MUI_WELCOMEPAGE_TITLE "Install Meshpool"
!define MUI_WELCOMEPAGE_TEXT "Meshpool is a coding and cowork agent that runs on your computer with your Meshpool API key.$\r$\n$\r$\nEverything is included: the app, three agents (Meshpool, Pi and Hermes), browser use, computer use, Office files, connectors for GitHub, Jira, Linear, Notion and more, and the meshpool command for your terminal. Nothing else to install, and no administrator rights needed."
!define MUI_FINISHPAGE_RUN "$INSTDIR\Meshpool.exe"
!define MUI_FINISHPAGE_RUN_TEXT "Open Meshpool now"
!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH
!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES
!insertmacro MUI_LANGUAGE "English"

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

Section "Meshpool" SecMain
  InitPluginsDir
  File "/oname=$PLUGINSDIR\winsetup.ps1" "winsetup.ps1"
  ; close a running copy (the app and everything it started from this folder) so its files can be replaced
  nsExec::Exec 'taskkill /F /IM Meshpool.exe'
  Pop $0
  !insertmacro WinSetup "$INSTDIR" "" ""
  SetOutPath "$INSTDIR"
  ; replace old program files, keep the user's settings (they live in %APPDATA%\Meshpool)
  RMDir /r "$INSTDIR\app"
  RMDir /r "$INSTDIR\runtime"
  RMDir /r "$INSTDIR\git"
  RMDir /r "$INSTDIR\node"
  RMDir /r "$INSTDIR\harness-docs"
  RMDir /r "$INSTDIR\bin"
  ; older installs had the terminal command next to Meshpool.exe; it now lives in bin
  Delete "$INSTDIR\meshpool.cmd"
  File /r "Meshpool\*.*"
  WriteUninstaller "$INSTDIR\Uninstall Meshpool.exe"
  CreateShortcut "$SMPROGRAMS\Meshpool.lnk" "$INSTDIR\Meshpool.exe" "" "$INSTDIR\Meshpool.ico"
  CreateShortcut "$DESKTOP\Meshpool.lnk" "$INSTDIR\Meshpool.exe" "" "$INSTDIR\Meshpool.ico"
  WriteRegStr HKCU "Software\Meshpool" "InstallDir" "$INSTDIR"
  ; the meshpool terminal command: only its own folder goes on the user's PATH. With the install folder itself on
  ; PATH, `meshpool` would start Meshpool.exe (Windows tries .EXE before .CMD), so an older entry for it is removed.
  !insertmacro WinSetup "" "$INSTDIR\bin" "$INSTDIR"
  !define UNINST "Software\Microsoft\Windows\CurrentVersion\Uninstall\Meshpool"
  WriteRegStr HKCU "${UNINST}" "DisplayName" "Meshpool"
  WriteRegStr HKCU "${UNINST}" "DisplayVersion" "@VERSION@"
  WriteRegStr HKCU "${UNINST}" "Publisher" "Meshpool"
  WriteRegStr HKCU "${UNINST}" "DisplayIcon" "$INSTDIR\Meshpool.ico"
  WriteRegStr HKCU "${UNINST}" "InstallLocation" "$INSTDIR"
  WriteRegStr HKCU "${UNINST}" "UninstallString" '"$INSTDIR\Uninstall Meshpool.exe"'
  WriteRegDWORD HKCU "${UNINST}" "NoModify" 1
  WriteRegDWORD HKCU "${UNINST}" "NoRepair" 1
SectionEnd

Section "Uninstall"
  InitPluginsDir
  File "/oname=$PLUGINSDIR\winsetup.ps1" "winsetup.ps1"
  ; close the app and everything running from this folder (its Python, Node, Git), or their files stay locked
  nsExec::Exec 'taskkill /F /IM Meshpool.exe'
  Pop $0
  !insertmacro WinSetup "$INSTDIR" "" ""
  Delete "$SMPROGRAMS\Meshpool.lnk"
  Delete "$DESKTOP\Meshpool.lnk"
  RMDir /r "$INSTDIR\app"
  RMDir /r "$INSTDIR\runtime"
  RMDir /r "$INSTDIR\git"
  RMDir /r "$INSTDIR\node"
  RMDir /r "$INSTDIR\harness-docs"
  RMDir /r "$INSTDIR\bin"
  Delete "$INSTDIR\Meshpool.exe"
  Delete "$INSTDIR\meshpool.cmd"
  Delete "$INSTDIR\Meshpool.ico"
  Delete "$INSTDIR\LICENSE.txt"
  Delete "$INSTDIR\Uninstall Meshpool.exe"
  RMDir "$INSTDIR"
  !insertmacro WinSetup "" "" "$INSTDIR\bin;$INSTDIR"
  DeleteRegKey HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\Meshpool"
  DeleteRegKey HKCU "Software\Meshpool"
  ; settings and sessions in %APPDATA%\Meshpool are kept on purpose
SectionEnd
