"""macOS network service configuration using supported command-line interfaces."""
import ipaddress
import re
import shlex
import threading
from datetime import datetime

from history import save_history_entry
from system_utils import run_cmd


def _run(args, *, privileged=False, timeout=30):
    if privileged:
        # AppleScript requests authorization for this one command only.
        script = "do shell script " + _apple_quote(" ".join(shlex.quote(str(a)) for a in args)) + " with administrator privileges"
        code, out, err = run_cmd(["/usr/bin/osascript", "-e", script], timeout=timeout)
        if code and "User canceled" in err:
            return code, out, "Authorization was cancelled."
        return code, out, err
    return run_cmd(args, timeout=timeout)


def _apple_quote(value):
    return '"' + value.replace('\\', '\\\\').replace('"', '\\"') + '"'


_previous = {}
_previous_lock = threading.Lock()

def _remember(name):
    previous = next((n for n in get_nics() if n["name"] == name), None)
    with _previous_lock:
        if previous:
            _previous[name] = previous

def _ordered_services():
    code, out, err = _run(["/usr/sbin/networksetup", "-listnetworkserviceorder"])
    if code:
        raise RuntimeError(err or "Could not list network services.")
    rows = []
    for match in re.finditer(r"^\((\d+)\) (.+)\n\(Hardware Port: .+, Device: ([^\)]*)\)", out, re.M):
        rows.append((int(match[1]), match[2].lstrip("*"), match[3], match[2].startswith("*")))
    return rows


def _services():
    """Physical services shown as NIC cards."""
    return [entry for entry in _ordered_services() if entry[2]]


def _all_service_names():
    code, out, err = _run(["/usr/sbin/networksetup", "-listallnetworkservices"])
    if code:
        raise RuntimeError(err or "Could not list all network services.")
    return [line.removeprefix("*") for line in out.splitlines()
            if line and not line.startswith("An asterisk")]


def _ipv4(service):
    code, out, _ = _run(["/usr/sbin/networksetup", "-getinfo", service])
    if code:
        return {}
    result = {}
    for line in out.splitlines():
        if ": " in line:
            key, value = line.split(": ", 1)
            result[key] = value.strip()
    result["_dhcp"] = "DHCP Configuration" in out
    return result


def _dns(service, device):
    code, out, _ = _run(["/usr/sbin/networksetup", "-getdnsservers", service])
    if not code and "aren't any DNS" not in out:
        return out.splitlines()
    code, out, _ = _run(["/usr/sbin/scutil", "--dns"] )
    if code:
        return []
    for block in re.split(r"\n\s*resolver #\d+", out):
        if re.search(r"\bif_index\s*:\s*\d+\s*\(" + re.escape(device) + r"\)", block):
            return re.findall(r"nameserver\[\d+\]\s*:\s*([^\s]+)", block)
    return []


def _link(device):
    code, out, _ = _run(["/sbin/ifconfig", device])
    if code:
        return False, ""
    mac = re.search(r"\bether ([0-9a-f:]+)", out)
    status = re.search(r"^[ \t]*status:\s*(\S+)", out, re.M)
    connected = status[1].lower() == "active" if status else "RUNNING" in out
    return connected, mac[1].upper() if mac else ""


def get_nics():
    result = []
    ordered_services = _ordered_services()
    for order, service, device, disabled in ordered_services:
        if not device or disabled:
            continue
        connected, mac = _link(device)
        if not connected:
            continue
        info = _ipv4(service)
        ip = info.get("IP address", "")
        if ip == "none":
            ip = ""
        subnet = info.get("Subnet mask", "")
        if subnet == "none":
            subnet = ""
        gateway = info.get("Router", "")
        if gateway == "none":
            gateway = ""
        dns = _dns(service, device)
        dhcp = info.get("_dhcp", False)
        # networksetup -getinfo prints a DHCP Configuration section for DHCP services.
        result.append({"name": service, "description": device, "device": device,
                       "if_index": order, "metric": order, "automatic_metric": False,
                       "order_count": len(ordered_services),
                       "mac": mac, "link_status": "Up" if connected and not disabled else "Disconnected",
                       "admin_status": "Disabled" if disabled else "Enabled",
                       "dhcp_raw": "Enabled" if dhcp else "Disabled",
                       "status": "disconnected" if not connected or disabled else "dhcp_no_lease" if dhcp and not ip else "dhcp" if dhcp else "static",
                       "ip": ip, "prefix": "", "subnet": subnet, "gateway": gateway,
                       "dns": dns, "dns1": dns[0] if dns else "", "dns2": dns[1] if len(dns) > 1 else ""})
    return result


def is_interface_connected(name):
    return any(n["name"] == name and n["link_status"] == "Up" for n in get_nics())


def _known(name):
    if not any(service == name for _, service, _, disabled in _services() if not disabled):
        return False, "Network service is unavailable or disabled."
    return True, ""


def _apply(args):
    command = ["/usr/sbin/networksetup", *args]
    code, out, err = _run(command)
    if code and _needs_authorization(out, err):
        code, out, err = _run(command, privileged=True)
    return code == 0, err or out or ("Network settings applied." if code == 0 else "Could not change network settings.")


