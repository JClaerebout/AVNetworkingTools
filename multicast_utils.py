from analyzer_health import apply_health, sustained_bandwidth, UNJOINED_SUSTAINED_SECONDS
import ipaddress
import re
import socket
import atexit
import threading
import time
from copy import deepcopy
from collections import defaultdict, deque
from typing import Optional

from analyzer_packets import parse_ipv4, parse_udp, parse_igmp, parse_ptp, parse_rtp
from analyzer_state import Analyzer, MAX_STREAMS, rates, record_rate
from analyzer_diagnostics import evaluate

from nic_utils import get_nics
from system_utils import run_cmd


RATE_WINDOW_SECONDS = 5
NO_QUERIER_WARNING_SECONDS = 130
SERVICE_NAMES = {
    "224.0.0.251": "mDNS",
    "224.0.0.252": "LLMNR",
    "239.255.255.250": "SSDP",
}

_lock = threading.RLock()
_lifecycle_lock = threading.Lock()
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
    "analyzer": Analyzer(),
    "heartbeat": time.monotonic(),
    "traffic": {"packets": 0, "bytes": 0, "buckets": deque(maxlen=5)},
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
        "analyzer": Analyzer(),
        "heartbeat": time.monotonic(),
        "traffic": {"packets": 0, "bytes": 0, "buckets": deque(maxlen=5)},
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
    code, stdout, _stderr = run_cmd(["netsh", "interface", "ipv4", "show", "joins"], timeout=3)
    if code != 0:
        return None
    groups = _parse_join_output(stdout, if_index)
    return groups if re.search(rf"\b{if_index}\s*:", stdout) else None


def _refresh_joined_groups(force: bool = False) -> None:
    with _lock:
        if_index = _state.get("if_index")
        checked_at = float(_state.get("membership_checked_at") or 0)
        generation = _state.get("started_at")
        running = _state["running"]
    now = time.time()
    if not running or if_index is None or (not force and now - checked_at < 5):
        return
    with _lock:
        # Reserve before the slow command so concurrent status calls do not pile up.
        if _state.get("started_at") != generation or _state["membership_checked_at"] != checked_at:
            return
        _state["membership_checked_at"] = now
    joined = _read_joined_groups(int(if_index))
    with _lock:
        if _state["running"] and _state.get("if_index") == if_index and _state.get("started_at") == generation:
            if joined is not None:
                _state["joined_groups"] = joined
                _state["membership_available"] = True
            else:
                _state["membership_available"] = False
            _state["membership_checked_at"] = now


_igmp_version_and_groups = parse_igmp


def _record_igmp(source: str, payload: bytes, timestamp: float) -> None:
    version, groups, event = _igmp_version_and_groups(payload)
    with _lock:
        analyzer = _state["analyzer"]
        if event == "invalid":
            analyzer.malformed["igmp"] += 1
        analyzer.events.append({"timestamp": timestamp, "source": source, "version": version,
                                "event_type": ("group_query" if groups else "general_query") if event == "query" else event,
                                "groups": groups})
        _state["igmp_counts"][event] += 1
        if version:
            _state["igmp_versions"].add(version)
        is_general_query = event == "query" and payload[4:8] == b"\x00\x00\x00\x00"
        if event == "query":
            _state["igmp_counts"]["general_query" if is_general_query else "group_query"] += 1
            if source not in _state["queriers"] and len(_state["queriers"]) >= 256:
                del _state["queriers"][min(_state["queriers"], key=lambda k: _state["queriers"][k]["last_seen"])]
            querier = _state["queriers"].setdefault(source, {"last_seen": 0.0, "intervals": deque(maxlen=5)})
            if querier["last_seen"]:
                querier["intervals"].append(timestamp - querier["last_seen"])
            querier["last_seen"] = timestamp


def _record_multicast(group: str, packet_bytes: int, timestamp: float) -> None:
    second = int(timestamp)
    with _lock:
        if group not in _state["groups"] and len(_state["groups"]) >= MAX_STREAMS:
            del _state["groups"][next(iter(_state["groups"]))]
            _state["analyzer"].evicted["groups"] += 1
        item = _state["groups"].setdefault(group, {"packets": 0, "bytes": 0, "buckets": deque(maxlen=UNJOINED_SUSTAINED_SECONDS + 1)})
        item["packets"] += 1
        item["bytes"] += packet_bytes
        _state["packets"] += 1
        _state["bytes"] += packet_bytes
        record_rate(_state["traffic"], packet_bytes, timestamp)
        if item["buckets"] and item["buckets"][-1][0] == second:
            item["buckets"][-1][1] += 1
            item["buckets"][-1][2] += packet_bytes
        else:
            item["buckets"].append([second, 1, packet_bytes])
        while item["buckets"] and second - item["buckets"][0][0] > UNJOINED_SUSTAINED_SECONDS:
            item["buckets"].popleft()


