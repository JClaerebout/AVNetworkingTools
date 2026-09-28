@echo off
setlocal
cd /d "%~dp0"

python -m unittest discover -s tests -q
if errorlevel 1 exit /b 1

for %%f in (tests\test*.js) do (
    node "%%f"
    if errorlevel 1 exit /b 1
)
for %%f in (static\*.js) do (
    node --check "%%f"
    if errorlevel 1 exit /b 1
)
echo All Python and JavaScript checks passed.
