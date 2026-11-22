"""Live IPv4 aliases. No saved address list or automatic persistence."""
import ctypes
import ipaddress
import json
import socket
import sys
import threading
import time

from system_utils import run_powershell

_lock = threading.RLock()


def validate_address(ip, mask):
    address = ipaddress.IPv4Address(ip)
    # IPv4Network also accepts host masks; require a genuine contiguous netmask.
    bits = bin(int(ipaddress.IPv4Address(mask)))[2:].zfill(32)
    if '01' in bits or not 1 <= bits.count('1') <= 32:
        raise ValueError('Choose a contiguous subnet mask with prefix 1–32.')
    network = ipaddress.IPv4Network(f'{address}/{bits.count("1")}', strict=False)
    if (address.is_multicast or address.is_loopback or address.is_unspecified or
            address.is_link_local or int(address) < 0x01000000 or int(address) >= 0xf0000000 or
            (network.prefixlen < 31 and address in (network.network_address, network.broadcast_address))):
        raise ValueError('Choose a usable unicast IPv4 host address.')
    return network


def windows_addresses():
    code, out, err = run_powershell(
        'Get-NetIPAddress -AddressFamily IPv4 -ErrorAction Stop | '
        'Select-Object InterfaceIndex,IPAddress,PrefixLength,PrefixOrigin,SkipAsSource,AddressState | ConvertTo-Json -Compress')
    if code:
        raise RuntimeError(err or out or 'Cannot read IPv4 addresses.')
    rows = json.loads(out or '[]')
    return [rows] if isinstance(rows, dict) else rows or []


def enrich_windows(nics):
    try:
        rows = windows_addresses()
    except Exception as exc:
        for nic in nics:
            nic.update(secondary_ips=[], secondary_safe=False, secondary_error=str(exc))
        return nics
    for nic in nics:
        addresses = [r for r in rows if int(r['InterfaceIndex']) == int(nic['if_index'])]
        primary = [r for r in addresses if not r.get('SkipAsSource')]
        if nic['dhcp_raw'] == 'Enabled':
            primary = [r for r in primary if str(r.get('PrefixOrigin')).lower() in ('dhcp', '3')]
        selected = primary[0] if len(primary) == 1 else None
        # Never guess a primary when OS information is ambiguous.
        nic['secondary_safe'] = selected is not None
        if selected:
            nic['ip'] = selected['IPAddress']
            nic['subnet'] = str(ipaddress.IPv4Network(f"0.0.0.0/{selected['PrefixLength']}").netmask)
        nic['secondary_ips'] = [dict(ip=r['IPAddress'], prefix=int(r['PrefixLength']),
            active=str(r.get('AddressState')).lower() in ('preferred', '4'),
            removable=bool(selected and r.get('SkipAsSource')))
            for r in addresses if r['IPAddress'] != nic['ip']]
    return nics


def mac_addresses(device, primary):
    from platform_backend.macos.nic import _run
    import re
    code, out, err = _run(['/sbin/ifconfig', device])
    if code:
        raise RuntimeError(err or 'Cannot read interface aliases.')
    result = []
    addresses = re.findall(r'^\s*inet (\S+) netmask (\S+)', out, re.M)
    if primary and primary not in {ip for ip, _ in addresses}:
        raise RuntimeError('Primary address is not present on the physical interface. Refresh and retry.')
    for ip, mask in addresses:
        if ip != primary:
            mask = str(ipaddress.IPv4Address(int(mask, 16))) if mask.startswith('0x') else mask
            prefix = ipaddress.IPv4Network(f'{ip}/{mask}', strict=False).prefixlen
            result.append(dict(ip=ip, prefix=prefix, active=True, removable=bool(primary)))
    return result


class _Address(ctypes.Union):
    _fields_ = [('raw', ctypes.c_ubyte * 28), ('alignment', ctypes.c_uint32)]


class _Row(ctypes.Structure):
    _fields_ = [('address', _Address), ('luid', ctypes.c_uint64),
        ('index', ctypes.c_uint32), ('prefix_origin', ctypes.c_uint32),
        ('suffix_origin', ctypes.c_uint32), ('valid', ctypes.c_uint32),
        ('preferred', ctypes.c_uint32), ('prefix', ctypes.c_ubyte),
        ('skip', ctypes.c_ubyte), ('dad', ctypes.c_uint32),
        ('scope', ctypes.c_uint32), ('created', ctypes.c_int64)]


