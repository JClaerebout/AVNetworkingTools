import binascii
import socket
import threading
from collections import deque
from datetime import datetime

try:
    import paramiko  # type: ignore
except Exception:  # Keep TCP/UDP/Telnet working if paramiko is not installed.
    paramiko = None

try:
    import serial
    from serial.tools import list_ports
except Exception:
    serial = None
    list_ports = None

_conn_lock = threading.RLock()
_conn_output = deque(maxlen=1000)
_session = None


class _Session:
    def __init__(self, protocol, target):
        self.protocol = protocol
        self.target = target
        self.state = "Connecting"
        self.message = f"Connecting to {target}..."
        self.stop = threading.Event()
        self.send_lock = threading.Lock()
        self.resources = []
        self.transport = None
        self.thread = None


def _own(session, resource):
    with _conn_lock:
        cancelled = session.stop.is_set()
        if not cancelled:
            session.resources.append(resource)
    if cancelled:
        resource.close()
        raise RuntimeError("Connection cancelled.")
    return resource


def _close_resources(session):
    with _conn_lock:
        resources, session.resources = session.resources, []
    for resource in reversed(resources):
        try:
            resource.close()
        except Exception:
            pass


def _finish(session, state, message):
    session.stop.set()
    with _conn_lock:
        session.state, session.message = state, message
        if _session is session:
            _conn_output.append(f"[{_stamp()}] {message}")
    _close_resources(session)


def _record(session, data, direction="RX", sent_as_hex=False):
    with _conn_lock:
        if _session is session and not session.stop.is_set():
            _conn_output.append({"time": _stamp(), "direction": direction,
                                 "ascii": _format_bytes(data), "hex": _format_bytes(data, True),
                                 "sent_as_hex": sent_as_hex})


def _stamp() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _format_bytes(data: bytes, show_hex: bool = False) -> str:
    if show_hex:
        return " ".join(f"{b:02X}" for b in data)

    return data.decode("utf-8", errors="replace")


def _parse_send_data(value: str, is_hex: bool, add_cr: bool, add_lf: bool) -> bytes:
    value = value or ""

    if is_hex:
        # Accept: \x50\x4F\x57\x0D, 50 4F 57 0D, 504F570D, 0x50 0x4F
        cleaned = value.replace("\\x", " ").replace("0x", " ").replace(",", " ")
        cleaned = " ".join(cleaned.split())
        if " " in cleaned:
            parts = cleaned.split()
            try:
                data = bytes(int(part, 16) for part in parts)
            except ValueError as exc:
                raise ValueError("Invalid HEX value. Example: \\x50\\x4F\\x57 or 50 4F 57") from exc
        else:
            hex_string = cleaned.strip()
            if len(hex_string) % 2:
                raise ValueError("HEX string must contain an even number of characters.")
            try:
                data = binascii.unhexlify(hex_string)
            except binascii.Error as exc:
                raise ValueError("Invalid HEX value. Example: \\x50\\x4F\\x57 or 50 4F 57") from exc
    else:
        # unicode_escape makes typed sequences like \x0D work in ASCII mode too.
        data = value.encode("utf-8").decode("unicode_escape").encode("latin-1", errors="replace")

    if add_cr:
        data += b"\r"
    if add_lf:
        data += b"\n"
    return data


def _reader(session):
    transport = session.transport
    try:
        while not session.stop.is_set():
            try:
                if session.protocol == "ssh":
                    if not transport.recv_ready():
                        if transport.closed or transport.eof_received:
                            _finish(session, "Disconnected", "SSH channel closed.")
                            return
                        session.stop.wait(0.05)
                        continue
                    data = transport.recv(4096)
                elif session.protocol == "rs232":
                    data = transport.read(4096)
                else:
                    data = transport.recv(65535 if session.protocol == "udp" else 4096)
                if data:
                    _record(session, data)
                elif session.protocol not in {"rs232", "udp"}:
                    _finish(session, "Disconnected", "Connection closed by remote host.")
                    return
            except (socket.timeout, TimeoutError):
                continue
    except Exception as exc:
        if not session.stop.is_set():
            _finish(session, "Failed", f"Receive failed: {exc}")
    finally:
        _close_resources(session)


