@echo off
setlocal
rem The meshpool terminal command, using the Python inside the Meshpool install.
rem It lives in its own folder (<install>\bin) and only that folder goes on PATH: next to Meshpool.exe, Windows would
rem run the app instead (PATHEXT tries .EXE before .CMD).
for %%I in ("%~dp0..") do set "MESHPOOL_ROOT=%%~fI"
if not defined MESHPOOL_HOME set "MESHPOOL_HOME=%APPDATA%\Meshpool"
set "PYTHONPATH=%MESHPOOL_ROOT%\app"
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
set "PYTHONNOUSERSITE=1"
set "PYTHONDONTWRITEBYTECODE=1"
set "MESHPOOL_CLI=%~dp0meshpool.cmd"
set "PATH=%MESHPOOL_ROOT%\runtime;%MESHPOOL_ROOT%\runtime\Scripts;%PATH%"
rem -P: never import modules from the folder the command is typed in (a json.py in a project must not run).
"%MESHPOOL_ROOT%\runtime\python.exe" -P -m meshpool_harness %*
exit /b %ERRORLEVEL%
