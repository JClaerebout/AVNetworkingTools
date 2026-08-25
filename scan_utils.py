import ipaddress
import re
import socket
import struct
import sys
import threading
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import Dict, List, Optional

from config import MANUFACTURER_ONLINE_FALLBACK
from nic_utils import get_nics, subnet_to_prefix
from manufacturer_db import lookup_local_manufacturer
from system_utils import run_cmd

_MAX_HOSTS = 1024
_SCAN_PING_RETRIES = 2
_MONITOR_MISSING_AFTER = 3
_WEB_PORT_TIMEOUT = 0.4
_SNMP_TIMEOUT = 0.4

_scan_lock = threading.Lock()
_scan_thread: Optional[threading.Thread] = None
_lookup_thread: Optional[threading.Thread] = None
_scan_stop = threading.Event()
_scan_running = False
_lookup_running = False
_monitor_running = False
_monitor_paused = False
_monitor_thread: Optional[threading.Thread] = None
_monitor_stop = threading.Event()

_last_scan_context = {
    "interface": "",
    "source_ip": "",
    "network": "",
    "local_network": "",
    "hosts": [],
    "quick_scan": False,
    "completed": False,
}
_scan_message = "Idle"
_scan_total = 0
_scan_done = 0
_lookup_total = 0
_lookup_done = 0
_large_scan_quick_only = False
_scan_results: List[Dict] = []
_monitor_log: List[str] = []
_vendor_cache = {}
_vendor_lock = threading.Lock()
_mdns_cache = {"source_ip": "", "timestamp": 0.0, "names": {}}
_mdns_lock = threading.Lock()


def get_scannable_nics() -> List[Dict]:
    nics = []

    for nic in get_nics():
        ip = nic.get("ip") or ""
        subnet = nic.get("subnet") or ""

        if nic.get("status") == "disconnected" or not ip or not subnet:
            continue

        try:
            prefix = subnet_to_prefix(subnet)
            if prefix is None:
                continue
            network = ipaddress.ip_network(f"{ip}/{prefix}", strict=False)
        except ValueError:
            continue

        item = dict(nic)
        item["network"] = str(network)
        item["prefix"] = prefix
        item["host_count"] = max(network.num_addresses - 2, 0)
        nics.append(item)

    return nics


def _find_nic(interface_name: str):
    for nic in get_scannable_nics():
        if nic.get("name") == interface_name:
            return True, nic

    return False, "Selected NIC has no valid connected IPv4 subnet."


def _ping_command(source_ip: str, target_ip: str) -> List[str]:
    if sys.platform == "win32":
        return ["ping", "-n", "1", "-w", "350", "-S", source_ip, target_ip]

    return ["ping", "-c", "1", "-W", "1", target_ip]


def _ping_host(source_ip: str, target_ip: str) -> bool:
    code, stdout, stderr = run_cmd(_ping_command(source_ip, target_ip))
    text = f"{stdout}\n{stderr}".lower()
    return code == 0 and ("ttl=" in text or "bytes=" in text)

def _ping_host_reliable(source_ip: str, target_ip: str, attempts: int = 2, stop_event=None) -> bool:
    for _ in range(max(1, attempts)):
        if stop_event is not None and stop_event.is_set():
            return False

        if _ping_host(source_ip, target_ip):
            return True

        time.sleep(0.08)

    return False


def _arp_probe(source_ip: str, target_ip: str) -> str:
    """Actively resolve an on-link IPv4 address to a MAC on Windows."""
    if sys.platform != "win32":
        return ""

    try:
        import ctypes

        destination = struct.unpack("=I", socket.inet_aton(target_ip))[0]
        source = struct.unpack("=I", socket.inet_aton(source_ip))[0]
        mac_buffer = ctypes.create_string_buffer(6)
        mac_length = ctypes.c_ulong(len(mac_buffer))
        result = ctypes.windll.iphlpapi.SendARP(
            destination,
            source,
            mac_buffer,
            ctypes.byref(mac_length),
        )
    except (AttributeError, OSError, struct.error):
        return ""

    if result != 0 or mac_length.value != 6:
        return ""

    return ":".join(f"{byte:02X}" for byte in mac_buffer.raw[:mac_length.value])


def _discover_host(
    source_ip: str,
    target_ip: str,
    use_arp: bool,
    attempts: int = 2,
    stop_event=None,
) -> tuple[bool, str]:
    if stop_event is not None and stop_event.is_set():
        return False, ""

    if use_arp:
        mac = _arp_probe(source_ip, target_ip)
        if mac:
            return True, mac

    if not _ping_host_reliable(source_ip, target_ip, attempts, stop_event):
        return False, ""

    return True, _get_arp_entries(source_ip).get(target_ip, "")


