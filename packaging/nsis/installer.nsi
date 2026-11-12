Unicode true
!include "MUI2.nsh"
Name "Meshpool"
OutFile "Meshpool Setup.exe"
InstallDir "$LOCALAPPDATA\Programs\Meshpool"
InstallDirRegKey HKCU "Software\Meshpool" "InstallDir"
RequestExecutionLevel user
SetCompressor /SOLID lzma
SetCompressorDictSize 64
BrandingText "Meshpool 2.6.0"
VIProductVersion "2.6.0.0"
VIAddVersionKey "ProductName" "Meshpool"
VIAddVersionKey "FileDescription" "Meshpool Setup"
VIAddVersionKey "FileVersion" "2.6.0"
VIAddVersionKey "ProductVersion" "2.6.0"
VIAddVersionKey "CompanyName" "Meshpool"
VIAddVersionKey "LegalCopyright" "Copyright 2026 Meshpool. Apache License 2.0."

!define MUI_ICON "Meshpool\Meshpool.ico"
!define MUI_UNICON "Meshpool\Meshpool.ico"
!define MUI_ABORTWARNING
!define MUI_WELCOMEPAGE_TITLE "Install Meshpool"
!define MUI_WELCOMEPAGE_TEXT "Meshpool is a coding and cowork agent that runs on your computer with your Meshpool API key.$\r$\n$\r$\nEverything is included: the app, three agents (Meshpool, Pi and Hermes), browser use, computer use, Office files, connectors for GitHub, Jira, Linear, Notion and more, and the meshpool command for your terminal. Nothing else to install, and no administrator rights needed."
!define MUI_FINISHPAGE_RUN "$INSTDIR\Meshpool.exe"
!define MUI_FINISHPAGE_RUN_TEXT "Open Meshpool now"
!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_LICENSE "Meshpool\LICENSE.txt"
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH
!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES
!insertmacro MUI_LANGUAGE "English"

Section "Meshpool" SecMain
  ; close a running copy so its files can be replaced
  nsExec::Exec 'taskkill /F /IM Meshpool.exe'
  nsExec::Exec 'powershell -NoProfile -Command "Get-CimInstance Win32_Process | Where-Object { $$_.ExecutablePath -like ''$INSTDIR\*'' } | ForEach-Object { Stop-Process -Id $$_.ProcessId -Force }"'
  SetOutPath "$INSTDIR"
  ; replace old program files, keep the user's settings (they live in %APPDATA%\Meshpool)
  RMDir /r "$INSTDIR\app"
  RMDir /r "$INSTDIR\runtime"
  RMDir /r "$INSTDIR\git"
  File /r "Meshpool\*.*"
  WriteUninstaller "$INSTDIR\Uninstall Meshpool.exe"
  CreateShortcut "$SMPROGRAMS\Meshpool.lnk" "$INSTDIR\Meshpool.exe" "" "$INSTDIR\Meshpool.ico"
  CreateShortcut "$DESKTOP\Meshpool.lnk" "$INSTDIR\Meshpool.exe" "" "$INSTDIR\Meshpool.ico"
  WriteRegStr HKCU "Software\Meshpool" "InstallDir" "$INSTDIR"
  ; the meshpool terminal command: add the install folder to the user's PATH
  nsExec::ExecToLog 'powershell -NoProfile -ExecutionPolicy Bypass -Command "$$d = ''$INSTDIR''; $$p = [string][Environment]::GetEnvironmentVariable(''Path'',''User''); if (($$p -split '';'') -notcontains $$d) { [Environment]::SetEnvironmentVariable(''Path'', (($$p.TrimEnd('';'') + '';'' + $$d).TrimStart('';'')), ''User'') }"'
  !define UNINST "Software\Microsoft\Windows\CurrentVersion\Uninstall\Meshpool"
  WriteRegStr HKCU "${UNINST}" "DisplayName" "Meshpool"
  WriteRegStr HKCU "${UNINST}" "DisplayVersion" "2.6.0"
  WriteRegStr HKCU "${UNINST}" "Publisher" "Meshpool"
  WriteRegStr HKCU "${UNINST}" "DisplayIcon" "$INSTDIR\Meshpool.ico"
  WriteRegStr HKCU "${UNINST}" "InstallLocation" "$INSTDIR"
  WriteRegStr HKCU "${UNINST}" "UninstallString" '"$INSTDIR\Uninstall Meshpool.exe"'
  WriteRegDWORD HKCU "${UNINST}" "NoModify" 1
  WriteRegDWORD HKCU "${UNINST}" "NoRepair" 1
SectionEnd

Section "Uninstall"
  nsExec::Exec 'taskkill /F /IM Meshpool.exe'
  Delete "$SMPROGRAMS\Meshpool.lnk"
  Delete "$DESKTOP\Meshpool.lnk"
  RMDir /r "$INSTDIR\app"
  RMDir /r "$INSTDIR\runtime"
  RMDir /r "$INSTDIR\git"
  Delete "$INSTDIR\Meshpool.exe"
  Delete "$INSTDIR\meshpool.cmd"
  Delete "$INSTDIR\Meshpool.ico"
  Delete "$INSTDIR\Uninstall Meshpool.exe"
  RMDir "$INSTDIR"
  nsExec::ExecToLog 'powershell -NoProfile -ExecutionPolicy Bypass -Command "$$d = ''$INSTDIR''; $$p = [string][Environment]::GetEnvironmentVariable(''Path'',''User''); [Environment]::SetEnvironmentVariable(''Path'', ((($$p -split '';'') | Where-Object { $$_ -and $$_ -ne $$d }) -join '';''), ''User'')"'
  DeleteRegKey HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\Meshpool"
  DeleteRegKey HKCU "Software\Meshpool"
  ; settings and sessions in %APPDATA%\Meshpool are kept on purpose
SectionEnd
