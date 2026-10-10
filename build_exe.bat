@echo off
rem ---------------------------------------------------------------
rem  Build the DLNA caster into a single exe (GUI).
rem
rem  ffmpeg is NOT embedded: the exe looks for it at runtime
rem  (FFMPEG_PATH env var -> same folder as the exe -> PATH) and
rem  downloads it from GitHub only if it cannot find one.
rem
rem  All real logic lives in build_exe.py, which is UTF-8. This file
rem  stays ASCII-only on purpose: cmd.exe parses .bat as GBK on a
rem  Chinese Windows and any UTF-8 Chinese text here would turn into
rem  garbage.
rem
rem  Usage:
rem    build_exe.bat
rem    build_exe.bat --keep-build
rem    build_exe.bat --embed-ffmpeg --ffmpeg D:\ffmpeg\ffmpeg.exe
rem ---------------------------------------------------------------
setlocal

cd /d "%~dp0"

set "PY="
set "VENV=%USERPROFILE%\.workbuddy\binaries\python\envs\pybuild\Scripts\python.exe"

if exist "%VENV%" set "PY=%VENV%"
if not defined PY if exist "H:\miniforge3\python.exe" set "PY=H:\miniforge3\python.exe"
if not defined PY (
  for /f "delims=" %%i in ('where python 2^>nul') do (
    if not defined PY set "PY=%%i"
  )
)

if not defined PY (
  echo [ERROR] No Python interpreter found.
  echo         Install Python 3.10+ and make sure "python" is on PATH,
  echo         or edit VENV in this file.
  exit /b 1
)

echo Using Python: %PY%
echo.

"%PY%" "%~dp0build_exe.py" %*
set "RC=%ERRORLEVEL%"

if not "%RC%"=="0" (
  echo.
  echo [FAILED] build_exe.py exited with code %RC%
)

exit /b %RC%