def _normalize_mac(mac: str) -> str:
    mac = (mac or "").strip().upper().replace("-", ":")
    parts = [p.zfill(2) for p in mac.split(":") if p]
    return ":".join(parts) if len(parts) == 6 else mac


def _parse_arp(output: str) -> Dict[str, str]:
    entries = {}
    pattern = re.compile(r"(?P<ip>\d+\.\d+\.\d+\.\d+)\s+(?P<mac>[0-9a-fA-F:-]{17})\s+")

    for line in output.splitlines():
        match = pattern.search(line)
        if match:
            entries[match.group("ip")] = _normalize_mac(match.group("mac"))

    return entries


def _get_arp_entries(source_ip: str) -> Dict[str, str]:
    if sys.platform == "win32":
        code, stdout, stderr = run_cmd(["arp", "-a", "-N", source_ip])
        if code == 0:
            return _parse_arp(stdout)

    code, stdout, stderr = run_cmd(["arp", "-a"])
    return _parse_arp(stdout if code == 0 else stdout + "\n" + stderr)


def _reverse_dns(ip: str) -> str:
    if sys.platform == "win32":
        code, stdout, stderr = run_cmd([
            "powershell",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy", "Bypass",
            "-Command",
            (
                f"try {{ (Resolve-DnsName -Name '{ip}' -Type PTR -QuickTimeout "
                "-ErrorAction Stop).NameHost } catch { '' }"
            ),
        ], timeout=2)
        return stdout.strip().strip(".") if code == 0 else ""

    try:
        return socket.gethostbyaddr(ip)[0]
    except Exception:
        return ""

def _netbios_name(ip: str) -> str:
    if sys.platform != "win32":
        return ""

    code, stdout, stderr = run_cmd(["nbtstat", "-A", ip], timeout=2)
    if code != 0:
        return ""

    for line in stdout.splitlines():
        line = line.strip()

        if "<00>" in line and "UNIQUE" in line.upper():
            name = line.split("<00>")[0].strip()
            if name and name.upper() != "WORKGROUP":
                return name

    return ""


def _windows_resolved_name(ip: str) -> str:
    """Use the Windows resolver, which can include local name providers."""
    if sys.platform != "win32":
        return ""

    code, stdout, _stderr = run_cmd(["ping", "-a", "-n", "1", "-w", "250", ip], timeout=2)
    if code not in (0, 1):
        return ""

    bracketed_ip = re.escape(ip)
    for line in stdout.splitlines():
        match = re.search(rf"\b([^\s\[\]]+)\s+\[{bracketed_ip}\]", line)
        if match:
            return match.group(1).strip().strip(".")

    return ""


def _dns_encode_name(name: str) -> bytes:
    encoded = bytearray()
    for label in name.rstrip(".").split("."):
        label_bytes = label.encode("utf-8")
        if not label_bytes or len(label_bytes) > 63:
            raise ValueError("Invalid DNS label")
        encoded.append(len(label_bytes))
        encoded.extend(label_bytes)
    encoded.append(0)
    return bytes(encoded)


def _dns_read_name(data: bytes, offset: int) -> tuple[str, int]:
    labels = []
    next_offset = None
    visited = set()

    while True:
        if offset >= len(data) or offset in visited:
            raise ValueError("Invalid compressed DNS name")
        visited.add(offset)
        length = data[offset]

        if length == 0:
            offset += 1
            break
        if length & 0xC0 == 0xC0:
            if offset + 1 >= len(data):
                raise ValueError("Truncated DNS pointer")
            if next_offset is None:
                next_offset = offset + 2
            offset = ((length & 0x3F) << 8) | data[offset + 1]
            continue
        if length & 0xC0 or offset + 1 + length > len(data):
            raise ValueError("Invalid DNS label")

        raw_label = data[offset + 1:offset + 1 + length]
        labels.append(raw_label.decode("utf-8", errors="replace"))
        offset += 1 + length

    return ".".join(labels).rstrip("."), next_offset if next_offset is not None else offset