def _needs_authorization(stdout, stderr):
    text = f"{stdout}\n{stderr}".lower()
    return any(marker in text for marker in (
        "requires admin privileges", "requires administrator privileges",
        "requires root privileges", "you need to be root",
        "not authorized", "insufficient privileges", "permission denied",
    ))


def _apply_batch(commands):
    """Try the user's rights first; authorize IP and DNS together if needed."""
    script = " && ".join(
        " ".join(shlex.quote(str(part)) for part in ["/usr/sbin/networksetup", *args])
        for args in commands
    )
    command = ["/bin/sh", "-c", script]
    code, out, err = _run(command, timeout=60)
    if code and _needs_authorization(out, err):
        code, out, err = _run(command, privileged=True, timeout=60)
    return code == 0, err or out or ("Network settings applied." if code == 0 else "Could not change network settings.")


def set_dhcp(name):
    valid, message = _known(name)
    if not valid:
        return False, message
    _remember(name)
    return _apply_batch([["-setdhcp", name], ["-setdnsservers", name, "Empty"]])


def set_static(name, ip, subnet, gateway, dns_servers):
    valid, message = _known(name)
    if not valid:
        return False, message
    try:
        network = ipaddress.IPv4Network(f"{ip}/{subnet}", strict=False)
        address = ipaddress.IPv4Address(ip)
        if address.is_multicast or address.is_unspecified or address.is_loopback or address in (network.network_address, network.broadcast_address):
            raise ValueError("Choose a usable IPv4 address.")
        if gateway and ipaddress.IPv4Address(gateway) not in network:
            raise ValueError("Gateway must be in the selected subnet.")
        for dns in dns_servers:
            ipaddress.IPv4Address(dns)
    except ValueError as exc:
        return False, f"Invalid network settings: {exc}"
    _remember(name)
    ok, message = _apply_batch([
        ["-setmanual", name, ip, subnet, gateway or "none"],
        ["-setdnsservers", name, *(dns_servers or ["Empty"])],
    ])
    if ok:
        save_history_entry({"timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "interface": name,
                            "ip": ip, "subnet": subnet, "gateway": gateway,
                            "dns1": dns_servers[0] if dns_servers else "", "dns2": dns_servers[1] if len(dns_servers) > 1 else ""})
    return ok, message


def release_dhcp(name):
    valid, message = _known(name)
    if not valid:
        return False, message
    device = next(device for _, service, device, _ in _services() if service == name)
    command = ["/usr/sbin/ipconfig", "set", device, "NONE"]
    code, out, err = _run(command)
    if code and _needs_authorization(out, err):
        code, out, err = _run(command, privileged=True)
    return code == 0, err or out or "DHCP lease released."


def renew_dhcp(name):
    valid, message = _known(name)
    if not valid:
        return False, message
    device = next(device for _, service, device, _ in _services() if service == name)
    info = _ipv4(name)
    address = info.get("IP address", "")
    if address and address != "none" and not address.startswith("169.254."):
        # Re-evaluate an active DHCP service without changing its saved settings.
        command = ["/usr/sbin/scutil", "--renew", device]
        code, out, err = _run(command)
        if code and _needs_authorization(out, err):
            code, out, err = _run(command, privileged=True)
        return code == 0, err or out or ("DHCP lease renewal requested." if code == 0 else "Could not renew DHCP lease.")

    # A released interface can have a temporary ipconfig service that does not
    # restore the saved service's address or router. Reset IPv4 as macOS does
    # when switching away from DHCP and back in Network settings.
    ok, message = _apply(["-setv4off", name])
    if not ok:
        return False, message
    ok, message = _apply_batch([["-setdhcp", name], ["-setdnsservers", name, "Empty"]])
    if not ok:
        return False, f"Could not restore DHCP after turning IPv4 off: {message}"
    return True, "DHCP settings restored; waiting for an address from the server."


def restore_previous_config(name):
    with _previous_lock:
        previous = _previous.get(name)
    if not previous:
        return False, "No previous settings are available for this service in this session."
    if previous["dhcp_raw"] == "Enabled":
        return set_dhcp(name)
    return set_static(name, previous["ip"], previous["subnet"], previous["gateway"], previous["dns"])


def set_interface_metric(index, metric):
    try:
        selected = int(index)
        requested = int(metric)
    except (TypeError, ValueError):
        return False, "Choose a network service order number."
    services = _ordered_services()
    if not 1 <= requested <= len(services):
        return False, "Invalid service order."
    chosen = next(((service, disabled) for order, service, device, disabled in services
                   if order == selected and device), None)
    if chosen is None or chosen[1]:
        return False, "Network service is unavailable or disabled."
    names = [service for _, service, _, _ in services]
    if sorted(names) != sorted(_all_service_names()):
        return False, "macOS reported an incomplete service list. Refresh NICs and retry."
    names.remove(chosen[0])
    names.insert(requested - 1, chosen[0])
    return _apply(["-ordernetworkservices", *names])
