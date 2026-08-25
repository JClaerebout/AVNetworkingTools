import ipaddress
import re
import socket
import struct
import threading
import time
from collections import defaultdict, deque
from typing import Iterable, Optional

from nic_utils import get_nics
from system_utils import run_cmd


RATE_WINDOW_SECONDS = 5
NO_QUERIER_WARNING_SECONDS = 130
HIGH_GROUP_MBPS = 5.0
HIGH_TOTAL_MBPS = 10.0
FLOOD_PACKETS_PER_SECOND = 100.0
SERVICE_NAMES = {
    "224.0.0.251": "mDNS",
    "224.0.0.252": "LLMNR",
    "239.255.255.250": "SSDP",
}

_lock = threading.RLock()
_stop_event = threading.Event()
_capture_socket: Optional[socket.socket] = None
_thread: Optional[threading.Thread] = None
_state = {
    "running": False,
    "message": "Idle",
    "interface": "",
    "ip": "",
    "if_index": None,
    "started_at": None,
    "stopped_at": None,
    "error": "",
    "packets": 0,
    "bytes": 0,
    "groups": {},
    "queriers": {},
    "igmp_versions": set(),
    "igmp_counts": defaultdict(int),
    "joined_groups": set(),
    "membership_available": False,
    "membership_checked_at": 0.0,
}


def _fresh_state(interface: str, ip: str, if_index: int) -> dict:
    return {
        "running": True,
        "message": f"Listening for IGMP and multicast traffic on {interface}...",
        "interface": interface,
        "ip": ip,
        "if_index": if_index,
        "started_at": time.time(),
        "stopped_at": None,
        "error": "",
        "packets": 0,
        "bytes": 0,
        "groups": {},
        "queriers": {},
        "igmp_versions": set(),
        "igmp_counts": defaultdict(int),
        "joined_groups": set(),
        "membership_available": False,
        "membership_checked_at": 0.0,
    }


def _find_interface(interface_name: str) -> Optional[dict]:
    for nic in get_nics():
        if nic.get("name") == interface_name and nic.get("link_status") == "Up" and nic.get("ip"):
            return nic
    return None


def _parse_join_output(output: str, wanted_index: int) -> set[str]:
    current_index = None
    groups = set()
    for line in output.splitlines():
        header = re.search(r"\b(?:Interface\s+)?(\d+)\s*:", line, re.IGNORECASE)
        if header:
            current_index = int(header.group(1))
            continue
        if current_index != wanted_index:
            continue
        for candidate in re.findall(r"\b(?:22[4-9]|23\d)(?:\.\d{1,3}){3}\b", line):
            try:
                if ipaddress.ip_address(candidate).is_multicast:
                    groups.add(candidate)
            except ValueError:
                pass
    return groups


def _read_joined_groups(if_index: int) -> Optional[set[str]]:
    code, stdout, _stderr = run_cmd(["netsh", "interface", "ipv4", "show", "joins"])
    if code != 0:
        return None
    groups = _parse_join_output(stdout, if_index)
    return groups or None


def _refresh_joined_groups(force: bool = False) -> None:
    with _lock:
        if_index = _state.get("if_index")
        checked_at = float(_state.get("membership_checked_at") or 0)
    now = time.time()
    if if_index is None or (not force and now - checked_at < 5):
        return
    joined = _read_joined_groups(int(if_index))
    with _lock:
        if _state.get("if_index") == if_index:
            if joined is not None:
                _state["joined_groups"] = joined
                _state["membership_available"] = True
            _state["membership_checked_at"] = now


def _igmp_version_and_groups(payload: bytes) -> tuple[Optional[str], list[str], str]:
    if len(payload) < 8:
        return None, [], "invalid"
    igmp_type = payload[0]
    group = socket.inet_ntoa(payload[4:8])
    if igmp_type == 0x11:
        version = "v3" if len(payload) >= 12 else ("v1" if payload[1] == 0 else "v2")
        return version, [] if group == "0.0.0.0" else [group], "query"
    if igmp_type == 0x12:
        return "v1", [group], "report"
    if igmp_type == 0x16:
        return "v2", [group], "report"
    if igmp_type == 0x17:
        return "v2", [group], "leave"
    if igmp_type == 0x22:
        groups = []
        record_count = struct.unpack("!H", payload[6:8])[0]
        offset = 8
        for _ in range(record_count):
            if offset + 8 > len(payload):
                break
            aux_words = payload[offset + 1]
            source_count = struct.unpack("!H", payload[offset + 2:offset + 4])[0]
            groups.append(socket.inet_ntoa(payload[offset + 4:offset + 8]))
            offset += 8 + source_count * 4 + aux_words * 4
        return "v3", groups, "report"
    return None, [], f"type_{igmp_type:#04x}"


