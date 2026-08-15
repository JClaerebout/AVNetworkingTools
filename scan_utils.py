import ipaddress
import re
import socket
import sys
import threading
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, List, Optional

from config import MANUFACTURER_ONLINE_FALLBACK
from nic_utils import get_nics, subnet_to_prefix
from manufacturer_db import lookup_local_manufacturer
from system_utils import run_cmd

_MAX_HOSTS = 1024
_SCAN_PING_RETRIES = 2
_MONITOR_MISSING_AFTER = 3

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
_vendor_cache = {}
_vendor_lock = threading.Lock()


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

def lookup_hostname(ip: str) -> str:
    hostname = _reverse_dns(ip)

    if hostname:
        return hostname

    netbios = _netbios_name(ip)

    if netbios:
        return netbios

    return ""

def _lookup_new_monitor_device(ip: str, mac: str) -> None:
    hostname = lookup_hostname(ip)

    _update_result(ip, {
        "hostname": hostname
    })

    manufacturer = lookup_manufacturer(mac)

    _update_result(ip, {
        "hostname": hostname,
        "manufacturer": manufacturer,
    })


def _monitor_update_device(ip: str, mac: str, quick_scan: bool) -> bool:
    """
    Returns True when this is a newly discovered IP.
    """
    with _scan_lock:
        for result in _scan_results:
            if result["ip"] == ip:
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
        })

        _scan_results.sort(key=lambda x: ipaddress.ip_address(x["ip"]))
        return True


def _monitor_worker() -> None:
    global _monitor_running, _monitor_paused, _scan_message

    with _scan_lock:
        source_ip = _last_scan_context["source_ip"]
        hosts = list(_last_scan_context["hosts"])
        quick_scan = bool(_last_scan_context["quick_scan"])
        network = _last_scan_context["network"]

        _monitor_running = True
        _monitor_paused = False
        _scan_message = f"Monitoring active on {network}..."

    while not _monitor_stop.is_set():
        with _scan_lock:
            paused = _monitor_paused

        if paused:
            with _scan_lock:
                _scan_message = "Monitoring paused because IP Scan page is not active."
            time.sleep(1)
            continue

        seen_ips_this_round = set()

        workers = min(80, max(8, len(hosts)))

        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {
                executor.submit(_ping_host_reliable, source_ip, ip, 2, _monitor_stop): ip
                for ip in hosts
            }

            for future in as_completed(futures):
                if _monitor_stop.is_set():
                    break

                ip = futures[future]

                try:
                    alive = future.result()
                except Exception:
                    alive = False

                if not alive:
                    continue

                arp_entries = _get_arp_entries(source_ip)
                mac = arp_entries.get(ip, "")

                if not mac:
                    continue

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
                    result["miss_count"] = int(result.get("miss_count", 0)) + 1
                    result["missing"] = result["miss_count"] >= _MONITOR_MISSING_AFTER

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


def start_monitor() -> tuple[bool, str]:
    global _monitor_thread, _monitor_running

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

        _monitor_paused = paused

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
        req = urllib.request.Request(url, headers={"User-Agent": "Network-Manager"})

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
        items = [item for item in _scan_results if not item.get("is_local")]
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
        manufacturer = lookup_manufacturer(item.get("mac", ""), allow_online=False)
        _update_result(item.get("ip", ""), {"manufacturer": manufacturer})

    with _scan_lock:
        _scan_message = f"Local manufacturers loaded. Looking up hostnames for {len(items)} device(s)..."

    def lookup_item(item: Dict) -> None:
        if _scan_stop.is_set():
            return

        ip = item.get("ip", "")
        mac = item.get("mac", "")

        hostname = lookup_hostname(ip)

        if _scan_stop.is_set():
            return

        _update_result(ip, {
            "hostname": hostname
        })

        if MANUFACTURER_ONLINE_FALLBACK and item.get("manufacturer") == "Unknown":
            manufacturer = lookup_manufacturer(mac, allow_online=True)
            if not _scan_stop.is_set():
                _update_result(ip, {"manufacturer": manufacturer})

        global _lookup_done
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

    with _scan_lock:
        _last_scan_context["interface"] = interface_name
        _last_scan_context["source_ip"] = source_ip
        _last_scan_context["network"] = str(network)
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
        _scan_total = len(hosts)
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
        })

    workers = min(100, max(8, len(hosts)))

    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {
            executor.submit(_ping_host_reliable, source_ip, ip, _SCAN_PING_RETRIES, _scan_stop): ip
            for ip in hosts
        }

        for future in as_completed(futures):
            if _scan_stop.is_set():
                break

            ip = futures[future]
            alive = False

            try:
                alive = future.result()
            except Exception:
                alive = False

            with _scan_lock:
                _scan_done += 1

            if alive:
                arp_entries = _get_arp_entries(source_ip)
                mac = arp_entries.get(ip, "")

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
    global _large_scan_quick_only, _scan_results

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
            "large_scan_quick_only": _large_scan_quick_only,
            "can_lookup": can_lookup,
            "message": _scan_message,
            "total": _scan_total,
            "done": _scan_done,
            "lookup_total": _lookup_total,
            "lookup_done": _lookup_done,
            "results": list(_scan_results),
        }