def _parse_mdns_packet(data: bytes) -> list[tuple[str, int, object]]:
    if len(data) < 12:
        return []

    try:
        _identifier, _flags, questions, answers, authorities, additionals = struct.unpack("!6H", data[:12])
        offset = 12
        for _ in range(questions):
            _name, offset = _dns_read_name(data, offset)
            if offset + 4 > len(data):
                return []
            offset += 4

        records = []
        for _ in range(answers + authorities + additionals):
            name, offset = _dns_read_name(data, offset)
            if offset + 10 > len(data):
                return []
            record_type, _record_class, _ttl, data_length = struct.unpack("!HHIH", data[offset:offset + 10])
            data_offset = offset + 10
            end = data_offset + data_length
            if end > len(data):
                return []

            value = None
            if record_type == 1 and data_length == 4:
                value = socket.inet_ntoa(data[data_offset:end])
            elif record_type == 12:
                value, _ = _dns_read_name(data, data_offset)
            elif record_type == 33 and data_length >= 7:
                target, _ = _dns_read_name(data, data_offset + 6)
                value = target
            elif record_type == 16:
                fields = {}
                cursor = data_offset
                while cursor < end:
                    field_length = data[cursor]
                    cursor += 1
                    if cursor + field_length > end:
                        break
                    field = data[cursor:cursor + field_length].decode("utf-8", errors="replace")
                    cursor += field_length
                    key, separator, field_value = field.partition("=")
                    if separator:
                        fields[key.lower()] = field_value
                value = fields

            if value is not None:
                records.append((name, record_type, value))
            offset = end
        return records
    except (OSError, struct.error, ValueError):
        return []


def _mdns_query(names: List[str], source_ip: str, timeout: float) -> list[tuple[str, int, object]]:
    if not names:
        return []

    try:
        packets = [
            struct.pack("!6H", 0, 0, 1, 0, 0, 0)
            + _dns_encode_name(name)
            + struct.pack("!HH", 12, 0x8001)
            for name in names
        ]
    except ValueError:
        return []
    records = []
    deadline = time.monotonic() + timeout

    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP) as client:
            client.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton(source_ip))
            client.bind((source_ip, 0))
            for packet in packets:
                client.sendto(packet, ("224.0.0.251", 5353))

            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                client.settimeout(remaining)
                try:
                    response, _sender = client.recvfrom(65535)
                except socket.timeout:
                    break
                records.extend(_parse_mdns_packet(response))
    except OSError:
        return records

    return records


def _friendly_mdns_instance(instance: str, service_type: str) -> str:
    suffix = "." + service_type.lower().rstrip(".")
    if instance.lower().endswith(suffix):
        return instance[:-len(suffix)].strip().strip(".")
    return instance.split(".", 1)[0].strip()


def _discover_mdns_names(source_ip: str) -> Dict[str, str]:
    if not source_ip:
        return {}

    with _mdns_lock:
        now = time.monotonic()
        if _mdns_cache["source_ip"] == source_ip and now - _mdns_cache["timestamp"] < 60:
            return dict(_mdns_cache["names"])

        enumeration = _mdns_query(["_services._dns-sd._udp.local"], source_ip, 0.45)
        service_types = sorted({
            value for _name, record_type, value in enumeration
            if record_type == 12 and isinstance(value, str) and value.lower().endswith(".local")
        })
        records = list(enumeration)
        for start in range(0, len(service_types), 20):
            records.extend(_mdns_query(service_types[start:start + 20], source_ip, 0.35))

        addresses = {}
        services = {}
        targets = {}
        text_fields = {}
        for name, record_type, value in records:
            key = name.lower()
            if record_type == 1:
                addresses[key] = value
            elif record_type == 12 and isinstance(value, str):
                services[value.lower()] = name
            elif record_type == 33 and isinstance(value, str):
                targets[key] = value
            elif record_type == 16 and isinstance(value, dict):
                text_fields[key] = value

        names_by_ip = {}
        for instance_key, target in targets.items():
            ip = addresses.get(target.lower())
            service_type = services.get(instance_key, "")
            if not ip or not service_type:
                continue
            txt = text_fields.get(instance_key, {})
            service_name = txt.get("fn") or txt.get("name") or _friendly_mdns_instance(instance_key, service_type)
            hostname = target.removesuffix(".local").split(".", 1)[0]
            candidate = (hostname or service_name).replace("\\032", " ").strip().strip(".")
            if candidate and len(candidate) <= 255:
                names_by_ip.setdefault(ip, candidate)

        _mdns_cache.update({"source_ip": source_ip, "timestamp": now, "names": names_by_ip})
        return dict(names_by_ip)


def _read_ber_tlv(data: bytes, offset: int = 0) -> tuple[int, bytes, int]:
    if offset + 2 > len(data):
        raise ValueError("Truncated BER value")

    tag = data[offset]
    length_byte = data[offset + 1]
    offset += 2

    if length_byte & 0x80:
        length_size = length_byte & 0x7F
        if not length_size or length_size > 4 or offset + length_size > len(data):
            raise ValueError("Invalid BER length")
        length = int.from_bytes(data[offset:offset + length_size], "big")
        offset += length_size
    else:
        length = length_byte

    end = offset + length
    if end > len(data):
        raise ValueError("Truncated BER payload")

    return tag, data[offset:end], end


