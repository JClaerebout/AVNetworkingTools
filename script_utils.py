import socket
import threading
import time
import re
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

from connection_utils import _format_bytes, _parse_send_data

try:
    import paramiko  # type: ignore
except Exception:
    paramiko = None


_lock = threading.Lock()
_stop = threading.Event()
_pause = threading.Event()
_thread = None
FINAL_RESPONSE_TIMEOUT = 3.0
FINAL_RESPONSE_QUIET_SECONDS = 0.5
SSH_PROMPT_TIMEOUT = 3.0
_state = {
    "running": False,
    "paused": False,
    "status_text": "Idle",
    "current_block": None,
    "output": deque(maxlen=1500),
}


def _stamp():
    return datetime.now().strftime("%H:%M:%S")


def _log(message, level="info"):
    with _lock:
        _state["output"].append({"time": _stamp(), "level": level, "message": str(message)})


def _set_state(**values):
    with _lock:
        _state.update(values)


def _wait(seconds):
    remaining = max(0, seconds)
    while remaining > 0 or _pause.is_set():
        if _stop.is_set():
            return False
        while _pause.is_set() and not _stop.is_set():
            _stop.wait(0.1)
        started = time.monotonic()
        _stop.wait(min(0.1, remaining))
        remaining -= time.monotonic() - started
    return not _stop.is_set()


class _TargetConnection:
    def __init__(self, host, protocol, port, username="", password=""):
        self.host = host
        self.protocol = protocol
        self.port = port
        self.username = username
        self.password = password
        self.socket = None
        self.ssh_client = None
        self.ssh_channel = None
        self.closed = threading.Event()
        self.command_sent = threading.Event()
        self.response_received = threading.Event()
        self.response_complete = threading.Event()
        self.response_lock = threading.Lock()
        self.response_buffer = bytearray()
        self.last_response_at = 0.0
        self.reader_thread = None

    @property
    def label(self):
        return f"{self.host}:{self.port}"

    def connect(self):
        if self.protocol in {"tcp", "telnet"}:
            self.socket = socket.create_connection((self.host, self.port), timeout=5)
            self.socket.settimeout(0.25)
        elif self.protocol == "udp":
            self.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self.socket.settimeout(0.25)
            self.socket.connect((self.host, self.port))
        elif self.protocol == "ssh":
            if paramiko is None:
                raise RuntimeError("SSH requires paramiko")
            client = paramiko.SSHClient()
            client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            client.connect(
                hostname=self.host, port=self.port, username=self.username,
                password=self.password or None, timeout=7,
                look_for_keys=True, allow_agent=True,
            )
            self.ssh_client = client
            self.ssh_channel = client.invoke_shell()
        else:
            raise ValueError(f"Unsupported protocol: {self.protocol}")
        self.reader_thread = threading.Thread(target=self._reader, daemon=True)
        self.reader_thread.start()

    def send(self, payload):
        with self.response_lock:
            self.response_buffer.clear()
            self.response_received.clear()
            self.response_complete.clear()
            self.command_sent.set()
        if self.protocol == "ssh":
            self.ssh_channel.sendall(payload)
        else:
            self.socket.sendall(payload)

    def _reader(self):
        while not self.closed.is_set() and not _stop.is_set():
            try:
                if self.protocol == "ssh":
                    if not self.ssh_channel.recv_ready():
                        self.closed.wait(0.03)
                        continue
                    data = self.ssh_channel.recv(4096)
                else:
                    data = self.socket.recv(4096)
                if not data:
                    break
                _log(f"RX {self.label}: {_format_bytes(data)}", "rx")
                with self.response_lock:
                    self.last_response_at = time.monotonic()
                    self.response_buffer.extend(data)
                    if len(self.response_buffer) > 16384:
                        del self.response_buffer[:-16384]
                    self.response_received.set()
                    if self.protocol == "ssh" and _ends_with_shell_prompt(self.response_buffer):
                        self.response_complete.set()
            except (socket.timeout, TimeoutError):
                continue
            except Exception as exc:
                if not self.closed.is_set() and not _stop.is_set():
                    _log(f"RX failed for {self.label}: {exc}", "error")
                break

    def close(self):
        self.closed.set()
        for resource in (self.ssh_channel, self.ssh_client, self.socket):
            try:
                if resource:
                    resource.close()
            except Exception:
                pass
        if self.reader_thread and self.reader_thread is not threading.current_thread():
            self.reader_thread.join(timeout=0.5)

    def wait_for_ssh_prompt(self):
        if self.protocol != "ssh":
            return
        _log(f"Waiting for SSH shell prompt from {self.label}.")
        deadline = time.monotonic() + SSH_PROMPT_TIMEOUT
        while not self.response_complete.is_set() and time.monotonic() < deadline and not _stop.is_set():
            _stop.wait(0.05)


