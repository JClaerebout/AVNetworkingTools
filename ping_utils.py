import ipaddress
import json
import re
import subprocess
import sys
import threading
from collections import deque
from datetime import datetime
from typing import Dict, List, Optional

from config import PING_HISTORY_FILE

_ping_lock = threading.Lock()
_ping_process: Optional[subprocess.Popen] = None
_ping_thread: Optional[threading.Thread] = None
_ping_target: str = ""
_ping_output = deque(maxlen=500)


def load_ping_history() -> List[str]:
    if not PING_HISTORY_FILE.exists():
        return []
    try:
        data = json.loads(PING_HISTORY_FILE.read_text(encoding="utf-8"))
        if isinstance(data, list):
            return [str(item) for item in data if str(item).strip()]
    except Exception:
        pass
    return []


def save_ping_history_entry(ip: str) -> None:
    history = [item for item in load_ping_history() if item != ip]
    history.insert(0, ip)
    history = history[:30]
    PING_HISTORY_FILE.write_text(json.dumps(history, indent=2), encoding="utf-8")


_HOSTNAME_LABEL_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?$")


def validate_target(value: str) -> tuple[bool, str]:
    value = value.strip()
    if not value:
        return False, "IP address or hostname is required."

    try:
        ipaddress.ip_address(value)
        return True, value
    except ValueError:
        pass

    # A final dot is valid DNS notation, but omit it from the ping command and
    # history so equivalent hostnames are stored consistently.
    hostname = value[:-1] if value.endswith(".") else value
    labels = hostname.split(".")
    if len(hostname) <= 253 and all(_HOSTNAME_LABEL_RE.fullmatch(label) for label in labels):
        return True, hostname

    return False, "Invalid IP address or hostname. Example: 192.168.1.1 or switch.local"


def validate_ip(value: str) -> tuple[bool, str]:
    """Backward-compatible alias for callers that used the old validator name."""
    return validate_target(value)


def _ping_command(ip: str) -> List[str]:
    if sys.platform == "win32":
        return ["ping", "-t", ip]
    return ["ping", ip]


def _reader_thread(process: subprocess.Popen, target: str) -> None:
    _append_output(f"Started continuous ping to {target} at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    try:
        assert process.stdout is not None
        for line in iter(process.stdout.readline, ""):
            if not line:
                break
            _append_output(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {line.rstrip()}")
    except Exception as exc:
        _append_output(f"Ping reader error: {exc}")
    finally:
        _append_output(f"Ping stopped at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")


def _append_output(line: str) -> None:
    with _ping_lock:
        _ping_output.append(line)


def start_ping(ip: str) -> tuple[bool, str]:
    global _ping_process, _ping_thread, _ping_target

    valid, value_or_error = validate_target(ip)
    if not valid:
        return False, value_or_error

    ip = value_or_error

    with _ping_lock:
        running = _ping_process is not None and _ping_process.poll() is None
        if running:
            return False, f"A ping to {_ping_target} is already running. Stop it first."

        _ping_output.clear()

        startupinfo = None
        creationflags = 0
        if sys.platform == "win32":
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startupinfo.wShowWindow = 0
            creationflags = subprocess.CREATE_NO_WINDOW

        try:
            _ping_process = subprocess.Popen(
                _ping_command(ip),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                universal_newlines=True,
                startupinfo=startupinfo,
                creationflags=creationflags,
            )
            _ping_target = ip
        except Exception as exc:
            _ping_process = None
            _ping_target = ""
            return False, f"Could not start ping: {exc}"

        _ping_thread = threading.Thread(target=_reader_thread, args=(_ping_process, ip), daemon=True)
        _ping_thread.start()

    save_ping_history_entry(ip)
    return True, f"Started ping to {ip}."


def stop_ping() -> tuple[bool, str]:
    global _ping_process, _ping_target

    with _ping_lock:
        process = _ping_process
        target = _ping_target

    if process is None or process.poll() is not None:
        return False, "No ping is running."

    try:
        process.terminate()
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            process.kill()
    except Exception as exc:
        return False, f"Could not stop ping: {exc}"

    with _ping_lock:
        _ping_process = None
        _ping_target = ""

    return True, f"Stopped ping to {target}."


def get_ping_status() -> Dict:
    with _ping_lock:
        running = _ping_process is not None and _ping_process.poll() is None
        return {
            "running": running,
            "target": _ping_target if running else "",
            "output": list(_ping_output),
            "history": load_ping_history(),
        }
