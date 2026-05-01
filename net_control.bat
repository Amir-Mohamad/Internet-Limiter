@echo off
REM Internet Limiter Control for Windows

if "%1"=="start" goto start
if "%1"=="stop" goto stop
if "%1"=="reset-proxy" goto reset_proxy
if "%1"=="status" goto status
goto usage

:start
echo Starting internet limiter...
python internet_limiter.py
goto end

:stop
echo Stopping limiter...
taskkill /F /IM python.exe /FI "WINDOWTITLE eq internet_limiter*" 2>nul
if %errorlevel%==0 (
    echo Limiter stopped
) else (
    echo No limiter process found
)
goto end

:reset_proxy
echo Turning off WinINet system proxy...
python internet_limiter.py --reset-proxy
goto end

:status
tasklist /FI "IMAGENAME eq python.exe" 2>nul | find /I "python.exe" >nul
if %errorlevel%==0 (
    echo Limiter might be running
) else (
    echo Limiter is stopped
)
goto end

:usage
echo Usage: net_control.bat [start^|stop^|reset-proxy^|status]
echo.
echo   start        - Start monitoring
echo   stop         - Stop monitoring
echo   reset-proxy  - Disable Windows user proxy (WinINet)
echo   status       - Check if running
goto end

:end
