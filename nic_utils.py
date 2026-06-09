import json
import re
import time
from datetime import datetime
from typing import Dict, List, Optional

from history import save_history_entry
import win32com.client
from system_utils import run_cmd, run_powershell


def prefix_to_subnet(prefix: Optional[int]) -> str:
    if prefix is None:
        return ""
    try:
        prefix = int(prefix)
        if prefix < 0 or prefix > 32:
            return ""
        mask = (0xffffffff >> (32 - prefix)) << (32 - prefix) if prefix else 0
        return ".".join(str((mask >> (8 * i)) & 0xff) for i in reversed(range(4)))
    except Exception:
        return ""


def subnet_to_prefix(mask: str) -> Optional[int]:
    try:
        parts = [int(x) for x in mask.split(".")]
        if len(parts) != 4 or any(x < 0 or x > 255 for x in parts):
            return None
        binary = "".join(f"{x:08b}" for x in parts)
        if "01" in binary:
            return None
        return binary.count("1")
    except Exception:
        return None


def clean_dns(dns_value: str) -> List[str]:
    dns_value = dns_value.strip()
    if not dns_value:
        return []
    return [x.strip() for x in re.split(r"[,;\s]+", dns_value) if x.strip()]


def normalize_dhcp_status(value) -> str:
    if isinstance(value, list):
        values = [normalize_dhcp_status(v) for v in value]
        return "dhcp" if "dhcp" in values else "static"

    text = str(value).strip().lower()
    if text in {"enabled", "enable", "true", "1", "yes", "dhcp"}:
        return "dhcp"
    return "static"


def get_wmi():
    return win32com.client.GetObject("winmgmts:")


def is_interface_connected(interface_name: str) -> bool:
    wmi = get_wmi()
    adapters = wmi.ExecQuery(
        f"SELECT * FROM Win32_NetworkAdapter WHERE NetConnectionID = '{interface_name}'"
    )

    for adapter in adapters:
        return adapter.NetConnectionStatus == 2

    return False


def sort_nics(nics: List[Dict]) -> List[Dict]:

    def nic_priority(nic):
        name = str(nic.get("name", "")).lower()
        desc = str(nic.get("description", "")).lower()

        if any(x in name for x in ["ethernet", "lan"]) or "ethernet" in desc:
            device_type = 0
        elif any(x in name for x in ["wifi", "wi-fi", "wireless", "wlan"]) or any(x in desc for x in ["wifi", "wi-fi", "wireless", "wlan"]):
            device_type = 1
        else:
            device_type = 2

        return (
            device_type,
            name,
        )

    return sorted(nics, key=nic_priority)


def get_nics() -> List[Dict]:
    wmi = get_wmi()

    adapters = wmi.ExecQuery(
        "SELECT * FROM Win32_NetworkAdapter WHERE NetConnectionID IS NOT NULL"
    )

    configs = {}
    for config in wmi.ExecQuery("SELECT * FROM Win32_NetworkAdapterConfiguration"):
        if config.InterfaceIndex is not None:
            configs[int(config.InterfaceIndex)] = config

    nics = []

    for adapter in adapters:
        name = adapter.NetConnectionID
        config = configs.get(int(adapter.InterfaceIndex))
        connected = adapter.NetConnectionStatus == 2

        ip = ""
        subnet = ""
        gateway = ""
        dns = []
        dhcp_enabled = False

        if config:
            dhcp_enabled = bool(config.DHCPEnabled)

            if config.IPAddress:
                ipv4 = [
                    x for x in config.IPAddress
                    if "." in x
                ]

                normal_ipv4 = [
                    x for x in ipv4
                    if not x.startswith("169.254.")
                ]

                apipa_ipv4 = [
                    x for x in ipv4
                    if x.startswith("169.254.")
                ]

                if normal_ipv4:
                    ip = normal_ipv4[0]
                elif apipa_ipv4:
                    ip = apipa_ipv4[0]

            if config.IPSubnet:
                subnets = [x for x in config.IPSubnet if "." in x]
                subnet = subnets[0] if subnets else ""

            if config.DefaultIPGateway:
                gateway = config.DefaultIPGateway[0]

            if config.DNSServerSearchOrder:
                dns = list(config.DNSServerSearchOrder)

        if not connected:
            status = "disconnected"
        elif dhcp_enabled and ip.startswith("169.254."):
            status = "dhcp_no_lease"
        elif dhcp_enabled:
            status = "dhcp"
        else:
            status = "static"

        nics.append({
            "name": name,
            "description": adapter.Description or "",
            "if_index": adapter.InterfaceIndex,
            "mac": adapter.MACAddress or "",
            "link_status": "Up" if connected else "Disconnected",
            "admin_status": "",
            "dhcp_raw": "Enabled" if dhcp_enabled else "Disabled",
            "status": status,
            "ip": ip,
            "prefix": "",
            "subnet": subnet,
            "gateway": gateway,
            "dns": dns,
            "dns1": dns[0] if len(dns) > 0 else "",
            "dns2": dns[1] if len(dns) > 1 else "",
        })

    return sort_nics(nics)