def _parse_snmp_sysname(data: bytes) -> str:
    try:
        outer_tag, outer, _ = _read_ber_tlv(data)
        if outer_tag != 0x30:
            return ""

        offset = 0
        _version_tag, _version, offset = _read_ber_tlv(outer, offset)
        _community_tag, _community, offset = _read_ber_tlv(outer, offset)
        pdu_tag, pdu, _ = _read_ber_tlv(outer, offset)
        if pdu_tag not in (0xA2, 0xA0):
            return ""

        offset = 0
        _request_tag, _request_id, offset = _read_ber_tlv(pdu, offset)
        _error_tag, error, offset = _read_ber_tlv(pdu, offset)
        _index_tag, _error_index, offset = _read_ber_tlv(pdu, offset)
        if int.from_bytes(error, "big") != 0:
            return ""

        list_tag, varbind_list, _ = _read_ber_tlv(pdu, offset)
        if list_tag != 0x30:
            return ""
        varbind_tag, varbind, _ = _read_ber_tlv(varbind_list)
        if varbind_tag != 0x30:
            return ""

        offset = 0
        oid_tag, oid, offset = _read_ber_tlv(varbind, offset)
        value_tag, value, _ = _read_ber_tlv(varbind, offset)
        if oid_tag != 0x06 or oid != bytes.fromhex("2B06010201010500") or value_tag != 0x04:
            return ""
    except (TypeError, ValueError):
        return ""

    name = value.decode("utf-8", errors="replace").replace("\x00", "").strip().strip(".")
    return name if name and len(name) <= 255 else ""


def _snmp_name(ip: str, source_ip: str = "") -> str:
    # SNMPv2c GET for SNMPv2-MIB::sysName.0 using the conventional read-only community.
    request = bytes.fromhex(
        "302902010104067075626C6963A01C020400000001020100020100"
        "300E300C06082B060102010105000500"
    )

    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as client:
            client.settimeout(_SNMP_TIMEOUT)
            if source_ip:
                client.bind((source_ip, 0))
            client.sendto(request, (ip, 161))
            response, sender = client.recvfrom(4096)
    except OSError:
        return ""

    if sender[0] != ip:
        return ""
    return _parse_snmp_sysname(response)


def lookup_hostname(ip: str, source_ip: str = "", mdns_names: Optional[Dict[str, str]] = None) -> str:
    hostname = _reverse_dns(ip)

    if hostname:
        return hostname

    netbios = _netbios_name(ip)

    if netbios:
        return netbios

    windows_name = _windows_resolved_name(ip)

    if windows_name:
        return windows_name

    mdns_name = (mdns_names if mdns_names is not None else _discover_mdns_names(source_ip)).get(ip, "")

    if mdns_name:
        return mdns_name

    snmp_name = _snmp_name(ip, source_ip)

    if snmp_name:
        return snmp_name

    return ""


def probe_web_services(ip: str, source_ip: str = "") -> List[str]:
    services = []

    for scheme, port in (("http", 80), ("https", 443)):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as connection:
                connection.settimeout(_WEB_PORT_TIMEOUT)
                if source_ip:
                    connection.bind((source_ip, 0))
                connection.connect((ip, port))
            services.append(scheme)
        except OSError:
            pass

    return services

def _lookup_new_monitor_device(ip: str, mac: str) -> None:
    with _scan_lock:
        source_ip = _last_scan_context.get("source_ip", "")
    hostname = lookup_hostname(ip, source_ip)
    web_services = probe_web_services(ip, source_ip)

    _update_result(ip, {
        "hostname": hostname,
        "web_services": web_services,
    })

    manufacturer = lookup_manufacturer(mac)

    _update_result(ip, {
        "hostname": hostname,
        "manufacturer": manufacturer,
    })