def _process_ipv4_packet(packet: bytes, timestamp: Optional[float] = None) -> None:
    ip = parse_ipv4(packet)
    now = timestamp if timestamp is not None else time.time()
    udp = ptp = rtp = None
    with _lock:
        analyzer = _state["analyzer"]
        if ip is None:
            analyzer.malformed["ipv4"] += 1
            return
        if 224 <= int(ip["group"].split(".")[0]) <= 239:
            _record_multicast(ip["group"], ip["bytes"], now)
        if not ip["fragmented"]:
            if ip["protocol"] == 2:
                _record_igmp(ip["source"], ip["payload"], now)
            if ip["protocol"] == 17:
                udp = parse_udp(ip["payload"])
                if udp is None:
                    analyzer.malformed["udp"] += 1
                elif udp["destination_port"] in (319, 320) or udp["source_port"] in (319, 320):
                    ptp = parse_ptp(udp["payload"])
                    if ptp is None:
                        analyzer.ptp_candidates += 1
                else:
                    rtp = parse_rtp(udp["payload"])
        analyzer.record(ip, udp, ptp, rtp, now)


def touch_multicast_capture():
    """A closed/crashed page cannot leave capture running indefinitely."""
    with _lock:
        _state["heartbeat"] = time.monotonic()


def _open_raw_capture(interface_ip: str):
    """Backend boundary: return a socket delivering IPv4 packets, without link headers."""
    capture = socket.socket(socket.AF_INET, socket.SOCK_RAW, socket.IPPROTO_IP)
    try:
        capture.bind((interface_ip, 0))
        capture.setsockopt(socket.IPPROTO_IP, socket.IP_HDRINCL, 1)
        capture.ioctl(socket.SIO_RCVALL, socket.RCVALL_ON)
        capture.settimeout(1.0)
    except Exception:
        capture.close()
        raise
    return capture


def _capture(interface_ip: str) -> None:
    global _capture_socket, _thread
    capture = None
    try:
        capture = _open_raw_capture(interface_ip)
        with _lock:
            _capture_socket = capture
        while not _stop_event.is_set():
            with _lock:
                expired = time.monotonic() - _state["heartbeat"] > 30
            if expired:
                break
            try:
                packet, _address = capture.recvfrom(65535)
                if not _stop_event.is_set():
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
    except (OSError, AttributeError) as exc:
        with _lock:
            _state["error"] = f"Could not capture on {interface_ip}: {exc}"
            _state["message"] = _state["error"]
    finally:
        if capture is not None:
            try:
                capture.ioctl(socket.SIO_RCVALL, socket.RCVALL_OFF)
            except (OSError, AttributeError):
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


def _start_multicast_test(interface_name: str) -> tuple[bool, str]:
    global _thread
    interface_name = str(interface_name or "").strip()
    with _lock:
        if _state["running"]:
            return False, "An AV Network Analyzer capture is already running."
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
    with _lock:
        _stop_event.clear()
        _thread = threading.Thread(target=_capture, args=(nic["ip"],), daemon=True)
        try:
            _thread.start()
        except RuntimeError as exc:
            _thread = None
            _state.update(running=False, stopped_at=time.time(), error=str(exc), message=str(exc))
            return False, str(exc)
        return True, _state["message"]


def _stop_multicast_test() -> tuple[bool, str]:
    requested_at = time.time()
    with _lock:
        if not _state["running"]:
            return False, "No AV Network Analyzer capture is running."
        _state["message"] = "Stopping AV Network Analyzer capture..."
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
            return True, "Stopping AV Network Analyzer capture..."
        return True, "AV Network Analyzer capture stopped."


def _rate(item: dict, now: float) -> tuple[float, float]:
    with _lock:
        return rates(item, now, float(_state.get("started_at") or now))


