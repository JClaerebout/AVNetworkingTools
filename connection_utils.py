import binascii
import socket
import threading
from collections import deque
from datetime import datetime
from typing import Dict, Optional

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

_conn_lock = threading.Lock()
_conn_stop = threading.Event()
_conn_socket: Optional[socket.socket] = None
_conn_ssh_client = None
_conn_ssh_channel = None
_conn_thread: Optional[threading.Thread] = None
_conn_running = False
_conn_protocol = ""
_conn_target = ""
_conn_output = deque(maxlen=1000)
_conn_serial = None
_conn_status_text = ""

def _append(line: str) -> None:
    with _conn_lock:
        _conn_output.append(line)


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


def _tcp_reader(sock: socket.socket) -> None:
    while not _conn_stop.is_set():
        try:
            data = sock.recv(4096)
            if not data:
                _mark_connection_closed("Connection closed by remote host.")
                break
            _append({
                "time": _stamp(),
                "direction": "RX",
                "ascii": _format_bytes(data, False),
                "hex": _format_bytes(data, True),
            })
        except socket.timeout:
            continue
        except OSError:
            break
        except Exception as exc:
            _append(f"[{_stamp()}] RX error: {exc}")
            break


def _udp_reader(sock: socket.socket) -> None:
    while not _conn_stop.is_set():
        try:
            data = sock.recv(4096)
            if data:
                _append({
                    "time": _stamp(),
                    "direction": "RX",
                    "ascii": _format_bytes(data, False),
                    "hex": _format_bytes(data, True),
                })
        except socket.timeout:
            continue
        except OSError:
            break
        except Exception as exc:
            _append(f"[{_stamp()}] UDP RX error: {exc}")
            break


def _ssh_reader(channel) -> None:
    while not _conn_stop.is_set():
        try:
            if channel.recv_ready():
                data = channel.recv(4096)
                if data:
                    _append({
                        "time": _stamp(),
                        "direction": "RX",
                        "ascii": _format_bytes(data, False),
                        "hex": _format_bytes(data, True),
                    })
                continue
            if channel.closed:
                _mark_connection_closed("SSH channel closed.")
                break
            _conn_stop.wait(0.1)
        except Exception as exc:
            _append(f"[{_stamp()}] SSH RX error: {exc}")
            break

def _serial_reader(ser) -> None:
    while not _conn_stop.is_set():
        try:
            data = ser.read(4096)
            if data:
                _append(f"[{_stamp()}] RX\n{_format_bytes(data)}")
        except Exception as exc:
            _mark_connection_closed(f"RS232 RX error: {exc}")
            break

def start_connection(
    protocol: str,
    host: str,
    port: str,
    username: str = "",
    password: str = "",
    baudrate: str = "9600",
    databits: str = "8",
    parity: str = "N",
    stopbits: str = "1",
) -> tuple[bool, str]:
    global _conn_socket, _conn_thread, _conn_running, _conn_protocol, _conn_target, _conn_ssh_client, _conn_ssh_channel, _conn_serial, _conn_status_text

    protocol = (protocol or "").strip().lower()
    host = (host or "").strip()

    if protocol not in {"tcp", "udp", "telnet", "ssh", "rs232"}:
        return False, "Select TCP, UDP, Telnet, SSH or RS232."
    if protocol == "rs232":
        if not host:
            return False, "COM port is required."
    else:
        if not host:
            return False, "IP/host is required."

    port_int = None

    if protocol != "rs232":
        try:
            port_int = int(port)
            if port_int < 1 or port_int > 65535:
                raise ValueError
        except Exception:
            return False, "Port must be between 1 and 65535."

    with _conn_lock:
        if _conn_running:
            return False, "A connection is already running. Stop it first."
        _conn_output.clear()
        _conn_stop.clear()

    try:
        if protocol in {"tcp", "telnet"}:
            sock = socket.create_connection((host, port_int), timeout=5)
            sock.settimeout(0.25)
            _conn_socket = sock
            _conn_thread = threading.Thread(target=_tcp_reader, args=(sock,), daemon=True)
            _conn_thread.start()

        elif protocol == "udp":
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.settimeout(0.25)
            sock.connect((host, port_int))
            _conn_socket = sock
            _conn_thread = threading.Thread(target=_udp_reader, args=(sock,), daemon=True)
            _conn_thread.start()
        
        elif protocol == "rs232":
            if serial is None:
                return False, "RS232 requires pyserial. Install it with: pip install pyserial"

            baud_int = int(baudrate)

            ser = serial.Serial(
                port=host,
                baudrate=baud_int,
                bytesize=int(databits),
                parity=parity,
                stopbits=float(stopbits),
                timeout=0.25,
                write_timeout=2,
            )

            _conn_serial = ser
            _conn_thread = threading.Thread(target=_serial_reader, args=(ser,), daemon=True)
            _conn_thread.start()

        elif protocol == "ssh":
            if paramiko is None:
                return False, "SSH requires paramiko. Install it with: pip install paramiko"
            if not username:
                return False, "SSH username is required."

            client = paramiko.SSHClient()
            client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            client.connect(hostname=host, port=port_int, username=username, password=password or None, timeout=7, look_for_keys=True, allow_agent=True)
            channel = client.invoke_shell()
            channel.settimeout(0.0)
            _conn_ssh_client = client
            _conn_ssh_channel = channel
            _conn_thread = threading.Thread(target=_ssh_reader, args=(channel,), daemon=True)
            _conn_thread.start()

    except Exception as exc:
        stop_connection()
        return False, f"Could not open {protocol.upper()} connection: {exc}"

    with _conn_lock:
        _conn_running = True
        _conn_protocol = protocol
        if protocol == "udp":
            _conn_target = f"{host}:{port_int}"
            _conn_status_text = f"UDP socket opened for {host}:{port_int}"

        elif protocol == "rs232":
            _conn_target = host
            _conn_status_text = f"RS232 open on {host}"

        else:
            _conn_target = f"{host}:{port_int}"
            _conn_status_text = f"{protocol.upper()} connected to {host}:{port_int}"

    if protocol == "rs232":
        _append(f"[{_stamp()}] Connected using RS232 to {host} @ {baudrate} baud")
    elif protocol == "udp":
        _append(f"[{_stamp()}] {protocol.upper()} socket opened for {host}:{port_int}")
    else:
        _append(f"[{_stamp()}] Connected using {protocol.upper()} to {host}:{port_int}")
    return True, "Connection opened."


