import locale
import os
import subprocess
from pathlib import Path


COMMAND_TIMEOUT_SECONDS = 120


def _output_encoding() -> str:
    return "oem" if os.name == "nt" else (locale.getpreferredencoding(False) or "utf-8")


def run_command(command: str, working_directory: str = "") -> dict:
    command = str(command or "").strip()
    if not command:
        return {
            "success": False,
            "message": "Enter a command first.",
            "output": "",
            "exit_code": None,
        }

    try:
        cwd = Path(working_directory).expanduser() if working_directory else Path.cwd()
        cwd = cwd.resolve()
    except (OSError, RuntimeError) as exc:
        return {
            "success": False,
            "message": f"Invalid working directory: {exc}",
            "output": "",
            "exit_code": None,
        }

    if not cwd.is_dir():
        return {
            "success": False,
            "message": f"Working directory does not exist: {cwd}",
            "output": "",
            "exit_code": None,
        }

    shell = os.environ.get("COMSPEC", "cmd.exe")
    creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)

    try:
        completed = subprocess.run(
            [shell, "/d", "/s", "/c", command],
            cwd=str(cwd),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=COMMAND_TIMEOUT_SECONDS,
            check=False,
            creationflags=creation_flags,
        )
        output = completed.stdout.decode(_output_encoding(), errors="replace")
        return {
            "success": completed.returncode == 0,
            "message": "Command completed." if completed.returncode == 0 else "Command failed.",
            "output": output,
            "exit_code": completed.returncode,
            "working_directory": str(cwd),
        }
    except subprocess.TimeoutExpired as exc:
        partial_output = exc.stdout or b""
        if isinstance(partial_output, bytes):
            partial_output = partial_output.decode(_output_encoding(), errors="replace")
        return {
            "success": False,
            "message": f"Command timed out after {COMMAND_TIMEOUT_SECONDS} seconds.",
            "output": partial_output,
            "exit_code": None,
            "working_directory": str(cwd),
        }
    except OSError as exc:
        return {
            "success": False,
            "message": f"Could not start command: {exc}",
            "output": "",
            "exit_code": None,
            "working_directory": str(cwd),
        }