def _ends_with_shell_prompt(data):
    return bool(re.search(rb"(?:^|[\r\n])[^\r\n]*[>#$]\s*$", bytes(data)))


def _wait_for_final_responses(connections):
    awaiting = [
        connection for connection in connections
        if connection.command_sent.is_set()
    ]
    if not awaiting or _stop.is_set():
        return
    _log("Waiting for final response(s).")
    deadline = time.monotonic() + FINAL_RESPONSE_TIMEOUT
    while time.monotonic() < deadline and not _stop.is_set():
        while _pause.is_set() and not _stop.is_set():
            _stop.wait(0.1)
        now = time.monotonic()
        incomplete = []
        for connection in awaiting:
            if not connection.response_received.is_set():
                incomplete.append(connection)
            elif connection.protocol == "ssh" and not connection.response_complete.is_set():
                incomplete.append(connection)
            elif connection.protocol != "ssh" and now - connection.last_response_at < FINAL_RESPONSE_QUIET_SECONDS:
                incomplete.append(connection)
        if not incomplete:
            return
        _stop.wait(0.05)


def _normalize_blocks(raw_blocks):
    if not isinstance(raw_blocks, list) or not raw_blocks:
        raise ValueError("Add at least one block before running the script.")
    if len(raw_blocks) > 200:
        raise ValueError("A script can contain at most 200 blocks.")

    blocks = []
    has_target = False
    for raw in raw_blocks:
        if not isinstance(raw, dict):
            raise ValueError("Invalid script block.")
        kind = str(raw.get("type", "")).lower()
        if kind == "target":
            targets = [line.strip() for line in str(raw.get("targets", "")).replace(",", "\n").splitlines() if line.strip()]
            targets = list(dict.fromkeys(targets))
            if not targets:
                raise ValueError("Every Target block needs at least one IP or hostname.")
            if len(targets) > 100:
                raise ValueError("A Target block can contain at most 100 devices.")
            protocol = str(raw.get("protocol", "tcp")).lower()
            if protocol not in {"tcp", "udp", "telnet", "ssh"}:
                raise ValueError("Target protocol must be TCP, UDP, Telnet or SSH.")
            try:
                port = int(raw.get("port", ""))
                if not 1 <= port <= 65535:
                    raise ValueError
            except (TypeError, ValueError):
                raise ValueError("Target port must be between 1 and 65535.")
            mode = str(raw.get("mode", "sequential")).lower()
            if mode not in {"sequential", "parallel"}:
                mode = "sequential"
            try:
                device_delay = max(0, min(float(raw.get("device_delay", 0)), 3600))
            except (TypeError, ValueError):
                raise ValueError("Between-device delay must be a number.")
            blocks.append({
                "type": kind, "targets": targets, "protocol": protocol, "port": port,
                "mode": mode, "device_delay": device_delay,
                "username": str(raw.get("username", "")), "password": str(raw.get("password", "")),
            })
            has_target = True
        elif kind == "command":
            if not has_target:
                raise ValueError("Place a Target block before the first Command block.")
            value = str(raw.get("value", ""))
            if not value:
                raise ValueError("Command blocks cannot be empty.")
            blocks.append({
                "type": kind, "value": value, "is_hex": bool(raw.get("is_hex")),
                "add_cr": bool(raw.get("add_cr")), "add_lf": bool(raw.get("add_lf")),
            })
        elif kind == "delay":
            try:
                duration = float(raw.get("duration", 0))
                if not 0 <= duration <= 3600:
                    raise ValueError
            except (TypeError, ValueError):
                raise ValueError("Delay must be between 0 and 3600 seconds.")
            blocks.append({"type": kind, "duration": duration})
        else:
            raise ValueError("Unknown script block type.")
    return blocks