def send_data(value: str, is_hex: bool = False, add_cr: bool = False, add_lf: bool = False) -> tuple[bool, str]:
    with _conn_lock:
        running = _conn_running
        protocol = _conn_protocol
        sock = _conn_socket
        channel = _conn_ssh_channel

    if not running:
        return False, "No active connection."

    try:
        data = _parse_send_data(value, is_hex, add_cr, add_lf)
    except ValueError as exc:
        return False, str(exc)

    if not data:
        return False, "Nothing to send."

    try:
        if protocol == "ssh":
            if channel is None or channel.closed:
                _mark_connection_closed("SSH channel closed.")
                return False, "SSH channel is closed."
            channel.send(data)
        elif protocol == "rs232":
            global _conn_serial
            assert _conn_serial is not None
            _conn_serial.write(data)
        else:
            assert sock is not None
            sock.sendall(data)
        _append({
            "time": _stamp(),
            "direction": "TX",
            "ascii": _format_bytes(data, False),
            "hex": _format_bytes(data, True),
            "sent_as_hex": is_hex,
        })
        return True, "Data sent."
    except Exception as exc:
        return False, f"Send failed: {exc}"


def stop_connection() -> tuple[bool, str]:
    global _conn_socket, _conn_running, _conn_protocol, _conn_target, _conn_ssh_client, _conn_ssh_channel, _conn_serial, _conn_status_text

    with _conn_lock:
        was_running = _conn_running
        sock = _conn_socket
        ssh_client = _conn_ssh_client
        ssh_channel = _conn_ssh_channel
        target = _conn_target
        protocol = _conn_protocol
        _conn_running = False
        _conn_protocol = ""
        _conn_target = ""
        _conn_socket = None
        _conn_ssh_client = None
        _conn_ssh_channel = None
        ser = _conn_serial
        _conn_serial = None
        _conn_status_text = ""

    _conn_stop.set()

    try:
        if sock:
            sock.close()
    except Exception:
        pass
    try:
        if ssh_channel:
            ssh_channel.close()
    except Exception:
        pass
    try:
        if ssh_client:
            ssh_client.close()
    except Exception:
        pass
    try:
        if ser:
            ser.close()
    except Exception:
        pass

    if was_running:
        if protocol == "udp":
            _append(f"[{_stamp()}] UDP socket closed for {target}.")
        else:
            _append(f"[{_stamp()}] Disconnected from {target}.")
        return True, "Connection closed."
    return False, "No active connection."


def get_connection_status() -> Dict:
    with _conn_lock:
        return {
            "running": _conn_running,
            "protocol": _conn_protocol,
            "target": _conn_target,
            "status_text": _conn_status_text,
            "output": list(_conn_output),
        }

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

def _mark_connection_closed(reason: str = "") -> None:
    global _conn_running, _conn_protocol, _conn_target, _conn_socket
    global _conn_ssh_client, _conn_ssh_channel, _conn_serial

    with _conn_lock:
        if not _conn_running:
            return

        _conn_running = False
        _conn_protocol = ""
        _conn_target = ""

        _conn_socket = None
        _conn_ssh_client = None
        _conn_ssh_channel = None
        _conn_serial = None

    _conn_stop.set()

    if reason:
        _append(f"[{_stamp()}] {reason}")