def _append_monitor_log_locked(message: str) -> None:
    _monitor_log.append(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {message}")


def get_monitor_log() -> List[str]:
    with _scan_lock:
        return list(_monitor_log)


def _monitor_update_device(ip: str, mac: str, quick_scan: bool) -> bool:
    """
    Returns True when this is a newly discovered IP.
    """
    with _scan_lock:
        for result in _scan_results:
            if result["ip"] == ip:
                was_missing = bool(result.get("missing"))
                was_duplicate = bool(result.get("duplicate_ip"))
                seen_macs = set(result.get("seen_macs", []))

                current_mac = result.get("mac", "")
                if current_mac and "," not in current_mac:
                    seen_macs.add(current_mac)

                if mac:
                    seen_macs.add(mac)

                result["seen_macs"] = sorted(seen_macs)
                result["missing"] = False
                result["miss_count"] = 0
                result["duplicate_ip"] = len(seen_macs) > 1
                result["duplicate_macs"] = sorted(seen_macs)

                if result["duplicate_ip"]:
                    result["mac"] = ", ".join(sorted(seen_macs))
                else:
                    result["mac"] = mac

                if was_missing:
                    _append_monitor_log_locked(f"RESTORED {ip} ({mac or 'MAC unknown'})")
                if result["duplicate_ip"] and not was_duplicate:
                    _append_monitor_log_locked(
                        f"DUPLICATE IP {ip}: {', '.join(result['duplicate_macs'])}"
                    )

                return False

        _scan_results.append({
            "ip": ip,
            "mac": mac,
            "manufacturer": "-" if quick_scan else "Looking up...",
            "hostname": "-" if quick_scan else "Looking up...",
            "missing": False,
            "miss_count": 0,
            "duplicate_ip": False,
            "duplicate_macs": [],
            "seen_macs": [mac] if mac else [],
            "is_local": False,
            "web_services": [],
        })

        _scan_results.sort(key=lambda x: ipaddress.ip_address(x["ip"]))
        _append_monitor_log_locked(f"NEW DEVICE {ip} ({mac or 'MAC unknown'})")
        return True


def _monitor_probe_round(
    source_ip: str,
    hosts: List[str],
    local_network,
    stop_event,
) -> Dict[str, str]:
    discovered = {}
    targets = [ip for ip in hosts if ip != source_ip]
    workers = min(80, max(8, len(targets)))

    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {
            executor.submit(
                _discover_host,
                source_ip,
                ip,
                ipaddress.ip_address(ip) in local_network,
                2,
                stop_event,
            ): ip
            for ip in targets
        }

        for future in as_completed(futures):
            if stop_event.is_set():
                break

            ip = futures[future]
            try:
                alive, mac = future.result()
            except Exception:
                continue

            if alive and mac:
                discovered[ip] = mac

    return discovered


def _monitor_worker() -> None:
    global _monitor_running, _monitor_paused, _scan_message

    with _scan_lock:
        source_ip = _last_scan_context["source_ip"]
        hosts = list(_last_scan_context["hosts"])
        quick_scan = bool(_last_scan_context["quick_scan"])
        network = _last_scan_context["network"]
        local_network_text = _last_scan_context.get("local_network") or network

        _monitor_running = True
        _monitor_paused = False
        _scan_message = f"Monitoring active on {network}..."

    local_network = ipaddress.ip_network(local_network_text, strict=False)

    while not _monitor_stop.is_set():
        with _scan_lock:
            paused = _monitor_paused

        if paused:
            with _scan_lock:
                _scan_message = "Monitoring paused because IP Scan page is not active."
            time.sleep(1)
            continue

        seen_ips_this_round = set()

        discovered = _monitor_probe_round(source_ip, hosts, local_network, _monitor_stop)

        for ip, mac in discovered.items():
            seen_ips_this_round.add(ip)

            is_new = _monitor_update_device(ip, mac, quick_scan)

            if is_new and not quick_scan:
                threading.Thread(
                    target=_lookup_new_monitor_device,
                    args=(ip, mac),
                    daemon=True
                ).start()

        with _scan_lock:
            for result in _scan_results:
                ip = result.get("ip", "")

                if result.get("is_local"):
                    result["missing"] = False
                    result["miss_count"] = 0
                    continue

                if ip not in hosts:
                    continue

                if ip in seen_ips_this_round:
                    result["miss_count"] = 0
                    result["missing"] = False
                else:
                    was_missing = bool(result.get("missing"))
                    result["miss_count"] = int(result.get("miss_count", 0)) + 1
                    result["missing"] = result["miss_count"] >= _MONITOR_MISSING_AFTER
                    if result["missing"] and not was_missing:
                        _append_monitor_log_locked(
                            f"MISSING {ip} ({result.get('mac') or 'MAC unknown'})"
                        )

            missing_count = len([r for r in _scan_results if r.get("missing")])
            duplicate_count = len([r for r in _scan_results if r.get("duplicate_ip")])

            _scan_message = (
                f"Monitoring active. "
                f"{len(_scan_results)} device(s), "
                f"{missing_count} missing, "
                f"{duplicate_count} duplicate conflict(s)."
            )

        for _ in range(50):
            if _monitor_stop.is_set():
                break
            time.sleep(0.1)

    with _scan_lock:
        _monitor_running = False
        _monitor_paused = False
        _scan_message = "Monitoring stopped."
        _append_monitor_log_locked("Monitoring stopped.")


def start_monitor() -> tuple[bool, str]:
    global _monitor_thread, _monitor_running, _monitor_log

    with _scan_lock:
        if _scan_running or _lookup_running:
            return False, "Wait until the scan and lookup are complete."

        if not _scan_results:
            return False, "Run an IP scan first."

        if not _last_scan_context["hosts"] or not _last_scan_context["source_ip"]:
            return False, "No previous scan context found."

        if _monitor_running:
            return True, "Monitoring is already active."

        _monitor_stop.clear()
        _monitor_running = True
        _monitor_log = []
        _append_monitor_log_locked(
            f"Monitoring started on {_last_scan_context['network']} with "
            f"{len(_scan_results)} known device(s)."
        )

    _monitor_thread = threading.Thread(target=_monitor_worker, daemon=True)
    _monitor_thread.start()

    return True, "Monitoring started."


def stop_monitor() -> tuple[bool, str]:
    with _scan_lock:
        if not _monitor_running:
            return True, "Monitoring is already stopped."

    _monitor_stop.set()
    return True, "Monitoring stopping..."


def set_monitor_paused(paused: bool) -> tuple[bool, str]:
    global _monitor_paused

    with _scan_lock:
        if not _monitor_running:
            return True, "Monitoring is not active."

        if _monitor_paused != paused:
            _monitor_paused = paused
            _append_monitor_log_locked("Monitoring paused." if paused else "Monitoring resumed.")

    return True, "Monitoring paused." if paused else "Monitoring resumed."

def lookup_manufacturer(mac: str, allow_online: Optional[bool] = None) -> str:
    clean_mac = re.sub(r"[^0-9A-Fa-f]", "", mac or "").upper()
    if len(clean_mac) != 12:
        return "Unknown"

    if int(clean_mac[:2], 16) & 0x03:
        return "Unknown"

    local_manufacturer = lookup_local_manufacturer(clean_mac)
    if local_manufacturer:
        return local_manufacturer

    if allow_online is None:
        allow_online = MANUFACTURER_ONLINE_FALLBACK
    if not allow_online:
        return "Unknown"

    with _vendor_lock:
        cached = _vendor_cache.get(clean_mac)
        if cached and cached != "Unknown":
            return cached

    try:
        time.sleep(0.35)

        url = f"https://api.macvendors.com/{clean_mac}"
        req = urllib.request.Request(url, headers={"User-Agent": "AVNetworkingTools"})

        with urllib.request.urlopen(req, timeout=10) as response:
            text = response.read().decode("utf-8", errors="replace").strip()
            vendor = text[:120] if text else "Unknown"

    except Exception:
        vendor = "Unknown"

    # Only cache real manufacturer names.
    # Never let a failed lookup overwrite a good cached value.
    if vendor != "Unknown":
        with _vendor_lock:
            _vendor_cache[clean_mac] = vendor

    return vendor


def _add_result(item: Dict) -> None:
    global _scan_results

    with _scan_lock:
        existing_ips = {result["ip"] for result in _scan_results}
        if item["ip"] not in existing_ips:
            _scan_results.append(item)
            _scan_results.sort(key=lambda x: ipaddress.ip_address(x["ip"]))

def _update_result(ip: str, updates: Dict) -> None:
    with _scan_lock:
        for result in _scan_results:
            if result["ip"] == ip:
                result.update(updates)
                return


def _set_pending_lookup_values_to_dash() -> None:
    with _scan_lock:
        for result in _scan_results:
            if result.get("hostname") == "Looking up...":
                result["hostname"] = "-"
            if result.get("manufacturer") == "Looking up...":
                result["manufacturer"] = "-"


def _lookup_worker(reused_quick_scan: bool = False) -> None:
    global _lookup_running, _lookup_total, _lookup_done, _scan_message

    with _scan_lock:
        items = list(_scan_results)
        source_ip = _last_scan_context.get("source_ip", "")
        _lookup_total = len(items)
        _lookup_done = 0
        _lookup_running = bool(items)
        if items:
            prefix = "Quick scan reused." if reused_quick_scan else "Scan complete."
            _scan_message = f"{prefix} Loading local manufacturers for {len(items)} device(s)..."

    if not items:
        with _scan_lock:
            _lookup_running = False
            if reused_quick_scan:
                _last_scan_context["quick_scan"] = False
                _scan_message = f"Extended scan complete. Found {len(_scan_results)} device(s)."
            else:
                _scan_message = f"Lookup complete. Found {len(_scan_results)} device(s)."
        return

    # Local database lookups complete before any potentially slow hostname work.
    for item in items:
        if item.get("is_local"):
            continue
        manufacturer = lookup_manufacturer(item.get("mac", ""), allow_online=False)
        _update_result(item.get("ip", ""), {"manufacturer": manufacturer})

    with _scan_lock:
        _scan_message = f"Local manufacturers loaded. Looking up hostnames and web ports for {len(items)} device(s)..."

    mdns_names = _discover_mdns_names(source_ip)

    def lookup_item(item: Dict) -> None:
        global _lookup_done

        if _scan_stop.is_set():
            return

        ip = item.get("ip", "")
        mac = item.get("mac", "")
        web_services = probe_web_services(ip, source_ip)

        if item.get("is_local"):
            _update_result(ip, {"web_services": web_services})
            with _scan_lock:
                _lookup_done += 1
            return

        hostname = lookup_hostname(ip, source_ip, mdns_names)

        if _scan_stop.is_set():
            return

        _update_result(ip, {
            "hostname": hostname,
            "web_services": web_services,
        })

        if MANUFACTURER_ONLINE_FALLBACK and item.get("manufacturer") == "Unknown":
            manufacturer = lookup_manufacturer(mac, allow_online=True)
            if not _scan_stop.is_set():
                _update_result(ip, {"manufacturer": manufacturer})

        with _scan_lock:
            _lookup_done += 1

    workers = min(12, max(4, len(items)))

    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = [executor.submit(lookup_item, item) for item in items]

        for future in as_completed(futures):
            if _scan_stop.is_set():
                break
            try:
                future.result()
            except Exception:
                pass

    with _scan_lock:
        _lookup_running = False
        if _scan_stop.is_set():
            _scan_message = "Lookup stopped."
        else:
            if reused_quick_scan:
                _last_scan_context["quick_scan"] = False
                _scan_message = f"Extended scan complete. Found {len(_scan_results)} device(s)."
            else:
                _scan_message = f"Lookup complete. Found {len(_scan_results)} device(s)."

def _scan_worker(interface_name: str, custom_subnet: str = "", quick_scan: bool = False) -> None:
    global _scan_running, _scan_message, _scan_total, _scan_done
    global _large_scan_quick_only, _scan_results

    ok, nic_or_message = _find_nic(interface_name)
    if not ok:
        with _scan_lock:
            _scan_message = str(nic_or_message)
            _scan_running = False
        return

    nic = nic_or_message
    source_ip = nic["ip"]
    source_mac = nic.get("mac", "")

    if custom_subnet:
        try:
            network = ipaddress.ip_network(custom_subnet, strict=False)
        except ValueError:
            with _scan_lock:
                _scan_message = "Invalid custom subnet. Example: 192.168.1.0/24"
                _scan_running = False
            return
    else:
        network = ipaddress.ip_network(nic["network"], strict=False)

    hosts = [str(host) for host in network.hosts()]
    local_network = ipaddress.ip_network(nic["network"], strict=False)
    scan_targets = [ip for ip in hosts if ip != source_ip]

    with _scan_lock:
        _last_scan_context["interface"] = interface_name
        _last_scan_context["source_ip"] = source_ip
        _last_scan_context["network"] = str(network)
        _last_scan_context["local_network"] = str(local_network)
        _last_scan_context["hosts"] = hosts
        _last_scan_context["quick_scan"] = quick_scan
        _last_scan_context["completed"] = False

    if len(hosts) > _MAX_HOSTS:
        quick_scan = True

        with _scan_lock:
            _large_scan_quick_only = True
            _last_scan_context["quick_scan"] = True
            _scan_message = (
                f"Large subnet {network} has {len(hosts)} hosts. "
                "Forcing quick scan only."
            )
    else:
        with _scan_lock:
            _large_scan_quick_only = False

    with _scan_lock:
        _scan_total = len(scan_targets)
        _scan_done = 0
        _scan_message = f"Scanning {network}..."
    
    if source_ip in hosts:
        _add_result({
            "ip": source_ip,
            "mac": source_mac,
            "manufacturer": "This PC",
            "hostname": socket.gethostname(),
            "missing": False,
            "miss_count": 0,
            "duplicate_ip": False,
            "duplicate_macs": [],
            "seen_macs": [source_mac] if source_mac else [],
            "is_local": True,
            "web_services": [],
        })

    workers = min(100, max(8, len(scan_targets)))

    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {
            executor.submit(
                _discover_host,
                source_ip,
                ip,
                ipaddress.ip_address(ip) in local_network,
                _SCAN_PING_RETRIES,
                _scan_stop,
            ): ip
            for ip in scan_targets
        }

        for future in as_completed(futures):
            if _scan_stop.is_set():
                break

            ip = futures[future]
            alive = False
            mac = ""

            try:
                alive, mac = future.result()
            except Exception:
                alive = False

            with _scan_lock:
                _scan_done += 1

            if alive:
                if mac:
                    _add_result({
                        "ip": ip,
                        "mac": mac,
                        "manufacturer": "-" if quick_scan else "Looking up...",
                        "hostname": "-" if quick_scan else "Looking up...",
                        "missing": False,
                        "miss_count": 0,
                        "duplicate_ip": False,
                        "duplicate_macs": [],
                        "seen_macs": [mac] if mac else [],
                        "web_services": [],
                    })

    with _scan_lock:
        found = len(_scan_results)
        _scan_running = False

        if _scan_stop.is_set():
            _scan_message = "Scan stopped."
            return

        _last_scan_context["completed"] = True
        _scan_message = f"Scan complete. Found {found} device(s)."

    if found and not quick_scan:
        threading.Thread(target=_lookup_worker, daemon=True).start()
    else:
        with _scan_lock:
            _scan_message = f"Quick scan complete. Found {found} device(s)."


def start_scan(interface_name: str, custom_subnet: str = "", quick_scan: bool = False) -> tuple[bool, str]:
    global _scan_thread, _scan_running, _lookup_running, _monitor_running, _monitor_paused
    global _scan_message, _scan_total, _scan_done, _lookup_total, _lookup_done
    global _large_scan_quick_only, _scan_results, _monitor_log

    interface_name = interface_name.strip()
    custom_subnet = custom_subnet.strip()

    if not interface_name:
        return False, "Select a NIC first."

    with _scan_lock:
        if _scan_running or _lookup_running:
            return False, "A scan, lookup or duplicate check is already running."

        _scan_stop.clear()
        _monitor_stop.set()
        _scan_running = True
        _scan_message = "Starting scan..."
        _scan_total = 0
        _scan_done = 0
        _large_scan_quick_only = False
        _lookup_running = False
        _lookup_total = 0
        _lookup_done = 0
        _monitor_running = False
        _monitor_paused = False
        _scan_results = []
        _monitor_log = []

    _scan_thread = threading.Thread(
        target=_scan_worker,
        args=(interface_name, custom_subnet, quick_scan),
        daemon=True
    )
    _scan_thread.start()

    return True, "Scan started."


def start_lookup() -> tuple[bool, str]:
    global _lookup_thread, _lookup_running, _monitor_running, _monitor_paused, _scan_message

    with _scan_lock:
        if _scan_running or _lookup_running:
            return False, "A scan or lookup is already running."

        if (
            not _scan_results
            or not _last_scan_context.get("completed")
            or not _last_scan_context.get("quick_scan")
        ):
            return False, "Run a quick scan before looking up device details."

        if _large_scan_quick_only:
            return False, "Detail lookup is disabled for subnets larger than 1024 hosts."

        _scan_stop.clear()
        _monitor_stop.set()
        _monitor_running = False
        _monitor_paused = False

        for result in _scan_results:
            if not result.get("is_local"):
                result["manufacturer"] = "Looking up..."
                result["hostname"] = "Looking up..."

        _lookup_running = True
        _scan_message = "Quick scan reused. Starting local manufacturer and hostname lookup..."

    _lookup_thread = threading.Thread(
        target=_lookup_worker,
        args=(True,),
        daemon=True,
    )
    _lookup_thread.start()

    return True, "Device detail lookup started."


def stop_scan() -> tuple[bool, str]:
    global _scan_message

    with _scan_lock:
        if not _scan_running and not _lookup_running:
            return False, "No scan or lookup is running."
        stopping_lookup = _lookup_running

    _scan_stop.set()
    if stopping_lookup:
        _set_pending_lookup_values_to_dash()
        with _scan_lock:
            _scan_message = "Stopping lookup..."
        return True, "Stopping lookup..."

    return True, "Stopping scan/lookup..."


def get_scan_status() -> Dict:
    with _scan_lock:
        can_lookup = (
            not _scan_running
            and not _lookup_running
            and bool(_scan_results)
            and bool(_last_scan_context.get("completed"))
            and bool(_last_scan_context.get("quick_scan"))
            and not _large_scan_quick_only
        )
        return {
            "running": _scan_running,
            "lookup_running": _lookup_running,
            "monitor_running": _monitor_running,
            "monitor_paused": _monitor_paused,
            "monitor_log_available": bool(_monitor_log),
            "large_scan_quick_only": _large_scan_quick_only,
            "can_lookup": can_lookup,
            "message": _scan_message,
            "total": _scan_total,
            "done": _scan_done,
            "lookup_total": _lookup_total,
            "lookup_done": _lookup_done,
            "results": list(_scan_results),
        }
