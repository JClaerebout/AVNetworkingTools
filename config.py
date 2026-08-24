from pathlib import Path
import os
import sys


APP_NAME = "AVNetworkingTools"
SECRET_KEY = os.getenv("AVNETWORKINGTOOLS_SECRET_KEY", "local-avnetworkingtools-secret")
MANUFACTURER_ONLINE_FALLBACK = os.getenv(
    "AVNETWORKINGTOOLS_ONLINE_VENDOR_LOOKUP", "0"
).strip().lower() in {"1", "true", "yes", "on"}


def get_base_dir() -> Path:
    """
    Return a persistent writable folder for app data.

    Important for PyInstaller one-file EXE:
    __file__ can point to the temporary _MEI extraction folder, which is deleted
    when the app closes. So do not store history next to __file__ in EXE mode.

    History will be stored here:
    C:\\Users\\<user>\\AppData\\Roaming\\AVNetworkingTools\\
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


def get_downloads_dir() -> Path:
    """Return the current user's configured Downloads folder."""
    if os.name == "nt":
        try:
            import winreg

            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders",
            ) as key:
                configured_path, _ = winreg.QueryValueEx(
                    key,
                    "{374DE290-123F-4565-9164-39C4925E467B}",
                )
                return Path(os.path.expandvars(configured_path)).expanduser()
        except (ImportError, OSError, TypeError):
            pass

    return Path.home() / "Downloads"


DOWNLOADS_DIR = get_downloads_dir()
HISTORY_FILE = BASE_DIR / "nic_history.json"
PING_HISTORY_FILE = BASE_DIR / "ping_history.json"
CONNECTION_HISTORY_FILE = BASE_DIR / "connection_history.json"
MANUFACTURER_DB_FILE = BASE_DIR / "ieee_manufacturers.json"
