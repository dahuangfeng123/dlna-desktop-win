@echo off
chcp 65001 >nul
title DLNA Screen Cast
cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
    echo [ERROR] python not found in PATH
    echo Please install Python 3 and add it to PATH.
    pause
    exit /b 1
)

rem Any command-line argument is passed straight through to the script.
if not "%~1"=="" goto passthru

:menu
cls
echo ============================================================
echo   DLNA Screen Cast  -  select a profile
echo ============================================================
echo    [1] 720p    light          ^<- save bandwidth
echo    [2] 1080p   standard
echo    [3] 1080p   best picture   ^<- RECOMMENDED (tuned to your link)
echo    [4] 2K / 4K auto-fit       ^<- downgrades if screen is smaller
echo    [5] SMOOTHEST              (x264 + GPU capture + 30 fps)
echo    [6] 60 fps                 (try this if the TV accepts it)
echo    [7] Custom flags ...
echo    [0] Quit
echo ------------------------------------------------------------
echo    Picture stutters after a while?  Choose [2] or [1].
echo    The program also prints a "link throughput" warning
echo    when it detects that. Start with [3]; step down if needed.
echo ------------------------------------------------------------
echo    LOWEST LATENCY tip: once running, also open this address
echo    in the TV's own browser (full URL is printed at startup):
echo        http://^<PC-IP^>:^<port^>/tv
echo    It bypasses the TV's built-in player, so the buffer depth
echo    is under our control instead of the TV vendor's.
echo ============================================================
set "CH="
set /p "CH=Select [3]: "
if "%CH%"=="" set "CH=3"

set "ARGS="
if "%CH%"=="1" set "ARGS=--quality 720"
if "%CH%"=="2" set "ARGS=--quality 1080"
if "%CH%"=="3" set "ARGS=--quality 1080hq"
if "%CH%"=="4" set "ARGS=--quality 2160"
if "%CH%"=="5" set "ARGS=--low-latency --quality 1080"
if "%CH%"=="6" set "ARGS=--quality 1080 --fps 60 --encoder x264 --latency-ms 250 --gop-ms 400"
if "%CH%"=="0" goto end
if "%CH%"=="7" goto custom
if not defined ARGS goto menu
goto run

:custom
echo.
echo Examples:
echo   --quality 1080hq --fps 60 --latency-ms 250 --gop-ms 400
echo   --low-latency --quality 1080
echo   --capture gdigrab --encoder mf --ip 192.168.1.100 --clock
echo.
set "ARGS="
set /p "ARGS=Flags: "
if not defined ARGS goto menu
goto run

:run
echo.
python screen-cast.py %ARGS%
goto finished

:passthru
python screen-cast.py %*
goto finished

:finished
echo.
echo Program exited.
pause
exit /b 0

:end
exit /b 0
