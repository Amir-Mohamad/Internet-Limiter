@echo off
setlocal
cd /d "%~dp0"

echo.
echo Preparing build...
echo - Close "Internet Limiter" if it is running (otherwise the old .exe stays locked).
echo.

REM Stop a running build output so PyInstaller can overwrite dist\InternetLimiter.exe
taskkill /F /IM InternetLimiter.exe >nul 2>&1

REM Brief pause so Windows releases the file handle
ping -n 2 127.0.0.1 >nul

set "OUT=%~dp0dist\InternetLimiter.exe"
if exist "%OUT%" (
  del /f /q "%OUT%" >nul 2>&1
  if exist "%OUT%" (
    echo.
    echo ERROR: Cannot delete or replace:
    echo   %OUT%
    echo.
    echo Fix: Close Internet Limiter, close any Explorer window showing that file,
    echo      then run this script again. Temporarily disable antivirus if it locks the exe.
    echo.
    exit /b 1
  )
)

python -m pip install -r requirements-build.txt --quiet
if errorlevel 1 (
  echo Failed to install dependencies.
  exit /b 1
)

python -m PyInstaller --noconfirm --clean ^
  --onefile --windowed --name InternetLimiter ^
  --uac-admin ^
  --collect-all customtkinter ^
  app_gui.py

if errorlevel 1 (
  echo Build failed.
  exit /b 1
)

echo.
echo Done. Executable: dist\InternetLimiter.exe
echo Copy that file anywhere and double-click to run (approve UAC when prompted).
endlocal
