import ctypes
import locale
import subprocess
import sys
from typing import List, Optional


def is_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False

DEFAULT_COMMAND_TIMEOUT = 10
POWERSHELL_TIMEOUT = 8


def run_cmd(command: List[str], timeout: Optional[float] = None) -> tuple[int, str, str]:
    """Run a command hidden in the background and return code, stdout, stderr."""
    try:
        startupinfo = None
        creationflags = 0

        if sys.platform == "win32":
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startupinfo.wShowWindow = 0
            creationflags = subprocess.CREATE_NO_WINDOW

        deadline = DEFAULT_COMMAND_TIMEOUT if timeout is None else timeout
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="oem" if sys.platform == "win32" else (locale.getpreferredencoding(False) or "utf-8"),
            errors="replace",
            shell=False,
            startupinfo=startupinfo,
            creationflags=creationflags,
            timeout=deadline,
        )
        return result.returncode, result.stdout.strip(), result.stderr.strip()
    except subprocess.TimeoutExpired:
        return 124, "", f"Command timed out after {deadline:g} seconds."
    except Exception as exc:
        return 1, "", str(exc)


def run_powershell(script: str, timeout: float = POWERSHELL_TIMEOUT) -> tuple[int, str, str]:
    powershell_path = r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"

    return run_cmd([
        powershell_path,
        "-NoLogo",
        "-NoProfile",
        "-NonInteractive",
        "-ExecutionPolicy",
        "Bypass",
        "-Command",
        script,
    ], timeout=timeout)