def _connect_one(target, block):
    connection = _TargetConnection(
        target, block["protocol"], block["port"], block["username"], block["password"]
    )
    try:
        connection.connect()
        connection.wait_for_ssh_prompt()
        _log(f"Connected {connection.label} using {block['protocol'].upper()}.", "success")
        return connection
    except Exception as exc:
        connection.close()
        _log(f"Connection failed for {connection.label}: {exc}", "error")
        return None


def _run_script(blocks):
    connections = []
    target_settings = None
    completed = False
    try:
        _log("Script started.")
        for index, block in enumerate(blocks):
            if not _wait(0):
                break
            _set_state(current_block=index)
            if block["type"] == "target":
                _wait_for_final_responses(connections)
                for connection in connections:
                    connection.close()
                connections = []
                target_settings = block
                _log(f"Target group: {len(block['targets'])} device(s), {block['mode']}.")
                if block["mode"] == "parallel":
                    with ThreadPoolExecutor(max_workers=min(16, len(block["targets"]))) as pool:
                        connections = [item for item in pool.map(lambda host: _connect_one(host, block), block["targets"]) if item]
                else:
                    for target in block["targets"]:
                        if _stop.is_set():
                            break
                        connection = _connect_one(target, block)
                        if connection:
                            connections.append(connection)
                if not connections and not _stop.is_set():
                    _log("No targets in this group could be reached.", "error")
            elif block["type"] == "delay":
                _log(f"Waiting {block['duration']:g} second(s).")
                if not _wait(block["duration"]):
                    break
            elif block["type"] == "command":
                try:
                    payload = _parse_send_data(block["value"], block["is_hex"], block["add_cr"], block["add_lf"])
                except ValueError as exc:
                    _log(f"Invalid command: {exc}", "error")
                    continue
                display = _format_bytes(payload, block["is_hex"])

                def send_one(connection):
                    try:
                        connection.send(payload)
                        _log(f"TX {connection.label}: {display}", "tx")
                    except Exception as exc:
                        _log(f"Send failed for {connection.label}: {exc}", "error")

                if target_settings and target_settings["mode"] == "parallel":
                    with ThreadPoolExecutor(max_workers=min(16, max(1, len(connections)))) as pool:
                        list(pool.map(send_one, connections))
                else:
                    for connection_index, connection in enumerate(connections):
                        if _stop.is_set():
                            break
                        send_one(connection)
                        if connection_index < len(connections) - 1 and not _wait(target_settings["device_delay"]):
                            break
        completed = not _stop.is_set()
    except Exception as exc:
        _log(f"Script error: {exc}", "error")
    finally:
        _wait_for_final_responses(connections)
        for connection in connections:
            connection.close()
        if completed:
            _log("Script completed.", "success")
            status = "Completed"
        else:
            _log("Script stopped.", "warning")
            status = "Stopped"
        _pause.clear()
        _set_state(running=False, paused=False, status_text=status, current_block=None)


def start_script(raw_blocks):
    global _thread
    try:
        blocks = _normalize_blocks(raw_blocks)
    except ValueError as exc:
        return False, str(exc)
    with _lock:
        if _state["running"]:
            return False, "A script is already running."
        _state["output"].clear()
        _state.update(running=True, paused=False, status_text="Running", current_block=None)
    _stop.clear()
    _pause.clear()
    _thread = threading.Thread(target=_run_script, args=(blocks,), daemon=True)
    _thread.start()
    return True, "Script started."


def set_script_paused(paused):
    with _lock:
        if not _state["running"]:
            return False, "No script is running."
    if paused:
        _pause.set()
        _set_state(paused=True, status_text="Paused")
        _log("Script paused.", "warning")
    else:
        _pause.clear()
        _set_state(paused=False, status_text="Running")
        _log("Script resumed.")
    return True, "Script paused." if paused else "Script resumed."


def stop_script():
    with _lock:
        if not _state["running"]:
            return False, "No script is running."
        _state["status_text"] = "Stopping"
    _stop.set()
    _pause.clear()
    return True, "Stopping script."


def get_script_status():
    with _lock:
        return {
            "running": _state["running"],
            "paused": _state["paused"],
            "status_text": _state["status_text"],
            "current_block": _state["current_block"],
            "output": list(_state["output"]),
        }