def _record_igmp(source: str, payload: bytes, timestamp: float) -> None:
    version, _groups, event = _igmp_version_and_groups(payload)
    with _lock:
        _state["igmp_counts"][event] += 1
        if version:
            _state["igmp_versions"].add(version)
        is_general_query = event == "query" and payload[4:8] == b"\x00\x00\x00\x00"
        if is_general_query:
            _state["igmp_counts"]["general_query"] += 1
            querier = _state["queriers"].setdefault(source, {"last_seen": 0.0, "intervals": deque(maxlen=5)})
            if querier["last_seen"]:
                querier["intervals"].append(timestamp - querier["last_seen"])
            querier["last_seen"] = timestamp


def _record_multicast(group: str, packet_bytes: int, timestamp: float) -> None:
    second = int(timestamp)
    with _lock:
        item = _state["groups"].setdefault(group, {"packets": 0, "bytes": 0, "buckets": deque()})
        item["packets"] += 1
        item["bytes"] += packet_bytes
        _state["packets"] += 1
        _state["bytes"] += packet_bytes
        if item["buckets"] and item["buckets"][-1][0] == second:
            item["buckets"][-1][1] += 1
            item["buckets"][-1][2] += packet_bytes
        else:
            item["buckets"].append([second, 1, packet_bytes])
        while item["buckets"] and second - item["buckets"][0][0] >= RATE_WINDOW_SECONDS:
            item["buckets"].popleft()


def _process_ipv4_packet(packet: bytes, timestamp: Optional[float] = None) -> None:
    if len(packet) < 20 or packet[0] >> 4 != 4:
        return
    header_length = (packet[0] & 0x0F) * 4
    if header_length < 20 or len(packet) < header_length:
        return
    total_length = struct.unpack("!H", packet[2:4])[0]
    packet_length = min(len(packet), total_length) if total_length >= header_length else len(packet)
    source = socket.inet_ntoa(packet[12:16])
    destination = socket.inet_ntoa(packet[16:20])
    now = timestamp if timestamp is not None else time.time()
    try:
        is_multicast = ipaddress.ip_address(destination).is_multicast
    except ValueError:
        is_multicast = False
    if is_multicast:
        _record_multicast(destination, packet_length + 14, now)
    if packet[9] == 2:
        _record_igmp(source, packet[header_length:packet_length], now)


def _capture(interface_ip: str) -> None:
    global _capture_socket, _thread
    capture = None
    try:
        capture = socket.socket(socket.AF_INET, socket.SOCK_RAW, socket.IPPROTO_IP)
        capture.bind((interface_ip, 0))
        capture.setsockopt(socket.IPPROTO_IP, socket.IP_HDRINCL, 1)
        capture.ioctl(socket.SIO_RCVALL, socket.RCVALL_ON)
        capture.settimeout(1.0)
        with _lock:
            _capture_socket = capture
        _refresh_joined_groups(force=True)
        while not _stop_event.is_set():
            try:
                packet, _address = capture.recvfrom(65535)
                _process_ipv4_packet(packet)
            except socket.timeout:
                continue
            except OSError:
                if not _stop_event.is_set():
                    raise
                break
    except PermissionError:
        with _lock:
            _state["error"] = "Packet capture requires running AVNetworkingTools as administrator."
            _state["message"] = _state["error"]
    except OSError as exc:
        with _lock:
            _state["error"] = f"Could not capture on {interface_ip}: {exc}"
            _state["message"] = _state["error"]
    finally:
        if capture is not None:
            try:
                capture.ioctl(socket.SIO_RCVALL, socket.RCVALL_OFF)
            except OSError:
                pass
            capture.close()
        with _lock:
            _capture_socket = None
            _state["running"] = False
            if _state["stopped_at"] is None:
                _state["stopped_at"] = time.time()
            if not _state["error"]:
                _state["message"] = "Capture stopped."
            if _thread is threading.current_thread():
                _thread = None


def start_multicast_test(interface_name: str) -> tuple[bool, str]:
    global _thread
    interface_name = str(interface_name or "").strip()
    with _lock:
        if _state["running"]:
            return False, "A multicast health test is already running."
    if not interface_name:
        return False, "Select a connected interface."
    try:
        nic = _find_interface(interface_name)
    except Exception as exc:
        return False, f"Could not load network interfaces: {exc}"
    if not nic:
        return False, "The selected interface is disconnected or has no IPv4 address."
    with _lock:
        _state.clear()
        _state.update(_fresh_state(interface_name, nic["ip"], int(nic["if_index"])))
    _stop_event.clear()
    _thread = threading.Thread(target=_capture, args=(nic["ip"],), daemon=True)
    _thread.start()
    return True, _state["message"]


def stop_multicast_test() -> tuple[bool, str]:
    requested_at = time.time()
    with _lock:
        if not _state["running"]:
            return False, "No multicast health test is running."
        _state["message"] = "Stopping multicast health test..."
        _state["stopped_at"] = requested_at
        capture = _capture_socket
        capture_thread = _thread
    _stop_event.set()
    if capture is not None:
        try:
            capture.close()
        except OSError:
            pass
    if capture_thread is not None and capture_thread is not threading.current_thread():
        capture_thread.join(timeout=2.5)
    with _lock:
        if _state["running"]:
            return True, "Stopping multicast health test..."
        return True, "Multicast health test stopped."


