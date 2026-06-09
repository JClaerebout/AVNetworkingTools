from pathlib import Path
import os
import sys


APP_NAME = "Windows NIC Manager"
SECRET_KEY = os.getenv("NETWORK_MANAGER_SECRET_KEY", "local-network-manager-secret")


def get_base_dir() -> Path:
    """
    Return a persistent writable folder for app data.

    Important for PyInstaller one-file EXE:
    __file__ can point to the temporary _MEI extraction folder, which is deleted
    when the app closes. So do not store history next to __file__ in EXE mode.

    History will be stored here:
    C:\\Users\\<user>\\AppData\\Roaming\\Windows NIC Manager\\
    """
    appdata = os.getenv("APPDATA")

    if appdata:
        base_dir = Path(appdata) / APP_NAME
    elif getattr(sys, "frozen", False):
        base_dir = Path(sys.executable).resolve().parent / "data"
    else:
        base_dir = Path(__file__).resolve().parent

    base_dir.mkdir(parents=True, exist_ok=True)
    return base_dir


BASE_DIR = get_base_dir()
HISTORY_FILE = BASE_DIR / "nic_history.json"
PING_HISTORY_FILE = BASE_DIR / "ping_history.json"
CONNECTION_HISTORY_FILE = BASE_DIR / "connection_history.json"