def start_connection(protocol, host, port, username="", password="", baudrate="9600",
                     databits="8", parity="N", stopbits="1"):
    global _session
    protocol, host = str(protocol or "").strip().lower(), str(host or "").strip()
    if protocol not in {"tcp", "udp", "telnet", "ssh", "rs232"}:
        return False, "Select TCP, UDP, Telnet, SSH or RS232."
    if not host:
        return False, "Serial port is required." if protocol == "rs232" else "IP/host is required."
    try:
        if protocol == "rs232":
            if serial is None:
                return False, "RS232 requires pyserial."
            serial_options = dict(baudrate=int(baudrate), bytesize=int(databits), parity=parity,
                                  stopbits=float(stopbits), timeout=0.25, write_timeout=2)
        else:
            port = int(port)
            if not 1 <= port <= 65535:
                raise ValueError()
    except (ValueError, TypeError):
        return False, "Invalid serial settings or port (1-65535)."
    if protocol == "ssh" and (paramiko is None or not username):
        return False, "SSH requires paramiko and a username."
    session = _Session(protocol, host if protocol == "rs232" else f"{host}:{port}")
    with _conn_lock:
        if _session and _session.state in {"Connecting", "Connected", "Stopping"}:
            return False, "A connection is already active. Stop it first."
        _session = session
        _conn_output.clear()
    try:
        if protocol in {"tcp", "telnet"}:
            transport = _own(session, socket.create_connection((host, port), timeout=5))
            transport.settimeout(0.25)
        elif protocol == "udp":
            transport = _own(session, socket.socket(socket.AF_INET, socket.SOCK_DGRAM))
            transport.settimeout(0.25)
            transport.connect((host, port))
        elif protocol == "rs232":
            transport = _own(session, serial.Serial(port=host, **serial_options))
        else:
            client = _own(session, paramiko.SSHClient())
            client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            client.connect(hostname=host, port=port, username=username, password=password or None,
                           timeout=7, banner_timeout=7, auth_timeout=7, channel_timeout=7,
                           look_for_keys=True, allow_agent=True)
            transport = _own(session, client.invoke_shell())
            transport.settimeout(2)
        with _conn_lock:
            if session.stop.is_set() or _session is not session:
                raise RuntimeError("Connection cancelled.")
            session.transport = transport
            session.state = "Connected"
            session.message = f"{protocol.upper()} {'socket open for' if protocol == 'udp' else 'connected to'} {session.target}"
            _conn_output.append(f"[{_stamp()}] {session.message}")
            session.thread = threading.Thread(target=_reader, args=(session,), daemon=True)
            session.thread.start()
        return True, "Connection opened."
    except Exception as exc:
        cancelled = session.stop.is_set()
        message = "Connection cancelled." if cancelled else f"Could not open {protocol.upper()} connection: {exc}"
        _finish(session, "Disconnected" if cancelled else "Failed", message)
        return False, message


def send_data(value, is_hex=False, add_cr=False, add_lf=False):
    with _conn_lock:
        session = _session
        if not session or session.state != "Connected":
            return False, "No active connection."
    try:
        data = _parse_send_data(value, is_hex, add_cr, add_lf)
    except (ValueError, TypeError, AttributeError) as exc:
        return False, str(exc)
    if not data:
        return False, "Nothing to send."
    try:
        with session.send_lock:
            if session.stop.is_set():
                return False, "Connection closed."
            if session.protocol == "rs232":
                written = session.transport.write(data)
                if written != len(data):
                    raise OSError(f"Only {written} of {len(data)} bytes written")
            else:
                session.transport.sendall(data)
            if session.stop.is_set():
                return False, "Connection closed during send; delivery is unconfirmed."
            _record(session, data, "TX", is_hex)
        return True, "Data sent."
    except Exception as exc:
        message = f"Send failed; delivery may be partial: {exc}"
        _finish(session, "Failed", message)
        return False, message


def stop_connection():
    with _conn_lock:
        session = _session
        if not session or session.state not in {"Connecting", "Connected", "Stopping"}:
            return False, "No active connection."
        session.state = "Stopping"
        session.stop.set()
    _close_resources(session)
    if session.thread and session.thread is not threading.current_thread():
        session.thread.join(timeout=1)
    _finish(session, "Disconnected", f"Disconnected from {session.target}.")
    return True, "Connection closed."


def get_connection_status():
    with _conn_lock:
        session = _session
        state = session.state if session else "Disconnected"
        return {"running": state in {"Connecting", "Connected", "Stopping"},
                "connected": state == "Connected", "state": state,
                "protocol": session.protocol if session else "",
                "target": session.target if session else "",
                "status_text": session.message if session else "Disconnected",
                "output": list(_conn_output)}


def get_connection_activity() -> str:
    with _conn_lock:
        return _session.state if _session else "Disconnected"


def get_serial_ports() -> list[dict]:
    if list_ports is None:
        return []

    ports = []
    for port in list_ports.comports():
        ports.append({
            "device": port.device,
            "description": port.description,
            "hwid": port.hwid,
        })

    return ports