def _rate(item: dict, now: float) -> tuple[float, float]:
    cutoff = int(now) - RATE_WINDOW_SECONDS + 1
    buckets: Iterable[list] = item.get("buckets", ())
    active = [bucket for bucket in buckets if bucket[0] >= cutoff]
    duration = min(RATE_WINDOW_SECONDS, max(1.0, now - float(_state.get("started_at") or now)))
    return sum(x[1] for x in active) / duration, (sum(x[2] for x in active) * 8 / 1_000_000) / duration


def get_multicast_status() -> dict:
    _refresh_joined_groups()
    now = time.time()
    with _lock:
        started_at = _state.get("started_at")
        observed_until = _state.get("stopped_at") or now
        elapsed = max(0.0, observed_until - started_at) if started_at else 0.0
        joined = set(_state["joined_groups"])
        membership_available = bool(_state["membership_available"])
        groups = []
        total_mbps = 0.0
        flood_groups = []
        for address, item in _state["groups"].items():
            packets_per_second, mbps = _rate(item, observed_until)
            total_mbps += mbps
            link_local_control = ipaddress.ip_address(address) in ipaddress.ip_network("224.0.0.0/24")
            suspected_flood = (
                packets_per_second >= FLOOD_PACKETS_PER_SECOND
                and membership_available
                and address not in joined
                and not link_local_control
            )
            if suspected_flood:
                flood_groups.append(address)
            groups.append({
                "address": address,
                "service": SERVICE_NAMES.get(address, "Unknown"),
                "packets": item["packets"],
                "bytes": item["bytes"],
                "packets_per_second": round(packets_per_second, 1),
                "mbps": round(mbps, 3),
                "joined": address in joined,
                "membership_known": membership_available,
                "suspected_flood": suspected_flood,
            })
        groups.sort(key=lambda item: (-item["mbps"], -item["packets_per_second"], item["address"]))

        queriers = []
        for address, item in _state["queriers"].items():
            intervals = list(item["intervals"])
            queriers.append({
                "ip": address,
                "last_query_seconds": round(max(0, observed_until - item["last_seen"]), 1),
                "query_interval_seconds": round(sum(intervals) / len(intervals), 1) if intervals else None,
            })
        queriers.sort(key=lambda item: item["ip"])

        warnings = []
        high_groups = [item["address"] for item in groups if item["mbps"] >= HIGH_GROUP_MBPS]
        if total_mbps >= HIGH_TOTAL_MBPS or high_groups:
            warnings.append({"severity": "warning", "code": "high_traffic", "message": "High multicast traffic detected."})
        if flood_groups:
            warnings.append({"severity": "warning", "code": "unjoined_traffic", "message": "High-rate traffic is arriving for groups this interface has not joined."})
            warnings.append({"severity": "danger", "code": "snooping", "message": "Multicast flooding suspected; verify IGMP snooping and uplink/router configuration."})
        if _state["groups"] and not membership_available:
            warnings.append({"severity": "warning", "code": "membership_unknown", "message": "Windows joined-group data is unavailable; flooding assessment is incomplete."})
        if len(queriers) > 1:
            warnings.append({"severity": "warning", "code": "multiple_queriers", "message": "Multiple IGMP query sources observed; querier election or duplicate configuration may be occurring."})
        if len(_state["igmp_versions"]) > 1 or "v1" in _state["igmp_versions"]:
            warnings.append({"severity": "warning", "code": "igmp_compatibility", "message": "Mixed or legacy IGMP versions observed; verify endpoint and switch compatibility."})
        if not _state["error"] and not _state["igmp_counts"] and elapsed >= NO_QUERIER_WARNING_SECONDS:
            warnings.append({"severity": "warning", "code": "no_igmp", "message": "No IGMP activity observed during the test window."})
        if not _state["error"] and not queriers and elapsed >= NO_QUERIER_WARNING_SECONDS:
            warnings.append({"severity": "warning", "code": "no_querier", "message": "No IGMP querier observed during a full typical query interval."})

        return {
            "running": _state["running"],
            "message": _state["message"],
            "error": _state["error"],
            "interface": _state["interface"],
            "ip": _state["ip"],
            "if_index": _state["if_index"],
            "elapsed_seconds": round(elapsed, 1),
            "packets": _state["packets"],
            "bytes": _state["bytes"],
            "total_mbps": round(total_mbps, 3),
            "groups": groups,
            "joined_groups": sorted(joined, key=lambda value: tuple(int(x) for x in value.split("."))),
            "membership_available": membership_available,
            "querier_detected": bool(queriers),
            "queriers": queriers,
            "igmp_versions": sorted(_state["igmp_versions"]),
            "igmp_counts": dict(_state["igmp_counts"]),
            "warnings": warnings,
            "no_querier_warning_after_seconds": NO_QUERIER_WARNING_SECONDS,
        }
