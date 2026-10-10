@echo off
chcp 65001 >nul
title TCL Cast Helper
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0tcl-cast.ps1"
echo.
echo ---- script finished ----
pause