def _windows_change(nic, ip, prefix, remove):
    # IP Helper changes only this unicast row, never the DHCP interface settings.
    api = ctypes.WinDLL('iphlpapi.dll')
    row = _Row()
    api.InitializeUnicastIpAddressEntry(ctypes.byref(row))
    row.address.raw[0] = socket.AF_INET
    for offset, value in enumerate(socket.inet_aton(ip), 4):
        row.address.raw[offset] = value
    row.index = int(nic['if_index'])
    row.prefix = prefix
    row.skip = 1
    row.prefix_origin = row.suffix_origin = 1  # IpPrefixOriginManual
    function = api.DeleteUnicastIpAddressEntry if remove else api.CreateUnicastIpAddressEntry
    result = function(ctypes.byref(row))
    if result:
        raise OSError(result, ctypes.FormatError(result))


def _change(nic, ip, prefix, remove):
    if sys.platform != 'darwin':
        return _windows_change(nic, ip, prefix, remove)
    from platform_backend.macos.nic import _run, _needs_authorization
    command = ['/sbin/ifconfig', nic['device'], 'inet', ip]
    command += ['-alias'] if remove else ['netmask', str(ipaddress.IPv4Network(f'0.0.0.0/{prefix}').netmask), 'alias']
    code, out, err = _run(command)
    if code and _needs_authorization(out, err):
        code, out, err = _run(command, privileged=True)
    if code:
        raise RuntimeError(err or out or 'Alias operation failed.')


def _signature(nic):
    return tuple(nic.get(k) if k != 'dns' else tuple(nic.get(k, []))
                 for k in ('ip', 'subnet', 'dhcp_raw', 'gateway', 'dns', 'metric', 'automatic_metric'))


def change_secondary(index, ip, mask='', remove=False, preview=False, expected_name=None):
    from nic_utils import get_nics
    with _lock:
        changed = False
        nic = None
        prefix = None
        try:
            nics = get_nics()
            nic = next((n for n in nics if str(n['if_index']) == str(index)), None)
            if nic is None:
                raise ValueError('The adapter is unavailable. Refresh the NIC page and retry.')
            if expected_name is not None and nic['name'] != expected_name:
                raise ValueError('The adapter identity changed. Refresh the NIC page and retry.')
            if not nic.get('secondary_safe', bool(nic.get('ip'))) or not nic.get('ip'):
                raise ValueError('Primary address is unavailable or ambiguous. Refresh and retry.')
            ip = str(ipaddress.IPv4Address(ip))
            entries = nic.get('secondary_ips', [])
            warnings = []
            if remove:
                entry = next((a for a in entries if a['ip'] == ip and a.get('removable')), None)
                if ip == nic['ip'] or entry is None:
                    raise ValueError('Only an unambiguous secondary address can be removed.')
                prefix = entry['prefix']
            else:
                network = validate_address(ip, mask)
                prefix = network.prefixlen
                for other in nics:
                    if ip == other.get('ip') or any(a['ip'] == ip for a in other.get('secondary_ips', [])):
                        raise ValueError('This address is already assigned locally.')
                    for address, subnet in [(other.get('ip'), other.get('subnet'))] + [
                            (a['ip'], a['prefix']) for a in other.get('secondary_ips', [])]:
                        if address and subnet and network.overlaps(ipaddress.IPv4Network(f'{address}/{subnet}', strict=False)):
                            warnings.append('Overlapping subnet; routing may be ambiguous.')
                warnings.append('Conflict checks cannot prove an address is unused. Verify it is available on this network.')
            if not remove and sys.platform == 'win32':
                from scan_utils import _arp_probe
                if _arp_probe(nic['ip'], ip):
                    raise ValueError('A device responded to ARP for this address. Choose another IP.')
            if not remove:
                for assigned in _all_local_ipv4():
                    if assigned == ip:
                        raise ValueError('This address is already assigned locally.')
            if preview:
                return True, ' '.join(dict.fromkeys(warnings)) or 'Remove this secondary address?'
            _change(nic, ip, prefix, remove)
            changed = True
            deadline = time.monotonic() + 8
            while True:
                current = next(n for n in get_nics() if n['name'] == nic['name'])
                if current.get('secondary_error') or not current.get('secondary_safe', True):
                    raise RuntimeError('Cannot verify the live secondary address configuration.')
                entry = next((a for a in current.get('secondary_ips', []) if a['ip'] == ip), None)
                if _signature(current) != _signature(nic):
                    raise RuntimeError('Primary configuration changed during the operation.')
                if (remove and entry is None) or (not remove and entry and entry['active'] and entry['prefix'] == prefix):
                    return True, ('Secondary IP removed.' if remove else 'Secondary IP added. ') + ' '.join(dict.fromkeys(warnings))
                if time.monotonic() >= deadline:
                    raise RuntimeError('OS verification failed or duplicate address detection did not complete.')
                time.sleep(.5)
        except Exception as exc:
            rollback = ''
            if changed:
                try:
                    _change(nic, ip, prefix, not remove)
                    rollback = ' Reversal of the secondary address operation was requested; refresh to verify the configuration.'
                except Exception as failure:
                    rollback = f' Rollback failed: {failure}. Check the adapter configuration.'
            return False, str(exc) + rollback


