import ctypes
import subprocess
import sys
from typing import List


def is_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False

def run_cmd(command: List[str]) -> tuple[int, str, str]:
    """Run a command hidden in the background and return code, stdout, stderr."""
    try:
        startupinfo = None
        creationflags = 0

        if sys.platform == "win32":
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startupinfo.wShowWindow = 0
            creationflags = subprocess.CREATE_NO_WINDOW

        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            shell=False,
            startupinfo=startupinfo,
            creationflags=creationflags,
        )
        return result.returncode, result.stdout.strip(), result.stderr.strip()
    except Exception as exc:
        return 1, "", str(exc)


def run_powershell(script: str) -> tuple[int, str, str]:
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
    ])