def set_dhcp(interface_name: str) -> tuple[bool, str]:
    if not is_interface_connected(interface_name):
        return False, f"Cannot enable DHCP on '{interface_name}' because the NIC is disconnected. Connect the cable or enable the adapter first, then try again."

    commands = [
        ["netsh", "interface", "ip", "set", "address", f"name={interface_name}", "source=dhcp"],
        ["netsh", "interface", "ip", "set", "dns", f"name={interface_name}", "source=dhcp"],
    ]

    output = []
    for cmd in commands:
        code, stdout, stderr = run_cmd(cmd)
        output.append(stdout or stderr)
        if code != 0:
            return False, "\n".join(output)

    return True, "DHCP enabled for IP and DNS."

def check_windows_ip_duplicate(interface_name: str, ip: str) -> tuple[bool, str]:
    script = f"""
$ip = Get-NetIPAddress -InterfaceAlias {json.dumps(interface_name)} -IPAddress {json.dumps(ip)} -ErrorAction SilentlyContinue
if ($ip) {{ [string]$ip.AddressState }} else {{ 'NotFound' }}
"""
    code, stdout, stderr = run_powershell(script)

    if code != 0:
        return False, ""

    state = stdout.strip()

    if state.lower() == "duplicate":
        return True, state

    return False, state

def set_static(interface_name: str, ip: str, subnet: str, gateway: str, dns_servers: List[str]) -> tuple[bool, str]:
    if not is_interface_connected(interface_name):
        return False, f"Cannot set a static address on '{interface_name}' because the NIC is disconnected. Connect the cable or enable the adapter first, then try again."

    if not ip or not subnet:
        return False, "IP and subnet are required for static mode."

    if subnet_to_prefix(subnet) is None:
        return False, "Invalid subnet mask. Example: 255.255.255.0"

    cmd = [
        "netsh", "interface", "ip", "set", "address",
        f"name={interface_name}",
        "source=static",
        f"address={ip}",
        f"mask={subnet}",
    ]

    if gateway:
        cmd.append(f"gateway={gateway}")
        cmd.append("gwmetric=1")
    else:
        cmd.append("gateway=none")

    code, stdout, stderr = run_cmd(cmd)
    if code != 0:
        return False, stderr or stdout or "Failed to set static IP."

    if dns_servers:
        code, stdout, stderr = run_cmd([
            "netsh", "interface", "ip", "set", "dns",
            f"name={interface_name}",
            "source=static",
            f"address={dns_servers[0]}",
            "primary",
        ])
        if code != 0:
            return False, "Static IP was set, but DNS failed: " + (stderr or stdout)

        for index, dns in enumerate(dns_servers[1:], start=2):
            code, stdout, stderr = run_cmd([
                "netsh", "interface", "ip", "add", "dns",
                f"name={interface_name}",
                f"address={dns}",
                f"index={index}",
            ])
            if code != 0:
                return False, "Static IP was set, but extra DNS failed: " + (stderr or stdout)
    else:
        code, stdout, stderr = run_cmd([
            "netsh", "interface", "ip", "set", "dns",
            f"name={interface_name}",
            "source=dhcp",
        ])
        if code != 0:
            return False, "Static IP was set, but DNS reset failed: " + (stderr or stdout)

    save_history_entry({
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "interface": interface_name,
        "ip": ip,
        "subnet": subnet,
        "gateway": gateway,
        "dns1": dns_servers[0] if len(dns_servers) > 0 else "",
        "dns2": dns_servers[1] if len(dns_servers) > 1 else "",
    })

    time.sleep(2.0)

    conflict_found, address_state = check_windows_ip_duplicate(interface_name, ip)

    if conflict_found:
        return True, (
            f"Static network settings applied, but WARNING: "
            f"Windows detected an IP conflict for {ip}. "
            f"Address state: {address_state}."
        )

    return True, "Static network settings applied. No IP conflict detected."


def release_dhcp(interface_name: str) -> tuple[bool, str]:
    if not is_interface_connected(interface_name):
        return False, f"Cannot release DHCP on '{interface_name}' because the NIC is disconnected."

    code, stdout, stderr = run_cmd(["ipconfig", "/release", interface_name])
    if code != 0:
        return False, stderr or stdout or f"Failed to release DHCP lease on '{interface_name}'."
    return True, f"DHCP lease released on '{interface_name}'."


def renew_dhcp(interface_name: str) -> tuple[bool, str]:
    if not is_interface_connected(interface_name):
        return False, f"Cannot renew DHCP on '{interface_name}' because the NIC is disconnected."

    code, stdout, stderr = run_cmd(["ipconfig", "/renew", interface_name])
    if code != 0:
        return False, stderr or stdout or f"Failed to renew DHCP lease on '{interface_name}'."
    return True, f"DHCP lease renewed on '{interface_name}'."
