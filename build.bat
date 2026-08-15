@echo off
setlocal

cd /d "%~dp0"

if not exist "manufacturer_data\ieee_manufacturers.json" (
    echo Missing bundled manufacturer database:
    echo manufacturer_data\ieee_manufacturers.json
    exit /b 1
)

for /f %%v in ('python -c "from version import APP_VERSION; print(APP_VERSION)"') do set "APP_RELEASE_VERSION=%%v"
if not defined APP_RELEASE_VERSION (
    echo Could not read APP_VERSION from version.py.
    exit /b 1
)

echo Installing build dependencies...
python -m pip install -r requirements.txt
if errorlevel 1 (
    echo.
    echo Failed to install requirements.
    exit /b 1
)

echo.
echo Building Network Manager V%APP_RELEASE_VERSION%...
python -m PyInstaller "Network Manager.spec" --noconfirm
if errorlevel 1 (
    echo.
    echo Build failed.
    exit /b 1
)

echo.
echo Build complete: dist\NetworkManager.exe ^(V%APP_RELEASE_VERSION%^)
