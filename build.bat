@echo off
setlocal

cd /d "%~dp0"

python -c "import sys; sys.exit(0 if sys.version_info[:3] == (3, 14, 3) and sys.maxsize > 2**32 else 1)"
if errorlevel 1 (
    echo Build requires 64-bit Python 3.14.3.
    exit /b 1
)

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
python -m pip install -r requirements-build.txt
if errorlevel 1 (
    echo.
    echo Failed to install requirements.
    exit /b 1
)

echo.
call check.bat
if errorlevel 1 (
    echo Checks failed. Build cancelled.
    exit /b 1
)

echo.
echo Building AVNetworkingTools V%APP_RELEASE_VERSION%...
set "APP_STAGE_DIR=dist\pending"
python -m PyInstaller "AVNetworkingTools.spec" --noconfirm --distpath "%APP_STAGE_DIR%"
if errorlevel 1 (
    echo.
    echo Build failed.
    exit /b 1
)

echo.
powershell -NoProfile -Command "Copy-Item -LiteralPath '%APP_STAGE_DIR%\AVNetworkingTools.exe' -Destination 'dist\AVNetworkingTools.exe' -Force -ErrorAction Stop" >nul 2>&1
if errorlevel 1 (
    echo Build complete: %APP_STAGE_DIR%\AVNetworkingTools.exe ^(V%APP_RELEASE_VERSION%^)
    echo Could not replace dist\AVNetworkingTools.exe ^(it may still be open^).
    echo Close the running app, then copy the staged EXE over it or run build.bat again.
    exit /b 0
)

echo Build complete: dist\AVNetworkingTools.exe ^(V%APP_RELEASE_VERSION%^)
