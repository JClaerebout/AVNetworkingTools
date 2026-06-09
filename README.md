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
history.py          Static-address history logic
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
python -m PyInstaller --onefile --windowed --uac-admin --add-data "templates;templates" --add-data "static;static" --hidden-import win32timezone --icon=NetworkManager.ico --name "Network Manager" app.py
```
