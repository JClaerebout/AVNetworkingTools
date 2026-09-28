# Windows build and checks

Use 64-bit Python 3.14.3 and Node.js. `check.bat` runs the Python and JavaScript checks in one command. `build.bat` installs the exact dependency versions in `requirements-build.txt`, runs those checks, then builds with the sole PyInstaller spec, `AVNetworkingTools.spec`.

After the build, run `powershell -ExecutionPolicy Bypass -File .\smoke-packaged.ps1`. The EXE requests administrator access. This headless packaged check starts its real local server, visits the home, IP Scan, Connect and Health Check pages, checks that cancelling Save As writes nothing, and closes the server. It writes `dist\packaged-smoke.json`.

If the app is open and Windows locks `dist\AVNetworkingTools.exe`, `build.bat` leaves the new build at `dist\pending\AVNetworkingTools.exe`. Test that build with `powershell -ExecutionPolicy Bypass -File .\smoke-packaged.ps1 -Executable 'dist\pending\AVNetworkingTools.exe'`.

Finally, open the EXE normally and check the Connect suggestion picker with scanned manufacturer and hostname data, its alternative profiles, editable protocol/port, and learned-setting priority. The headless check does not inspect the rendered desktop window.
