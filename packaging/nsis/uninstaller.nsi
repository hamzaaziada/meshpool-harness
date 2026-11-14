; Writes Meshpool's uninstaller on the build machine, so it can be signed before it goes into the installer.
;
; The uninstaller is normally pieced together on the user's machine by WriteUninstaller, which leaves no chance to
; sign it. This script writes the same uninstaller to a file at build time; the release build signs that file and
; installer.nsi then ships it with File instead of WriteUninstaller.
;
; Usage: makensis /DMP_UNINSTALLER_OUT=<path> uninstaller.nsi
; The Uninstall section below must stay in step with the one in installer.nsi.

Unicode true
Name "Meshpool Uninstaller"
OutFile "Meshpool-Uninstaller-Generator.exe"
RequestExecutionLevel user
SilentInstall silent

!include "winsetup.nsh"

!ifndef MP_UNINSTALLER_OUT
  !error "MP_UNINSTALLER_OUT is not set (the path to write the uninstaller to)"
!endif

Section
  ; $EXEDIR is the folder this generator runs from, so the uninstaller lands next to it.
  WriteUninstaller "${MP_UNINSTALLER_OUT}"
  SetErrorLevel 0
  Quit
SectionEnd

Section "Uninstall"
  ; Keep in step with installer.nsi.
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