@echo off
setlocal
rem The meshpool terminal command, using the Python inside the Meshpool install.
if not defined MESHPOOL_HOME set "MESHPOOL_HOME=%APPDATA%\Meshpool"
set "PYTHONPATH=%~dp0app"
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
set "PYTHONNOUSERSITE=1"
set "PYTHONDONTWRITEBYTECODE=1"
set "MESHPOOL_CLI=%~dp0meshpool.cmd"
set "PATH=%~dp0runtime;%~dp0runtime\Scripts;%PATH%"
"%~dp0runtime\python.exe" -m meshpool_harness %*
exit /b %ERRORLEVEL%
