@echo off
chcp 65001 >nul
title DLNA Cast Capability Probe
cd /d "%~dp0"
where python >nul 2>nul
if errorlevel 1 (
    echo [ERROR] python not found in PATH
    pause
    exit /b 1
)
python dlna-probe.py %*
echo.
pause