def get_multicast_status() -> dict:
    _refresh_joined_groups()
    now = time.time()
    with _lock:
        state = {key: deepcopy(value) for key, value in _state.items() if key != "analyzer"}
        until = state["stopped_at"] or now
        snapshot = _state["analyzer"].snapshot(until, state["started_at"] or until)
    started_at = state.get("started_at")
    observed_until = state.get("stopped_at") or now
    elapsed = max(0.0, observed_until - started_at) if started_at else 0.0
    joined = set(state["joined_groups"])
    membership_available = bool(state["membership_available"])
    groups = []
    _, total_mbps = rates(state["traffic"], observed_until, started_at or observed_until)
    for address, item in state["groups"].items():
        packets_per_second, mbps = rates(item, observed_until, started_at or observed_until)
        groups.append({
            "address": address,
            "service": SERVICE_NAMES.get(address, "Unknown"),
            "packets": item["packets"],
            "bytes": item["bytes"],
            "packets_per_second": round(packets_per_second, 1),
            "mbps": round(mbps, 3),
            "joined": address in joined,
            "membership_known": membership_available,
            "sustained_min_mbps": sustained_bandwidth(item["buckets"], observed_until),
        })
    groups.sort(key=lambda item: (-item["mbps"], -item["packets_per_second"], item["address"]))

    queriers = []
    for address, item in state["queriers"].items():
        intervals = list(item["intervals"])
        queriers.append({
            "ip": address,
            "last_query_seconds": round(max(0, observed_until - item["last_seen"]), 1),
            "query_interval_seconds": round(sum(intervals) / len(intervals), 1) if intervals else None,
        })
    queriers.sort(key=lambda item: item["ip"])

    warnings = []
    if state["groups"] and not membership_available:
        warnings.append({"severity": "warning", "code": "membership_unknown", "message": "Windows joined-group data is unavailable; flooding assessment is incomplete."})
    if len(queriers) > 1:
        warnings.append({"severity": "warning", "code": "multiple_queriers", "message": "Multiple IGMP query sources observed; querier election or duplicate configuration may be occurring."})
    if len(state["igmp_versions"]) > 1 or "v1" in state["igmp_versions"]:
        warnings.append({"severity": "warning", "code": "igmp_compatibility", "message": "Mixed or legacy IGMP versions observed; verify endpoint and switch compatibility."})
    if not state["error"] and not state["igmp_counts"] and elapsed >= NO_QUERIER_WARNING_SECONDS:
        warnings.append({"severity": "warning", "code": "no_igmp", "message": "No IGMP activity observed during the test window."})
    recent_queriers = [q for q in queriers if q["last_query_seconds"] < NO_QUERIER_WARNING_SECONDS]
    if not state["error"] and not recent_queriers and elapsed >= NO_QUERIER_WARNING_SECONDS:
        warnings.append({"severity": "warning", "code": "no_querier", "message": "No IGMP querier observed during a full typical query interval."})

    warnings.extend(evaluate(snapshot, observed_until))
    if state["error"]:
        warnings.append({"severity": "error", "code": "capture", "message": state["error"]})
    unjoined = [g["address"] for g in groups if membership_available and not g["joined"] and not g["address"].startswith("224.0.0.") and g["packets_per_second"] > 0]
    if unjoined:
        warnings.append({"severity": "information", "code": "unjoined_observed", "message": "Unjoined multicast traffic observed. This alone does not indicate multicast flooding."})
    warnings.sort(key=lambda w: {"error": 0, "danger": 0, "warning": 1, "information": 2}.get(w["severity"], 2))
    roles_by_group = defaultdict(set)
    for stream in snapshot["streams"]:
        protocol = stream["possible_protocol"]
        role = ("Timing / control / discovery" if stream["ip_protocol"] == 2 or protocol in ("PTP", "mDNS", "SSDP", "LLMNR") or protocol.startswith("Possible PTP")
                else "Possible media" if protocol == "Possible RTP" else "Unknown")
        roles_by_group[stream["group"]].add(role)
    for group in groups:
        group["traffic_roles"] = sorted(roles_by_group[group["address"]])
    result = {
        **snapshot,
        "warning_count": sum(w["severity"] != "information" for w in warnings),
        "running": state["running"],
        "message": state["message"],
        "error": state["error"],
        "interface": state["interface"],
        "ip": state["ip"],
        "if_index": state["if_index"],
        "elapsed_seconds": round(elapsed, 1),
        "packets": state["packets"],
        "bytes": state["bytes"],
        "total_mbps": round(total_mbps, 3),
        "groups": groups,
        "joined_groups": sorted(joined, key=lambda value: tuple(int(x) for x in value.split("."))),
        "membership_available": membership_available,
        "querier_detected": bool(recent_queriers),
        "queriers": queriers,
        "igmp_versions": sorted(state["igmp_versions"]),
        "igmp_counts": dict(state["igmp_counts"]),
        "warnings": warnings,
        "no_querier_warning_after_seconds": NO_QUERIER_WARNING_SECONDS,
    }
    return apply_health(result)




def start_multicast_test(interface_name: str) -> tuple[bool, str]:
    with _lifecycle_lock:
        return _start_multicast_test(interface_name)


def stop_multicast_test() -> tuple[bool, str]:
    with _lifecycle_lock:
        return _stop_multicast_test()


atexit.register(stop_multicast_test)
