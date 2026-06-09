@echo off
setlocal

cd /d "%~dp0"

echo Installing build dependencies...
python -m pip install -r requirements.txt
if errorlevel 1 (
    echo.
    echo Failed to install requirements.
    exit /b 1
)

echo.
echo Building Network Manager...
python -m PyInstaller "Network Manager.spec" --noconfirm
if errorlevel 1 (
    echo.
    echo Build failed.
    exit /b 1
)

echo.
echo Build complete: dist\Network Manager.exe