def _restore_secondary(name, previous):
    """Reconcile a confirmed snapshot with live aliases, never saved workspace state."""
    from nic_utils import get_nics
    if 'secondary_ips' not in previous:
        return True, ''
    desired = {a['ip']: a for a in previous['secondary_ips']}
    current = next(n for n in get_nics() if n['name'] == name)
    for entry in current.get('secondary_ips', []):
        if entry['ip'] not in desired:
            if not entry.get('removable'):
                return False, 'Primary restored, but an ambiguous secondary address needs manual review.'
            ok, message = change_secondary(current['if_index'], entry['ip'], remove=True)
            if not ok:
                return False, 'Primary restored; secondary restore incomplete: ' + message
    current = next(n for n in get_nics() if n['name'] == name)
    present = {a['ip']: a for a in current.get('secondary_ips', [])}
    for ip, entry in desired.items():
        if ip in present and present[ip]['prefix'] != entry['prefix']:
            return False, 'Primary restored; a secondary subnet changed. Review it manually.'
        if ip not in present:
            mask = str(ipaddress.IPv4Network(f"0.0.0.0/{entry['prefix']}").netmask)
            ok, message = change_secondary(current['if_index'], ip, mask)
            if not ok:
                return False, 'Primary restored; secondary restore incomplete: ' + message
    return True, ' Secondary addresses restored.'


def source_for_target(target):
    """Bind a secondary source only for an unambiguous most-specific subnet."""
    try:
        destination = ipaddress.IPv4Address(target)
        from nic_utils import get_nics
        matches = []
        for nic in get_nics():
            if nic.get('ip') and nic.get('subnet'):
                network = ipaddress.IPv4Network(f"{nic['ip']}/{nic['subnet']}", strict=False)
                if destination in network:
                    matches.append((network.prefixlen, nic['ip'], False))
            for entry in nic.get('secondary_ips', []):
                network = ipaddress.IPv4Network(f"{entry['ip']}/{entry['prefix']}", strict=False)
                if entry['active'] and destination in network:
                    matches.append((network.prefixlen, entry['ip'], True))
        if matches:
            best = max(m[0] for m in matches)
            selected = [m for m in matches if m[0] == best]
            if len(selected) == 1 and selected[0][2]:
                return selected[0][1]
    except Exception:
        pass
    return ''


def _all_local_ipv4():
    if sys.platform == 'win32':
        return {row['IPAddress'] for row in windows_addresses()}
    if sys.platform == 'darwin':
        import re
        from platform_backend.macos.nic import _run
        code, out, err = _run(['/sbin/ifconfig', '-a'])
        if code:
            raise RuntimeError(err or 'Cannot check local address duplicates.')
        return set(re.findall(r'^\s*inet (\S+)', out, re.M))
    return set()


def restore_secondary(name, previous):
    try:
        return _restore_secondary(name, previous)
    except Exception as exc:
        return False, f' Primary restore was requested, but secondary restore could not be verified: {exc or "adapter unavailable"}.'
