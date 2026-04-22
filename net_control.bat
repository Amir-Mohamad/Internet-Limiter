@echo off
REM Internet Limiter Control for Windows
REM Run as Administrator

if "%1"=="start" goto start
if "%1"=="stop" goto stop
if "%1"=="unblock" goto unblock
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

:unblock
echo Unblocking internet...
python internet_limiter.py --unblock
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
echo Usage: net_control.bat [start^|stop^|unblock^|status]
echo.
echo   start    - Start monitoring
echo   stop     - Stop monitoring
echo   unblock  - Unblock internet
echo   status   - Check if running
goto end

:end
