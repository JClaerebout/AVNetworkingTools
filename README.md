# AVNetworkingTools

Current release: **V1.2.0**

AVNetworkingTools — Network tools for AV commissioning.
A Windows toolbox for AV integrators and programmers to quickly configure network adapters, discover devices, test connections and troubleshoot systems during commissioning.

## Quick start

For a normal user, the workflow is simple:

1. Download the latest `AVNetworkingTools.exe` from the GitHub release.
2. Place it in a logical folder such as `C:\Tools\AVNetworkingTools` or a project folder you can find easily.
3. Double-click the EXE to launch it.
4. If Windows asks for elevation, allow it so the app can manage adapters and network diagnostics.

No Python install, virtual environment or source checkout is required for day-to-day use.

The application opens its own desktop window and runs locally at `http://127.0.0.1:49780`.

## Features

- View connected and disconnected network adapters.
- Switch adapters between DHCP and static IPv4 configurations.
- Reuse saved network configurations from local history.
- Run quick IP/MAC scans or complete scans with manufacturer and hostname data.
- Enrich an existing quick scan with **Lookup details** without rescanning.
- Resolve manufacturers immediately from the bundled IEEE MA-L, MA-M and MA-S database using longest-prefix matching.
- Refresh the local IEEE database weekly in the background while retaining the last valid copy when offline.
- Monitor discovered devices for missing hosts and duplicate-IP conflicts.
- Export IP-scan results to CSV and ping output to text.
- Run continuous ping tests with saved history.
- Build, save and run multi-device TCP, UDP, Telnet or SSH command scripts.
- Inspect Wi-Fi SSIDs, channels, signal levels, channel load and conflicts.
- Observe IGMP queriers and versions, measure traffic by multicast group, and flag likely multicast flooding against the selected interface's joined groups.
- Test TCP, UDP, SSH and serial connections using saved presets.
- Run local command-line diagnostics from the application.
- Check for, download and install integrity-checked GitHub release updates.

## IP scan and manufacturer lookup

**Start Scan** always performs a fresh subnet scan. With **Quick scan** enabled, the result contains IP and MAC information first. When that scan completes, **Lookup details** enriches the existing result without repeating the subnet sweep.

Manufacturer names come from `manufacturer_data/ieee_manufacturers.json` and normally require no per-device internet request. Hostname lookup runs separately with bounded DNS and NetBIOS timeouts. The optional online manufacturer fallback is disabled by default; enable it before startup only when required:

```powershell
$env:AVNETWORKINGTOOLS_ONLINE_VENDOR_LOOKUP="1"
AVNetworkingTools.exe
```

The weekly IEEE update runs in the background and stores its persistent copy in `%APPDATA%\AVNetworkingTools\ieee_manufacturers.json`.

---

## Advanced users

The sections below are for developers, maintainers and anyone building or debugging from source.

### Run from source

Open PowerShell or CMD as Administrator:

```bat
pip install -r requirements.txt
python app.py
```

### Run tests

```bat
python -m unittest discover -s tests
```

### Project structure

```text
app.py              Flask app factory and startup
routes.py           Web routes
nic_utils.py        Windows NIC scan/change logic
wifi_utils.py       Wi-Fi scan parsing, grouping, and channel analysis
multicast_utils.py  IGMP parsing, multicast rate measurement, and flooding diagnostics
scan_utils.py       LAN scan and device monitoring logic
manufacturer_db.py  Local IEEE MAC manufacturer database/update logic
ping_utils.py       Ping monitor logic
connection_utils.py TCP/UDP/SSH/serial connection testing
history.py          Static-address history logic
connection_history.py Connection preset/history logic
command_utils.py     Local command execution logic
system_utils.py     Admin check and command runners
update_utils.py      GitHub release update logic
config.py           Paths/config
manufacturer_data/  Bundled IEEE manufacturer database
templates/          HTML templates
static/             CSS and JavaScript
tests/              Unit tests
```

### Compile from source

Build with the included script:

```bat
build.bat
```

Or build directly with the current PyInstaller spec:

```bat
pyinstaller "AVNetworkingTools.spec"
```

Equivalent one-file command:

```bat
python -m PyInstaller --onefile --windowed --uac-admin --add-data "templates;templates" --add-data "static;static" --add-data "manufacturer_data;manufacturer_data" --hidden-import win32timezone --icon=AVNetworkingTools.ico --name "AVNetworkingTools" app.py
```

### Releases and automatic updates

The current application version is defined once in `version.py` and is displayed in the UI, used in update requests, and compared with GitHub release tags.

For this release, build with `APP_VERSION = "1.2.0"` and create a normal (non-draft, non-prerelease) GitHub release tagged `V1.2.0`. Attach the built EXE using this name:

- `AVNetworkingTools.exe`

The packaged app checks GitHub once when it starts. A newer semantic version is offered in the UI. The downloaded EXE must have the SHA-256 digest supplied by GitHub's release API before the running EXE will be replaced and restarted. The installer records its source path, target path, retries and errors in `%APPDATA%\AVNetworkingTools\update.log`.
