# Network Manager

Windows desktop network utility for viewing and changing NIC IPv4 settings,
running LAN scans, monitoring ping status, testing TCP/UDP/SSH/serial
connections, and checking Wi-Fi channel usage.

## Run

Open PowerShell or CMD as Administrator:

```bat
pip install -r requirements.txt
python app.py
```

The app opens its own desktop window. The local server is bound to
`http://127.0.0.1:5050`.

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
ping_utils.py       Ping monitor logic
connection_utils.py TCP/UDP/SSH/serial connection testing
history.py          Static-address history logic
connection_history.py Connection preset/history logic
system_utils.py     Admin check and command runners
config.py           Paths/config
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
pyinstaller "Network Manager.spec"
```

Equivalent one-file command:

```bat
python -m PyInstaller --onefile --windowed --uac-admin --add-data "templates;templates" --add-data "static;static" --hidden-import win32timezone --icon=NetworkManager.ico --name "NetworkManager" app.py
```

## Releases and automatic updates

Before building a release, update `APP_VERSION` in `version.py`. Create a normal
(non-draft, non-prerelease) GitHub release with a matching version tag such as
`V1.1.0`, then attach the built EXE using this name:

- `NetworkManager.exe`

The updater also recognizes the legacy `Network.Manager.exe` and
`Network Manager.exe` asset names.

The packaged app checks GitHub once when it starts. A newer semantic version is
offered in the UI. The downloaded EXE must have the SHA-256 digest supplied by
GitHub's release API before the running EXE will be replaced and restarted.
