# AVNetKit

Current release: **V1.2.0**

Windows desktop network utility for configuring adapters, discovering devices,
monitoring network availability, testing connections, and inspecting Wi-Fi
channel usage.

## Features

- View connected and disconnected network adapters.
- Switch adapters between DHCP and static IPv4 configurations.
- Reuse saved network configurations from local history.
- Run quick IP/MAC scans or complete scans with manufacturer and hostname data.
- Enrich an existing quick scan with **Lookup details** without rescanning.
- Resolve manufacturers immediately from the bundled IEEE MA-L, MA-M and MA-S
  database using longest-prefix matching.
- Refresh the local IEEE database weekly in the background while retaining the
  last valid copy when offline.
- Monitor discovered devices for missing hosts and duplicate-IP conflicts.
- Export IP-scan results to CSV and ping output to text.
- Run continuous ping tests with saved history.
- Inspect Wi-Fi SSIDs, channels, signal levels, channel load and conflicts.
- Test TCP, UDP, SSH and serial connections using saved presets.
- Run local command-line diagnostics from the application.
- Check for, download and install integrity-checked GitHub release updates.

## Run

Open PowerShell or CMD as Administrator:

```bat
pip install -r requirements.txt
python app.py
```

The app opens its own desktop window. The local server is bound to
`http://127.0.0.1:5050`.

## IP scan and manufacturer lookup

**Start Scan** always performs a fresh subnet scan. With **Quick scan** enabled,
the result contains IP and MAC information first. When that scan completes,
**Lookup details** enriches the existing result without repeating the subnet
sweep.

Manufacturer names come from `manufacturer_data/ieee_manufacturers.json` and
normally require no per-device internet request. Hostname lookup runs separately
with bounded DNS and NetBIOS timeouts. The optional online manufacturer fallback
is disabled by default; enable it before startup only when required:

```powershell
$env:AVNETKIT_ONLINE_VENDOR_LOOKUP="1"
python app.py
```

The weekly IEEE update runs in the background and stores its persistent copy in
`%APPDATA%\AVNetKit\ieee_manufacturers.json`.

## Run tests

```bat
python -m unittest discover -s tests
```

## Structure

```text
app.py              Flask app factory and startup
routes.py           Web routes
nic_utils.py        Windows NIC scan/change logic
wifi_utils.py       Wi-Fi scan parsing, grouping, and channel analysis
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

## Compile

Build with the included script:

```bat
build.bat
```

Or build directly with the current PyInstaller spec:

```bat
pyinstaller "AVNetKit.spec"
```

Equivalent one-file command:

```bat
python -m PyInstaller --onefile --windowed --uac-admin --add-data "templates;templates" --add-data "static;static" --add-data "manufacturer_data;manufacturer_data" --hidden-import win32timezone --icon=AVNetKit.ico --name "AVNetKit" app.py
```

## Releases and automatic updates

The current application version is defined once in `version.py` and is displayed
in the UI, used in update requests, and compared with GitHub release tags.

For this release, build with `APP_VERSION = "1.2.0"` and create a normal
(non-draft, non-prerelease) GitHub release tagged `V1.2.0`. Attach the built EXE
using this name:

- `AVNetKit.exe`

The packaged app checks GitHub once when it starts. A newer semantic version is
offered in the UI. The downloaded EXE must have the SHA-256 digest supplied by
GitHub's release API before the running EXE will be replaced and restarted.
The installer records its source path, target path, retries and errors in
`%APPDATA%\AVNetKit\update.log`.
